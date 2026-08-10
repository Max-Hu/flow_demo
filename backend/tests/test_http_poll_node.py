from unittest.mock import Mock

import pytest

from app.nodes.base import NodeContext, PollPending
from app.nodes.catalog.http_poll import handler
from app.nodes.catalog.http_poll.handler import HttpPollNode


def config(**overrides) -> dict:
    result = {
        "url": "http://partner-api/jobs/{jobId}",
        "responsePath": "status.code",
        "operator": "equals",
        "expectedValue": "200",
        "intervalSeconds": 5,
        "maxPolls": 3,
        "requestTimeoutSeconds": 10,
    }
    result.update(overrides)
    return result


def context(attempt: int) -> Mock:
    node_context = Mock(spec=NodeContext)
    node_context.attempt = attempt
    return node_context


def test_http_poll_returns_response_when_expected_value_matches(monkeypatch) -> None:
    monkeypatch.setattr(handler, "fetch_json", lambda url, timeout: {"status": {"code": 200}})

    output = HttpPollNode().execute({"jobId": "JOB-1"}, config(), context(1))

    assert isinstance(output, dict)
    assert output["status"]["code"] == 200
    assert output["_poll"]["matched"] is True
    assert output["_poll"]["pollCount"] == 1


def test_http_poll_returns_durable_wait_without_sleeping(monkeypatch) -> None:
    monkeypatch.setattr(handler, "fetch_json", lambda url, timeout: {"status": {"code": 102}})

    output = HttpPollNode().execute({"jobId": "JOB-1"}, config(), context(1))

    assert isinstance(output, PollPending)
    assert output.delay_seconds == 5
    assert output.last_output["_poll"]["observedValue"] == 102


def test_http_poll_fails_after_maximum_poll_count(monkeypatch) -> None:
    monkeypatch.setattr(handler, "fetch_json", lambda url, timeout: {"status": {"code": 102}})

    with pytest.raises(ValueError, match="Polling limit reached after 3 requests"):
        HttpPollNode().execute({"jobId": "JOB-1"}, config(), context(3))
