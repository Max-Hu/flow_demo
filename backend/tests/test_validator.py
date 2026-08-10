from app.input_schema import DEMO_INPUT_SCHEMA
from app.schemas import FlowContent
from app.seed import demo_flow_content, poll_flow_content
from app.workflow.validator import validate_flow, validate_input_schema


def test_seeded_demo_flow_is_valid() -> None:
    content = FlowContent.model_validate(demo_flow_content())

    assert validate_flow(content) == []


def test_seeded_http_poll_flow_is_valid() -> None:
    content = FlowContent.model_validate(poll_flow_content())

    assert validate_flow(content) == []


def test_cycles_are_rejected() -> None:
    raw = demo_flow_content()
    raw["edges"].append(
        {
            "id": "cycle",
            "source": "end",
            "target": "partner-score",
            "sourceHandle": None,
            "targetHandle": "input",
        }
    )
    content = FlowContent.model_validate(raw)

    codes = {issue.code for issue in validate_flow(content)}
    assert "CYCLE" in codes
    assert "END_HAS_OUTPUT" in codes


def test_demo_input_schema_is_valid() -> None:
    assert validate_input_schema(DEMO_INPUT_SCHEMA) == []


def test_input_schema_must_describe_an_object() -> None:
    issues = validate_input_schema({"type": "string"})

    assert [issue.code for issue in issues] == ["INVALID_INPUT_SCHEMA"]
