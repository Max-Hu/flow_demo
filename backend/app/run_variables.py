from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FlowEvent, FlowRun, FlowRunVariable, new_id, utc_now

VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,199}$")
WRITE_MODES = {"REPLACE", "MERGE", "APPEND", "INCREMENT"}


def value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def list_run_variables(db: Session, run_id: str) -> list[FlowRunVariable]:
    return list(
        db.scalars(
            select(FlowRunVariable)
            .where(FlowRunVariable.flow_run_id == run_id)
            .order_by(FlowRunVariable.name)
        )
    )


def variables_as_dict(db: Session, run_id: str) -> dict[str, Any]:
    return {item.name: deepcopy(item.value) for item in list_run_variables(db, run_id)}


def get_variable(db: Session, run_id: str, name: str, default: Any = None) -> Any:
    item = db.scalar(
        select(FlowRunVariable).where(
            FlowRunVariable.flow_run_id == run_id,
            FlowRunVariable.name == name,
        )
    )
    return deepcopy(item.value) if item is not None else default


def get_variable_path(db: Session, run_id: str, path: str, default: Any = None) -> Any:
    variables = variables_as_dict(db, run_id)
    if path in variables:
        return variables[path]
    parts = path.split(".")
    for split_at in range(len(parts) - 1, 0, -1):
        name = ".".join(parts[:split_at])
        if name not in variables:
            continue
        current = variables[name]
        for part in parts[split_at:]:
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return deepcopy(current)
    return default


def _apply_mode(existing: Any, incoming: Any, mode: str) -> Any:
    if mode == "REPLACE":
        return incoming
    if mode == "MERGE":
        if not isinstance(incoming, dict) or (
            existing is not None and not isinstance(existing, dict)
        ):
            raise ValueError("MERGE requires both the existing and new values to be objects")
        return {**(existing or {}), **incoming}
    if mode == "APPEND":
        if existing is not None and not isinstance(existing, list):
            raise ValueError("APPEND requires the existing value to be an array")
        return [*(existing or []), *(incoming if isinstance(incoming, list) else [incoming])]
    if mode == "INCREMENT":
        if isinstance(existing, bool) or isinstance(incoming, bool):
            raise ValueError("INCREMENT requires numeric values")
        if not isinstance(incoming, int | float) or (
            existing is not None and not isinstance(existing, int | float)
        ):
            raise ValueError("INCREMENT requires numeric values")
        return (existing or 0) + incoming
    raise ValueError(f"Unsupported variable write mode: {mode}")


def set_variable(
    db: Session,
    run_id: str,
    node_id: str,
    name: str,
    value: Any,
    mode: str = "REPLACE",
) -> FlowRunVariable:
    if not VARIABLE_NAME.fullmatch(name):
        raise ValueError(
            "Variable names must start with a letter or underscore and contain only "
            "letters, numbers, dots, dashes, and underscores"
        )
    mode = mode.upper()
    if mode not in WRITE_MODES:
        raise ValueError(f"Unsupported variable write mode: {mode}")

    # Serializing writes at the run row prevents lost updates across parallel branches.
    if db.scalar(select(FlowRun.id).where(FlowRun.id == run_id).with_for_update()) is None:
        raise ValueError("Flow run not found")
    item = db.scalar(
        select(FlowRunVariable)
        .where(
            FlowRunVariable.flow_run_id == run_id,
            FlowRunVariable.name == name,
        )
        .with_for_update()
    )
    next_value = _apply_mode(item.value if item else None, value, mode)
    now = utc_now()
    if item is None:
        item = FlowRunVariable(
            id=new_id(),
            flow_run_id=run_id,
            name=name,
            value=next_value,
            value_type=value_type(next_value),
            updated_by_node_id=node_id,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
    else:
        item.value = next_value
        item.value_type = value_type(next_value)
        item.updated_by_node_id = node_id
        item.revision += 1
        item.updated_at = now
    db.add(
        FlowEvent(
            flow_run_id=run_id,
            node_id=node_id,
            event_type="VARIABLE_SET",
            payload={"name": name, "mode": mode, "revision": item.revision},
        )
    )
    db.flush()
    return item
