import base64
import hashlib
import hmac
from collections.abc import Generator
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import callbacks, seed
from app import callback_api
from app.callback_api import router as callback_router
from app.config import Settings
from app.database import Base, get_db
from app.enums import FlowStatus, NodeRunStatus, RunStatus
from app.models import (
    CallbackWait,
    FlowCredential,
    FlowCredentialRevision,
    FlowDefinition,
    FlowEvent,
    FlowRun,
    FlowVersion,
    NodeRun,
    NodeRunAttempt,
    utc_now,
)
from app.schemas import FlowContent
from app.security import crypto
from app.security.crypto import encrypt_secret
from app.workflow.validator import validate_flow


def callback_settings() -> Settings:
    return Settings(
        credential_keys={"test": base64.b64encode(b"1" * 32).decode()},
        active_credential_key_id="test",
        callback_base_url="http://localhost:8000",
    )


def add_credential(
    db: Session,
    flow: FlowDefinition,
    credential_id: str,
    alias: str,
    credential_type: str,
    secret: dict,
) -> None:
    credential = FlowCredential(
        id=credential_id,
        flow=flow,
        alias=alias,
        credential_type=credential_type,
        allowed_origins=["http://localhost:8000"],
        enabled=True,
        current_revision=1,
    )
    encrypted = encrypt_secret(secret, flow.id, credential.id, 1)
    db.add_all(
        [
            credential,
            FlowCredentialRevision(
                credential=credential,
                revision=1,
                key_id=encrypted.key_id,
                nonce=encrypted.nonce,
                ciphertext=encrypted.ciphertext,
            ),
        ]
    )


def test_callback_demo_graph_is_valid() -> None:
    content = seed.callback_demo_flow_content()

    assert validate_flow(FlowContent.model_validate(content)) == []
    callback_node = next(
        node for node in content["nodes"] if node["data"]["nodeType"] == "http_callback"
    )
    assert callback_node["data"]["config"] == {
        "timeoutSeconds": 3600,
        "authMode": "BEARER",
        "credentialRef": seed.CALLBACK_CREDENTIAL_ALIAS,
    }


def test_callback_auth_supports_api_key_and_hmac(monkeypatch) -> None:
    configured = callback_settings()
    monkeypatch.setattr(crypto, "get_settings", lambda: configured)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        flow = FlowDefinition(
            id="flow-auth",
            name="Callback auth test",
            description="",
            status=FlowStatus.ACTIVE,
            draft_content={"schemaVersion": 1, "nodes": [], "edges": []},
        )
        db.add(flow)
        add_credential(
            db,
            flow,
            "credential-api-key",
            "callback_key",
            "API_KEY_HEADER",
            {"headerName": "X-Callback-Key", "value": "top-secret", "prefix": "Key "},
        )
        db.commit()
        item = CallbackWait(
            id="callback-api-key",
            flow_run_id="run-unused",
            node_run_id="node-unused",
            node_id="wait",
            attempt_number=1,
            status="WAITING",
            auth_mode="API_KEY_HEADER",
            credential_alias="callback_key",
            expires_at=utc_now() + timedelta(minutes=5),
        )
        assert callbacks.verify_callback_auth(
            db,
            item,
            flow.id,
            {"X-Callback-Key": "Key top-secret"},
            b"{}",
        ) == 1

        body = b'{"approved":true}'
        item.auth_mode = "HMAC_SHA256"
        signature = "sha256=" + hmac.new(
            b"top-secret", body, hashlib.sha256
        ).hexdigest()
        assert callbacks.verify_callback_auth(
            db,
            item,
            flow.id,
            {"x-flowforge-signature": signature},
            body,
        ) == 1


