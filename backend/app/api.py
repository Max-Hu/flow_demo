import asyncio
import json
from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from jsonschema import Draft202012Validator
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal, get_db
from app.enums import FlowStatus, NodeRunStatus, RunStatus, RunTriggerType
from app.flow_config import config_validation_errors, deep_merge
from app.models import (
    FlowCredential,
    FlowDefinition,
    FlowEvent,
    FlowRun,
    FlowSchedule,
    FlowVersion,
    utc_now,
)
from app.nodes import list_node_definitions
from app.run_service import create_flow_run
from app.scheduling import validate_schedule
from app.schemas import (
    CredentialCreate,
    CredentialResponse,
    CredentialRotate,
    CredentialUpdate,
    FlowContent,
    FlowCreate,
    FlowDetail,
    FlowDraftUpdate,
    FlowStatusUpdate,
    FlowSummary,
    FlowVersionResponse,
    NodeResume,
    NodeRunResponse,
    NodeTypeResponse,
    RunCreate,
    RunDetail,
    RunSummary,
    RunVariableResponse,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    ValidationIssue,
    ValidationResponse,
)
from app.security.credentials import (
    add_audit,
    create_revision,
    normalize_origin,
    validate_credential_definition,
)
from app.workflow.engine import advance_run, emit_event
from app.workflow.validator import validate_flow, validate_input_schema

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


