from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.callbacks import verify_callback_auth
from app.database import get_db
from app.enums import NodeRunStatus, RunStatus
from app.events import emit_event
from app.models import CallbackWait, FlowRun, NodeRun, NodeRunAttempt, utc_now
from app.security.redaction import redact_sensitive
from app.temporal.client import get_temporal_client
from app.temporal.workflows import GenericFlowWorkflow

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
MAX_CALLBACK_BYTES = 1_000_000


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _response(item: CallbackWait, *, idempotent: bool) -> dict[str, Any]:
    return {
        "status": "accepted",
        "runId": item.flow_run_id,
        "nodeId": item.node_id,
        "idempotent": idempotent,
        "receivedAt": item.received_at.isoformat() if item.received_at else None,
    }


def _reject(
    db: Session, item: CallbackWait, reason: str, source: str
) -> None:
    emit_event(
        db,
        item.flow_run_id,
        "CALLBACK_REJECTED",
        {"reason": reason, "source": source, "authMode": item.auth_mode},
        item.node_id,
    )
    db.commit()


def _expire(db: Session, item: CallbackWait) -> None:
    now = utc_now()
    item.status = "EXPIRED"
    node_run = item.node_run
    run = node_run.flow_run
    if node_run.status == NodeRunStatus.WAITING_CALLBACK:
        node_run.status = NodeRunStatus.FAILED
        node_run.error_message = "Callback wait timed out"
        node_run.finished_at = now
        run.status = RunStatus.FAILED
        run.error_message = node_run.error_message
        run.finished_at = now
        attempt = db.scalar(
            select(NodeRunAttempt).where(
                NodeRunAttempt.node_run_id == node_run.id,
                NodeRunAttempt.attempt_number == item.attempt_number,
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
            {"callbackId": item.id, "expiredAt": now.isoformat()},
            node_run.node_id,
        )
        emit_event(
            db,
            run.id,
            "NODE_FAILED",
            {"status": NodeRunStatus.FAILED, "error": node_run.error_message},
            node_run.node_id,
        )
    db.commit()


@router.post("/{callback_id}", status_code=status.HTTP_202_ACCEPTED)
async def receive_callback(
    callback_id: str, request: Request, db: DbSession
) -> dict[str, Any]:
    body = await request.body()
    if len(body) > MAX_CALLBACK_BYTES:
        raise HTTPException(status_code=413, detail="Callback payload exceeds 1 MB")
    source = request.client.host if request.client else "unknown"
    item = db.scalar(
        select(CallbackWait)
        .where(CallbackWait.id == callback_id)
        .options(
            selectinload(CallbackWait.node_run)
            .selectinload(NodeRun.flow_run)
            .selectinload(FlowRun.node_runs),
            selectinload(CallbackWait.node_run)
            .selectinload(NodeRun.flow_run)
            .selectinload(FlowRun.flow_version),
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Callback URL not found")
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key and len(idempotency_key) > 200:
        raise HTTPException(status_code=422, detail="Idempotency-Key is too long")
    if item.status == "RECEIVED":
        if idempotency_key and idempotency_key == item.idempotency_key:
            return _response(item, idempotent=True)
        raise HTTPException(status_code=409, detail="Callback was already consumed")
    if item.status in {"EXPIRED", "CANCELLED"}:
        raise HTTPException(status_code=410, detail=f"Callback is {item.status.lower()}")
    if _aware(item.expires_at) <= utc_now():
        _expire(db, item)
        raise HTTPException(status_code=410, detail="Callback has expired")
    try:
        revision = verify_callback_auth(
            db, item, item.node_run.flow_run.flow_id, request.headers, body
        )
    except ValueError as exc:
        _reject(db, item, str(exc), source)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/json":
        _reject(db, item, "JSON content type required", source)
        raise HTTPException(status_code=415, detail="Callback body must use application/json")
    try:
        decoded = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        _reject(db, item, "Invalid JSON body", source)
        raise HTTPException(status_code=400, detail="Callback body is not valid JSON") from exc
    payload = decoded if isinstance(decoded, dict) else {"data": decoded}
    safe_payload = redact_sensitive(payload)
    now = utc_now()
    item.status = "RECEIVED"
    item.received_at = now
    item.idempotency_key = idempotency_key
    item.payload = safe_payload
    item.request_metadata = {
        "source": source,
        "contentType": content_type,
        "authMode": item.auth_mode,
        "credentialAlias": item.credential_alias,
        "credentialRevision": revision,
    }
    node_run = item.node_run
    run = node_run.flow_run
    if revision is not None:
        emit_event(
            db,
            run.id,
            "CREDENTIAL_USED",
            {
                "alias": item.credential_alias,
                "revision": revision,
                "direction": "inbound-callback",
            },
            node_run.node_id,
        )
    emit_event(
        db,
        run.id,
        "CALLBACK_RECEIVED",
        {
            "callbackId": item.id,
            "source": source,
            "authMode": item.auth_mode,
            "idempotencyKey": idempotency_key,
        },
        node_run.node_id,
    )
    emit_event(
        db,
        run.id,
        "CALLBACK_SIGNALLED",
        {"callbackId": item.id, "nodeId": node_run.node_id},
        node_run.node_id,
    )
    db.commit()
    if not run.temporal_workflow_id:
        raise HTTPException(status_code=409, detail="Run is not attached to a Temporal workflow")
    client = await get_temporal_client()
    await client.get_workflow_handle(run.temporal_workflow_id).signal(
        GenericFlowWorkflow.receive_callback,
        {"node_id": node_run.node_id, "callback_id": item.id, "data": safe_payload},
    )
    return _response(item, idempotent=False)
