import base64
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import partner_api
from app import seed
from app.config import Settings
from app.database import Base
from app.models import (
    FlowCredential,
    FlowCredentialRevision,
    FlowDefinition,
    FlowVersion,
    SecurityAuditEvent,
)
from app.schemas import FlowContent
from app.security import crypto
from app.workflow.validator import validate_flow


def demo_settings() -> Settings:
    return Settings(
        credential_keys={"test": base64.b64encode(b"0" * 32).decode()},
        active_credential_key_id="test",
        demo_partner_token="test-demo-token",
    )


def test_partner_secure_job_requires_and_accepts_bearer(monkeypatch) -> None:
    monkeypatch.setattr(partner_api, "demo_partner_token", "test-demo-token")
    job_id = f"JOB-{uuid4().hex[:8]}"
    with TestClient(partner_api.app) as client:
        assert client.get(f"/secure/jobs/{job_id}/submit").status_code == 401
        headers = {"Authorization": "Bearer test-demo-token"}
        submitted = client.get(f"/secure/jobs/{job_id}/submit", headers=headers)
        assert submitted.status_code == 200
        assert submitted.json()["authenticated"] is True
        responses = [
            client.get(f"/secure/jobs/{job_id}", headers=headers).json()
            for _ in range(3)
        ]
        assert [item["status"] for item in responses] == [
            "PROCESSING",
            "PROCESSING",
            "COMPLETED",
        ]
        assert responses[-1]["approved"] is True


def test_credential_demo_graph_is_valid_and_references_seeded_alias() -> None:
    content = seed.credential_demo_flow_content()

    assert validate_flow(FlowContent.model_validate(content)) == []
    credential_refs = {
        node["data"]["config"].get("credentialRef")
        for node in content["nodes"]
        if node["data"]["config"].get("credentialRef")
    }
    assert credential_refs == {seed.CREDENTIAL_ALIAS}
    assert any(
        "{{ flowConfig.partnerBaseUrl }}" in node["data"]["config"].get("url", "")
        for node in content["nodes"]
    )


def test_credential_demo_seed_is_idempotent(monkeypatch) -> None:
    configured = demo_settings()
    monkeypatch.setattr(seed, "get_settings", lambda: configured)
    monkeypatch.setattr(crypto, "get_settings", lambda: configured)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        seed.seed_credential_demo_flow(db)
        db.commit()
        seed.seed_credential_demo_flow(db)
        db.commit()

        flow = db.scalar(
            select(FlowDefinition).where(
                FlowDefinition.name == seed.CREDENTIAL_FLOW_NAME
            )
        )
        assert flow is not None
        assert db.scalar(
            select(func.count()).select_from(FlowVersion).where(FlowVersion.flow_id == flow.id)
        ) == 1
        assert db.scalar(
            select(func.count())
            .select_from(FlowCredential)
            .where(FlowCredential.flow_id == flow.id)
        ) == 1
        assert db.scalar(select(func.count()).select_from(FlowCredentialRevision)) == 1
        assert db.scalar(select(func.count()).select_from(SecurityAuditEvent)) == 1
