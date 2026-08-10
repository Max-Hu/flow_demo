from copy import deepcopy
from typing import Any

EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

DEMO_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customerId": {
            "type": "string",
            "title": "Customer ID",
            "description": "Partner customer identifier.",
            "default": "CUST-1001",
            "minLength": 1,
        }
    },
    "required": ["customerId"],
    "additionalProperties": False,
}


def empty_input_schema() -> dict[str, Any]:
    return deepcopy(EMPTY_INPUT_SCHEMA)


def demo_input_schema() -> dict[str, Any]:
    return deepcopy(DEMO_INPUT_SCHEMA)