def get_flow_or_404(db: Session, flow_id: str) -> FlowDefinition:
    flow = db.get(FlowDefinition, flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow


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


def ensure_credentials_available(db: Session, flow_id: str, content: dict) -> None:
    references = credential_references(content)
    if not references:
        return
    available = set(
        db.scalars(select(FlowCredential.alias).where(FlowCredential.flow_id == flow_id)).all()
    )
    missing = sorted(references - available)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Flow references missing credentials: {', '.join(missing)}",
        )


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
        node_runs=[NodeRunResponse.model_validate(item) for item in run.node_runs],
        variables=[RunVariableResponse.model_validate(item) for item in run.variables],
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
        config_overrides=schedule.config_overrides,
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        last_triggered_at=schedule.last_triggered_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def load_run(db: Session, run_id: str) -> FlowRun:
    run = db.scalar(
        select(FlowRun)
        .where(FlowRun.id == run_id)
        .options(
            selectinload(FlowRun.flow),
            selectinload(FlowRun.flow_version),
            selectinload(FlowRun.node_runs),
            selectinload(FlowRun.variables),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/node-types", response_model=list[NodeTypeResponse])
def node_types() -> list[NodeTypeResponse]:
    return list_node_definitions()


@router.get("/flows", response_model=list[FlowSummary])
def list_flows(db: DbSession) -> list[FlowDefinition]:
    return list(db.scalars(select(FlowDefinition).order_by(desc(FlowDefinition.updated_at))))


@router.post("/flows", response_model=FlowDetail, status_code=status.HTTP_201_CREATED)
def create_flow(payload: FlowCreate, db: DbSession) -> FlowDefinition:
    ensure_valid_input_schema(payload.input_schema)
    ensure_valid_flow_configuration(payload.config_schema, payload.default_config)
    flow = FlowDefinition(
        name=payload.name,
        description=payload.description,
        draft_content=payload.content.model_dump(by_alias=True),
        input_schema=payload.input_schema,
        config_schema=payload.config_schema,
        default_config=payload.default_config,
    )
    db.add(flow)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A flow with this name already exists") from exc
    db.refresh(flow)
    return flow


@router.get("/flows/{flow_id}", response_model=FlowDetail)
def get_flow(flow_id: str, db: DbSession) -> FlowDefinition:
    return get_flow_or_404(db, flow_id)


@router.put("/flows/{flow_id}/draft", response_model=FlowDetail)
def update_draft(
    flow_id: str, payload: FlowDraftUpdate, db: DbSession
) -> FlowDefinition:
    flow = get_flow_or_404(db, flow_id)
    if flow.row_version != payload.expected_row_version:
        raise HTTPException(
            status_code=409,
            detail="The flow was changed by another editor. Reload before saving.",
        )
    flow.draft_content = payload.content.model_dump(by_alias=True)
    ensure_valid_input_schema(payload.input_schema)
    ensure_valid_flow_configuration(payload.config_schema, payload.default_config)
    flow.input_schema = payload.input_schema
    flow.config_schema = payload.config_schema
    flow.default_config = payload.default_config
    if payload.description is not None:
        flow.description = payload.description
    flow.row_version += 1
    flow.updated_at = utc_now()
    db.commit()
    db.refresh(flow)
    return flow


@router.post("/flows/{flow_id}/validate", response_model=ValidationResponse)
def validate_draft(flow_id: str, db: DbSession) -> ValidationResponse:
    flow = get_flow_or_404(db, flow_id)
    content = FlowContent.model_validate(flow.draft_content)
    issues = validate_flow(content) + validate_input_schema(flow.input_schema)
    issues.extend(
        ValidationIssue(code="INVALID_FLOW_CONFIG", message=message)
        for message in config_validation_errors(flow.config_schema, flow.default_config)
    )
    try:
        ensure_credentials_available(db, flow.id, flow.draft_content)
    except HTTPException as exc:
        issues.append(ValidationIssue(code="MISSING_CREDENTIAL", message=str(exc.detail)))
    return ValidationResponse(valid=not issues, issues=issues)


@router.post("/flows/{flow_id}/publish", response_model=FlowVersionResponse)
def publish_flow(flow_id: str, db: DbSession) -> FlowVersion:
    flow = get_flow_or_404(db, flow_id)
    content = FlowContent.model_validate(flow.draft_content)
    issues = validate_flow(content) + validate_input_schema(flow.input_schema)
    issues.extend(
        ValidationIssue(code="INVALID_FLOW_CONFIG", message=message)
        for message in config_validation_errors(flow.config_schema, flow.default_config)
    )
    ensure_credentials_available(db, flow.id, flow.draft_content)
    if issues:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Flow validation failed",
                "issues": [item.model_dump() for item in issues],
            },
        )
    version = FlowVersion(
        flow_id=flow.id,
        version_number=flow.current_version + 1,
        content=content.model_dump(by_alias=True),
        input_schema=deepcopy(flow.input_schema),
        config_schema=deepcopy(flow.config_schema),
        default_config=deepcopy(flow.default_config),
    )
    db.add(version)
    flow.current_version = version.version_number
    flow.status = FlowStatus.ACTIVE
    flow.updated_at = utc_now()
    db.commit()
    db.refresh(version)
    return version


@router.get("/flows/{flow_id}/versions", response_model=list[FlowVersionResponse])
def list_versions(flow_id: str, db: DbSession) -> list[FlowVersion]:
    get_flow_or_404(db, flow_id)
    return list(
        db.scalars(
            select(FlowVersion)
            .where(FlowVersion.flow_id == flow_id)
            .order_by(desc(FlowVersion.version_number))
        )
    )


