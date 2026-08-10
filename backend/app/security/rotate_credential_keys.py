import argparse

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import FlowCredential, FlowCredentialRevision
from app.security.crypto import decrypt_secret, encrypt_secret, validate_key_ring


def rotate(to_key_id: str) -> int:
    settings = get_settings()
    validate_key_ring(settings)
    if to_key_id not in settings.credential_keys:
        raise ValueError(f"Credential key '{to_key_id}' is not configured")

    changed = 0
    with SessionLocal.begin() as db:
        rows = db.execute(
            select(FlowCredentialRevision, FlowCredential)
            .join(FlowCredential, FlowCredential.id == FlowCredentialRevision.credential_id)
            .with_for_update()
        ).all()
        for revision, credential in rows:
            if revision.key_id == to_key_id:
                continue
            secret = decrypt_secret(
                revision.key_id,
                revision.nonce,
                revision.ciphertext,
                credential.flow_id,
                credential.id,
                revision.revision,
                settings=settings,
            )
            encrypted = encrypt_secret(
                secret,
                credential.flow_id,
                credential.id,
                revision.revision,
                settings=settings,
                key_id=to_key_id,
            )
            revision.key_id = encrypted.key_id
            revision.nonce = encrypted.nonce
            revision.ciphertext = encrypted.ciphertext
            changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-encrypt all credential revisions")
    parser.add_argument("--to", required=True, dest="to_key_id")
    args = parser.parse_args()
    changed = rotate(args.to_key_id)
    print(f"Re-encrypted {changed} credential revisions with key '{args.to_key_id}'.")


if __name__ == "__main__":
    main()
