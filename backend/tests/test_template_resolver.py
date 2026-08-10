import pytest

from app.template_resolver import resolve_templates

DATA = {
    "input": {"customer": {"id": "CUST-1001"}, "score": 86},
    "variables": {"assessment": {"decision": "APPROVED"}, "attempts": 2},
    "run": {"id": "run-1", "triggerType": "MANUAL"},
}


def test_full_template_preserves_native_type() -> None:
    assert resolve_templates("{{ input.score }}", DATA) == 86


def test_inline_template_combines_scopes() -> None:
    result = resolve_templates(
        "{{ variables.assessment.decision }} for {{ input.customer.id }}", DATA
    )
    assert result == "APPROVED for CUST-1001"


def test_nested_configuration_is_resolved() -> None:
    result = resolve_templates(
        {"body": {"attempts": "{{ variables.attempts }}", "run": "{{ run.id }}"}}, DATA
    )
    assert result == {"body": {"attempts": 2, "run": "run-1"}}


def test_missing_template_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        resolve_templates("{{ variables.missing }}", DATA)
