from typing import Any

from app.nodes.loader import NodeRegistry, build_registry
from app.schemas import NodeTypeResponse

REGISTRY: NodeRegistry = build_registry()


def list_node_definitions() -> list[NodeTypeResponse]:
    return list(REGISTRY.definitions)


def get_node_definition(node_type: str, version: str) -> NodeTypeResponse | None:
    entry = REGISTRY.entries.get((node_type, version))
    return entry.definition if entry else None


def get_node_execution_kind(node_type: str, version: str) -> str:
    entry = REGISTRY.entries.get((node_type, version))
    if entry is None:
        raise ValueError(f"Node implementation not found: {node_type}@{version}")
    return entry.execution_kind


def get_node_handler(node_type: str, version: str) -> Any:
    entry = REGISTRY.entries.get((node_type, version))
    if entry is None or entry.handler_factory is None:
        raise ValueError(f"Python node implementation not found: {node_type}@{version}")
    return entry.handler_factory()


def get_registry_fingerprint() -> str:
    return REGISTRY.fingerprint


def get_registry_count() -> int:
    return len(REGISTRY.entries)
