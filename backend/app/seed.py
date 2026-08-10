from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import FlowStatus
from app.input_schema import demo_input_schema
from app.models import FlowCredential, FlowDefinition, FlowVersion
from app.security.credentials import add_audit, create_revision

DEMO_FLOW_NAME = "Customer Score Automation"
MANUAL_FLOW_NAME = "Manual Approval Demo"
VARIABLE_FLOW_NAME = "Run Variables Demo"
POLL_FLOW_NAME = "HTTP Poll Demo"
CREDENTIAL_FLOW_NAME = "Credential Authentication Demo"
CREDENTIAL_ALIAS = "demo_partner_api"
CALLBACK_FLOW_NAME = "HTTP Callback Demo"
CALLBACK_CREDENTIAL_ALIAS = "callback_demo_auth"


def poll_input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "jobId": {
                "type": "string",
                "title": "Job ID",
                "description": "A demo third-party job identifier.",
                "default": "JOB-1001",
                "minLength": 1,
            }
        },
        "required": ["jobId"],
        "additionalProperties": False,
    }


def credential_demo_input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "jobId": {
                "type": "string",
                "title": "Secure job ID",
                "description": (
                    "Use JOB-AUTH-1001 for approval or JOB-AUTH-DENY for the denied branch."
                ),
                "default": "JOB-AUTH-1001",
                "minLength": 1,
            }
        },
        "required": ["jobId"],
        "additionalProperties": False,
    }


def credential_demo_config_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "partnerBaseUrl": {
                "type": "string",
                "title": "Partner base URL",
                "minLength": 1,
            }
        },
        "required": ["partnerBaseUrl"],
        "additionalProperties": False,
    }


def callback_demo_input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "requestId": {
                "type": "string",
                "title": "Request ID",
                "description": "Identifier carried into the callback result.",
                "default": "CALLBACK-1001",
                "minLength": 1,
            }
        },
        "required": ["requestId"],
        "additionalProperties": False,
    }


def callback_demo_flow_content() -> dict:
    nodes = [
        ("start", "Start callback run", "start", {}, 80, 220),
        (
            "wait-callback",
            "Wait for partner callback",
            "http_callback",
            {
                "timeoutSeconds": 3600,
                "authMode": "BEARER",
                "credentialRef": CALLBACK_CREDENTIAL_ALIAS,
            },
            380,
            220,
        ),
        (
            "callback-approved",
            "Callback approved?",
            "condition",
            {"field": "approved", "operator": "equals", "value": True},
            700,
            220,
        ),
        (
            "approved",
            "Record callback approval",
            "result",
            {
                "result": "CALLBACK_APPROVED",
                "message": "Callback request {requestId} was approved",
            },
            1020,
            100,
        ),
        (
            "denied",
            "Record callback denial",
            "result",
            {
                "result": "CALLBACK_DENIED",
                "message": "Callback request {requestId} was denied",
            },
            1020,
            340,
        ),
        ("end", "Complete callback run", "end", {}, 1340, 220),
    ]
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": node_id,
                "type": "workflow",
                "position": {"x": x, "y": y},
                "data": {
                    "label": label,
                    "nodeType": node_type,
                    "nodeVersion": "1.0",
                    "config": config,
                },
            }
            for node_id, label, node_type, config, x, y in nodes
        ],
        "edges": [
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "sourceHandle": source_handle,
                "targetHandle": "input",
            }
            for edge_id, source, target, source_handle in [
                ("e-start-wait", "start", "wait-callback", "output"),
                ("e-wait-check", "wait-callback", "callback-approved", "output"),
                ("e-check-approved", "callback-approved", "approved", "true"),
                ("e-check-denied", "callback-approved", "denied", "false"),
                ("e-approved-end", "approved", "end", "output"),
                ("e-denied-end", "denied", "end", "output"),
            ]
        ],
    }


