from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.enums import GroupRole
from app.group_api import router
from app.models import Group, GroupMember, User
from app.security.auth import require_user
from app.database import Base, get_db


def flow_content() -> dict:
    return {
        "schemaVersion": 1,
        "nodes": [
            {
                "id": "start",
                "type": "workflow",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Start", "nodeType": "start", "nodeVersion": "1.0", "config": {}},
            },
            {
                "id": "end",
                "type": "workflow",
                "position": {"x": 300, "y": 0},
                "data": {"label": "End", "nodeType": "end", "nodeVersion": "1.0", "config": {}},
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "sourceHandle": "output",
                "targetHandle": "input",
            }
        ],
    }


def empty_schema() -> dict:
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def required_job_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "jobId": {
                "type": "string",
                "title": "Job ID",
                "minLength": 1,
            }
        },
        "required": ["jobId"],
        "additionalProperties": False,
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    def db_override() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    with session_factory() as db:
        user = User(username="designer", password_hash="unused", is_super_admin=True)
        group = Group(id="default", name="Default Workspace")
        db.add_all([user, group])
        db.flush()
        for role in GroupRole:
            db.add(GroupMember(group_id=group.id, user_id=user.id, role=role))
        db.commit()
        db.expunge(user)

    async def temporal_noop(*args, **kwargs) -> None:
        return None

    def user_override() -> User:
        with session_factory() as db:
            user = db.query(User).filter(User.username == "designer").one()
            db.expunge(user)
            return user

    app = FastAPI()
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[require_user] = user_override
    app.include_router(router, prefix="/api")
    import app.group_api as group_api

    monkeypatch.setattr(group_api, "_start_temporal_run", temporal_noop)
    monkeypatch.setattr(group_api, "_create_temporal_schedule", temporal_noop)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_flow(
    client: TestClient,
    name: str,
    input_schema: dict | None = None,
    config_schema: dict | None = None,
    default_config: dict | None = None,
) -> dict:
    response = client.post(
        "/api/groups/default/flows",
        json={
            "name": name,
            "content": flow_content(),
            **({"inputSchema": input_schema} if input_schema is not None else {}),
            **({"configSchema": config_schema} if config_schema is not None else {}),
            **({"defaultConfig": default_config} if default_config is not None else {}),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish_flow(client: TestClient, flow_id: str) -> dict:
    response = client.post(f"/api/groups/default/flows/{flow_id}/publish")
    assert response.status_code == 200, response.text
    return response.json()


def run_flow(client: TestClient, flow_id: str, input_data: dict, version: int | None = None):
    payload = {"inputData": input_data}
    if version is not None:
        payload["versionNumber"] = version
    return client.post(f"/api/groups/default/flows/{flow_id}/runs", json=payload)


def test_flow_without_input_fields_runs_without_global_required_inputs(client: TestClient) -> None:
    flow = create_flow(client, "No input flow")
    publish_flow(client, flow["id"])

    response = run_flow(client, flow["id"], {})

    assert response.status_code == 201, response.text
    assert response.json()["input_data"] == {}


def test_required_inputs_are_flow_scoped(client: TestClient) -> None:
    required_flow = create_flow(client, "Requires job", required_job_schema())
    empty_flow = create_flow(client, "Does not require job", empty_schema())
    publish_flow(client, required_flow["id"])
    publish_flow(client, empty_flow["id"])

    missing_required = run_flow(client, required_flow["id"], {})
    empty_flow_run = run_flow(client, empty_flow["id"], {})

    assert missing_required.status_code == 422
    assert "jobId" in missing_required.text
    assert empty_flow_run.status_code == 201, empty_flow_run.text


def test_published_versions_keep_their_own_required_inputs(client: TestClient) -> None:
    flow = create_flow(client, "Versioned inputs", empty_schema())
    version_one = publish_flow(client, flow["id"])

    draft = client.put(
        f"/api/groups/default/flows/{flow['id']}/draft",
        json={
            "content": flow_content(),
            "inputSchema": required_job_schema(),
            "configSchema": empty_schema(),
            "defaultConfig": {},
            "expectedRowVersion": flow["row_version"],
        },
    )
    assert draft.status_code == 200, draft.text
    version_two = publish_flow(client, flow["id"])

    old_version_run = run_flow(client, flow["id"], {}, version_one["version_number"])
    new_version_run = run_flow(client, flow["id"], {}, version_two["version_number"])

    assert old_version_run.status_code == 201, old_version_run.text
    assert new_version_run.status_code == 422
    assert "jobId" in new_version_run.text


def test_schedule_creation_validates_selected_version_input_schema(client: TestClient) -> None:
    flow = create_flow(client, "Scheduled inputs", required_job_schema())
    version = publish_flow(client, flow["id"])

    missing_required = client.post(
        f"/api/groups/default/flows/{flow['id']}/schedules",
        json={
            "name": "Missing required input",
            "cronExpression": "0 9 * * *",
            "timezone": "UTC",
            "versionNumber": version["version_number"],
            "inputData": {},
        },
    )
    valid = client.post(
        f"/api/groups/default/flows/{flow['id']}/schedules",
        json={
            "name": "Has required input",
            "cronExpression": "0 9 * * *",
            "timezone": "UTC",
            "versionNumber": version["version_number"],
            "inputData": {"jobId": "JOB-1"},
        },
    )

    assert missing_required.status_code == 422
    assert "jobId" in missing_required.text
    assert valid.status_code == 201, valid.text


def test_run_rejects_flow_configuration_overrides(client: TestClient) -> None:
    flow = create_flow(
        client,
        "No run overrides",
        empty_schema(),
        {
            "type": "object",
            "properties": {"partnerBaseUrl": {"type": "string"}},
            "required": ["partnerBaseUrl"],
            "additionalProperties": False,
        },
        {"partnerBaseUrl": "https://default.example"},
    )
    version = publish_flow(client, flow["id"])

    response = client.post(
        f"/api/groups/default/flows/{flow['id']}/runs",
        json={
            "versionNumber": version["version_number"],
            "inputData": {},
            "configOverrides": {"partnerBaseUrl": "https://override.example"},
        },
    )

    assert response.status_code == 422
    assert "configOverrides" in response.text

    valid = client.post(
        f"/api/groups/default/flows/{flow['id']}/runs",
        json={"versionNumber": version["version_number"], "inputData": {}},
    )

    assert valid.status_code == 201, valid.text
    assert valid.json()["flow_config"] == {"partnerBaseUrl": "https://default.example"}


def test_schedule_endpoints_reject_flow_configuration_overrides(client: TestClient) -> None:
    flow = create_flow(client, "No schedule overrides", empty_schema())
    version = publish_flow(client, flow["id"])

    create_response = client.post(
        f"/api/groups/default/flows/{flow['id']}/schedules",
        json={
            "name": "Override attempt",
            "cronExpression": "0 9 * * *",
            "timezone": "UTC",
            "versionNumber": version["version_number"],
            "inputData": {},
            "configOverrides": {"partnerBaseUrl": "https://override.example"},
        },
    )
    valid = client.post(
        f"/api/groups/default/flows/{flow['id']}/schedules",
        json={
            "name": "Valid schedule",
            "cronExpression": "0 9 * * *",
            "timezone": "UTC",
            "versionNumber": version["version_number"],
            "inputData": {},
        },
    )
    update_response = client.put(
        f"/api/groups/default/flows/{flow['id']}/schedules/{valid.json()['id']}",
        json={"configOverrides": {"partnerBaseUrl": "https://override.example"}},
    )

    assert create_response.status_code == 422
    assert "configOverrides" in create_response.text
    assert valid.status_code == 201, valid.text
    assert "config_overrides" not in valid.json()
    assert update_response.status_code == 422
    assert "configOverrides" in update_response.text
