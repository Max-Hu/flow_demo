from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import (
        complete_callback_wait_activity,
        complete_manual_wait_activity,
        create_callback_wait_activity,
        create_manual_wait_activity,
        create_scheduled_run_activity,
        execute_node_activity,
        mark_node_failed_activity,
        mark_node_skipped_activity,
        mark_run_activity,
    )


TERMINAL_SUCCESS = {"SUCCESS", "SKIPPED"}


def _node_data(node: dict[str, Any]) -> dict[str, Any]:
    return node.get("data", {})


def _active_edge(edge: dict[str, Any], upstream_status: str, upstream_output: dict[str, Any] | None, upstream_type: str) -> bool:
    if upstream_status != "SUCCESS":
        return False
    if upstream_type != "condition":
        return True
    return bool(upstream_output) and upstream_output.get("branch") == edge.get("sourceHandle")


def _max_attempts(node_data: dict[str, Any]) -> int:
    config = node_data.get("config", {})
    if node_data.get("nodeType") == "http_poll":
        return max(1, min(int(config.get("maxPolls", 60)), 1000))
    return max(1, min(int(config.get("maxAttempts", 1)), 5))


def _activity_timeout(node_data: dict[str, Any]) -> timedelta:
    config = node_data.get("config", {})
    seconds = max(float(config.get("requestTimeoutSeconds", config.get("timeoutSeconds", 30))), 1)
    return timedelta(seconds=min(seconds + 5, 120))


