from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.callbacks import callback_url, create_callback_wait
from app.config import get_settings
from app.database import SessionLocal
from app.enums import NODE_TERMINAL_STATUSES, NodeRunStatus, RunStatus
from app.models import CallbackWait, FlowEvent, FlowRun, NodeRun, NodeRunAttempt, utc_now
from app.nodes import get_node_execution_kind, get_node_handler
from app.nodes.base import NodeContext, PollPending
from app.security.redaction import redact_sensitive
from app.template_resolver import resolve_templates

logger = logging.getLogger(__name__)
settings = get_settings()


def emit_event(
    db: Session,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    node_id: str | None = None,
) -> None:
    db.add(
        FlowEvent(
            flow_run_id=run_id,
            node_id=node_id,
            event_type=event_type,
            payload=redact_sensitive(payload),
        )
    )


def initialize_run(db: Session, run: FlowRun) -> None:
    content = run.flow_version.content
    for node in content["nodes"]:
        data = node["data"]
        config = data.get("config", {})
        execution_kind = get_node_execution_kind(
            data["nodeType"], data.get("nodeVersion", "1.0")
        )
        max_attempts = (
            max(1, min(int(config.get("maxPolls", 60)), 1000))
            if execution_kind == "durable_poll"
            else max(1, min(int(config.get("maxAttempts", 1)), 5))
        )
        db.add(
            NodeRun(
                flow_run_id=run.id,
                node_id=node["id"],
                node_type=data["nodeType"],
                node_version=data.get("nodeVersion", "1.0"),
                config=config,
                max_attempts=max_attempts,
                status=(
                    NodeRunStatus.READY
                    if data["nodeType"] == "start"
                    else NodeRunStatus.PENDING
                ),
            )
        )
    run.status = RunStatus.RUNNING
    run.started_at = utc_now()
    emit_event(db, run.id, "RUN_STARTED", {"status": RunStatus.RUNNING})
    db.flush()


def _active_edge(edge: dict, upstream: NodeRun) -> bool:
    if upstream.status != NodeRunStatus.SUCCESS:
        return False
    if upstream.node_type != "condition":
        return True
    return bool(upstream.output_data) and upstream.output_data.get("branch") == edge.get(
        "sourceHandle"
    )


def advance_run(db: Session, run_id: str) -> None:
    run = db.scalar(
        select(FlowRun)
        .where(FlowRun.id == run_id)
        .options(selectinload(FlowRun.node_runs), selectinload(FlowRun.flow_version))
    )
    if run is None or run.status not in {
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.WAITING,
        RunStatus.WAITING_CALLBACK,
    }:
        return

    if run.cancel_requested:
        callbacks = db.scalars(
            select(CallbackWait).where(
                CallbackWait.flow_run_id == run.id,
                CallbackWait.status == "WAITING",
            )
        ).all()
        for callback in callbacks:
            callback.status = "CANCELLED"
        for node_run in run.node_runs:
            if node_run.status not in NODE_TERMINAL_STATUSES:
                node_run.status = NodeRunStatus.CANCELLED
                node_run.finished_at = utc_now()
                emit_event(
                    db,
                    run.id,
                    "NODE_CANCELLED",
                    {"status": NodeRunStatus.CANCELLED},
                    node_run.node_id,
                )
        run.status = RunStatus.CANCELLED
        run.finished_at = utc_now()
        emit_event(db, run.id, "RUN_CANCELLED", {"status": RunStatus.CANCELLED})
        return

    failed = next((item for item in run.node_runs if item.status == NodeRunStatus.FAILED), None)
    if failed:
        for node_run in run.node_runs:
            if node_run.status in {
                NodeRunStatus.PENDING,
                NodeRunStatus.READY,
                NodeRunStatus.RETRY_WAIT,
                NodeRunStatus.POLL_WAIT,
                NodeRunStatus.WAITING_CALLBACK,
            }:
                node_run.status = NodeRunStatus.CANCELLED
                node_run.finished_at = utc_now()
        run.status = RunStatus.FAILED
        run.error_message = (
            f"Node '{failed.node_id}' failed: {failed.error_message or 'Unknown error'}"
        )
        run.finished_at = utc_now()
        emit_event(
            db,
            run.id,
            "RUN_FAILED",
            {"status": RunStatus.FAILED, "error": run.error_message},
        )
        return

    content = run.flow_version.content
    node_runs = {item.node_id: item for item in run.node_runs}
    incoming: dict[str, list[dict]] = {node_id: [] for node_id in node_runs}
    for edge in content["edges"]:
        incoming[edge["target"]].append(edge)

    changed = True
    while changed:
        changed = False
        for node_run in run.node_runs:
            if node_run.status != NodeRunStatus.PENDING:
                continue
            edges = incoming[node_run.node_id]
            upstream_runs = [node_runs[edge["source"]] for edge in edges]
            if not all(
                item.status in {NodeRunStatus.SUCCESS, NodeRunStatus.SKIPPED}
                for item in upstream_runs
            ):
                continue
            active_edges = [
                edge for edge in edges if _active_edge(edge, node_runs[edge["source"]])
            ]
            if active_edges:
                node_run.status = NodeRunStatus.READY
                emit_event(
                    db,
                    run.id,
                    "NODE_READY",
                    {"status": NodeRunStatus.READY},
                    node_run.node_id,
                )
            else:
                node_run.status = NodeRunStatus.SKIPPED
                node_run.finished_at = utc_now()
                emit_event(
                    db,
                    run.id,
                    "NODE_SKIPPED",
                    {"status": NodeRunStatus.SKIPPED},
                    node_run.node_id,
                )
            changed = True

    if all(item.status in {NodeRunStatus.SUCCESS, NodeRunStatus.SKIPPED} for item in run.node_runs):
        end_ids = {
            node["id"] for node in content["nodes"] if node["data"]["nodeType"] == "end"
        }
        outputs = {
            item.node_id: item.output_data
            for item in run.node_runs
            if item.node_id in end_ids and item.status == NodeRunStatus.SUCCESS
        }
        run.output_data = next(iter(outputs.values())) if len(outputs) == 1 else outputs
        run.status = RunStatus.SUCCESS
        run.finished_at = utc_now()
        emit_event(
            db,
            run.id,
            "RUN_SUCCEEDED",
            {"status": RunStatus.SUCCESS, "output": run.output_data or {}},
        )


