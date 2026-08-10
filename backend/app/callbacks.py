from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import CallbackWait, FlowRun, NodeRun, utc_now
from app.security.credentials import InboundCredential, resolve_inbound_credential

CALLBACK_AUTH_MODES = {
    "CAPABILITY_URL",
    "BEARER",
    "API_KEY_HEADER",
    "HMAC_SHA256",
}


def callback_url(item: CallbackWait, settings: Settings | None = None) -> str:
    configured = settings or get_settings()
    return f"{configured.callback_base_url.rstrip('/')}/api/callbacks/{item.id}"


def create_callback_wait(
    db: Session,
    run: FlowRun,
    node_run: NodeRun,
    config: dict[str, Any],
) -> CallbackWait:
    auth_mode = str(config.get("authMode", "CAPABILITY_URL")).upper()
    if auth_mode not in CALLBACK_AUTH_MODES:
        raise ValueError(f"Unsupported callback authentication mode: {auth_mode}")
    credential_alias = str(config.get("credentialRef", "")).strip().lower() or None
    if auth_mode != "CAPABILITY_URL" and credential_alias is None:
        raise ValueError(f"{auth_mode} callback authentication requires a credential")
    if credential_alias is not None:
        credential = resolve_inbound_credential(db, run.flow_id, credential_alias)
        _validate_credential_type(auth_mode, credential)
    timeout_seconds = min(max(int(config.get("timeoutSeconds", 3600)), 10), 604800)
    item = CallbackWait(
        flow_run_id=run.id,
        node_run_id=node_run.id,
        node_id=node_run.node_id,
        attempt_number=node_run.attempts,
        status="WAITING",
        auth_mode=auth_mode,
        credential_alias=credential_alias,
        expires_at=utc_now() + timedelta(seconds=timeout_seconds),
        request_metadata={},
    )
    db.add(item)
    db.flush()
    return item


def _validate_credential_type(
    auth_mode: str, credential: InboundCredential
) -> None:
    if auth_mode == "BEARER" and credential.credential_type != "BEARER":
        raise ValueError("BEARER callback authentication requires a Bearer credential")
    if auth_mode == "API_KEY_HEADER" and credential.credential_type != "API_KEY_HEADER":
        raise ValueError(
            "API_KEY_HEADER callback authentication requires an API key credential"
        )
    if auth_mode == "HMAC_SHA256" and credential.credential_type not in {
        "BEARER",
        "API_KEY_HEADER",
    }:
        raise ValueError("HMAC_SHA256 requires a Bearer or API key credential")


def verify_callback_auth(
    db: Session,
    item: CallbackWait,
    flow_id: str,
    headers: Mapping[str, str],
    body: bytes,
) -> int | None:
    if item.auth_mode == "CAPABILITY_URL":
        return None
    if item.credential_alias is None:
        raise ValueError("Callback credential is not configured")
    credential = resolve_inbound_credential(db, flow_id, item.credential_alias)
    _validate_credential_type(item.auth_mode, credential)
    if item.auth_mode == "BEARER":
        actual = headers.get("authorization", "")
        expected = f"Bearer {credential.secret['token']}"
        if not secrets.compare_digest(actual, expected):
            raise ValueError("Valid Bearer authentication is required")
    elif item.auth_mode == "API_KEY_HEADER":
        header_name = str(credential.secret["headerName"])
        actual = headers.get(header_name, "")
        expected = f"{credential.secret.get('prefix', '')}{credential.secret['value']}"
        if not secrets.compare_digest(actual, expected):
            raise ValueError(f"Valid {header_name} authentication is required")
    else:
        signature = headers.get("x-flowforge-signature", "")
        if credential.credential_type == "BEARER":
            key = str(credential.secret["token"])
        else:
            key = str(credential.secret["value"])
        expected = "sha256=" + hmac.new(
            key.encode(), body, hashlib.sha256
        ).hexdigest()
        if not secrets.compare_digest(signature, expected):
            raise ValueError("Valid X-FlowForge-Signature is required")
    return credential.revision