@workflow.defn
class GenericFlowWorkflow:
    def __init__(self) -> None:
        self._manual_signals: dict[str, dict[str, Any]] = {}
        self._callback_signals: dict[str, dict[str, Any]] = {}

    @workflow.signal
    def resume_manual(self, payload: dict[str, Any]) -> None:
        self._manual_signals[payload["node_id"]] = payload

    @workflow.signal
    def receive_callback(self, payload: dict[str, Any]) -> None:
        self._callback_signals[payload["node_id"]] = payload

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "schedule_id" in payload:
            payload = await workflow.execute_activity(
                create_scheduled_run_activity,
                {
                    "schedule_id": payload["schedule_id"],
                    "workflow_id": workflow.info().workflow_id,
                },
                start_to_close_timeout=timedelta(seconds=15),
            )
        run_id = payload["run_id"]
        content = payload["flow_content"]
        nodes = {node["id"]: node for node in content["nodes"]}
        node_types = {node_id: _node_data(node).get("nodeType") for node_id, node in nodes.items()}
        incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        for edge in content["edges"]:
            incoming[edge["target"]].append(edge)
            outgoing[edge["source"]].append(edge)

        statuses = {node_id: "PENDING" for node_id in nodes}
        outputs: dict[str, dict[str, Any]] = {}
        attempts = {node_id: 0 for node_id in nodes}

        await workflow.execute_activity(
            mark_run_activity,
            {"run_id": run_id, "status": "RUNNING", "event_type": "RUN_STARTED"},
            start_to_close_timeout=timedelta(seconds=10),
        )

        while True:
            progressed = False
            for node_id in sorted(nodes):
                if statuses[node_id] != "PENDING":
                    continue
                upstream_edges = incoming[node_id]
                if not upstream_edges:
                    executable = True
                    active_edges: list[dict[str, Any]] = []
                else:
                    upstream_done = all(statuses[edge["source"]] in TERMINAL_SUCCESS for edge in upstream_edges)
                    if not upstream_done:
                        continue
                    active_edges = [
                        edge
                        for edge in upstream_edges
                        if _active_edge(
                            edge,
                            statuses[edge["source"]],
                            outputs.get(edge["source"]),
                            str(node_types.get(edge["source"])),
                        )
                    ]
                    executable = bool(active_edges)

                if not executable:
                    statuses[node_id] = "SKIPPED"
                    await workflow.execute_activity(
                        mark_node_skipped_activity,
                        {"run_id": run_id, "node_id": node_id},
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                    progressed = True
                    continue

                inputs = self._collect_inputs(active_edges, outputs, node_types, payload["input_data"])
                node = nodes[node_id]
                data = _node_data(node)
                attempts[node_id] += 1
                try:
                    result = await self._execute_node(run_id, node_id, data, inputs, attempts[node_id])
                except Exception as exc:
                    await workflow.execute_activity(
                        mark_node_failed_activity,
                        {"run_id": run_id, "node_id": node_id, "error": str(exc)},
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                    return {"status": "FAILED", "error": str(exc)}

                while result.get("kind") == "poll_pending":
                    await workflow.sleep(float(result.get("delay_seconds", 1)))
                    attempts[node_id] += 1
                    try:
                        result = await self._execute_node(run_id, node_id, data, inputs, attempts[node_id])
                    except Exception as exc:
                        await workflow.execute_activity(
                            mark_node_failed_activity,
                            {"run_id": run_id, "node_id": node_id, "error": str(exc)},
                            start_to_close_timeout=timedelta(seconds=10),
                        )
                        return {"status": "FAILED", "error": str(exc)}

                statuses[node_id] = "SUCCESS"
                outputs[node_id] = result.get("output", {})
                progressed = True

            if all(status in TERMINAL_SUCCESS for status in statuses.values()):
                end_outputs = {
                    node_id: outputs.get(node_id, {})
                    for node_id, node_type in node_types.items()
                    if node_type == "end" and statuses[node_id] == "SUCCESS"
                }
                output = next(iter(end_outputs.values())) if len(end_outputs) == 1 else end_outputs
                await workflow.execute_activity(
                    mark_run_activity,
                    {
                        "run_id": run_id,
                        "status": "SUCCESS",
                        "output": output,
                        "event_type": "RUN_SUCCEEDED",
                    },
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return {"status": "SUCCESS", "output": output}

            if not progressed:
                error = "Workflow made no progress; graph may be invalid"
                await workflow.execute_activity(
                    mark_run_activity,
                    {
                        "run_id": run_id,
                        "status": "FAILED",
                        "error": error,
                        "event_type": "RUN_FAILED",
                    },
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return {"status": "FAILED", "error": error}

    def _collect_inputs(
        self,
        active_edges: list[dict[str, Any]],
        outputs: dict[str, dict[str, Any]],
        node_types: dict[str, str],
        run_input: dict[str, Any],
    ) -> dict[str, Any]:
        if not active_edges:
            return run_input
        collected: dict[str, dict[str, Any]] = {}
        for edge in active_edges:
            source = edge["source"]
            output = outputs.get(source, {})
            if node_types.get(source) == "condition":
                output = output.get("data", {})
            collected[source] = output
        if len(collected) == 1:
            return next(iter(collected.values()))
        return {"sources": collected}

    async def _execute_node(
        self,
        run_id: str,
        node_id: str,
        node_data: dict[str, Any],
        inputs: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        node_type = str(node_data.get("nodeType"))
        base = {
            "run_id": run_id,
            "node_id": node_id,
            "node_data": node_data,
            "config": node_data.get("config", {}),
            "inputs": inputs,
            "attempt": attempt,
        }
        if node_type == "manual_approval":
            await workflow.execute_activity(
                create_manual_wait_activity,
                base,
                start_to_close_timeout=timedelta(seconds=10),
            )
            await workflow.wait_condition(lambda: node_id in self._manual_signals)
            signal = self._manual_signals.pop(node_id)
            return await workflow.execute_activity(
                complete_manual_wait_activity,
                {**base, **signal},
                start_to_close_timeout=timedelta(seconds=10),
            )
        if node_type == "http_callback":
            await workflow.execute_activity(
                create_callback_wait_activity,
                base,
                start_to_close_timeout=timedelta(seconds=10),
            )
            await workflow.wait_condition(lambda: node_id in self._callback_signals)
            signal = self._callback_signals.pop(node_id)
            return await workflow.execute_activity(
                complete_callback_wait_activity,
                {**base, **signal},
                start_to_close_timeout=timedelta(seconds=10),
            )

        try:
            return await workflow.execute_activity(
                execute_node_activity,
                base,
                start_to_close_timeout=_activity_timeout(node_data),
                retry_policy=RetryPolicy(maximum_attempts=_max_attempts(node_data)),
            )
        except ActivityError:
            raise
