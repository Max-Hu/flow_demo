from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings, get_settings


class CredentialCryptoError(ValueError):
    pass


@dataclass(frozen=True)
class EncryptedPayload:
    key_id: str
    nonce: str
    ciphertext: str


def _aad(flow_id: str, credential_id: str, revision: int) -> bytes:
    return f"flowforge:{flow_id}:{credential_id}:{revision}".encode()


def _decode_key(encoded: str, key_id: str) -> bytes:
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise CredentialCryptoError(f"Credential key '{key_id}' is not valid base64") from exc
    if len(key) != 32:
        raise CredentialCryptoError(f"Credential key '{key_id}' must decode to 32 bytes")
    return key


def validate_key_ring(settings: Settings | None = None) -> None:
    configured = settings or get_settings()
    if not configured.active_credential_key_id:
        raise CredentialCryptoError("WORKFLOW_ACTIVE_CREDENTIAL_KEY_ID is required")
    if configured.active_credential_key_id not in configured.credential_keys:
        raise CredentialCryptoError(
            "The active credential key is missing from WORKFLOW_CREDENTIAL_KEYS"
        )
    for key_id, encoded in configured.credential_keys.items():
        _decode_key(encoded, key_id)


def key_ring_fingerprint(settings: Settings | None = None) -> str:
    configured = settings or get_settings()
    material = json.dumps(
        {
            "active": configured.active_credential_key_id,
            "keys": configured.credential_keys,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def encrypt_secret(
    payload: dict[str, Any],
    flow_id: str,
    credential_id: str,
    revision: int,
    *,
    settings: Settings | None = None,
    key_id: str | None = None,
) -> EncryptedPayload:
    configured = settings or get_settings()
    selected_key_id = key_id or configured.active_credential_key_id
    encoded_key = configured.credential_keys.get(selected_key_id)
    if encoded_key is None:
        raise CredentialCryptoError(f"Credential key '{selected_key_id}' is not configured")
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ciphertext = AESGCM(_decode_key(encoded_key, selected_key_id)).encrypt(
        nonce, plaintext, _aad(flow_id, credential_id, revision)
    )
    return EncryptedPayload(
        key_id=selected_key_id,
        nonce=base64.b64encode(nonce).decode(),
        ciphertext=base64.b64encode(ciphertext).decode(),
    )


def decrypt_secret(
    key_id: str,
    nonce: str,
    ciphertext: str,
    flow_id: str,
    credential_id: str,
    revision: int,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    configured = settings or get_settings()
    encoded_key = configured.credential_keys.get(key_id)
    if encoded_key is None:
        raise CredentialCryptoError(f"Credential key '{key_id}' is not configured")
    try:
        plaintext = AESGCM(_decode_key(encoded_key, key_id)).decrypt(
            base64.b64decode(nonce, validate=True),
            base64.b64decode(ciphertext, validate=True),
            _aad(flow_id, credential_id, revision),
        )
        decoded = json.loads(plaintext)
    except Exception as exc:
        raise CredentialCryptoError("Credential decryption failed") from exc
    if not isinstance(decoded, dict):
        raise CredentialCryptoError("Credential payload must be an object")
    return decoded