def test_bearer_callback_is_authenticated_idempotent_and_resumes_run(
    monkeypatch,
) -> None:
    configured = callback_settings()
    monkeypatch.setattr(crypto, "get_settings", lambda: configured)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    content = {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": "wait",
                "data": {
                    "nodeType": "http_callback",
                    "nodeVersion": "1.0",
                    "config": {},
                },
            }
        ],
        "edges": [],
    }
    with session_factory() as db:
        flow = FlowDefinition(
            id="flow-1",
            name="Callback integration",
            description="",
            status=FlowStatus.ACTIVE,
            draft_content=content,
            current_version=1,
        )
        version = FlowVersion(
            id="version-1", flow=flow, version_number=1, content=content
        )
        run = FlowRun(
            id="run-1",
            flow=flow,
            flow_version=version,
            temporal_workflow_id="default:run-1",
            status=RunStatus.WAITING_CALLBACK,
            input_data={"requestId": "REQ-1"},
            flow_config={},
            source_metadata={},
        )
        node_run = NodeRun(
            id="node-run-1",
            flow_run=run,
            node_id="wait",
            node_type="http_callback",
            node_version="1.0",
            status=NodeRunStatus.WAITING_CALLBACK,
            input_data={"requestId": "REQ-1"},
            attempts=1,
            max_attempts=1,
        )
        db.add_all(
            [
                flow,
                version,
                run,
                node_run,
                NodeRunAttempt(
                    node_run=node_run,
                    attempt_number=1,
                    status=NodeRunStatus.WAITING_CALLBACK,
                ),
                CallbackWait(
                    id="callback-1",
                    flow_run=run,
                    node_run=node_run,
                    node_id="wait",
                    attempt_number=1,
                    status="WAITING",
                    auth_mode="BEARER",
                    credential_alias="callback_auth",
                    expires_at=utc_now() + timedelta(minutes=5),
                ),
            ]
        )
        add_credential(
            db,
            flow,
            "credential-bearer",
            "callback_auth",
            "BEARER",
            {"token": "callback-secret"},
        )
        db.commit()

    def db_override() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.dependency_overrides[get_db] = db_override
    app.include_router(callback_router, prefix="/api/callbacks")
    signalled: list[dict] = []

    class WorkflowHandle:
        async def signal(self, _signal, payload: dict) -> None:
            signalled.append(payload)

    class TemporalClient:
        def get_workflow_handle(self, workflow_id: str) -> WorkflowHandle:
            assert workflow_id == "default:run-1"
            return WorkflowHandle()

    async def temporal_client() -> TemporalClient:
        return TemporalClient()

    monkeypatch.setattr(callback_api, "get_temporal_client", temporal_client)
    headers = {
        "Authorization": "Bearer callback-secret",
        "Content-Type": "application/json",
        "Idempotency-Key": "partner-event-1",
    }
    with TestClient(app) as client:
        assert client.post(
            "/api/callbacks/callback-1", json={"approved": True}
        ).status_code == 401
        accepted = client.post(
            "/api/callbacks/callback-1",
            content=b'{"approved":true}',
            headers=headers,
        )
        assert accepted.status_code == 202
        assert accepted.json()["idempotent"] is False
        repeated = client.post(
            "/api/callbacks/callback-1",
            content=b'{"approved":true}',
            headers=headers,
        )
        assert repeated.status_code == 202
        assert repeated.json()["idempotent"] is True
        conflict_headers = {**headers, "Idempotency-Key": "partner-event-2"}
        assert client.post(
            "/api/callbacks/callback-1",
            content=b'{"approved":true}',
            headers=conflict_headers,
        ).status_code == 409

    with session_factory() as db:
        run = db.get(FlowRun, "run-1")
        node_run = db.get(NodeRun, "node-run-1")
        callback = db.get(CallbackWait, "callback-1")
        assert run is not None and run.status == RunStatus.WAITING_CALLBACK
        assert node_run is not None and node_run.status == NodeRunStatus.WAITING_CALLBACK
        assert callback is not None and callback.status == "RECEIVED"
        assert callback.idempotency_key == "partner-event-1"
        event_types = set(db.scalars(select(FlowEvent.event_type)).all())
        assert {"CALLBACK_REJECTED", "CALLBACK_RECEIVED", "CALLBACK_SIGNALLED"} <= event_types
    assert signalled == [
        {
            "node_id": "wait",
            "callback_id": "callback-1",
            "data": {"approved": True},
        }
    ]


def test_expired_callback_fails_node_and_run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    with session_factory() as db:
        content = {"schemaVersion": 1, "nodes": [], "edges": []}
        flow = FlowDefinition(
            id="flow-timeout",
            name="Callback timeout",
            description="",
            draft_content=content,
            current_version=1,
        )
        version = FlowVersion(
            id="version-timeout", flow=flow, version_number=1, content=content
        )
        run = FlowRun(
            id="run-timeout",
            flow=flow,
            flow_version=version,
            status=RunStatus.WAITING_CALLBACK,
            input_data={},
            flow_config={},
            source_metadata={},
        )
        node_run = NodeRun(
            id="node-timeout",
            flow_run=run,
            node_id="wait",
            node_type="http_callback",
            node_version="1.0",
            status=NodeRunStatus.WAITING_CALLBACK,
            attempts=1,
            max_attempts=1,
        )
        callback = CallbackWait(
            id="callback-timeout",
            flow_run=run,
            node_run=node_run,
            node_id="wait",
            attempt_number=1,
            status="WAITING",
            auth_mode="CAPABILITY_URL",
            expires_at=utc_now() - timedelta(seconds=1),
        )
        db.add_all([flow, version, run, node_run, callback])
        db.commit()

    def db_override() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.dependency_overrides[get_db] = db_override
    app.include_router(callback_router, prefix="/api/callbacks")
    with TestClient(app) as client:
        response = client.post("/api/callbacks/callback-timeout", json={"ok": True})
        assert response.status_code == 410

    with session_factory() as db:
        run = db.get(FlowRun, "run-timeout")
        node_run = db.get(NodeRun, "node-timeout")
        callback = db.get(CallbackWait, "callback-timeout")
        assert callback.status == "EXPIRED"
        assert node_run.status == NodeRunStatus.FAILED
        assert run.status == RunStatus.FAILED
