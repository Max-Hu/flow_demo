from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.flow_config import empty_config_schema
from app.input_schema import empty_input_schema


class Position(BaseModel):
    x: float
    y: float


class FlowNodeData(BaseModel):
    label: str
    node_type: str = Field(alias="nodeType")
    node_version: str = Field(default="1.0", alias="nodeVersion")
    config: dict[str, Any] = Field(default_factory=dict)
    flow_config_patch: dict[str, Any] | None = Field(
        default=None, alias="flowConfigPatch"
    )

    model_config = ConfigDict(populate_by_name=True)


class FlowNode(BaseModel):
    id: str
    type: str = "workflow"
    position: Position
    data: FlowNodeData


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str | None = Field(default=None, alias="sourceHandle")
    target_handle: str | None = Field(default=None, alias="targetHandle")

    model_config = ConfigDict(populate_by_name=True)


class FlowContent(BaseModel):
    schema_version: int = Field(default=1, alias="schemaVersion")
    nodes: list[FlowNode]
    edges: list[FlowEdge]

    model_config = ConfigDict(populate_by_name=True)


class FlowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    content: FlowContent
    input_schema: dict[str, Any] = Field(default_factory=empty_input_schema, alias="inputSchema")
    config_schema: dict[str, Any] = Field(default_factory=empty_config_schema, alias="configSchema")
    default_config: dict[str, Any] = Field(default_factory=dict, alias="defaultConfig")

    model_config = ConfigDict(populate_by_name=True)


class FlowDraftUpdate(BaseModel):
    description: str | None = None
    content: FlowContent
    expected_row_version: int = Field(alias="expectedRowVersion")
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    config_schema: dict[str, Any] = Field(alias="configSchema")
    default_config: dict[str, Any] = Field(alias="defaultConfig")

    model_config = ConfigDict(populate_by_name=True)


class FlowSummary(BaseModel):
    id: str
    name: str
    description: str
    status: str
    current_version: int
    row_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FlowDetail(FlowSummary):
    draft_content: dict[str, Any]
    input_schema: dict[str, Any]
    config_schema: dict[str, Any]
    default_config: dict[str, Any]


class FlowVersionResponse(BaseModel):
    id: str
    flow_id: str
    version_number: int
    content: dict[str, Any]
    input_schema: dict[str, Any]
    config_schema: dict[str, Any]
    default_config: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ValidationIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None


class ValidationResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssue]


class GroupResponse(BaseModel):
    id: str
    name: str
    description: str
    roles: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class GroupMemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    display_name: str = Field(default="", alias="displayName", max_length=200)
    roles: list[str] = Field(default_factory=list)
    is_super_admin: bool = Field(default=False, alias="isSuperAdmin")

    model_config = ConfigDict(populate_by_name=True)


class GroupMemberUpdate(BaseModel):
    roles: list[str]


class GroupMemberResponse(BaseModel):
    user_id: str
    username: str
    display_name: str
    is_super_admin: bool
    roles: list[str]


class ApprovalGroupCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class ApprovalGroupResponse(BaseModel):
    id: str
    group_id: str
    alias: str
    name: str
    member_user_ids: list[str] = []


class AuthUserResponse(BaseModel):
    username: str
    csrf_token: str
    is_super_admin: bool = False
    groups: list[GroupResponse] = []
    current_group_id: str | None = None


class ApprovalTaskResponse(BaseModel):
    id: str
    group_id: str
    flow_run_id: str
    node_run_id: str
    node_id: str
    status: str
    prompt: str
    decision: str | None
    comment: str
    decided_by_user_id: str | None
    decided_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RunCreate(BaseModel):
    input_data: dict[str, Any] = Field(default_factory=dict, alias="inputData")
    version_number: int | None = Field(default=None, ge=1, alias="versionNumber")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FlowStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "PAUSED", "ARCHIVED"]