def credential_demo_flow_content() -> dict:
    nodes = [
        ("start", "Start secure job", "start", {}, 80, 220),
        (
            "submit-job",
            "Submit with Bearer auth",
            "http_request",
            {
                "method": "GET",
                "url": "{{ flowConfig.partnerBaseUrl }}/secure/jobs/{jobId}/submit",
                "credentialRef": CREDENTIAL_ALIAS,
                "timeoutSeconds": 10,
                "maxAttempts": 3,
            },
            360,
            220,
        ),
        (
            "poll-job",
            "Poll with Bearer auth",
            "http_poll",
            {
                "url": "{{ flowConfig.partnerBaseUrl }}/secure/jobs/{jobId}",
                "credentialRef": CREDENTIAL_ALIAS,
                "responsePath": "status",
                "operator": "equals",
                "expectedValue": "COMPLETED",
                "intervalSeconds": 1,
                "maxPolls": 5,
                "requestTimeoutSeconds": 10,
            },
            660,
            220,
        ),
        (
            "approval-check",
            "Partner approved?",
            "condition",
            {"field": "approved", "operator": "equals", "value": True},
            960,
            220,
        ),
        (
            "approved",
            "Record approval",
            "result",
            {
                "result": "AUTHENTICATED_APPROVAL",
                "message": "Secure job {jobId} was approved",
            },
            1260,
            100,
        ),
        (
            "denied",
            "Record denial",
            "result",
            {
                "result": "AUTHENTICATED_DENIAL",
                "message": "Secure job {jobId} was denied",
            },
            1260,
            340,
        ),
        ("end", "Complete", "end", {}, 1560, 220),
    ]
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": node_id,
                "type": "workflow",
                "position": {"x": x, "y": y},
                "data": {
                    "label": label,
                    "nodeType": node_type,
                    "nodeVersion": "1.0",
                    "config": config,
                },
            }
            for node_id, label, node_type, config, x, y in nodes
        ],
        "edges": [
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "sourceHandle": source_handle,
                "targetHandle": "input",
            }
            for edge_id, source, target, source_handle in [
                ("e-start-submit", "start", "submit-job", "output"),
                ("e-submit-poll", "submit-job", "poll-job", "output"),
                ("e-poll-check", "poll-job", "approval-check", "output"),
                ("e-check-approved", "approval-check", "approved", "true"),
                ("e-check-denied", "approval-check", "denied", "false"),
                ("e-approved-end", "approved", "end", "output"),
                ("e-denied-end", "denied", "end", "output"),
            ]
        ],
    }


