from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata, resources
from types import MappingProxyType, ModuleType
from typing import Any

import yaml

from app.nodes.manifest import ManifestError, NodeManifest, validate_manifest
from app.schemas import NodeTypeResponse, PortDefinition

ENTRY_POINT_GROUP = "flowforge.node_providers"
HANDLER_PATTERN = re.compile(r"^\.([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass(frozen=True)
class RegistryEntry:
    definition: NodeTypeResponse
    execution_kind: str
    handler_factory: Callable[[], Any] | None
    source: str
    manifest: NodeManifest


@dataclass(frozen=True)
class NodeRegistry:
    entries: Mapping[tuple[str, str], RegistryEntry]
    definitions: tuple[NodeTypeResponse, ...]
    fingerprint: str


def _read_manifest(node_resource: Any, source: str) -> NodeManifest:
    try:
        document = yaml.safe_load(node_resource.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid YAML in {source}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise ManifestError(f"Cannot read node manifest {source}: {exc}") from exc
    if not isinstance(document, dict):
        raise ManifestError(f"Node manifest {source} must contain a YAML object")
    return validate_manifest(document, source)


def _definition_from_manifest(manifest: NodeManifest) -> NodeTypeResponse:
    metadata_model = manifest.metadata
    spec = manifest.spec
    return NodeTypeResponse(
        type=metadata_model.type,
        version=metadata_model.version,
        name=metadata_model.name,
        description=metadata_model.description,
        lifecycle=metadata_model.lifecycle,
        availableForNewFlows=metadata_model.lifecycle == "active",
        category=spec.category,
        color=spec.color,
        inputs=[
            PortDefinition(name=port.name, label=port.label, dataType=port.data_type)
            for port in spec.inputs
        ],
        outputs=[
            PortDefinition(name=port.name, label=port.label, dataType=port.data_type)
            for port in spec.outputs
        ],
        configSchema=spec.config_schema,
        defaultConfig=spec.default_config,
    )


def _resolve_handler(node_package: str, handler_ref: str, source: str) -> Callable[[], Any]:
    match = HANDLER_PATTERN.fullmatch(handler_ref)
    if match is None:
        raise ManifestError(
            f"Unsafe handler in {source}: use a relative '.module:ClassName' reference"
        )
    module_name, class_name = match.groups()
    qualified_module = f"{node_package}.{module_name}"
    try:
        module = importlib.import_module(qualified_module)
    except Exception as exc:
        raise ManifestError(f"Cannot import handler {handler_ref} from {source}: {exc}") from exc
    if module.__name__ != qualified_module or not module.__name__.startswith(f"{node_package}."):
        raise ManifestError(f"Handler {handler_ref} in {source} escaped its node package")
    try:
        handler_factory = getattr(module, class_name)
        instance = handler_factory()
    except Exception as exc:
        raise ManifestError(
            f"Handler {handler_ref} in {source} cannot be instantiated: {exc}"
        ) from exc
    if not callable(getattr(instance, "execute", None)):
        raise ManifestError(f"Handler {handler_ref} in {source} must define execute()")
    return handler_factory


def load_provider(provider: ModuleType, provider_name: str | None = None) -> list[RegistryEntry]:
    if not isinstance(provider, ModuleType) or not provider.__package__:
        raise ManifestError(f"Node provider {provider_name or provider!r} must be a Python package")
    root = resources.files(provider)
    loaded: list[RegistryEntry] = []
    node_directories = (item for item in root.iterdir() if item.is_dir())
    for node_dir in sorted(node_directories, key=lambda item: item.name):
        manifest_resource = node_dir.joinpath("node.yaml")
        if not manifest_resource.is_file():
            continue
        source = f"{provider.__name__}:{node_dir.name}/node.yaml"
        manifest = _read_manifest(manifest_resource, source)
        handler_factory = None
        if manifest.spec.execution.kind in {"python", "durable_poll"}:
            handler_factory = _resolve_handler(
                f"{provider.__name__}.{node_dir.name}",
                manifest.spec.execution.handler or "",
                source,
            )
        loaded.append(
            RegistryEntry(
                definition=_definition_from_manifest(manifest),
                execution_kind=manifest.spec.execution.kind,
                handler_factory=handler_factory,
                source=source,
                manifest=manifest,
            )
        )
    return loaded


def _entry_point_providers() -> Iterable[tuple[str, ModuleType]]:
    entry_points = metadata.entry_points()
    selected = entry_points.select(group=ENTRY_POINT_GROUP)
    for entry_point in sorted(selected, key=lambda item: item.name):
        try:
            provider = entry_point.load()
        except Exception as exc:
            raise ManifestError(
                f"Cannot load node provider entry point '{entry_point.name}': {exc}"
            ) from exc
        if not isinstance(provider, ModuleType):
            raise ManifestError(
                f"Node provider entry point '{entry_point.name}' must resolve to a package"
            )
        yield entry_point.name, provider


def build_registry(
    providers: Iterable[tuple[str, ModuleType]] | None = None,
    *,
    include_builtin: bool = True,
    include_entry_points: bool = True,
) -> NodeRegistry:
    provider_list: list[tuple[str, ModuleType]] = []
    if include_builtin:
        provider_list.append(("builtin", importlib.import_module("app.nodes.catalog")))
    if providers:
        provider_list.extend(providers)
    if include_entry_points:
        provider_list.extend(_entry_point_providers())

    entries: dict[tuple[str, str], RegistryEntry] = {}
    for provider_name, provider in provider_list:
        for entry in load_provider(provider, provider_name):
            key = (entry.definition.type, entry.definition.version)
            if key in entries:
                previous = entries[key]
                raise ManifestError(
                    f"Duplicate node {key[0]}@{key[1]} in {entry.source}; "
                    f"already registered by {previous.source}"
                )
            entries[key] = entry

    if not entries:
        raise ManifestError("Node registry is empty")
    ordered_keys = sorted(entries)
    canonical = [
        entries[key].manifest.model_dump(by_alias=True, mode="json") for key in ordered_keys
    ]
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    immutable_entries = MappingProxyType({key: entries[key] for key in ordered_keys})
    return NodeRegistry(
        entries=immutable_entries,
        definitions=tuple(entries[key].definition for key in ordered_keys),
        fingerprint=fingerprint,
    )


__all__ = [
    "ENTRY_POINT_GROUP",
    "NodeRegistry",
    "RegistryEntry",
    "build_registry",
    "load_provider",
]
