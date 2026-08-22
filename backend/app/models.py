from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import FlowStatus, NodeRunStatus, RunStatus, RunTriggerType
from app.flow_config import empty_config_schema
from app.input_schema import empty_input_schema


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    memberships: Mapped[list["GroupMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Group(Base):
    __tablename__ = "app_group"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )
    flows: Mapped[list["FlowDefinition"]] = relationship(back_populates="group")


class GroupMember(Base):
    __tablename__ = "group_member"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", "role", name="uq_group_member_role"),
        Index("ix_group_member_user_group", "user_id", "group_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class FlowDefinition(Base):
    __tablename__ = "flow_definition"
    __table_args__ = (UniqueConstraint("group_id", "name", name="uq_flow_definition_group_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id"), default="default", nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=FlowStatus.DRAFT, nullable=False)
    draft_content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=empty_input_schema, nullable=False
    )
    config_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=empty_config_schema, nullable=False
    )
    default_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    versions: Mapped[list[FlowVersion]] = relationship(
        back_populates="flow", cascade="all, delete-orphan"
    )
    runs: Mapped[list[FlowRun]] = relationship(back_populates="flow")
    schedules: Mapped[list[FlowSchedule]] = relationship(
        back_populates="flow", cascade="all, delete-orphan"
    )
    credentials: Mapped[list[FlowCredential]] = relationship(
        back_populates="flow", cascade="all, delete-orphan"
    )
    group: Mapped[Group] = relationship(back_populates="flows")


class FlowVersion(Base):
    __tablename__ = "flow_version"
    __table_args__ = (UniqueConstraint("flow_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id"), default="default", nullable=False
    )
    flow_id: Mapped[str] = mapped_column(
        ForeignKey("flow_definition.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=empty_input_schema, nullable=False
    )
    config_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=empty_config_schema, nullable=False
    )
    default_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    node_registry_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    flow: Mapped[FlowDefinition] = relationship(back_populates="versions")
    runs: Mapped[list[FlowRun]] = relationship(back_populates="flow_version")


