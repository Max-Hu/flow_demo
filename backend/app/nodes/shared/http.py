from typing import Any
from urllib.parse import urljoin

import httpx

from app.nodes.base import NodeContext
from app.security.credentials import request_origin

MAX_REDIRECTS = 10


def request_json(
    method: str,
    url: str,
    timeout_seconds: float,
    context: NodeContext,
    credential_ref: str = "",
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    current_method = method
    credential_origin = ""
    allowed_origins: tuple[str, ...] = ()
    if credential_ref:
        credential = context.resolve_credential(credential_ref, url)
        headers = credential.headers
        credential_origin = credential.origin
        allowed_origins = credential.allowed_origins

    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            response = client.request(current_method, current_url, headers=headers)
            if not response.is_redirect:
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"body": response.text}
                return payload if isinstance(payload, dict) else {"data": payload}

            if redirect_count == MAX_REDIRECTS:
                raise ValueError(f"HTTP redirect limit exceeded ({MAX_REDIRECTS})")
            location = response.headers.get("location")
            if not location:
                raise ValueError("HTTP redirect is missing a Location header")
            target_url = urljoin(str(response.url), location)
            target_origin = request_origin(target_url)
            if credential_ref and target_origin not in allowed_origins:
                raise ValueError(
                    f"Credential '{credential_ref}' is not allowed for redirect origin "
                    f"{target_origin}"
                )
            if credential_ref and target_origin != credential_origin:
                headers = {}
            if response.status_code == 303 or (
                response.status_code in {301, 302} and current_method == "POST"
            ):
                current_method = "GET"
            current_url = target_url

    raise RuntimeError("HTTP request terminated unexpectedly")