def demo_flow_content() -> dict:
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": "start",
                "type": "workflow",
                "position": {"x": 80, "y": 220},
                "data": {
                    "label": "Start with customer ID",
                    "nodeType": "start",
                    "nodeVersion": "1.0",
                    "config": {},
                },
            },
            {
                "id": "partner-score",
                "type": "workflow",
                "position": {"x": 350, "y": 220},
                "data": {
                    "label": "Fetch partner score",
                    "nodeType": "http_request",
                    "nodeVersion": "1.0",
                    "config": {
                        "method": "GET",
                        "url": "http://partner-api:8001/customers/{customerId}/score",
                        "timeoutSeconds": 10,
                        "maxAttempts": 3,
                    },
                },
            },
            {
                "id": "score-check",
                "type": "workflow",
                "position": {"x": 650, "y": 220},
                "data": {
                    "label": "Score at least 70?",
                    "nodeType": "condition",
                    "nodeVersion": "1.0",
                    "config": {
                        "field": "score",
                        "operator": "greater_than_or_equal",
                        "value": 70,
                    },
                },
            },
            {
                "id": "approved",
                "type": "workflow",
                "position": {"x": 950, "y": 100},
                "data": {
                    "label": "Approve customer",
                    "nodeType": "result",
                    "nodeVersion": "1.0",
                    "config": {
                        "result": "APPROVED",
                        "message": "Customer {customerId} approved with score {score}",
                    },
                },
            },
            {
                "id": "manual-review",
                "type": "workflow",
                "position": {"x": 950, "y": 340},
                "data": {
                    "label": "Request manual review",
                    "nodeType": "result",
                    "nodeVersion": "1.0",
                    "config": {
                        "result": "MANUAL_REVIEW",
                        "message": "Customer {customerId} requires review; score is {score}",
                    },
                },
            },
            {
                "id": "end",
                "type": "workflow",
                "position": {"x": 1260, "y": 220},
                "data": {
                    "label": "Complete",
                    "nodeType": "end",
                    "nodeVersion": "1.0",
                    "config": {},
                },
            },
        ],
        "edges": [
            {
                "id": "e-start-score",
                "source": "start",
                "target": "partner-score",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "e-score-check",
                "source": "partner-score",
                "target": "score-check",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "e-approved",
                "source": "score-check",
                "target": "approved",
                "sourceHandle": "true",
                "targetHandle": "input",
            },
            {
                "id": "e-review",
                "source": "score-check",
                "target": "manual-review",
                "sourceHandle": "false",
                "targetHandle": "input",
            },
            {
                "id": "e-approved-end",
                "source": "approved",
                "target": "end",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "e-review-end",
                "source": "manual-review",
                "target": "end",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
    }


def seed_credential_demo_flow(db: Session) -> None:
    flow = db.scalar(
        select(FlowDefinition).where(FlowDefinition.name == CREDENTIAL_FLOW_NAME)
    )
    if flow is None:
        content = credential_demo_flow_content()
        input_schema = credential_demo_input_schema()
        config_schema = credential_demo_config_schema()
        default_config = {"partnerBaseUrl": "http://partner-api:8001"}
        flow = FlowDefinition(
            name=CREDENTIAL_FLOW_NAME,
            description=(
                "Uses an encrypted Flow Credential for an authenticated request and "
                "durable HTTP polling."
            ),
            status=FlowStatus.ACTIVE,
            draft_content=content,
            input_schema=input_schema,
            config_schema=config_schema,
            default_config=default_config,
            current_version=1,
        )
        db.add(flow)
        db.flush()
        db.add(
            FlowVersion(
                flow_id=flow.id,
                version_number=1,
                content=content,
                input_schema=input_schema,
                config_schema=config_schema,
                default_config=default_config,
            )
        )

    credential = db.scalar(
        select(FlowCredential).where(
            FlowCredential.flow_id == flow.id,
            FlowCredential.alias == CREDENTIAL_ALIAS,
        )
    )
    if credential is None:
        credential = FlowCredential(
            flow_id=flow.id,
            alias=CREDENTIAL_ALIAS,
            credential_type="BEARER",
            allowed_origins=["http://partner-api:8001"],
            enabled=True,
            current_revision=0,
        )
        db.add(credential)
        db.flush()
        create_revision(
            db,
            credential,
            {"token": get_settings().demo_partner_token},
        )
        add_audit(
            db,
            "CREDENTIAL_CREATED",
            actor="system",
            flow_id=flow.id,
            credential_id=credential.id,
            payload={
                "alias": credential.alias,
                "type": credential.credential_type,
                "revision": credential.current_revision,
                "source": "demo-seed",
            },
        )


def seed_callback_demo_flow(db: Session) -> None:
    flow = db.scalar(
        select(FlowDefinition).where(FlowDefinition.name == CALLBACK_FLOW_NAME)
    )
    if flow is None:
        content = callback_demo_flow_content()
        input_schema = callback_demo_input_schema()
        flow = FlowDefinition(
            name=CALLBACK_FLOW_NAME,
            description=(
                "Waits without holding a worker until an authenticated third party "
                "posts a JSON callback."
            ),
            status=FlowStatus.ACTIVE,
            draft_content=content,
            input_schema=input_schema,
            current_version=1,
        )
        db.add(flow)
        db.flush()
        db.add(
            FlowVersion(
                flow_id=flow.id,
                version_number=1,
                content=content,
                input_schema=input_schema,
            )
        )

    credential = db.scalar(
        select(FlowCredential).where(
            FlowCredential.flow_id == flow.id,
            FlowCredential.alias == CALLBACK_CREDENTIAL_ALIAS,
        )
    )
    if credential is None:
        credential = FlowCredential(
            flow_id=flow.id,
            alias=CALLBACK_CREDENTIAL_ALIAS,
            credential_type="BEARER",
            allowed_origins=["http://localhost:8000"],
            enabled=True,
            current_revision=0,
        )
        db.add(credential)
        db.flush()
        create_revision(db, credential, {"token": get_settings().demo_partner_token})
        add_audit(
            db,
            "CREDENTIAL_CREATED",
            actor="system",
            flow_id=flow.id,
            credential_id=credential.id,
            payload={
                "alias": credential.alias,
                "type": credential.credential_type,
                "revision": credential.current_revision,
                "source": "callback-demo-seed",
            },
        )


def seed_demo_flow(db: Session) -> None:
    existing = db.scalar(select(FlowDefinition).where(FlowDefinition.name == DEMO_FLOW_NAME))
    if existing is None:
        content = demo_flow_content()
        flow = FlowDefinition(
            name=DEMO_FLOW_NAME,
            description="Calls a partner API and routes customers based on the returned score.",
            status=FlowStatus.ACTIVE,
            draft_content=content,
            input_schema=demo_input_schema(),
            current_version=1,
        )
        db.add(flow)
        db.flush()
        db.add(
            FlowVersion(
                flow_id=flow.id,
                version_number=1,
                content=content,
                input_schema=demo_input_schema(),
            )
        )
    manual_existing = db.scalar(
        select(FlowDefinition).where(FlowDefinition.name == MANUAL_FLOW_NAME)
    )
    if manual_existing is None:
        content = manual_flow_content()
        flow = FlowDefinition(
            name=MANUAL_FLOW_NAME,
            description="Pauses for an operator decision before completing the run.",
            status=FlowStatus.ACTIVE,
            draft_content=content,
            input_schema=demo_input_schema(),
            current_version=1,
        )
        db.add(flow)
        db.flush()
        db.add(
            FlowVersion(
                flow_id=flow.id,
                version_number=1,
                content=content,
                input_schema=demo_input_schema(),
            )
        )
    variable_existing = db.scalar(
        select(FlowDefinition).where(FlowDefinition.name == VARIABLE_FLOW_NAME)
    )
    if variable_existing is None:
        content = variable_flow_content()
        flow = FlowDefinition(
            name=VARIABLE_FLOW_NAME,
            description="Stores an API result in the run context and reuses it from a template.",
            status=FlowStatus.ACTIVE,
            draft_content=content,
            input_schema=demo_input_schema(),
            current_version=1,
        )
        db.add(flow)
        db.flush()
        db.add(
            FlowVersion(
                flow_id=flow.id,
                version_number=1,
                content=content,
                input_schema=demo_input_schema(),
            )
        )
    poll_existing = db.scalar(
        select(FlowDefinition).where(FlowDefinition.name == POLL_FLOW_NAME)
    )
    if poll_existing is None:
        content = poll_flow_content()
        input_schema = poll_input_schema()
        flow = FlowDefinition(
            name=POLL_FLOW_NAME,
            description="Polls a demo third-party job without holding a worker between requests.",
            status=FlowStatus.ACTIVE,
            draft_content=content,
            input_schema=input_schema,
            current_version=1,
        )
        db.add(flow)
        db.flush()
        db.add(
            FlowVersion(
                flow_id=flow.id,
                version_number=1,
                content=content,
                input_schema=input_schema,
            )
        )
    seed_credential_demo_flow(db)
    seed_callback_demo_flow(db)
    db.commit()


def manual_flow_content() -> dict:
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": "start",
                "type": "workflow",
                "position": {"x": 100, "y": 180},
                "data": {
                    "label": "Start review",
                    "nodeType": "start",
                    "nodeVersion": "1.0",
                    "config": {},
                },
            },
            {
                "id": "approval",
                "type": "workflow",
                "position": {"x": 420, "y": 180},
                "data": {
                    "label": "Operator approval",
                    "nodeType": "manual_approval",
                    "nodeVersion": "1.0",
                    "config": {"prompt": "Approve or reject this customer request."},
                },
            },
            {
                "id": "end",
                "type": "workflow",
                "position": {"x": 740, "y": 180},
                "data": {
                    "label": "Complete",
                    "nodeType": "end",
                    "nodeVersion": "1.0",
                    "config": {},
                },
            },
        ],
        "edges": [
            {
                "id": "e-start-approval",
                "source": "start",
                "target": "approval",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
            {
                "id": "e-approval-end",
                "source": "approval",
                "target": "end",
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
    }


def variable_flow_content() -> dict:
    nodes = [
        ("start", "Start", "start", {}, 80),
        (
            "score",
            "Fetch customer score",
            "http_request",
            {
                "method": "GET",
                "url": "http://partner-api:8001/customers/{customerId}/score",
                "timeoutSeconds": 10,
                "maxAttempts": 3,
            },
            350,
        ),
        (
            "store-score",
            "Store score variable",
            "set_variable",
            {
                "name": "customerScore",
                "value": "{{ input.score }}",
                "writeMode": "REPLACE",
            },
            650,
        ),
        (
            "result",
            "Use score variable",
            "result",
            {
                "result": "VARIABLE_STORED",
                "message": "Stored score {{ variables.customerScore }} for {customerId}",
            },
            950,
        ),
        ("end", "Complete", "end", {}, 1250),
    ]
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": node_id,
                "type": "workflow",
                "position": {"x": x, "y": 220},
                "data": {
                    "label": label,
                    "nodeType": node_type,
                    "nodeVersion": "1.0",
                    "config": config,
                },
            }
            for node_id, label, node_type, config, x in nodes
        ],
        "edges": [
            {
                "id": f"e-{source}-{target}",
                "source": source,
                "target": target,
                "sourceHandle": "output",
                "targetHandle": "input",
            }
            for source, target in [
                ("start", "score"),
                ("score", "store-score"),
                ("store-score", "result"),
                ("result", "end"),
            ]
        ],
    }


