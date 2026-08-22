from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import FlowEvent, FlowRun


def emit_event(
    db: Session,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    node_id: str | None = None,
) -> None:
    run = db.get(FlowRun, run_id)
    db.add(
        FlowEvent(
            group_id=run.group_id if run is not None else None,
            flow_run_id=run_id,
            event_type=event_type,
            node_id=node_id,
            payload=payload,
        )
    )
