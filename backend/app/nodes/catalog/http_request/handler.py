from typing import Any

from app.nodes.base import NodeContext
from app.nodes.shared.http import request_json


class HttpRequestNode:
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any]:
        method = str(config.get("method", "GET")).upper()
        url_template = str(config["url"])
        try:
            url = url_template.format_map(inputs)
        except KeyError as exc:
            raise ValueError(f"URL template variable {exc!s} is missing from the input") from exc
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only HTTP and HTTPS URLs are supported")
        timeout = min(max(float(config.get("timeoutSeconds", 10)), 1), 30)
        return request_json(
            method,
            url,
            timeout,
            context,
            str(config.get("credentialRef", "")).strip(),
        )
