import json
from types import SimpleNamespace

import pytest

from app.nodes import (
    get_node_execution_kind,
    get_registry_count,
    get_registry_fingerprint,
    list_node_definitions,
    loader,
)
from app.nodes.loader import build_registry
from app.nodes.manifest import ManifestError, validate_manifest


def manifest_document() -> dict:
    return {
        "apiVersion": "flowforge/v1",
        "kind": "NodeType",
        "metadata": {
            "type": "example",
            "version": "1.0",
            "name": "Example",
            "description": "Example node.",
            "lifecycle": "active",
        },
        "spec": {
            "category": "Test",
            "color": "#123456",
            "execution": {"kind": "python", "handler": ".handler:ExampleNode"},
            "inputs": [{"name": "input", "label": "Input", "dataType": "object"}],
            "outputs": [{"name": "output", "label": "Output", "dataType": "object"}],
            "configSchema": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
                "additionalProperties": False,
            },
            "defaultConfig": {"enabled": True},
        },
    }


def test_builtin_registry_contains_all_nodes_and_stable_fingerprint() -> None:
    definitions = list_node_definitions()

    assert get_registry_count() == 9
    assert len(definitions) == 9
    assert get_node_execution_kind("http_poll", "1.0") == "durable_poll"
    assert len(get_registry_fingerprint()) == 64
    assert get_node_execution_kind("manual_approval", "1.0") == "manual_wait"
    assert all(item.available_for_new_flows for item in definitions)


def test_manifest_rejects_unknown_fields_and_invalid_ports() -> None:
    document = manifest_document()
    document["metadata"]["unexpected"] = True
    document["spec"]["inputs"][0]["dataType"] = "database"

    with pytest.raises(ManifestError, match="Invalid node manifest"):
        validate_manifest(document, "test:node.yaml")


def test_manifest_rejects_invalid_json_schema() -> None:
    document = manifest_document()
    document["spec"]["configSchema"] = {"type": "not-a-json-schema-type"}

    with pytest.raises(ManifestError, match="Invalid configSchema"):
        validate_manifest(document, "test:node.yaml")


def test_manifest_rejects_default_config_that_does_not_match_schema() -> None:
    document = manifest_document()
    document["spec"]["defaultConfig"] = {"enabled": "yes"}

    with pytest.raises(ManifestError, match="does not satisfy configSchema"):
        validate_manifest(document, "test:node.yaml")


def test_duplicate_type_and_version_fails_registry_build() -> None:
    catalog = loader.importlib.import_module("app.nodes.catalog")

    with pytest.raises(ManifestError, match="Duplicate node"):
        build_registry(
            providers=[("first", catalog), ("second", catalog)],
            include_builtin=False,
            include_entry_points=False,
        )


def test_handler_reference_must_be_relative_to_node_package() -> None:
    with pytest.raises(ManifestError, match="Unsafe handler"):
        loader._resolve_handler(
            "app.nodes.catalog.start", "app.nodes.handlers:StartNode", "test:node.yaml"
        )


def test_missing_handler_class_fails_loading() -> None:
    with pytest.raises(ManifestError, match="cannot be instantiated"):
        loader._resolve_handler(
            "app.nodes.catalog.start", ".handler:MissingNode", "test:node.yaml"
        )


def test_installed_entry_point_provider_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = loader.importlib.import_module("app.nodes.catalog")
    fake_entry_point = SimpleNamespace(name="acme", load=lambda: catalog)
    fake_entry_points = SimpleNamespace(select=lambda **kwargs: [fake_entry_point])
    monkeypatch.setattr(loader.metadata, "entry_points", lambda: fake_entry_points)

    registry = build_registry(include_builtin=False, include_entry_points=True)

    assert len(registry.entries) == 9


def test_provider_can_keep_multiple_versions_of_one_node(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_root = tmp_path / "versioned_provider"
    provider_root.mkdir()
    (provider_root / "__init__.py").write_text("", encoding="utf-8")
    for directory, version in (("customer_lookup_v1", "1.0"), ("customer_lookup_v2", "2.0")):
        node_root = provider_root / directory
        node_root.mkdir()
        (node_root / "__init__.py").write_text("", encoding="utf-8")
        (node_root / "handler.py").write_text(
            "class ExampleNode:\n"
            "    def execute(self, inputs, config, context):\n"
            "        return inputs\n",
            encoding="utf-8",
        )
        document = manifest_document()
        document["metadata"]["type"] = "customer_lookup"
        document["metadata"]["version"] = version
        (node_root / "node.yaml").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    loader.importlib.invalidate_caches()
    provider = loader.importlib.import_module("versioned_provider")

    entries = loader.load_provider(provider)

    assert {(item.definition.type, item.definition.version) for item in entries} == {
        ("customer_lookup", "1.0"),
        ("customer_lookup", "2.0"),
    }


def test_deprecated_manifest_maps_to_unavailable_for_new_flows() -> None:
    document = manifest_document()
    document["metadata"]["lifecycle"] = "deprecated"
    manifest = validate_manifest(document, "test:node.yaml")

    definition = loader._definition_from_manifest(manifest)

    assert definition.lifecycle == "deprecated"
    assert definition.available_for_new_flows is False
