from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from temporalio import activity

from app.callbacks import callback_url, create_callback_wait
from app.database import SessionLocal
from app.enums import NodeRunStatus, RunStatus
from app.flow_config import config_validation_errors, deep_merge
from app.models import ApprovalGroup, ApprovalTask, FlowEvent, FlowRun, FlowSchedule, NodeRun, NodeRunAttempt, utc_now
from app.nodes import get_node_handler
from app.nodes.base import NodeContext, PollPending
from app.security.redaction import redact_sensitive
from app.template_resolver import resolve_templates


def _emit(db, run: FlowRun, event_type: str, payload: dict[str, Any], node_id: str | None = None) -> None:
    db.add(
        FlowEvent(
            group_id=run.group_id,
            flow_run_id=run.id,
            node_id=node_id,
            event_type=event_type,
            payload=redact_sensitive(payload),
        )
    )


def _load_run_and_node(db, run_id: str, node_id: str) -> tuple[FlowRun, NodeRun]:
    run = db.scalar(
        select(FlowRun)
        .where(FlowRun.id == run_id)
        .options(
            selectinload(FlowRun.flow_version),
            selectinload(FlowRun.node_runs),
            selectinload(FlowRun.variables),
        )
    )
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    node_run = next((item for item in run.node_runs if item.node_id == node_id), None)
    if node_run is None:
        raise ValueError(f"Node run not found: {node_id}")
    return run, node_run


