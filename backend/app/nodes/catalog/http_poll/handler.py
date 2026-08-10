from typing import Any

import httpx

from app.nodes.base import NodeContext, PollPending
from app.nodes.shared.http import request_json
from app.nodes.shared.paths import get_path


def fetch_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            payload = {"body": response.text}
    return payload if isinstance(payload, dict) else {"data": payload}


def coerce_expected(expected: Any, actual: Any) -> Any:
    if not isinstance(expected, str):
        return expected
    if isinstance(actual, bool):
        normalized = expected.strip().lower()
        if normalized in {"true", "false"}:
            return normalized == "true"
    if isinstance(actual, int) and not isinstance(actual, bool):
        try:
            return int(expected)
        except ValueError:
            return expected
    if isinstance(actual, float):
        try:
            return float(expected)
        except ValueError:
            return expected
    return expected


def matches(actual: Any, expected: Any, operator: str) -> bool:
    operations = {
        "equals": lambda: actual == expected,
        "not_equals": lambda: actual != expected,
        "greater_than": lambda: actual > expected,
        "greater_than_or_equal": lambda: actual >= expected,
        "less_than": lambda: actual < expected,
        "less_than_or_equal": lambda: actual <= expected,
        "contains": lambda: expected in actual,
    }
    operation = operations.get(operator)
    if operation is None:
        raise ValueError(f"Unsupported poll operator: {operator}")
    try:
        return bool(operation())
    except TypeError as exc:
        raise ValueError(
            f"Cannot compare observed value {actual!r} with {expected!r} using {operator}"
        ) from exc


class HttpPollNode:
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any] | PollPending:
        url_template = str(config["url"])
        try:
            url = url_template.format_map(inputs)
        except KeyError as exc:
            raise ValueError(f"URL template variable {exc!s} is missing from the input") from exc
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only HTTP and HTTPS URLs are supported")

        timeout_seconds = min(
            max(float(config.get("requestTimeoutSeconds", 10)), 1), 30
        )
        credential_ref = str(config.get("credentialRef", "")).strip()
        payload = (
            request_json("GET", url, timeout_seconds, context, credential_ref)
            if credential_ref
            else fetch_json(url, timeout_seconds)
        )
        response_path = str(config["responsePath"])
        actual = get_path(payload, response_path)
        expected = coerce_expected(config.get("expectedValue"), actual)
        operator = str(config.get("operator", "equals"))
        max_polls = min(max(int(config.get("maxPolls", 60)), 1), 1000)
        matched = matches(actual, expected, operator)
        poll_metadata = {
            "matched": matched,
            "pollCount": context.attempt,
            "maxPolls": max_polls,
            "responsePath": response_path,
            "observedValue": actual,
            "operator": operator,
            "expectedValue": expected,
        }
        output = {**payload, "_poll": poll_metadata}
        if matched:
            return output
        if context.attempt >= max_polls:
            raise ValueError(
                f"Polling limit reached after {max_polls} requests: "
                f"'{response_path}' was {actual!r}, expected {operator} {expected!r}"
            )
        interval_seconds = min(
            max(float(config.get("intervalSeconds", 10)), 1), 3600
        )
        return PollPending(delay_seconds=interval_seconds, last_output=output)
