from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from copy import deepcopy
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Header, Request
from fastapi.responses import StreamingResponse
from argon2 import PasswordHasher
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec, ScheduleState

from app.api_helpers import (
    build_run_detail,
    build_schedule_response,
    credential_is_referenced,
    credential_references,
    credential_response,
    ensure_valid_flow_configuration,
    ensure_valid_input_schema,
    ensure_valid_run_input,
    get_credential_or_404,
)
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.enums import FlowStatus, GroupRole, NodeRunStatus, RunStatus, RunTriggerType
from app.flow_config import config_validation_errors
from app.models import (
    ApprovalGroup,
    ApprovalGroupMember,
    ApprovalTask,
    FlowCredential,
    FlowDefinition,
    FlowEvent,
    FlowRun,
    FlowSchedule,
    FlowVersion,
    Group,
    GroupMember,
    NodeRun,
    User,
    utc_now,
)
from app.nodes import get_node_execution_kind, get_registry_fingerprint, list_node_definitions
from app.schemas import (
    ApprovalGroupCreate,
    ApprovalGroupResponse,
    ApprovalTaskResponse,
    CallbackWaitResponse,
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
    GroupCreate,
    GroupMemberCreate,
    GroupMemberResponse,
    GroupMemberUpdate,
    GroupResponse,
    NodeResume,
    NodeTypeResponse,
    RunCreate,
    RunDetail,
    RunSummary,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    ValidationIssue,
    ValidationResponse,
)
from app.scheduling import validate_schedule
from app.security.auth import require_user
from app.security.credentials import (
    add_audit,
    create_revision,
    normalize_origin,
    validate_credential_definition,
)
from app.security.permissions import (
    require_approve_task,
    require_design,
    require_execute,
    require_group_admin,
    require_view,
    roles_for,
)
from app.temporal.client import get_temporal_client
from app.temporal.workflows import GenericFlowWorkflow
from app.workflow.validator import validate_flow, validate_input_schema

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(require_user)]
password_hasher = PasswordHasher()


def _group_or_404(db: Session, group_id: str) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def _require_super_admin(user: User) -> None:
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin access is required")


def _validate_roles(roles: list[str]) -> list[str]:
    valid = {role.value for role in GroupRole}
    normalized = sorted({role.upper() for role in roles})
    invalid = [role for role in normalized if role not in valid]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown group roles: {', '.join(invalid)}")
    return normalized


def _member_response(db: Session, group_id: str, user: User) -> GroupMemberResponse:
    roles = sorted(
        db.scalars(
            select(GroupMember.role).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user.id,
            )
        ).all()
    )
    return GroupMemberResponse(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_super_admin=user.is_super_admin,
        roles=roles,
    )


def _flow_or_404(db: Session, group_id: str, flow_id: str) -> FlowDefinition:
    flow = db.scalar(
        select(FlowDefinition).where(
            FlowDefinition.id == flow_id,
            FlowDefinition.group_id == group_id,
        )
    )
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow


def _run_or_404(db: Session, group_id: str, run_id: str) -> FlowRun:
    run = db.scalar(
        select(FlowRun)
        .where(FlowRun.id == run_id, FlowRun.group_id == group_id)
        .options(
            selectinload(FlowRun.flow),
            selectinload(FlowRun.flow_version),
            selectinload(FlowRun.node_runs).selectinload(NodeRun.callback_waits),
            selectinload(FlowRun.variables),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _ensure_credentials_available(db: Session, flow_id: str, content: dict) -> None:
    references = credential_references(content)
    if not references:
        return
    available = set(
        db.scalars(select(FlowCredential.alias).where(FlowCredential.flow_id == flow_id)).all()
    )
    missing = sorted(references - available)
    if missing:
        raise HTTPException(status_code=422, detail=f"Flow references missing credentials: {', '.join(missing)}")


def _create_run_projection(
    db: Session,
    group_id: str,
    flow: FlowDefinition,
    version: FlowVersion,
    input_data: dict,
    *,
    trigger_type: str = RunTriggerType.MANUAL,
    trigger_id: str | None = None,
    parent_run_id: str | None = None,
    source_metadata: dict | None = None,
    flow_config: dict | None = None,
) -> FlowRun:
    run = FlowRun(
        group_id=group_id,
        flow=flow,
        flow_version=version,
        status=RunStatus.PENDING,
        input_data=input_data,
        flow_config=deepcopy(flow_config if flow_config is not None else version.default_config),
        trigger_type=trigger_type,
        trigger_id=trigger_id,
        parent_run_id=parent_run_id,
        source_metadata=source_metadata or {},
    )
    db.add(run)
    db.flush()
    for node in version.content["nodes"]:
        data = node["data"]
        config = data.get("config", {})
        execution_kind = get_node_execution_kind(data["nodeType"], data.get("nodeVersion", "1.0"))
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
                status=NodeRunStatus.PENDING,
                config=config,
                max_attempts=max_attempts,
            )
        )
    run.temporal_workflow_id = f"{group_id}:{run.id}"
    db.add(
        FlowEvent(
            group_id=group_id,
            flow_run_id=run.id,
            event_type="RUN_CREATED",
            payload={"status": RunStatus.PENDING, "version": version.version_number},
        )
    )
    db.flush()
    return run


