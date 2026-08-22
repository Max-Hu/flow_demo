from __future__ import annotations

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.callbacks import callback_url
from app.flow_config import config_validation_errors
from app.models import (
    FlowCredential,
    FlowDefinition,
    FlowRun,
    FlowSchedule,
    FlowVersion,
    NodeRun,
)
from app.schemas import (
    CallbackWaitResponse,
    CredentialResponse,
    NodeRunResponse,
    RunDetail,
    RunVariableResponse,
    ScheduleResponse,
)
from app.workflow.validator import validate_input_schema


def ensure_valid_input_schema(input_schema: dict) -> None:
    issues = validate_input_schema(input_schema)
    if issues:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Flow input schema is invalid",
                "issues": [item.model_dump() for item in issues],
            },
        )


def ensure_valid_run_input(input_schema: dict, input_data: dict) -> None:
    errors = sorted(
        Draft202012Validator(input_schema).iter_errors(input_data),
        key=lambda item: list(item.path),
    )
    if not errors:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "message": "Run input validation failed",
            "issues": [
                {
                    "path": ".".join(str(part) for part in error.path) or "$",
                    "message": error.message,
                }
                for error in errors
            ],
        },
    )


def ensure_valid_flow_configuration(schema: dict, value: dict) -> None:
    errors = config_validation_errors(schema, value)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Flow configuration validation failed", "issues": errors},
        )


def credential_references(content: dict) -> set[str]:
    references: set[str] = set()
    for node in content.get("nodes", []):
        config = node.get("data", {}).get("config", {})
        reference = config.get("credentialRef") if isinstance(config, dict) else None
        if isinstance(reference, str) and reference.strip():
            references.add(reference.strip().lower())
    return references


def credential_response(item: FlowCredential) -> CredentialResponse:
    return CredentialResponse(
        id=item.id,
        flow_id=item.flow_id,
        alias=item.alias,
        type=item.credential_type,
        allowed_origins=item.allowed_origins,
        enabled=item.enabled,
        revision=item.current_revision,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def credential_is_referenced(db: Session, flow: FlowDefinition, alias: str) -> bool:
    if alias in credential_references(flow.draft_content):
        return True
    versions = db.scalars(select(FlowVersion.content).where(FlowVersion.flow_id == flow.id)).all()
    return any(alias in credential_references(content) for content in versions)


def get_credential_or_404(
    db: Session, flow_id: str, credential_id: str
) -> FlowCredential:
    item = db.scalar(
        select(FlowCredential).where(
            FlowCredential.id == credential_id,
            FlowCredential.flow_id == flow_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return item


def build_run_detail(run: FlowRun) -> RunDetail:
    return RunDetail(
        id=run.id,
        flow_id=run.flow_id,
        flow_name=run.flow.name,
        version_number=run.flow_version.version_number,
        status=run.status,
        input_data=run.input_data,
        flow_config=run.flow_config,
        output_data=run.output_data,
        error_message=run.error_message,
        cancel_requested=run.cancel_requested,
        trigger_type=run.trigger_type,
        trigger_id=run.trigger_id,
        parent_run_id=run.parent_run_id,
        source_metadata=run.source_metadata,
        requested_at=run.requested_at,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        flow_content=run.flow_version.content,
        node_runs=[build_node_run_response(item) for item in run.node_runs],
        variables=[RunVariableResponse.model_validate(item) for item in run.variables],
    )


def build_node_run_response(item: NodeRun) -> NodeRunResponse:
    response = NodeRunResponse.model_validate(item)
    if not item.callback_waits:
        return response
    callback = max(item.callback_waits, key=lambda value: value.attempt_number)
    return response.model_copy(
        update={
            "callback": CallbackWaitResponse(
                id=callback.id,
                status=callback.status,
                callback_url=callback_url(callback),
                auth_mode=callback.auth_mode,
                credential_alias=callback.credential_alias,
                expires_at=callback.expires_at,
                received_at=callback.received_at,
                created_at=callback.created_at,
            )
        }
    )


def build_schedule_response(schedule: FlowSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=schedule.id,
        flow_id=schedule.flow_id,
        name=schedule.name,
        cron_expression=schedule.cron_expression,
        timezone=schedule.timezone,
        version_number=schedule.flow_version.version_number,
        input_data=schedule.input_data,
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        last_triggered_at=schedule.last_triggered_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )
