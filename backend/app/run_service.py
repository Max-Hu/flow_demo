from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.enums import RunStatus, RunTriggerType
from app.models import FlowDefinition, FlowRun, FlowVersion
from app.workflow.engine import initialize_run


def create_flow_run(
    db: Session,
    flow: FlowDefinition,
    version: FlowVersion,
    input_data: dict[str, Any],
    *,
    trigger_type: str = RunTriggerType.MANUAL,
    trigger_id: str | None = None,
    parent_run_id: str | None = None,
    idempotency_key: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    flow_config: dict[str, Any] | None = None,
) -> FlowRun:
    run = FlowRun(
        flow=flow,
        flow_version=version,
        status=RunStatus.PENDING,
        input_data=input_data,
        flow_config=flow_config if flow_config is not None else version.default_config,
        trigger_type=trigger_type,
        trigger_id=trigger_id,
        parent_run_id=parent_run_id,
        idempotency_key=idempotency_key,
        source_metadata=source_metadata or {},
    )
    db.add(run)
    db.flush()
    initialize_run(db, run)
    return run