def recover_and_promote(db: Session) -> None:
    now = utc_now()
    waiting_runs = db.scalars(
        select(NodeRun).where(
            NodeRun.status.in_([NodeRunStatus.RETRY_WAIT, NodeRunStatus.POLL_WAIT]),
            NodeRun.available_at <= now,
        )
    ).all()
    for node_run in waiting_runs:
        reason = "poll" if node_run.status == NodeRunStatus.POLL_WAIT else "retry"
        node_run.status = NodeRunStatus.READY
        emit_event(
            db,
            node_run.flow_run_id,
            "NODE_READY",
            {"status": NodeRunStatus.READY, "reason": reason},
            node_run.node_id,
        )

    expired = db.scalars(
        select(NodeRun).where(
            NodeRun.status == NodeRunStatus.RUNNING,
            NodeRun.lease_expires_at.is_not(None),
            NodeRun.lease_expires_at < now,
        )
    ).all()
    for node_run in expired:
        node_run.lease_owner = None
        node_run.lease_expires_at = None
        node_run.error_message = "Worker lease expired"
        if node_run.attempts < node_run.max_attempts:
            node_run.status = NodeRunStatus.RETRY_WAIT
            node_run.available_at = now + timedelta(seconds=2)
        else:
            node_run.status = NodeRunStatus.FAILED
            node_run.finished_at = now
        emit_event(
            db,
            node_run.flow_run_id,
            "NODE_LEASE_EXPIRED",
            {"status": node_run.status},
            node_run.node_id,
        )

    expired_callbacks = db.scalars(
        select(CallbackWait)
        .where(
            CallbackWait.status == "WAITING",
            CallbackWait.expires_at <= now,
        )
        .options(
            selectinload(CallbackWait.node_run).selectinload(NodeRun.flow_run)
        )
    ).all()
    for callback in expired_callbacks:
        callback.status = "EXPIRED"
        node_run = callback.node_run
        if node_run.status != NodeRunStatus.WAITING_CALLBACK:
            continue
        node_run.status = NodeRunStatus.FAILED
        node_run.error_message = "Callback wait timed out"
        node_run.finished_at = now
        run = node_run.flow_run
        run.status = RunStatus.RUNNING
        attempt = db.scalar(
            select(NodeRunAttempt).where(
                NodeRunAttempt.node_run_id == node_run.id,
                NodeRunAttempt.attempt_number == callback.attempt_number,
            )
        )
        if attempt is not None:
            attempt.status = NodeRunStatus.FAILED
            attempt.error_message = node_run.error_message
            attempt.finished_at = now
        emit_event(
            db,
            run.id,
            "CALLBACK_EXPIRED",
            {"callbackId": callback.id, "expiredAt": now.isoformat()},
            node_run.node_id,
        )
        emit_event(
            db,
            run.id,
            "NODE_FAILED",
            {"status": NodeRunStatus.FAILED, "error": node_run.error_message},
            node_run.node_id,
        )