@activity.defn
def create_scheduled_run_activity(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        schedule = db.scalar(
            select(FlowSchedule)
            .where(FlowSchedule.id == payload["schedule_id"])
            .options(
                selectinload(FlowSchedule.flow),
                selectinload(FlowSchedule.flow_version),
            )
        )
        if schedule is None or not schedule.enabled:
            raise ValueError("Schedule is not available")
        run = FlowRun(
            group_id=schedule.group_id,
            flow=schedule.flow,
            flow_version=schedule.flow_version,
            status=RunStatus.PENDING,
            input_data=schedule.input_data,
            flow_config=schedule.flow_version.default_config,
            trigger_type="SCHEDULE",
            trigger_id=schedule.id,
            temporal_workflow_id=payload["workflow_id"],
            source_metadata={
                "scheduleName": schedule.name,
                "cronExpression": schedule.cron_expression,
                "timezone": schedule.timezone,
            },
        )
        db.add(run)
        db.flush()
        for node in schedule.flow_version.content["nodes"]:
            data = node["data"]
            config = data.get("config", {})
            max_attempts = (
                max(1, min(int(config.get("maxPolls", 60)), 1000))
                if data["nodeType"] == "http_poll"
                else max(1, min(int(config.get("maxAttempts", 1)), 5))
            )
            db.add(
                NodeRun(
                    flow_run_id=run.id,
                    node_id=node["id"],
                    node_type=data["nodeType"],
                    node_version=data.get("nodeVersion", "1.0"),
                    status=NodeRunStatus.PENDING,
                    config=config,
                    max_attempts=max_attempts,
                )
            )
        schedule.last_triggered_at = utc_now()
        _emit(
            db,
            run,
            "RUN_CREATED",
            {"status": RunStatus.PENDING, "version": schedule.flow_version.version_number},
        )
        db.commit()
        return {
            "group_id": run.group_id,
            "run_id": run.id,
            "flow_id": run.flow_id,
            "flow_version_id": run.flow_version_id,
            "flow_content": schedule.flow_version.content,
            "input_data": run.input_data,
            "flow_config": run.flow_config,
            "trigger_type": run.trigger_type,
            "trigger_id": run.trigger_id,
        }


def _template_data(run: FlowRun, node_run: NodeRun, inputs: dict[str, Any], context: NodeContext) -> dict[str, Any]:
    return {
        "input": inputs,
        "variables": context.variables(),
        "run": {
            "id": run.id,
            "triggerType": run.trigger_type,
            "nodeId": node_run.node_id,
            "attempt": node_run.attempts,
        },
        "flowConfig": run.flow_config,
    }


def _apply_flow_config_patch(db, run: FlowRun, node_run: NodeRun, patch: Any, template_data: dict[str, Any]) -> None:
    if patch is None:
        return
    resolved = resolve_templates(patch, template_data)
    if not resolved:
        return
    if not isinstance(resolved, dict):
        raise ValueError("flowConfigPatch must resolve to an object")
    next_config = deep_merge(run.flow_config, resolved)
    errors = config_validation_errors(run.flow_version.config_schema, next_config)
    if errors:
        raise ValueError("flowConfigPatch created invalid flow configuration: " + "; ".join(errors))
    run.flow_config = next_config
    _emit(db, run, "FLOW_CONFIG_PATCHED", {"patch": resolved, "flowConfig": next_config}, node_run.node_id)


@activity.defn
def mark_run_activity(payload: dict[str, Any]) -> None:
    with SessionLocal() as db:
        run = db.get(FlowRun, payload["run_id"])
        if run is None:
            return
        status = payload["status"]
        run.status = status
        if status == RunStatus.RUNNING and run.started_at is None:
            run.started_at = utc_now()
        if status in {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED}:
            run.finished_at = utc_now()
        if "output" in payload:
            run.output_data = payload["output"]
        if "error" in payload:
            run.error_message = payload["error"]
        _emit(db, run, payload.get("event_type", f"RUN_{status}"), payload, None)
        db.commit()


@activity.defn
def mark_node_skipped_activity(payload: dict[str, Any]) -> None:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        node_run.status = NodeRunStatus.SKIPPED
        node_run.finished_at = utc_now()
        _emit(db, run, "NODE_SKIPPED", {"status": NodeRunStatus.SKIPPED}, node_run.node_id)
        db.commit()


@activity.defn
def mark_node_failed_activity(payload: dict[str, Any]) -> None:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        node_run.status = NodeRunStatus.FAILED
        node_run.error_message = payload.get("error", "Node failed")
        node_run.finished_at = utc_now()
        run.status = RunStatus.FAILED
        run.error_message = f"Node '{node_run.node_id}' failed: {node_run.error_message}"
        run.finished_at = utc_now()
        _emit(db, run, "NODE_FAILED", {"status": node_run.status, "error": node_run.error_message}, node_run.node_id)
        _emit(db, run, "RUN_FAILED", {"status": run.status, "error": run.error_message})
        db.commit()


@activity.defn
def execute_node_activity(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        inputs = payload.get("inputs", {})
        node_data = payload.get("node_data", {})
        node_run.status = NodeRunStatus.RUNNING
        node_run.attempts = max(
            int(payload.get("attempt", node_run.attempts + 1)),
            int(activity.info().attempt),
        )
        node_run.started_at = node_run.started_at or utc_now()
        node_run.input_data = inputs
        node_run.available_at = utc_now()
        attempt = db.scalar(
            select(NodeRunAttempt).where(
                NodeRunAttempt.node_run_id == node_run.id,
                NodeRunAttempt.attempt_number == node_run.attempts,
            )
        )
        if attempt is None:
            attempt = NodeRunAttempt(
                node_run_id=node_run.id,
                attempt_number=node_run.attempts,
                status=NodeRunStatus.RUNNING,
                started_at=utc_now(),
            )
            db.add(attempt)
        else:
            attempt.status = NodeRunStatus.RUNNING
            attempt.error_message = None
            attempt.finished_at = None
        _emit(db, run, "NODE_STARTED", {"status": node_run.status, "attempt": node_run.attempts}, node_run.node_id)
        db.flush()

        context = NodeContext(
            run_id=run.id,
            node_run_id=node_run.id,
            node_id=node_run.node_id,
            attempt=node_run.attempts,
            idempotency_key=f"{run.id}:{node_run.node_id}",
            db=db,
            flow_id=run.flow_id,
            flow_config_data=run.flow_config,
        )
        template_data = _template_data(run, node_run, inputs, context)
        try:
            resolved_config = resolve_templates(node_run.config, template_data)
            output = get_node_handler(node_run.node_type, node_run.node_version).execute(
                inputs, resolved_config, context
            )
            if isinstance(output, PollPending):
                safe_last_output = redact_sensitive(output.last_output)
                node_run.output_data = safe_last_output
                node_run.status = NodeRunStatus.POLL_WAIT
                node_run.error_message = None
                node_run.available_at = utc_now() + timedelta(seconds=output.delay_seconds)
                attempt.status = NodeRunStatus.POLL_WAIT
                attempt.finished_at = utc_now()
                _emit(
                    db,
                    run,
                    "NODE_POLL_WAIT",
                    {
                        "status": node_run.status,
                        "pollCount": node_run.attempts,
                        "maxPolls": node_run.max_attempts,
                        "nextPollAt": node_run.available_at.isoformat(),
                        "lastOutput": safe_last_output,
                    },
                    node_run.node_id,
                )
                db.commit()
                return {
                    "kind": "poll_pending",
                    "delay_seconds": output.delay_seconds,
                    "last_output": safe_last_output,
                }

            output = redact_sensitive(output)
            _apply_flow_config_patch(db, run, node_run, node_data.get("flowConfigPatch"), template_data)
            node_run.output_data = output
            node_run.status = NodeRunStatus.SUCCESS
            node_run.error_message = None
            node_run.finished_at = utc_now()
            attempt.status = NodeRunStatus.SUCCESS
            attempt.finished_at = utc_now()
            _emit(db, run, "NODE_SUCCEEDED", {"status": node_run.status, "output": output}, node_run.node_id)
            db.commit()
            return {"kind": "success", "output": output}
        except Exception as exc:
            node_run.error_message = str(exc)
            node_run.status = NodeRunStatus.FAILED
            attempt.status = NodeRunStatus.FAILED
            attempt.error_message = str(exc)
            attempt.finished_at = utc_now()
            _emit(db, run, "NODE_FAILED", {"status": node_run.status, "error": str(exc)}, node_run.node_id)
            db.commit()
            raise


@activity.defn
def create_manual_wait_activity(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        prompt = str(payload.get("config", {}).get("prompt", "Manual action required"))
        approval_group_alias = str(payload.get("config", {}).get("approvalGroupRef", "")).strip().lower()
        approval_group_id = None
        if approval_group_alias:
            approval_group = db.scalar(
                select(ApprovalGroup).where(
                    ApprovalGroup.group_id == run.group_id,
                    ApprovalGroup.alias == approval_group_alias,
                )
            )
            if approval_group is None:
                raise ValueError(f"Approval group '{approval_group_alias}' does not exist")
            approval_group_id = approval_group.id
        node_run.input_data = payload.get("inputs", {})
        node_run.status = NodeRunStatus.WAITING
        node_run.attempts = max(node_run.attempts, int(payload.get("attempt", 1)))
        run.status = RunStatus.WAITING
        task = db.scalar(
            select(ApprovalTask).where(
                ApprovalTask.flow_run_id == run.id,
                ApprovalTask.node_id == node_run.node_id,
            )
        )
        if task is None:
            task = ApprovalTask(
                group_id=run.group_id,
                approval_group_id=approval_group_id,
                flow_run_id=run.id,
                node_run_id=node_run.id,
                node_id=node_run.node_id,
                prompt=prompt,
            )
            db.add(task)
        elif approval_group_id and task.approval_group_id != approval_group_id:
            task.approval_group_id = approval_group_id
        _emit(
            db,
            run,
            "NODE_WAITING",
            {
                "status": node_run.status,
                "prompt": prompt,
                "approvalGroupRef": approval_group_alias or None,
            },
            node_run.node_id,
        )
        _emit(db, run, "RUN_WAITING", {"status": run.status})
        db.commit()
        return {"task_id": task.id, "prompt": prompt}


@activity.defn
def complete_manual_wait_activity(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        output = {
            **(node_run.input_data or {}),
            **payload.get("data", {}),
            "manualDecision": payload.get("decision", "CONTINUE"),
            "manualComment": payload.get("comment", ""),
            "manualResumedAt": utc_now().isoformat(),
        }
        node_run.output_data = output
        node_run.status = NodeRunStatus.SUCCESS
        node_run.finished_at = utc_now()
        run.status = RunStatus.RUNNING
        _emit(db, run, "NODE_RESUMED", {"status": node_run.status, "decision": output["manualDecision"]}, node_run.node_id)
        _emit(db, run, "RUN_RESUMED", {"status": run.status})
        db.commit()
        return {"kind": "success", "output": output}


@activity.defn
def create_callback_wait_activity(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        node_run.input_data = payload.get("inputs", {})
        node_run.attempts = max(node_run.attempts, int(payload.get("attempt", 1)))
        callback = create_callback_wait(db, run, node_run, payload.get("config", {}))
        callback.group_id = run.group_id
        node_run.output_data = {
            "_callback": {
                "id": callback.id,
                "status": callback.status,
                "url": callback_url(callback),
                "authMode": callback.auth_mode,
                "credentialAlias": callback.credential_alias,
                "expiresAt": callback.expires_at.isoformat(),
            }
        }
        node_run.status = NodeRunStatus.WAITING_CALLBACK
        node_run.available_at = callback.expires_at
        run.status = RunStatus.WAITING_CALLBACK
        _emit(
            db,
            run,
            "CALLBACK_WAITING",
            {
                "callbackId": callback.id,
                "status": node_run.status,
                "authMode": callback.auth_mode,
                "credentialAlias": callback.credential_alias,
                "expiresAt": callback.expires_at.isoformat(),
            },
            node_run.node_id,
        )
        _emit(db, run, "RUN_WAITING_CALLBACK", {"status": run.status})
        db.commit()
        return {"callback_id": callback.id, "expires_at": callback.expires_at.isoformat()}


@activity.defn
def complete_callback_wait_activity(payload: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        run, node_run = _load_run_and_node(db, payload["run_id"], payload["node_id"])
        safe_payload = redact_sensitive(payload.get("data", {}))
        output = {
            **(node_run.input_data or {}),
            **safe_payload,
            "_callback": {
                "id": payload.get("callback_id"),
                "status": "RECEIVED",
                "receivedAt": utc_now().isoformat(),
            },
        }
        node_run.output_data = output
        node_run.status = NodeRunStatus.SUCCESS
        node_run.error_message = None
        node_run.finished_at = utc_now()
        run.status = RunStatus.RUNNING
        _emit(db, run, "CALLBACK_RECEIVED", {"callbackId": payload.get("callback_id")}, node_run.node_id)
        _emit(db, run, "NODE_SUCCEEDED", {"status": node_run.status, "output": output}, node_run.node_id)
        _emit(db, run, "RUN_RESUMED", {"status": run.status})
        db.commit()
        return {"kind": "success", "output": output}


ACTIVITIES = [
    create_scheduled_run_activity,
    mark_run_activity,
    mark_node_skipped_activity,
    mark_node_failed_activity,
    execute_node_activity,
    create_manual_wait_activity,
    complete_manual_wait_activity,
    create_callback_wait_activity,
    complete_callback_wait_activity,
]
