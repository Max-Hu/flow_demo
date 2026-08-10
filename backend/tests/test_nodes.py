from unittest.mock import Mock

from app.nodes.base import NodeContext
from app.nodes.handlers import ConditionNode, ResultNode, SetVariableNode


def context() -> NodeContext:
    return NodeContext(
        run_id="run-1",
        node_run_id="node-run-1",
        node_id="condition",
        attempt=1,
        idempotency_key="run-1:condition",
        db=Mock(),
    )


def test_condition_selects_true_branch() -> None:
    output = ConditionNode().execute(
        {"customerId": "CUST-1001", "score": 86},
        {"field": "score", "operator": "greater_than_or_equal", "value": 70},
        context(),
    )

    assert output["branch"] == "true"
    assert output["data"]["customerId"] == "CUST-1001"


def test_result_formats_input_values() -> None:
    output = ResultNode().execute(
        {"customerId": "CUST-1002", "score": 42},
        {"result": "MANUAL_REVIEW", "message": "Review {customerId} with score {score}"},
        context(),
    )

    assert output["result"] == "MANUAL_REVIEW"
    assert output["message"] == "Review CUST-1002 with score 42"


def test_set_variable_writes_to_run_context() -> None:
    node_context = Mock(spec=NodeContext)
    node_context.set_variable.return_value = 86

    output = SetVariableNode().execute(
        {"customerId": "CUST-1001", "score": 86},
        {"name": "customerScore", "value": 86, "writeMode": "REPLACE"},
        node_context,
    )

    node_context.set_variable.assert_called_once_with("customerScore", 86, "REPLACE")
    assert output["variable"]["value"] == 86