async def _start_temporal_run(run: FlowRun) -> None:
    client = await get_temporal_client()
    settings = get_settings()
    await client.start_workflow(
        GenericFlowWorkflow.run,
        {
            "group_id": run.group_id,
            "run_id": run.id,
            "flow_id": run.flow_id,
            "flow_version_id": run.flow_version_id,
            "flow_content": run.flow_version.content,
            "input_data": run.input_data,
            "flow_config": run.flow_config,
            "trigger_type": run.trigger_type,
            "trigger_id": run.trigger_id,
        },
        id=run.temporal_workflow_id,
        task_queue=settings.temporal_task_queue,
    )


@router.get("/groups", response_model=list[GroupResponse])
def list_groups(db: DbSession, user: CurrentUser) -> list[GroupResponse]:
    groups = db.scalars(select(Group).order_by(Group.name)).all() if user.is_super_admin else db.scalars(
        select(Group).join(GroupMember).where(GroupMember.user_id == user.id).order_by(Group.name)
    ).all()
    return [
        GroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            roles=sorted(roles_for(db, user, group.id)),
        )
        for group in groups
    ]


@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, db: DbSession, user: CurrentUser) -> GroupResponse:
    _require_super_admin(user)
    group = Group(name=payload.name, description=payload.description)
    db.add(group)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A group with this name already exists") from exc
    db.refresh(group)
    return GroupResponse(id=group.id, name=group.name, description=group.description, roles=[])


@router.get("/groups/{group_id}/members", response_model=list[GroupMemberResponse])
def list_group_members(group_id: str, db: DbSession, user: CurrentUser) -> list[GroupMemberResponse]:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    members = db.scalars(
        select(User)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.username)
        .distinct()
    ).all()
    return [_member_response(db, group_id, item) for item in members]


@router.post("/groups/{group_id}/members", response_model=GroupMemberResponse, status_code=status.HTTP_201_CREATED)
def upsert_group_member(group_id: str, payload: GroupMemberCreate, db: DbSession, user: CurrentUser) -> GroupMemberResponse:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    roles = _validate_roles(payload.roles)
    if not roles:
        raise HTTPException(status_code=422, detail="At least one role is required")
    member_user = db.scalar(select(User).where(User.username == payload.username))
    if member_user is None:
        if payload.password is None:
            raise HTTPException(status_code=422, detail="Password is required for new users")
        member_user = User(
            username=payload.username,
            password_hash=password_hasher.hash(payload.password),
            display_name=payload.display_name or payload.username,
            is_super_admin=payload.is_super_admin and user.is_super_admin,
            enabled=True,
        )
        db.add(member_user)
        db.flush()
    for role in roles:
        exists = db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == member_user.id,
                GroupMember.role == role,
            )
        )
        if exists is None:
            db.add(GroupMember(group_id=group_id, user_id=member_user.id, role=role))
    db.commit()
    return _member_response(db, group_id, member_user)


@router.patch("/groups/{group_id}/members/{user_id}", response_model=GroupMemberResponse)
def update_group_member(group_id: str, user_id: str, payload: GroupMemberUpdate, db: DbSession, user: CurrentUser) -> GroupMemberResponse:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    roles = _validate_roles(payload.roles)
    if not roles:
        raise HTTPException(status_code=422, detail="At least one role is required")
    db.query(GroupMember).filter(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
    ).delete()
    for role in roles:
        db.add(GroupMember(group_id=group_id, user_id=user_id, role=role))
    db.commit()
    return _member_response(db, group_id, target)


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
def remove_group_member(group_id: str, user_id: str, db: DbSession, user: CurrentUser) -> None:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    deleted = db.query(GroupMember).filter(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
    ).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="Group member not found")
    db.commit()


