from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    FlowCredential,
    FlowCredentialRevision,
    FlowEvent,
    SecurityAuditEvent,
)
from app.security.crypto import decrypt_secret, encrypt_secret

CREDENTIAL_TYPES = {"BEARER", "BASIC", "API_KEY_HEADER"}
ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,99}$")
HEADER_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
FORBIDDEN_HEADERS = {"host", "content-length", "cookie", "set-cookie", "connection"}


@dataclass(frozen=True)
class ResolvedCredential:
    alias: str
    revision: int
    origin: str
    allowed_origins: tuple[str, ...]
    headers: dict[str, str]


def normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Allowed origin must use http:// or https:// and include a host")
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise ValueError("Allowed origin cannot contain credentials or a path")
    if parsed.query or parsed.fragment:
        raise ValueError("Allowed origin cannot contain a query or fragment")
    host = parsed.hostname.lower()
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    suffix = "" if port == default_port else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def request_origin(url: str) -> str:
    return normalize_origin(urlsplit(url)._replace(path="", query="", fragment="").geturl())


def validate_credential_definition(
    alias: str, credential_type: str, allowed_origins: list[str], secret: dict[str, Any]
) -> tuple[str, list[str], dict[str, Any]]:
    normalized_alias = alias.strip().lower()
    if not ALIAS_PATTERN.fullmatch(normalized_alias):
        raise ValueError("Credential alias must use lowercase letters, numbers, and underscores")
    normalized_type = credential_type.upper()
    if normalized_type not in CREDENTIAL_TYPES:
        raise ValueError(f"Unsupported credential type: {credential_type}")
    origins = sorted({normalize_origin(item) for item in allowed_origins})
    if not origins:
        raise ValueError("At least one allowed origin is required")
    if normalized_type == "BEARER":
        token = str(secret.get("token", "")).strip()
        if not token:
            raise ValueError("Bearer token is required")
        validated = {"token": token}
    elif normalized_type == "BASIC":
        username = str(secret.get("username", ""))
        password = str(secret.get("password", ""))
        if not username or not password:
            raise ValueError("Basic username and password are required")
        validated = {"username": username, "password": password}
    else:
        header_name = str(secret.get("headerName", "")).strip()
        value = str(secret.get("value", ""))
        prefix = str(secret.get("prefix", ""))
        if not HEADER_PATTERN.fullmatch(header_name) or header_name.lower() in FORBIDDEN_HEADERS:
            raise ValueError("API key header name is invalid or forbidden")
        if not value:
            raise ValueError("API key value is required")
        validated = {"headerName": header_name, "value": value, "prefix": prefix}
    return normalized_alias, origins, validated


def add_audit(
    db: Session,
    event_type: str,
    actor: str = "admin",
    *,
    flow_id: str | None = None,
    credential_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        SecurityAuditEvent(
            event_type=event_type,
            flow_id=flow_id,
            credential_id=credential_id,
            actor=actor,
            payload=payload or {},
        )
    )


def create_revision(
    db: Session,
    credential: FlowCredential,
    secret: dict[str, Any],
    revision: int | None = None,
) -> FlowCredentialRevision:
    revision = revision or credential.current_revision + 1
    encrypted = encrypt_secret(secret, credential.flow_id, credential.id, revision)
    item = FlowCredentialRevision(
        credential_id=credential.id,
        revision=revision,
        key_id=encrypted.key_id,
        nonce=encrypted.nonce,
        ciphertext=encrypted.ciphertext,
    )
    db.add(item)
    credential.current_revision = revision
    return item


def current_revision(db: Session, credential: FlowCredential) -> FlowCredentialRevision:
    revision = db.scalar(
        select(FlowCredentialRevision).where(
            FlowCredentialRevision.credential_id == credential.id,
            FlowCredentialRevision.revision == credential.current_revision,
        )
    )
    if revision is None:
        raise ValueError(f"Credential '{credential.alias}' has no current secret revision")
    return revision


def resolve_credential(
    db: Session,
    flow_id: str,
    alias: str,
    url: str,
    *,
    run_id: str,
    node_id: str,
) -> ResolvedCredential:
    credential = db.scalar(
        select(FlowCredential).where(
            FlowCredential.flow_id == flow_id,
            FlowCredential.alias == alias,
        )
    )
    if credential is None:
        raise ValueError(f"Credential '{alias}' does not exist for this flow")
    if not credential.enabled:
        raise ValueError(f"Credential '{alias}' is disabled")
    origin = request_origin(url)
    if origin not in credential.allowed_origins:
        raise ValueError(f"Credential '{alias}' is not allowed for origin {origin}")
    revision = current_revision(db, credential)
    secret = decrypt_secret(
        revision.key_id,
        revision.nonce,
        revision.ciphertext,
        credential.flow_id,
        credential.id,
        revision.revision,
    )
    if credential.credential_type == "BEARER":
        headers = {"Authorization": f"Bearer {secret['token']}"}
    elif credential.credential_type == "BASIC":
        token = base64.b64encode(
            f"{secret['username']}:{secret['password']}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {token}"}
    else:
        headers = {
            str(secret["headerName"]): f"{secret.get('prefix', '')}{secret['value']}"
        }
    db.add(
        FlowEvent(
            flow_run_id=run_id,
            node_id=node_id,
            event_type="CREDENTIAL_USED",
            payload={
                "alias": credential.alias,
                "revision": revision.revision,
                "origin": origin,
            },
        )
    )
    return ResolvedCredential(
        alias=credential.alias,
        revision=revision.revision,
        origin=origin,
        allowed_origins=tuple(credential.allowed_origins),
        headers=headers,
    )
