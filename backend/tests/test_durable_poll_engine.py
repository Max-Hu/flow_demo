from types import SimpleNamespace
from unittest.mock import Mock

from app.enums import NodeRunStatus
from app.models import NodeRun
from app.workflow.engine import initialize_run, recover_and_promote


def test_initialize_run_uses_max_polls_for_durable_poll_node() -> None:
    run = SimpleNamespace(
        id="run-1",
        flow_version=SimpleNamespace(
            content={
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "nodeType": "start",
                            "nodeVersion": "1.0",
                            "config": {},
                        },
                    },
                    {
                        "id": "poll",
                        "data": {
                            "nodeType": "http_poll",
                            "nodeVersion": "1.0",
                            "config": {"maxPolls": 37},
                        },
                    },
                ]
            }
        ),
    )
    db = Mock()

    initialize_run(db, run)

    added = [call.args[0] for call in db.add.call_args_list]
    poll_run = next(item for item in added if isinstance(item, NodeRun) and item.node_id == "poll")
    assert poll_run.max_attempts == 37


def test_due_poll_wait_is_promoted_without_blocking_worker() -> None:
    poll_run = SimpleNamespace(
        status=NodeRunStatus.POLL_WAIT,
        flow_run_id="run-1",
        node_id="poll",
    )
    waiting_result = Mock()
    waiting_result.all.return_value = [poll_run]
    expired_result = Mock()
    expired_result.all.return_value = []
    db = Mock()
    db.scalars.side_effect = [waiting_result, expired_result]

    recover_and_promote(db)

    assert poll_run.status == NodeRunStatus.READY
    event = db.add.call_args.args[0]
    assert event.event_type == "NODE_READY"
    assert event.payload["reason"] == "poll"