def poll_flow_content() -> dict:
    nodes = [
        ("start", "Start with job ID", "start", {}, 80),
        (
            "poll-job",
            "Wait for job completion",
            "http_poll",
            {
                "url": "http://partner-api:8001/jobs/{jobId}",
                "responsePath": "status",
                "operator": "equals",
                "expectedValue": "COMPLETED",
                "intervalSeconds": 2,
                "maxPolls": 5,
                "requestTimeoutSeconds": 10,
            },
            380,
        ),
        (
            "result",
            "Record completion",
            "result",
            {
                "result": "JOB_COMPLETED",
                "message": "Job {jobId} completed successfully",
            },
            700,
        ),
        ("end", "Complete", "end", {}, 1020),
    ]
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": node_id,
                "type": "workflow",
                "position": {"x": x, "y": 220},
                "data": {
                    "label": label,
                    "nodeType": node_type,
                    "nodeVersion": "1.0",
                    "config": config,
                },
            }
            for node_id, label, node_type, config, x in nodes
        ],
        "edges": [
            {
                "id": f"e-{source}-{target}",
                "source": source,
                "target": target,
                "sourceHandle": "output",
                "targetHandle": "input",
            }
            for source, target in [
                ("start", "poll-job"),
                ("poll-job", "result"),
                ("result", "end"),
            ]
        ],
    }