def claim_ready_node(db: Session, worker_name: str) -> str | None:
    now = utc_now()
    node_run = db.scalar(
        select(NodeRun)
        .join(FlowRun)
        .where(
            NodeRun.status == NodeRunStatus.READY,
            NodeRun.available_at <= now,
            FlowRun.status == RunStatus.RUNNING,
            FlowRun.cancel_requested.is_(False),
        )
        .order_by(NodeRun.available_at, NodeRun.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if node_run is None:
        return None
    node_run.status = NodeRunStatus.RUNNING
    node_run.attempts += 1
    node_run.started_at = node_run.started_at or now
    node_run.lease_owner = worker_name
    node_run.lease_expires_at = now + timedelta(seconds=settings.worker_lease_seconds)
    attempt = NodeRunAttempt(
        node_run_id=node_run.id,
        attempt_number=node_run.attempts,
        status=NodeRunStatus.RUNNING,
        started_at=now,
    )
    db.add(attempt)
    emit_event(
        db,
        node_run.flow_run_id,
        "NODE_STARTED",
        {"status": NodeRunStatus.RUNNING, "attempt": node_run.attempts},
        node_run.node_id,
    )
    db.flush()
    return node_run.id


def _collect_input(run: FlowRun, node_run: NodeRun) -> dict[str, Any]:
    if node_run.node_type == "start":
        return run.input_data
    content = run.flow_version.content
    node_runs = {item.node_id: item for item in run.node_runs}
    incoming = [edge for edge in content["edges"] if edge["target"] == node_run.node_id]
    active = [edge for edge in incoming if _active_edge(edge, node_runs[edge["source"]])]
    outputs: dict[str, dict[str, Any]] = {}
    for edge in active:
        upstream = node_runs[edge["source"]]
        output = upstream.output_data or {}
        if upstream.node_type == "condition":
            output = output.get("data", {})
        outputs[upstream.node_id] = output
    if len(outputs) == 1:
        return next(iter(outputs.values()))
    return {"sources": outputs}


def execute_node(node_run_id: str) -> None:
    with SessionLocal() as db:
        node_run = db.scalar(
            select(NodeRun)
            .where(NodeRun.id == node_run_id)
            .options(
                selectinload(NodeRun.flow_run).selectinload(FlowRun.node_runs),
                selectinload(NodeRun.flow_run).selectinload(FlowRun.flow_version),
                selectinload(NodeRun.attempts_log),
            )
        )
        if node_run is None or node_run.status != NodeRunStatus.RUNNING:
            return
        run = node_run.flow_run
        inputs = _collect_input(run, node_run)
        node_run.input_data = inputs
        db.commit()

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
        execution_kind = get_node_execution_kind(
            node_run.node_type, node_run.node_version
        )
        resolved_config = node_run.config
        try:
            resolved_config = resolve_templates(
                node_run.config,
                {
                    "input": inputs,
                    "variables": context.variables(),
                    "run": {
                        "id": run.id,
                        "triggerType": run.trigger_type,
                        "nodeId": node_run.node_id,
                        "attempt": node_run.attempts,
                    },
                    "flowConfig": run.flow_config,
                },
            )
            if execution_kind == "manual_wait":
                node_run.status = NodeRunStatus.WAITING
                run.status = RunStatus.WAITING
                node_run.lease_owner = None
                node_run.lease_expires_at = None
                attempt = next(
                    item for item in node_run.attempts_log
                    if item.attempt_number == node_run.attempts
                )
                attempt.status = NodeRunStatus.WAITING
                attempt.finished_at = utc_now()
                emit_event(
                    db,
                    run.id,
                    "NODE_WAITING",
                    {
                        "status": NodeRunStatus.WAITING,
                        "prompt": resolved_config.get("prompt", "Manual action required"),
                    },
                    node_run.node_id,
                )
                emit_event(db, run.id, "RUN_WAITING", {"status": RunStatus.WAITING})
                db.commit()
                return
            if execution_kind == "callback_wait":
                callback = create_callback_wait(db, run, node_run, resolved_config)
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
                node_run.lease_owner = None
                node_run.lease_expires_at = None
                attempt = next(
                    item
                    for item in node_run.attempts_log
                    if item.attempt_number == node_run.attempts
                )
                attempt.status = NodeRunStatus.WAITING_CALLBACK
                attempt.finished_at = utc_now()
                emit_event(
                    db,
                    run.id,
                    "CALLBACK_WAITING",
                    {
                        "callbackId": callback.id,
                        "status": NodeRunStatus.WAITING_CALLBACK,
                        "authMode": callback.auth_mode,
                        "credentialAlias": callback.credential_alias,
                        "expiresAt": callback.expires_at.isoformat(),
                    },
                    node_run.node_id,
                )
                emit_event(
                    db,
                    run.id,
                    "RUN_WAITING_CALLBACK",
                    {"status": RunStatus.WAITING_CALLBACK},
                )
                db.commit()
                return
            handler = get_node_handler(node_run.node_type, node_run.node_version)
            output = handler.execute(inputs, resolved_config, context)
            if isinstance(output, PollPending):
                safe_last_output = redact_sensitive(output.last_output)
                node_run.output_data = safe_last_output
                node_run.status = NodeRunStatus.POLL_WAIT
                node_run.error_message = None
                node_run.available_at = utc_now() + timedelta(seconds=output.delay_seconds)
                node_run.lease_owner = None
                node_run.lease_expires_at = None
                attempt = next(
                    item
                    for item in node_run.attempts_log
                    if item.attempt_number == node_run.attempts
                )
                attempt.status = NodeRunStatus.POLL_WAIT
                attempt.finished_at = utc_now()
                emit_event(
                    db,
                    run.id,
                    "NODE_POLL_WAIT",
                    {
                        "status": NodeRunStatus.POLL_WAIT,
                        "pollCount": node_run.attempts,
                        "maxPolls": node_run.max_attempts,
                        "nextPollAt": node_run.available_at.isoformat(),
                        "lastOutput": safe_last_output,
                    },
                    node_run.node_id,
                )
                db.commit()
                return
            output = redact_sensitive(output)
            node_run.output_data = output
            node_run.status = NodeRunStatus.SUCCESS
            node_run.error_message = None
            node_run.finished_at = utc_now()
            event_type = "NODE_SUCCEEDED"
            event_payload = {"status": NodeRunStatus.SUCCESS, "output": output}
        except Exception as exc:  # noqa: BLE001 - node failures are workflow data
            logger.exception("Node %s failed", node_run.node_id)
            node_run.error_message = str(exc)
            if node_run.attempts < node_run.max_attempts:
                node_run.status = NodeRunStatus.RETRY_WAIT
                retry_delay = (
                    float(resolved_config.get("intervalSeconds", 10))
                    if execution_kind == "durable_poll"
                    else 2**node_run.attempts
                )
                node_run.available_at = utc_now() + timedelta(seconds=retry_delay)
                event_type = "NODE_RETRY_SCHEDULED"
            else:
                node_run.status = NodeRunStatus.FAILED
                node_run.finished_at = utc_now()
                event_type = "NODE_FAILED"
            event_payload = {"status": node_run.status, "error": str(exc)}

        node_run.lease_owner = None
        node_run.lease_expires_at = None
        attempt = next(
            item for item in node_run.attempts_log if item.attempt_number == node_run.attempts
        )
        attempt.status = node_run.status
        attempt.error_message = node_run.error_message
        attempt.finished_at = utc_now()
        emit_event(db, run.id, event_type, event_payload, node_run.node_id)
        advance_run(db, run.id)
        db.commit()


def worker_tick(worker_name: str) -> bool:
    with SessionLocal() as db:
        recover_and_promote(db)
        active_run_ids = db.scalars(
            select(FlowRun.id).where(
                FlowRun.status.in_(
                    [
                        RunStatus.RUNNING,
                        RunStatus.WAITING,
                        RunStatus.WAITING_CALLBACK,
                    ]
                )
            )
        ).all()
        for run_id in active_run_ids:
            advance_run(db, run_id)
        db.commit()

    with SessionLocal() as db:
        node_run_id = claim_ready_node(db, worker_name)
        db.commit()
    if node_run_id is None:
        return False
    execute_node(node_run_id)
    return True