@router.get("/groups/{group_id}/approval-groups", response_model=list[ApprovalGroupResponse])
def list_approval_groups(group_id: str, db: DbSession, user: CurrentUser) -> list[ApprovalGroupResponse]:
    _group_or_404(db, group_id)
    require_view(db, user, group_id)
    groups = db.scalars(
        select(ApprovalGroup).where(ApprovalGroup.group_id == group_id).order_by(ApprovalGroup.alias)
    ).all()
    return [
        ApprovalGroupResponse(
            id=item.id,
            group_id=item.group_id,
            alias=item.alias,
            name=item.name,
            member_user_ids=sorted(
                db.scalars(
                    select(ApprovalGroupMember.user_id).where(
                        ApprovalGroupMember.approval_group_id == item.id
                    )
                ).all()
            ),
        )
        for item in groups
    ]


@router.post("/groups/{group_id}/approval-groups", response_model=ApprovalGroupResponse, status_code=status.HTTP_201_CREATED)
def create_approval_group(group_id: str, payload: ApprovalGroupCreate, db: DbSession, user: CurrentUser) -> ApprovalGroupResponse:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    item = ApprovalGroup(
        group_id=group_id,
        alias=payload.alias.strip().lower(),
        name=payload.name,
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Approval group alias already exists") from exc
    db.refresh(item)
    return ApprovalGroupResponse(id=item.id, group_id=item.group_id, alias=item.alias, name=item.name, member_user_ids=[])


@router.post("/groups/{group_id}/approval-groups/{approval_group_id}/members/{user_id}", status_code=204)
def add_approval_group_member(group_id: str, approval_group_id: str, user_id: str, db: DbSession, user: CurrentUser) -> None:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    approval_group = db.scalar(
        select(ApprovalGroup).where(ApprovalGroup.id == approval_group_id, ApprovalGroup.group_id == group_id)
    )
    if approval_group is None or db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="Approval group or user not found")
    exists = db.scalar(
        select(ApprovalGroupMember).where(
            ApprovalGroupMember.approval_group_id == approval_group_id,
            ApprovalGroupMember.user_id == user_id,
        )
    )
    if exists is None:
        db.add(ApprovalGroupMember(approval_group_id=approval_group_id, user_id=user_id))
        db.commit()


@router.delete("/groups/{group_id}/approval-groups/{approval_group_id}/members/{user_id}", status_code=204)
def remove_approval_group_member(group_id: str, approval_group_id: str, user_id: str, db: DbSession, user: CurrentUser) -> None:
    _group_or_404(db, group_id)
    require_group_admin(db, user, group_id)
    deleted = db.query(ApprovalGroupMember).filter(
        ApprovalGroupMember.approval_group_id == approval_group_id,
        ApprovalGroupMember.user_id == user_id,
    ).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="Approval group member not found")
    db.commit()


@router.get("/groups/{group_id}/node-types", response_model=list[NodeTypeResponse])
def node_types(group_id: str, db: DbSession, user: CurrentUser) -> list[NodeTypeResponse]:
    _group_or_404(db, group_id)
    require_view(db, user, group_id)
    return list_node_definitions()


@router.get("/groups/{group_id}/flows", response_model=list[FlowSummary])
def list_flows(group_id: str, db: DbSession, user: CurrentUser) -> list[FlowDefinition]:
    _group_or_404(db, group_id)
    require_view(db, user, group_id)
    return list(
        db.scalars(
            select(FlowDefinition)
            .where(FlowDefinition.group_id == group_id)
            .order_by(desc(FlowDefinition.updated_at))
        )
    )