class FlowRun(Base):
    __tablename__ = "flow_run"
    __table_args__ = (Index("ix_flow_run_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id"), default="default", nullable=False
    )
    flow_id: Mapped[str] = mapped_column(ForeignKey("flow_definition.id"), nullable=False)
    flow_version_id: Mapped[str] = mapped_column(ForeignKey("flow_version.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=RunStatus.PENDING, nullable=False)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    flow_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        String(30), default=RunTriggerType.MANUAL, nullable=False
    )
    trigger_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("flow_run.id"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    flow: Mapped[FlowDefinition] = relationship(back_populates="runs")
    flow_version: Mapped[FlowVersion] = relationship(back_populates="runs")
    node_runs: Mapped[list[NodeRun]] = relationship(
        back_populates="flow_run", cascade="all, delete-orphan"
    )
    events: Mapped[list[FlowEvent]] = relationship(
        back_populates="flow_run", cascade="all, delete-orphan"
    )
    variables: Mapped[list[FlowRunVariable]] = relationship(
        back_populates="flow_run", cascade="all, delete-orphan", order_by="FlowRunVariable.name"
    )
    callback_waits: Mapped[list[CallbackWait]] = relationship(
        back_populates="flow_run", cascade="all, delete-orphan"
    )


class FlowSchedule(Base):
    __tablename__ = "flow_schedule"
    __table_args__ = (Index("ix_flow_schedule_due", "enabled", "next_run_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id"), default="default", nullable=False
    )
    flow_id: Mapped[str] = mapped_column(
        ForeignKey("flow_definition.id", ondelete="CASCADE"), nullable=False
    )
    flow_version_id: Mapped[str] = mapped_column(
        ForeignKey("flow_version.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC", nullable=False)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    config_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    temporal_schedule_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    flow: Mapped[FlowDefinition] = relationship(back_populates="schedules")
    flow_version: Mapped[FlowVersion] = relationship()


class FlowRunVariable(Base):
    __tablename__ = "flow_run_variable"
    __table_args__ = (
        UniqueConstraint("flow_run_id", "name", name="uq_flow_run_variable"),
        Index("ix_flow_run_variable_run_name", "flow_run_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(String(36), default="default", nullable=False)
    flow_run_id: Mapped[str] = mapped_column(
        ForeignKey("flow_run.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    value_type: Mapped[str] = mapped_column(String(30), nullable=False)
    updated_by_node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    flow_run: Mapped[FlowRun] = relationship(back_populates="variables")


class FlowCredential(Base):
    __tablename__ = "flow_credential"
    __table_args__ = (UniqueConstraint("flow_id", "alias", name="uq_flow_credential_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id"), default="default", nullable=False
    )
    flow_id: Mapped[str] = mapped_column(
        ForeignKey("flow_definition.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    credential_type: Mapped[str] = mapped_column(String(30), nullable=False)
    allowed_origins: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    flow: Mapped[FlowDefinition] = relationship(back_populates="credentials")
    revisions: Mapped[list[FlowCredentialRevision]] = relationship(
        back_populates="credential", cascade="all, delete-orphan"
    )


class FlowCredentialRevision(Base):
    __tablename__ = "flow_credential_revision"
    __table_args__ = (
        UniqueConstraint("credential_id", "revision", name="uq_flow_credential_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credential_id: Mapped[str] = mapped_column(
        ForeignKey("flow_credential.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    key_id: Mapped[str] = mapped_column(String(100), nullable=False)
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    credential: Mapped[FlowCredential] = relationship(back_populates="revisions")


class AdminSession(Base):
    __tablename__ = "admin_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_event"
    __table_args__ = (Index("ix_security_audit_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    flow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class NodeRun(Base):
    __tablename__ = "node_run"
    __table_args__ = (
        UniqueConstraint("flow_run_id", "node_id"),
        Index("ix_node_run_status_available", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    flow_run_id: Mapped[str] = mapped_column(
        ForeignKey("flow_run.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    node_version: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=NodeRunStatus.PENDING, nullable=False
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    flow_run: Mapped[FlowRun] = relationship(back_populates="node_runs")
    attempts_log: Mapped[list[NodeRunAttempt]] = relationship(
        back_populates="node_run", cascade="all, delete-orphan"
    )
    callback_waits: Mapped[list[CallbackWait]] = relationship(
        back_populates="node_run", cascade="all, delete-orphan"
    )


class CallbackWait(Base):
    __tablename__ = "callback_wait"
    __table_args__ = (
        UniqueConstraint("node_run_id", "attempt_number", name="uq_callback_wait_attempt"),
        Index("ix_callback_wait_due", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    flow_run_id: Mapped[str] = mapped_column(
        ForeignKey("flow_run.id", ondelete="CASCADE"), nullable=False
    )
    node_run_id: Mapped[str] = mapped_column(
        ForeignKey("node_run.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="WAITING", nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    credential_alias: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    flow_run: Mapped[FlowRun] = relationship(back_populates="callback_waits")
    node_run: Mapped[NodeRun] = relationship(back_populates="callback_waits")


class NodeRunAttempt(Base):
    __tablename__ = "node_run_attempt"
    __table_args__ = (UniqueConstraint("node_run_id", "attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_run_id: Mapped[str] = mapped_column(
        ForeignKey("node_run.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    node_run: Mapped[NodeRun] = relationship(back_populates="attempts_log")


class FlowEvent(Base):
    __tablename__ = "flow_event"
    __table_args__ = (Index("ix_flow_event_run_id_id", "flow_run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(36), default="default", nullable=False)
    flow_run_id: Mapped[str] = mapped_column(
        ForeignKey("flow_run.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    flow_run: Mapped[FlowRun] = relationship(back_populates="events")


class ApprovalGroup(Base):
    __tablename__ = "approval_group"
    __table_args__ = (UniqueConstraint("group_id", "alias", name="uq_approval_group_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("app_group.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ApprovalGroupMember(Base):
    __tablename__ = "approval_group_member"
    __table_args__ = (
        UniqueConstraint("approval_group_id", "user_id", name="uq_approval_group_member"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_group_id: Mapped[str] = mapped_column(
        ForeignKey("approval_group.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )


class ApprovalTask(Base):
    __tablename__ = "approval_task"
    __table_args__ = (
        Index("ix_approval_task_group_status", "group_id", "status"),
        UniqueConstraint("flow_run_id", "node_id", name="uq_approval_task_node"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approval_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    flow_run_id: Mapped[str] = mapped_column(
        ForeignKey("flow_run.id", ondelete="CASCADE"), nullable=False
    )
    node_run_id: Mapped[str] = mapped_column(
        ForeignKey("node_run.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
