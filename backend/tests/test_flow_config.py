from app.flow_config import config_validation_errors, deep_merge
from app.template_resolver import resolve_templates


def test_flow_config_recursively_merges_objects_and_replaces_arrays() -> None:
    merged = deep_merge(
        {"partner": {"timeout": 10, "retries": 2}, "regions": ["us"]},
        {"partner": {"timeout": 30}, "regions": ["eu"]},
    )

    assert merged == {
        "partner": {"timeout": 30, "retries": 2},
        "regions": ["eu"],
    }


def test_flow_config_rejects_unknown_override_fields() -> None:
    schema = {
        "type": "object",
        "properties": {"batchSize": {"type": "integer"}},
        "additionalProperties": False,
    }

    errors = config_validation_errors(schema, {"batchSize": 10, "unknown": True})

    assert errors and "Additional properties are not allowed" in errors[0]


def test_full_flow_config_template_preserves_json_type() -> None:
    result = resolve_templates(
        {"limit": "{{ flowConfig.batchSize }}", "url": "{{ flowConfig.baseUrl }}/jobs"},
        {"flowConfig": {"batchSize": 100, "baseUrl": "https://example.test"}},
    )

    assert result == {"limit": 100, "url": "https://example.test/jobs"}
