import base64
import json
import os
from getpass import getpass

from argon2 import PasswordHasher


def main() -> None:
    password = getpass("Administrator password: ")
    confirmation = getpass("Confirm password: ")
    if not password or password != confirmation:
        raise SystemExit("Passwords are empty or do not match")
    key = base64.b64encode(os.urandom(32)).decode()
    print(f"WORKFLOW_ADMIN_PASSWORD_HASH={PasswordHasher().hash(password)}")
    print(f"WORKFLOW_CREDENTIAL_KEYS={json.dumps({'k1': key}, separators=(',', ':'))}")
    print("WORKFLOW_ACTIVE_CREDENTIAL_KEY_ID=k1")


if __name__ == "__main__":
    main()
