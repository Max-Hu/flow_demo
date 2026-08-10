import base64
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import FlowCredential, FlowCredentialRevision, FlowDefinition, FlowEvent
from app.security import crypto
from app.security.credentials import (
    normalize_origin,
    resolve_credential,
    validate_credential_definition,
)
from app.security.crypto import CredentialCryptoError, decrypt_secret, encrypt_secret
from app.security.redaction import REDACTED, redact_sensitive


def settings() -> Settings:
    return Settings(
        credential_keys={"k1": base64.b64encode(os.urandom(32)).decode()},
        active_credential_key_id="k1",
    )


def test_aes_gcm_uses_record_identity_as_authenticated_data() -> None:
    configured = settings()
    encrypted = encrypt_secret(
        {"token": "top-secret"}, "flow-1", "credential-1", 3, settings=configured
    )

    assert decrypt_secret(
        encrypted.key_id,
        encrypted.nonce,
        encrypted.ciphertext,
        "flow-1",
        "credential-1",
        3,
        settings=configured,
    ) == {"token": "top-secret"}
    with pytest.raises(CredentialCryptoError, match="decryption failed"):
        decrypt_secret(
            encrypted.key_id,
            encrypted.nonce,
            encrypted.ciphertext,
            "flow-2",
            "credential-1",
            3,
            settings=configured,
        )


def test_origin_and_api_key_header_validation() -> None:
    alias, origins, secret = validate_credential_definition(
        "Partner_API",
        "API_KEY_HEADER",
        ["https://PARTNER.example.com:443"],
        {"headerName": "X-API-Key", "value": "secret", "prefix": "Token "},
    )

    assert alias == "partner_api"
    assert origins == ["https://partner.example.com"]
    assert secret["headerName"] == "X-API-Key"
    with pytest.raises(ValueError, match="invalid or forbidden"):
        validate_credential_definition(
            "partner_api",
            "API_KEY_HEADER",
            ["https://partner.example.com"],
            {"headerName": "Cookie", "value": "secret"},
        )
    with pytest.raises(ValueError, match="path"):
        normalize_origin("https://partner.example.com/api")


def test_sensitive_fields_are_redacted_recursively() -> None:
    safe = redact_sensitive(
        {"status": "ok", "Authorization": "Bearer secret", "nested": {"password": "x"}}
    )

    assert safe == {
        "status": "ok",
        "Authorization": REDACTED,
        "nested": {"password": REDACTED},
    }


def test_resolve_credential_injects_header_and_emits_safe_usage_event(
    monkeypatch,
) -> None:
    configured = settings()
    monkeypatch.setattr(crypto, "get_settings", lambda: configured)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        flow = FlowDefinition(
            id="flow-1",
            name="Credential test",
            description="",
            draft_content={"schemaVersion": 1, "nodes": [], "edges": []},
        )
        credential = FlowCredential(
            id="credential-1",
            flow=flow,
            alias="partner_api",
            credential_type="BEARER",
            allowed_origins=["https://partner.example.com"],
            enabled=True,
            current_revision=1,
        )
        encrypted = encrypt_secret(
            {"token": "top-secret"},
            flow.id,
            credential.id,
            1,
            settings=configured,
        )
        db.add_all(
            [
                flow,
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
        db.commit()

        resolved = resolve_credential(
            db,
            flow.id,
            "partner_api",
            "https://partner.example.com/jobs/1",
            run_id="run-1",
            node_id="http-1",
        )

        assert resolved.headers == {"Authorization": "Bearer top-secret"}
        event = next(item for item in db.new if isinstance(item, FlowEvent))
        assert event.payload == {
            "alias": "partner_api",
            "revision": 1,
            "origin": "https://partner.example.com",
        }
        assert "top-secret" not in str(event.payload)