@router.post("/groups/{group_id}/flows", response_model=FlowDetail, status_code=status.HTTP_201_CREATED)
def create_flow(group_id: str, payload: FlowCreate, db: DbSession, user: CurrentUser) -> FlowDefinition:
    _group_or_404(db, group_id)
    require_design(db, user, group_id)
    ensure_valid_input_schema(payload.input_schema)
    ensure_valid_flow_configuration(payload.config_schema, payload.default_config)
    flow = FlowDefinition(
        group_id=group_id,
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


@router.get("/groups/{group_id}/flows/{flow_id}", response_model=FlowDetail)
def get_flow(group_id: str, flow_id: str, db: DbSession, user: CurrentUser) -> FlowDefinition:
    require_view(db, user, group_id)
    return _flow_or_404(db, group_id, flow_id)


@router.put("/groups/{group_id}/flows/{flow_id}/draft", response_model=FlowDetail)
def update_draft(group_id: str, flow_id: str, payload: FlowDraftUpdate, db: DbSession, user: CurrentUser) -> FlowDefinition:
    require_design(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    if flow.row_version != payload.expected_row_version:
        raise HTTPException(status_code=409, detail="The flow was changed by another editor. Reload before saving.")
    ensure_valid_input_schema(payload.input_schema)
    ensure_valid_flow_configuration(payload.config_schema, payload.default_config)
    flow.draft_content = payload.content.model_dump(by_alias=True)
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


@router.post("/groups/{group_id}/flows/{flow_id}/validate", response_model=ValidationResponse)
def validate_draft(group_id: str, flow_id: str, db: DbSession, user: CurrentUser) -> ValidationResponse:
    require_view(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    content = FlowContent.model_validate(flow.draft_content)
    issues = validate_flow(content) + validate_input_schema(flow.input_schema)
    issues.extend(
        ValidationIssue(code="INVALID_FLOW_CONFIG", message=message)
        for message in config_validation_errors(flow.config_schema, flow.default_config)
    )
    try:
        _ensure_credentials_available(db, flow.id, flow.draft_content)
    except HTTPException as exc:
        issues.append(ValidationIssue(code="MISSING_CREDENTIAL", message=str(exc.detail)))
    return ValidationResponse(valid=not issues, issues=issues)


@router.post("/groups/{group_id}/flows/{flow_id}/publish", response_model=FlowVersionResponse)
def publish_flow(group_id: str, flow_id: str, db: DbSession, user: CurrentUser) -> FlowVersion:
    require_design(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    content = FlowContent.model_validate(flow.draft_content)
    issues = validate_flow(content) + validate_input_schema(flow.input_schema)
    issues.extend(
        ValidationIssue(code="INVALID_FLOW_CONFIG", message=message)
        for message in config_validation_errors(flow.config_schema, flow.default_config)
    )
    _ensure_credentials_available(db, flow.id, flow.draft_content)
    if issues:
        raise HTTPException(status_code=422, detail={"message": "Flow validation failed", "issues": [item.model_dump() for item in issues]})
    version = FlowVersion(
        group_id=group_id,
        flow_id=flow.id,
        version_number=flow.current_version + 1,
        content=content.model_dump(by_alias=True),
        input_schema=deepcopy(flow.input_schema),
        config_schema=deepcopy(flow.config_schema),
        default_config=deepcopy(flow.default_config),
        node_registry_fingerprint=get_registry_fingerprint(),
    )
    db.add(version)
    flow.current_version = version.version_number
    flow.status = FlowStatus.ACTIVE
    flow.updated_at = utc_now()
    db.commit()
    db.refresh(version)
    return version


@router.get("/groups/{group_id}/flows/{flow_id}/versions", response_model=list[FlowVersionResponse])
def list_versions(group_id: str, flow_id: str, db: DbSession, user: CurrentUser) -> list[FlowVersion]:
    require_view(db, user, group_id)
    _flow_or_404(db, group_id, flow_id)
    return list(
        db.scalars(
            select(FlowVersion)
            .where(FlowVersion.group_id == group_id, FlowVersion.flow_id == flow_id)
            .order_by(desc(FlowVersion.version_number))
        )
    )


@router.post("/groups/{group_id}/flows/{flow_id}/versions/{version_number}/rollback", response_model=FlowVersionResponse, status_code=201)
def rollback_flow_version(group_id: str, flow_id: str, version_number: int, db: DbSession, user: CurrentUser) -> FlowVersion:
    require_design(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    source = db.scalar(
        select(FlowVersion).where(
            FlowVersion.group_id == group_id,
            FlowVersion.flow_id == flow.id,
            FlowVersion.version_number == version_number,
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Flow version not found")
    restored = FlowVersion(
        group_id=group_id,
        flow_id=flow.id,
        version_number=flow.current_version + 1,
        content=deepcopy(source.content),
        input_schema=deepcopy(source.input_schema),
        config_schema=deepcopy(source.config_schema),
        default_config=deepcopy(source.default_config),
        node_registry_fingerprint=get_registry_fingerprint(),
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


@router.patch("/groups/{group_id}/flows/{flow_id}/status", response_model=FlowDetail)
def update_flow_status(group_id: str, flow_id: str, payload: FlowStatusUpdate, db: DbSession, user: CurrentUser) -> FlowDefinition:
    require_design(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    if payload.status == FlowStatus.ACTIVE and flow.current_version < 1:
        raise HTTPException(status_code=409, detail="Publish the flow before activating it")
    flow.status = payload.status
    flow.row_version += 1
    flow.updated_at = utc_now()
    db.commit()
    db.refresh(flow)
    return flow


@router.post("/groups/{group_id}/flows/{flow_id}/runs", response_model=RunDetail, status_code=201)
async def create_run(group_id: str, flow_id: str, payload: RunCreate, db: DbSession, user: CurrentUser) -> RunDetail:
    require_execute(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    if flow.current_version < 1:
        raise HTTPException(status_code=409, detail="Publish the flow before running it")
    if flow.status != FlowStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Only active flows can start new runs")
    requested_version = payload.version_number or flow.current_version
    version = db.scalar(
        select(FlowVersion).where(
            FlowVersion.group_id == group_id,
            FlowVersion.flow_id == flow.id,
            FlowVersion.version_number == requested_version,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Published flow version not found")
    ensure_valid_run_input(version.input_schema, payload.input_data)
    ensure_valid_flow_configuration(version.config_schema, version.default_config)
    run = _create_run_projection(
        db,
        group_id,
        flow,
        version,
        payload.input_data,
        flow_config=deepcopy(version.default_config),
    )
    db.commit()
    loaded = _run_or_404(db, group_id, run.id)
    await _start_temporal_run(loaded)
    return build_run_detail(_run_or_404(db, group_id, run.id))


async def _create_temporal_schedule(schedule: FlowSchedule) -> None:
    client = await get_temporal_client()
    await client.create_schedule(
        schedule.temporal_schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                GenericFlowWorkflow.run,
                {"schedule_id": schedule.id},
                task_queue=get_settings().temporal_task_queue,
            ),
            spec=ScheduleSpec(
                cron_expressions=[schedule.cron_expression],
                time_zone_name=schedule.timezone,
            ),
            state=ScheduleState(paused=not schedule.enabled),
        ),
    )


async def _delete_temporal_schedule(schedule_id: str | None) -> None:
    if not schedule_id:
        return
    client = await get_temporal_client()
    try:
        await client.get_schedule_handle(schedule_id).delete()
    except Exception:
        # Temporal schedule deletion is best-effort for API idempotency; the DB row is authoritative.
        return


@router.get("/groups/{group_id}/runs", response_model=list[RunSummary])
def list_runs(group_id: str, db: DbSession, user: CurrentUser) -> list[RunSummary]:
    require_view(db, user, group_id)
    runs = db.scalars(
        select(FlowRun)
        .where(FlowRun.group_id == group_id)
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


@router.get("/groups/{group_id}/runs/{run_id}", response_model=RunDetail)
def get_run(group_id: str, run_id: str, db: DbSession, user: CurrentUser) -> RunDetail:
    require_view(db, user, group_id)
    return build_run_detail(_run_or_404(db, group_id, run_id))


@router.get("/groups/{group_id}/runs/{run_id}/events")
async def run_events(
    group_id: str,
    run_id: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    require_view(db, user, group_id)
    if db.get(FlowRun, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    cursor = max(after, int(last_event_id or 0))

    async def stream() -> AsyncGenerator[str, None]:
        nonlocal cursor
        heartbeat = 0
        while not await request.is_disconnected():
            with SessionLocal() as event_db:
                events = event_db.scalars(
                    select(FlowEvent)
                    .where(
                        FlowEvent.group_id == group_id,
                        FlowEvent.flow_run_id == run_id,
                        FlowEvent.id > cursor,
                    )
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


@router.post("/groups/{group_id}/runs/{run_id}/cancel", response_model=RunDetail)
async def cancel_run(group_id: str, run_id: str, db: DbSession, user: CurrentUser) -> RunDetail:
    require_execute(db, user, group_id)
    run = _run_or_404(db, group_id, run_id)
    run.cancel_requested = True
    run.status = RunStatus.CANCELLED
    run.finished_at = utc_now()
    db.add(FlowEvent(group_id=group_id, flow_run_id=run.id, event_type="RUN_CANCEL_REQUESTED", payload={"status": run.status}))
    db.commit()
    if run.temporal_workflow_id:
        client = await get_temporal_client()
        await client.get_workflow_handle(run.temporal_workflow_id).cancel()
    return build_run_detail(_run_or_404(db, group_id, run_id))


@router.post("/groups/{group_id}/runs/{run_id}/rerun", response_model=RunDetail, status_code=201)
async def rerun_run(group_id: str, run_id: str, db: DbSession, user: CurrentUser) -> RunDetail:
    require_execute(db, user, group_id)
    original = _run_or_404(db, group_id, run_id)
    if original.flow.status != FlowStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Only active flows can be rerun")
    run = _create_run_projection(
        db,
        group_id,
        original.flow,
        original.flow_version,
        deepcopy(original.input_data),
        trigger_type=RunTriggerType.RERUN,
        trigger_id=original.id,
        parent_run_id=original.id,
        source_metadata={"sourceRunId": original.id},
        flow_config=deepcopy(original.flow_config),
    )
    db.commit()
    loaded = _run_or_404(db, group_id, run.id)
    await _start_temporal_run(loaded)
    return build_run_detail(_run_or_404(db, group_id, run.id))


@router.post("/groups/{group_id}/runs/{run_id}/nodes/{node_id}/retry", response_model=RunDetail)
def retry_node(group_id: str, run_id: str, node_id: str, db: DbSession, user: CurrentUser) -> RunDetail:
    require_execute(db, user, group_id)
    _run_or_404(db, group_id, run_id)
    raise HTTPException(
        status_code=409,
        detail="Temporal runs cannot retry a completed node in-place yet. Use Run again.",
    )


@router.post("/groups/{group_id}/runs/{run_id}/nodes/{node_id}/resume", response_model=RunDetail)
async def resume_node(group_id: str, run_id: str, node_id: str, payload: NodeResume, db: DbSession, user: CurrentUser) -> RunDetail:
    run = _run_or_404(db, group_id, run_id)
    task = db.scalar(select(ApprovalTask).where(ApprovalTask.flow_run_id == run.id, ApprovalTask.node_id == node_id))
    if task is None:
        require_execute(db, user, group_id)
    else:
        require_approve_task(db, user, task)
        task.status = "APPROVED" if payload.decision != "REJECTED" else "REJECTED"
        task.decision = payload.decision
        task.comment = payload.comment
        task.decided_by_user_id = user.id
        task.decided_at = utc_now()
        db.commit()
    if not run.temporal_workflow_id:
        raise HTTPException(status_code=409, detail="Run is not attached to a Temporal workflow")
    client = await get_temporal_client()
    await client.get_workflow_handle(run.temporal_workflow_id).signal(
        GenericFlowWorkflow.resume_manual,
        {"node_id": node_id, "decision": payload.decision, "comment": payload.comment, "data": payload.data},
    )
    return build_run_detail(_run_or_404(db, group_id, run_id))


@router.get("/groups/{group_id}/approval-tasks", response_model=list[ApprovalTaskResponse])
def approval_tasks(group_id: str, db: DbSession, user: CurrentUser) -> list[ApprovalTask]:
    require_view(db, user, group_id)
    return list(
        db.scalars(
            select(ApprovalTask)
            .where(ApprovalTask.group_id == group_id)
            .order_by(desc(ApprovalTask.created_at))
        )
    )


@router.get("/groups/{group_id}/flows/{flow_id}/credentials", response_model=list[CredentialResponse])
def list_credentials(group_id: str, flow_id: str, db: DbSession, user: CurrentUser) -> list[CredentialResponse]:
    require_view(db, user, group_id)
    _flow_or_404(db, group_id, flow_id)
    items = db.scalars(
        select(FlowCredential)
        .where(FlowCredential.group_id == group_id, FlowCredential.flow_id == flow_id)
        .order_by(FlowCredential.alias)
    ).all()
    return [credential_response(item) for item in items]


@router.post("/groups/{group_id}/flows/{flow_id}/credentials", response_model=CredentialResponse, status_code=201)
def create_credential(group_id: str, flow_id: str, payload: CredentialCreate, db: DbSession, user: CurrentUser) -> CredentialResponse:
    flow = _flow_or_404(db, group_id, flow_id)
    require_design(db, user, group_id)
    try:
        alias, origins, secret = validate_credential_definition(payload.alias, payload.type, payload.allowed_origins, payload.secret)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = FlowCredential(flow=flow, group_id=group_id, alias=alias, credential_type=payload.type, allowed_origins=origins, enabled=True, current_revision=0)
    db.add(item)
    db.flush()
    create_revision(db, item, secret)
    add_audit(db, "CREDENTIAL_CREATED", actor=user.username, flow_id=flow.id, credential_id=item.id, payload={"alias": alias, "type": payload.type, "revision": item.current_revision})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Credential alias already exists") from exc
    db.refresh(item)
    return credential_response(item)


@router.post("/groups/{group_id}/flows/{flow_id}/credentials/{credential_id}/rotate", response_model=CredentialResponse)
def rotate_credential(group_id: str, flow_id: str, credential_id: str, payload: CredentialRotate, db: DbSession, user: CurrentUser) -> CredentialResponse:
    require_design(db, user, group_id)
    item = get_credential_or_404(db, flow_id, credential_id)
    if item.group_id != group_id:
        raise HTTPException(status_code=404, detail="Credential not found")
    try:
        _, _, secret = validate_credential_definition(item.alias, item.credential_type, item.allowed_origins, payload.secret)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    revision = create_revision(db, item, secret)
    item.updated_at = utc_now()
    add_audit(db, "CREDENTIAL_ROTATED", actor=user.username, flow_id=flow_id, credential_id=item.id, payload={"alias": item.alias, "revision": revision.revision})
    db.commit()
    db.refresh(item)
    return credential_response(item)


@router.patch("/groups/{group_id}/flows/{flow_id}/credentials/{credential_id}", response_model=CredentialResponse)
def update_credential(group_id: str, flow_id: str, credential_id: str, payload: CredentialUpdate, db: DbSession, user: CurrentUser) -> CredentialResponse:
    require_design(db, user, group_id)
    item = get_credential_or_404(db, flow_id, credential_id)
    if item.group_id != group_id:
        raise HTTPException(status_code=404, detail="Credential not found")
    if payload.allowed_origins is not None:
        try:
            item.allowed_origins = sorted({normalize_origin(origin) for origin in payload.allowed_origins})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.enabled is not None:
        item.enabled = payload.enabled
    item.updated_at = utc_now()
    add_audit(db, "CREDENTIAL_UPDATED", actor=user.username, flow_id=flow_id, credential_id=item.id, payload={"alias": item.alias, "enabled": item.enabled, "allowedOrigins": item.allowed_origins})
    db.commit()
    db.refresh(item)
    return credential_response(item)


@router.delete("/groups/{group_id}/flows/{flow_id}/credentials/{credential_id}", status_code=204)
def delete_credential(group_id: str, flow_id: str, credential_id: str, db: DbSession, user: CurrentUser) -> None:
    require_design(db, user, group_id)
    flow = _flow_or_404(db, group_id, flow_id)
    item = get_credential_or_404(db, flow_id, credential_id)
    if item.group_id != group_id:
        raise HTTPException(status_code=404, detail="Credential not found")
    if credential_is_referenced(db, flow, item.alias):
        raise HTTPException(status_code=409, detail="Referenced credentials cannot be deleted; disable or rotate it instead")
    db.delete(item)
    db.commit()


@router.get("/groups/{group_id}/flows/{flow_id}/schedules", response_model=list[ScheduleResponse])
def list_schedules(group_id: str, flow_id: str, db: DbSession, user: CurrentUser) -> list[ScheduleResponse]:
    require_view(db, user, group_id)
    _flow_or_404(db, group_id, flow_id)
    schedules = db.scalars(
        select(FlowSchedule)
        .where(FlowSchedule.group_id == group_id, FlowSchedule.flow_id == flow_id)
        .options(selectinload(FlowSchedule.flow_version))
        .order_by(desc(FlowSchedule.created_at))
    ).all()
    return [build_schedule_response(item) for item in schedules]


@router.post("/groups/{group_id}/flows/{flow_id}/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(group_id: str, flow_id: str, payload: ScheduleCreate, db: DbSession, user: CurrentUser) -> ScheduleResponse:
    flow = _flow_or_404(db, group_id, flow_id)
    require_execute(db, user, group_id)
    version = db.scalar(select(FlowVersion).where(FlowVersion.group_id == group_id, FlowVersion.flow_id == flow_id, FlowVersion.version_number == payload.version_number))
    if version is None:
        raise HTTPException(status_code=404, detail="Published flow version not found")
    ensure_valid_run_input(version.input_schema, payload.input_data)
    try:
        next_run_at = validate_schedule(payload.cron_expression, payload.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule = FlowSchedule(
        group_id=group_id,
        flow=flow,
        flow_version=version,
        name=payload.name,
        cron_expression=payload.cron_expression,
        timezone=payload.timezone,
        input_data=payload.input_data,
        config_overrides={},
        enabled=payload.enabled,
        next_run_at=next_run_at,
        temporal_schedule_id=f"{group_id}:{flow_id}:{payload.name}:{utc_now().timestamp()}",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    await _create_temporal_schedule(schedule)
    return build_schedule_response(schedule)


@router.put("/groups/{group_id}/flows/{flow_id}/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(group_id: str, flow_id: str, schedule_id: str, payload: ScheduleUpdate, db: DbSession, user: CurrentUser) -> ScheduleResponse:
    require_execute(db, user, group_id)
    schedule = db.scalar(select(FlowSchedule).where(FlowSchedule.id == schedule_id, FlowSchedule.group_id == group_id, FlowSchedule.flow_id == flow_id).options(selectinload(FlowSchedule.flow_version)))
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    version = schedule.flow_version
    old_temporal_schedule_id = schedule.temporal_schedule_id
    old_signature = (
        schedule.name,
        schedule.cron_expression,
        schedule.timezone,
        schedule.flow_version_id,
        json.dumps(schedule.input_data, sort_keys=True, default=str),
    )
    if payload.version_number is not None:
        version = db.scalar(select(FlowVersion).where(FlowVersion.group_id == group_id, FlowVersion.flow_id == flow_id, FlowVersion.version_number == payload.version_number))
        if version is None:
            raise HTTPException(status_code=404, detail="Published flow version not found")
        schedule.flow_version = version
    input_data = payload.input_data if payload.input_data is not None else schedule.input_data
    ensure_valid_run_input(version.input_schema, input_data)
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
    enabled_changed = payload.enabled is not None and payload.enabled != schedule.enabled
    if payload.enabled is not None:
        schedule.enabled = payload.enabled
    new_signature = (
        schedule.name,
        schedule.cron_expression,
        schedule.timezone,
        schedule.flow_version_id,
        json.dumps(schedule.input_data, sort_keys=True, default=str),
    )
    recreate_temporal = new_signature != old_signature
    if recreate_temporal:
        schedule.temporal_schedule_id = f"{group_id}:{flow_id}:{schedule.name}:{utc_now().timestamp()}"
    schedule.updated_at = utc_now()
    db.commit()
    db.refresh(schedule)
    if recreate_temporal:
        await _delete_temporal_schedule(old_temporal_schedule_id)
        await _create_temporal_schedule(schedule)
    elif enabled_changed and schedule.temporal_schedule_id:
        client = await get_temporal_client()
        handle = client.get_schedule_handle(schedule.temporal_schedule_id)
        if schedule.enabled:
            await handle.unpause(note="Enabled from FlowForge")
        else:
            await handle.pause(note="Paused from FlowForge")
    return build_schedule_response(schedule)


@router.delete("/groups/{group_id}/flows/{flow_id}/schedules/{schedule_id}", status_code=204)
async def delete_schedule(group_id: str, flow_id: str, schedule_id: str, db: DbSession, user: CurrentUser) -> None:
    require_execute(db, user, group_id)
    schedule = db.scalar(select(FlowSchedule).where(FlowSchedule.id == schedule_id, FlowSchedule.group_id == group_id, FlowSchedule.flow_id == flow_id))
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    temporal_schedule_id = schedule.temporal_schedule_id
    db.delete(schedule)
    db.commit()
    await _delete_temporal_schedule(temporal_schedule_id)