@router.post(
    "/flows/{flow_id}/versions/{version_number}/rollback",
    response_model=FlowVersionResponse,
    status_code=201,
)
def rollback_flow_version(flow_id: str, version_number: int, db: DbSession) -> FlowVersion:
    flow = get_flow_or_404(db, flow_id)
    source = db.scalar(
        select(FlowVersion).where(
            FlowVersion.flow_id == flow.id,
            FlowVersion.version_number == version_number,
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Flow version not found")
    restored = FlowVersion(
        flow_id=flow.id,
        version_number=flow.current_version + 1,
        content=deepcopy(source.content),
        input_schema=deepcopy(source.input_schema),
        config_schema=deepcopy(source.config_schema),
        default_config=deepcopy(source.default_config),
    )
    db.add(restored)
    flow.draft_content = deepcopy(source.content)
    flow.input_schema = deepcopy(source.input_schema)
    flow.config_schema = deepcopy(source.config_schema)
    flow.default_config = deepcopy(source.default_config)
    flow.current_version = restored.version_number
    flow.row_version += 1
    flow.status = FlowStatus.ACTIVE
    flow.updated_at = utc_now()
    db.commit()
    db.refresh(restored)
    return restored


@router.post("/flows/{flow_id}/runs", response_model=RunDetail, status_code=201)
def create_run(flow_id: str, payload: RunCreate, db: DbSession) -> RunDetail:
    flow = get_flow_or_404(db, flow_id)
    if flow.current_version < 1:
        raise HTTPException(status_code=409, detail="Publish the flow before running it")
    if flow.status != FlowStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Only active flows can start new runs")
    requested_version = payload.version_number or flow.current_version
    version = db.scalar(
        select(FlowVersion).where(
            FlowVersion.flow_id == flow.id,
            FlowVersion.version_number == requested_version,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Published flow version not found")
    ensure_valid_run_input(version.input_schema, payload.input_data)
    schedule_config = deep_merge(version.default_config, payload.config_overrides)
    ensure_valid_flow_configuration(version.config_schema, schedule_config)
    flow_config = deep_merge(version.default_config, payload.config_overrides)
    ensure_valid_flow_configuration(version.config_schema, flow_config)
    run = create_flow_run(db, flow, version, payload.input_data, flow_config=flow_config)
    db.commit()
    return build_run_detail(load_run(db, run.id))


@router.get("/runs", response_model=list[RunSummary])
def list_runs(db: DbSession) -> list[RunSummary]:
    runs = db.scalars(
        select(FlowRun)
        .options(selectinload(FlowRun.flow), selectinload(FlowRun.flow_version))
        .order_by(desc(FlowRun.created_at))
        .limit(100)
    ).all()
    return [
        RunSummary(
            id=run.id,
            flow_id=run.flow_id,
            flow_name=run.flow.name,
            version_number=run.flow_version.version_number,
            status=run.status,
            trigger_type=run.trigger_type,
            trigger_id=run.trigger_id,
            parent_run_id=run.parent_run_id,
            requested_at=run.requested_at,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        for run in runs
    ]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: DbSession) -> RunDetail:
    return build_run_detail(load_run(db, run_id))


@router.post("/runs/{run_id}/rerun", response_model=RunDetail, status_code=201)
def rerun_run(run_id: str, db: DbSession) -> RunDetail:
    original = load_run(db, run_id)
    if original.flow.status != FlowStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Only active flows can be rerun")
    run = create_flow_run(
        db,
        original.flow,
        original.flow_version,
        deepcopy(original.input_data),
        trigger_type=RunTriggerType.RERUN,
        trigger_id=original.id,
        parent_run_id=original.id,
        source_metadata={"sourceRunId": original.id},
        flow_config=deepcopy(original.flow_config),
    )
    emit_event(
        db,
        run.id,
        "RUN_RERUN_CREATED",
        {"sourceRunId": original.id, "version": original.flow_version.version_number},
    )
    db.commit()
    return build_run_detail(load_run(db, run.id))


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel_run(run_id: str, db: DbSession) -> RunDetail:
    run = load_run(db, run_id)
    if run.status not in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING}:
        raise HTTPException(status_code=409, detail="Only active runs can be cancelled")
    run.cancel_requested = True
    emit_event(db, run.id, "RUN_CANCEL_REQUESTED", {"status": run.status})
    advance_run(db, run.id)
    db.commit()
    return build_run_detail(load_run(db, run_id))


@router.post("/runs/{run_id}/nodes/{node_id}/resume", response_model=RunDetail)
def resume_node(run_id: str, node_id: str, payload: NodeResume, db: DbSession) -> RunDetail:
    run = load_run(db, run_id)
    node_run = next((item for item in run.node_runs if item.node_id == node_id), None)
    if node_run is None:
        raise HTTPException(status_code=404, detail="Node run not found")
    if node_run.status != NodeRunStatus.WAITING:
        raise HTTPException(status_code=409, detail="Only waiting nodes can be continued")
    node_run.output_data = {
        **(node_run.input_data or {}),
        **payload.data,
        "manualDecision": payload.decision,
        "manualComment": payload.comment,
        "manualResumedAt": utc_now().isoformat(),
    }
    node_run.status = NodeRunStatus.SUCCESS
    node_run.finished_at = utc_now()
    run.status = RunStatus.RUNNING
    emit_event(
        db,
        run.id,
        "NODE_RESUMED",
        {"status": NodeRunStatus.SUCCESS, "decision": payload.decision},
        node_id,
    )
    emit_event(db, run.id, "RUN_RESUMED", {"status": RunStatus.RUNNING})
    advance_run(db, run.id)
    db.commit()
    return build_run_detail(load_run(db, run_id))


@router.patch("/flows/{flow_id}/status", response_model=FlowDetail)
def update_flow_status(flow_id: str, payload: FlowStatusUpdate, db: DbSession) -> FlowDefinition:
    flow = get_flow_or_404(db, flow_id)
    if payload.status == FlowStatus.ACTIVE and flow.current_version < 1:
        raise HTTPException(status_code=409, detail="Publish the flow before activating it")
    flow.status = payload.status
    flow.row_version += 1
    flow.updated_at = utc_now()
    db.commit()
    db.refresh(flow)
    return flow


@router.get("/flows/{flow_id}/credentials", response_model=list[CredentialResponse])
def list_credentials(flow_id: str, db: DbSession) -> list[CredentialResponse]:
    get_flow_or_404(db, flow_id)
    items = db.scalars(
        select(FlowCredential)
        .where(FlowCredential.flow_id == flow_id)
        .order_by(FlowCredential.alias)
    ).all()
    return [credential_response(item) for item in items]


@router.post(
    "/flows/{flow_id}/credentials", response_model=CredentialResponse, status_code=201
)
def create_credential(
    flow_id: str, payload: CredentialCreate, db: DbSession
) -> CredentialResponse:
    flow = get_flow_or_404(db, flow_id)
    try:
        alias, origins, secret = validate_credential_definition(
            payload.alias,
            payload.type,
            payload.allowed_origins,
            payload.secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = FlowCredential(
        flow=flow,
        alias=alias,
        credential_type=payload.type,
        allowed_origins=origins,
        enabled=True,
        current_revision=0,
    )
    db.add(item)
    db.flush()
    create_revision(db, item, secret)
    add_audit(
        db,
        "CREDENTIAL_CREATED",
        flow_id=flow.id,
        credential_id=item.id,
        payload={"alias": alias, "type": payload.type, "revision": item.current_revision},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Credential alias already exists") from exc
    db.refresh(item)
    return credential_response(item)


def get_credential_or_404(db: Session, flow_id: str, credential_id: str) -> FlowCredential:
    item = db.scalar(
        select(FlowCredential).where(
            FlowCredential.id == credential_id,
            FlowCredential.flow_id == flow_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return item


@router.post(
    "/flows/{flow_id}/credentials/{credential_id}/rotate",
    response_model=CredentialResponse,
)
def rotate_credential(
    flow_id: str,
    credential_id: str,
    payload: CredentialRotate,
    db: DbSession,
) -> CredentialResponse:
    item = db.scalar(
        select(FlowCredential)
        .where(
            FlowCredential.id == credential_id,
            FlowCredential.flow_id == flow_id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    try:
        _, _, secret = validate_credential_definition(
            item.alias,
            item.credential_type,
            item.allowed_origins,
            payload.secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    revision = create_revision(db, item, secret)
    add_audit(
        db,
        "CREDENTIAL_ROTATED",
        flow_id=flow_id,
        credential_id=item.id,
        payload={"alias": item.alias, "revision": revision.revision},
    )
    item.updated_at = utc_now()
    db.commit()
    db.refresh(item)
    return credential_response(item)


@router.patch(
    "/flows/{flow_id}/credentials/{credential_id}", response_model=CredentialResponse
)
def update_credential(
    flow_id: str,
    credential_id: str,
    payload: CredentialUpdate,
    db: DbSession,
) -> CredentialResponse:
    item = get_credential_or_404(db, flow_id, credential_id)
    if payload.allowed_origins is not None:
        try:
            item.allowed_origins = sorted(
                {normalize_origin(origin) for origin in payload.allowed_origins}
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    event_type = "CREDENTIAL_UPDATED"
    if payload.enabled is not None and payload.enabled != item.enabled:
        item.enabled = payload.enabled
        event_type = "CREDENTIAL_ENABLED" if payload.enabled else "CREDENTIAL_DISABLED"
    item.updated_at = utc_now()
    add_audit(
        db,
        event_type,
        flow_id=flow_id,
        credential_id=item.id,
        payload={
            "alias": item.alias,
            "enabled": item.enabled,
            "allowedOrigins": item.allowed_origins,
        },
    )
    db.commit()
    db.refresh(item)
    return credential_response(item)


@router.delete("/flows/{flow_id}/credentials/{credential_id}", status_code=204)
def delete_credential(flow_id: str, credential_id: str, db: DbSession) -> None:
    flow = get_flow_or_404(db, flow_id)
    item = get_credential_or_404(db, flow_id, credential_id)
    if credential_is_referenced(db, flow, item.alias):
        add_audit(
            db,
            "CREDENTIAL_DELETE_REJECTED",
            flow_id=flow_id,
            credential_id=item.id,
            payload={"alias": item.alias, "reason": "referenced"},
        )
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Referenced credentials cannot be deleted; disable or rotate it instead",
        )
    add_audit(
        db,
        "CREDENTIAL_DELETED",
        flow_id=flow_id,
        credential_id=item.id,
        payload={"alias": item.alias},
    )
    db.delete(item)
    db.commit()


def get_schedule_or_404(db: Session, flow_id: str, schedule_id: str) -> FlowSchedule:
    schedule = db.scalar(
        select(FlowSchedule)
        .where(FlowSchedule.id == schedule_id, FlowSchedule.flow_id == flow_id)
        .options(selectinload(FlowSchedule.flow_version))
    )
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.get("/flows/{flow_id}/schedules", response_model=list[ScheduleResponse])
def list_schedules(flow_id: str, db: DbSession) -> list[ScheduleResponse]:
    get_flow_or_404(db, flow_id)
    schedules = db.scalars(
        select(FlowSchedule)
        .where(FlowSchedule.flow_id == flow_id)
        .options(selectinload(FlowSchedule.flow_version))
        .order_by(desc(FlowSchedule.created_at))
    ).all()
    return [build_schedule_response(item) for item in schedules]


@router.post("/flows/{flow_id}/schedules", response_model=ScheduleResponse, status_code=201)
def create_schedule(flow_id: str, payload: ScheduleCreate, db: DbSession) -> ScheduleResponse:
    flow = get_flow_or_404(db, flow_id)
    version = db.scalar(
        select(FlowVersion).where(
            FlowVersion.flow_id == flow_id,
            FlowVersion.version_number == payload.version_number,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Published flow version not found")
    ensure_valid_run_input(version.input_schema, payload.input_data)
    try:
        next_run_at = validate_schedule(payload.cron_expression, payload.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule = FlowSchedule(
        flow=flow,
        flow_version=version,
        name=payload.name,
        cron_expression=payload.cron_expression,
        timezone=payload.timezone,
        input_data=payload.input_data,
        config_overrides=payload.config_overrides,
        enabled=payload.enabled,
        next_run_at=next_run_at,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return build_schedule_response(schedule)


@router.put("/flows/{flow_id}/schedules/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    flow_id: str, schedule_id: str, payload: ScheduleUpdate, db: DbSession
) -> ScheduleResponse:
    schedule = get_schedule_or_404(db, flow_id, schedule_id)
    version = schedule.flow_version
    if payload.version_number is not None:
        candidate = db.scalar(
            select(FlowVersion).where(
                FlowVersion.flow_id == flow_id,
                FlowVersion.version_number == payload.version_number,
            )
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="Published flow version not found")
        version = candidate
        schedule.flow_version = candidate
    input_data = payload.input_data if payload.input_data is not None else schedule.input_data
    ensure_valid_run_input(version.input_schema, input_data)
    config_overrides = (
        payload.config_overrides
        if payload.config_overrides is not None
        else schedule.config_overrides
    )
    ensure_valid_flow_configuration(
        version.config_schema,
        deep_merge(version.default_config, config_overrides),
    )
    expression = payload.cron_expression or schedule.cron_expression
    timezone = payload.timezone or schedule.timezone
    try:
        schedule.next_run_at = validate_schedule(expression, timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.name is not None:
        schedule.name = payload.name
    schedule.cron_expression = expression
    schedule.timezone = timezone
    schedule.input_data = input_data
    schedule.config_overrides = config_overrides
    if payload.enabled is not None:
        schedule.enabled = payload.enabled
    schedule.updated_at = utc_now()
    db.commit()
    return build_schedule_response(get_schedule_or_404(db, flow_id, schedule_id))


@router.delete("/flows/{flow_id}/schedules/{schedule_id}", status_code=204)
def delete_schedule(flow_id: str, schedule_id: str, db: DbSession) -> None:
    schedule = get_schedule_or_404(db, flow_id, schedule_id)
    db.delete(schedule)
    db.commit()


@router.post("/runs/{run_id}/nodes/{node_id}/retry", response_model=RunDetail)
def retry_node(run_id: str, node_id: str, db: DbSession) -> RunDetail:
    run = load_run(db, run_id)
    node_run = next((item for item in run.node_runs if item.node_id == node_id), None)
    if node_run is None:
        raise HTTPException(status_code=404, detail="Node run not found")
    if node_run.status != NodeRunStatus.FAILED:
        raise HTTPException(status_code=409, detail="Only failed nodes can be retried")
    node_run.status = NodeRunStatus.READY
    node_run.max_attempts = node_run.attempts + max(1, node_run.max_attempts)
    node_run.error_message = None
    node_run.finished_at = None
    node_run.available_at = utc_now()
    run.status = RunStatus.RUNNING
    run.error_message = None
    run.finished_at = None
    for item in run.node_runs:
        if item.status == NodeRunStatus.CANCELLED:
            item.status = NodeRunStatus.PENDING
            item.finished_at = None
    emit_event(db, run.id, "NODE_MANUAL_RETRY", {"status": NodeRunStatus.READY}, node_id)
    db.commit()
    return build_run_detail(load_run(db, run_id))


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    with SessionLocal() as db:
        if db.get(FlowRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
    cursor = max(after, int(last_event_id or 0))

    async def stream() -> AsyncGenerator[str, None]:
        nonlocal cursor
        heartbeat = 0
        while not await request.is_disconnected():
            with SessionLocal() as db:
                events = db.scalars(
                    select(FlowEvent)
                    .where(FlowEvent.flow_run_id == run_id, FlowEvent.id > cursor)
                    .order_by(FlowEvent.id)
                ).all()
            if events:
                for event in events:
                    cursor = event.id
                    data = json.dumps(
                        {
                            "id": event.id,
                            "type": event.event_type,
                            "nodeId": event.node_id,
                            "payload": event.payload,
                            "createdAt": event.created_at.isoformat(),
                        },
                        default=str,
                    )
                    yield f"id: {event.id}\nevent: workflow\ndata: {data}\n\n"
                heartbeat = 0
            else:
                heartbeat += 1
                if heartbeat >= 10:
                    yield ": heartbeat\n\n"
                    heartbeat = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