class CredentialCreate(BaseModel):
    alias: str = Field(min_length=2, max_length=100)
    type: Literal["BEARER", "BASIC", "API_KEY_HEADER"]
    allowed_origins: list[str] = Field(alias="allowedOrigins", min_length=1, max_length=20)
    secret: dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)


class CredentialRotate(BaseModel):
    secret: dict[str, Any]


class CredentialUpdate(BaseModel):
    allowed_origins: list[str] | None = Field(
        default=None, alias="allowedOrigins", min_length=1, max_length=20
    )
    enabled: bool | None = None

    model_config = ConfigDict(populate_by_name=True)


class CredentialResponse(BaseModel):
    id: str
    flow_id: str
    alias: str
    type: str
    allowed_origins: list[str]
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cron_expression: str = Field(min_length=1, max_length=100, alias="cronExpression")
    timezone: str = "UTC"
    version_number: int = Field(ge=1, alias="versionNumber")
    input_data: dict[str, Any] = Field(default_factory=dict, alias="inputData")
    enabled: bool = True

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    cron_expression: str | None = Field(
        default=None, min_length=1, max_length=100, alias="cronExpression"
    )
    timezone: str | None = None
    version_number: int | None = Field(default=None, ge=1, alias="versionNumber")
    input_data: dict[str, Any] | None = Field(default=None, alias="inputData")
    enabled: bool | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ScheduleResponse(BaseModel):
    id: str
    flow_id: str
    name: str
    cron_expression: str
    timezone: str
    version_number: int
    input_data: dict[str, Any]
    enabled: bool
    next_run_at: datetime
    last_triggered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NodeResume(BaseModel):
    decision: str = Field(default="CONTINUE", min_length=1, max_length=100)
    comment: str = Field(default="", max_length=2000)
    data: dict[str, Any] = Field(default_factory=dict)


class CallbackWaitResponse(BaseModel):
    id: str
    status: str
    callback_url: str
    auth_mode: str
    credential_alias: str | None
    expires_at: datetime
    received_at: datetime | None
    created_at: datetime


class NodeRunResponse(BaseModel):
    id: str
    node_id: str
    node_type: str
    node_version: str
    status: str
    attempts: int
    max_attempts: int
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    error_message: str | None
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    callback: CallbackWaitResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class RunVariableResponse(BaseModel):
    id: str
    name: str
    value: Any
    value_type: str
    updated_by_node_id: str
    revision: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RunSummary(BaseModel):
    id: str
    flow_id: str
    flow_name: str
    version_number: int
    status: str
    trigger_type: str
    trigger_id: str | None
    parent_run_id: str | None
    requested_at: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(BaseModel):
    id: str
    flow_id: str
    flow_name: str
    version_number: int
    status: str
    input_data: dict[str, Any]
    flow_config: dict[str, Any]
    output_data: dict[str, Any] | None
    error_message: str | None
    cancel_requested: bool
    trigger_type: str
    trigger_id: str | None
    parent_run_id: str | None
    source_metadata: dict[str, Any]
    requested_at: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    flow_content: dict[str, Any]
    node_runs: list[NodeRunResponse]
    variables: list[RunVariableResponse]


class PortDefinition(BaseModel):
    name: str
    label: str
    data_type: Literal["string", "number", "boolean", "object", "array", "any"] = Field(
        alias="dataType"
    )

    model_config = ConfigDict(populate_by_name=True)


class NodeTypeResponse(BaseModel):
    type: str
    version: str
    name: str
    description: str
    category: str
    color: str
    inputs: list[PortDefinition]
    outputs: list[PortDefinition]
    config_schema: dict[str, Any] = Field(alias="configSchema")
    default_config: dict[str, Any] = Field(alias="defaultConfig")
    lifecycle: Literal["active", "deprecated"] = "active"
    available_for_new_flows: bool = Field(default=True, alias="availableForNewFlows")

    model_config = ConfigDict(populate_by_name=True)
