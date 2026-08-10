from typing import Any

from app.nodes.base import NodeContext
from app.nodes.shared.paths import get_path


class ConditionNode:
    OPERATORS = {
        "equals": lambda left, right: left == right,
        "not_equals": lambda left, right: left != right,
        "greater_than": lambda left, right: left > right,
        "greater_than_or_equal": lambda left, right: left >= right,
        "less_than": lambda left, right: left < right,
        "less_than_or_equal": lambda left, right: left <= right,
        "contains": lambda left, right: right in left,
    }

    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any]:
        field = str(config["field"])
        operator = str(config["operator"])
        expected = config.get("value")
        actual = get_path(inputs, field)
        operation = self.OPERATORS.get(operator)
        if operation is None:
            raise ValueError(f"Unsupported condition operator: {operator}")
        try:
            matched = bool(operation(actual, expected))
        except TypeError as exc:
            raise ValueError(
                f"Cannot compare value {actual!r} with {expected!r} using {operator}"
            ) from exc
        return {
            "branch": "true" if matched else "false",
            "data": inputs,
            "evaluation": {
                "field": field,
                "actual": actual,
                "operator": operator,
                "expected": expected,
                "matched": matched,
            },
        }
