from collections import defaultdict, deque

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.nodes import get_node_definition
from app.schemas import FlowContent, ValidationIssue


def validate_input_schema(input_schema: dict) -> list[ValidationIssue]:
    try:
        Draft202012Validator.check_schema(input_schema)
    except SchemaError as exc:
        return [
            ValidationIssue(
                code="INVALID_INPUT_SCHEMA",
                message=f"Input schema is invalid: {exc.message}",
            )
        ]
    if input_schema.get("type") != "object":
        return [
            ValidationIssue(
                code="INVALID_INPUT_SCHEMA",
                message="Flow input schema must describe a JSON object.",
            )
        ]
    return []


def validate_flow(content: FlowContent) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    nodes = {node.id: node for node in content.nodes}
    incoming: dict[str, list] = defaultdict(list)
    outgoing: dict[str, list] = defaultdict(list)

    if len(nodes) != len(content.nodes):
        issues.append(ValidationIssue(code="DUPLICATE_NODE_ID", message="Node IDs must be unique."))

    starts = [node for node in content.nodes if node.data.node_type == "start"]
    ends = [node for node in content.nodes if node.data.node_type == "end"]
    if len(starts) != 1:
        issues.append(
            ValidationIssue(
                code="START_COUNT", message="A flow must contain exactly one Start node."
            )
        )
    if not ends:
        issues.append(ValidationIssue(code="END_COUNT", message="A flow must contain an End node."))

    seen_edges: set[str] = set()
    for edge in content.edges:
        if edge.id in seen_edges:
            issues.append(
                ValidationIssue(code="DUPLICATE_EDGE_ID", message=f"Duplicate edge ID: {edge.id}")
            )
        seen_edges.add(edge.id)
        source = nodes.get(edge.source)
        target = nodes.get(edge.target)
        if source is None or target is None:
            issues.append(
                ValidationIssue(
                    code="INVALID_EDGE",
                    message=f"Edge {edge.id} references a node that does not exist.",
                )
            )
            continue
        incoming[target.id].append(edge)
        outgoing[source.id].append(edge)

        source_definition = get_node_definition(
            source.data.node_type, source.data.node_version
        )
        target_definition = get_node_definition(
            target.data.node_type, target.data.node_version
        )
        if source_definition and edge.source_handle not in {
            item.name for item in source_definition.outputs
        }:
            issues.append(
                ValidationIssue(
                    code="INVALID_SOURCE_PORT",
                    message=f"Output port '{edge.source_handle}' does not exist.",
                    node_id=source.id,
                )
            )
        if target_definition and edge.target_handle not in {
            item.name for item in target_definition.inputs
        }:
            issues.append(
                ValidationIssue(
                    code="INVALID_TARGET_PORT",
                    message=f"Input port '{edge.target_handle}' does not exist.",
                    node_id=target.id,
                )
            )

    for node in content.nodes:
        definition = get_node_definition(node.data.node_type, node.data.node_version)
        if definition is None:
            issues.append(
                ValidationIssue(
                    code="UNKNOWN_NODE_TYPE",
                    message=(
                        "Unknown node implementation: "
                        f"{node.data.node_type}@{node.data.node_version}"
                    ),
                    node_id=node.id,
                )
            )
            continue
        schema_errors = sorted(
            Draft202012Validator(definition.config_schema).iter_errors(node.data.config),
            key=lambda item: list(item.path),
        )
        for error in schema_errors:
            issues.append(
                ValidationIssue(
                    code="INVALID_NODE_CONFIG",
                    message=error.message,
                    node_id=node.id,
                )
            )

        if node.data.node_type == "start" and incoming[node.id]:
            issues.append(
                ValidationIssue(
                    code="START_HAS_INPUT",
                    message="Start cannot have incoming edges.",
                    node_id=node.id,
                )
            )
        elif node.data.node_type != "start" and not incoming[node.id]:
            issues.append(
                ValidationIssue(
                    code="MISSING_INPUT",
                    message="Node is not connected to an upstream node.",
                    node_id=node.id,
                )
            )

        if node.data.node_type == "end" and outgoing[node.id]:
            issues.append(
                ValidationIssue(
                    code="END_HAS_OUTPUT",
                    message="End cannot have outgoing edges.",
                    node_id=node.id,
                )
            )
        elif node.data.node_type != "end" and not outgoing[node.id]:
            issues.append(
                ValidationIssue(
                    code="MISSING_OUTPUT",
                    message="Node is not connected to a downstream node.",
                    node_id=node.id,
                )
            )

        if node.data.node_type == "condition":
            handles = {edge.source_handle for edge in outgoing[node.id]}
            if not {"true", "false"}.issubset(handles):
                issues.append(
                    ValidationIssue(
                        code="INCOMPLETE_CONDITION",
                        message="Condition must connect both True and False outputs.",
                        node_id=node.id,
                    )
                )

    indegree = {node_id: len(incoming[node_id]) for node_id in nodes}
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    while queue:
        node_id = queue.popleft()
        visited.append(node_id)
        for edge in outgoing[node_id]:
            indegree[edge.target] -= 1
            if indegree[edge.target] == 0:
                queue.append(edge.target)
    if len(visited) != len(nodes):
        issues.append(ValidationIssue(code="CYCLE", message="Cycles are not supported in the MVP."))

    if starts:
        reachable = {starts[0].id}
        queue = deque([starts[0].id])
        while queue:
            node_id = queue.popleft()
            for edge in outgoing[node_id]:
                if edge.target not in reachable:
                    reachable.add(edge.target)
                    queue.append(edge.target)
        for node_id in nodes.keys() - reachable:
            issues.append(
                ValidationIssue(
                    code="UNREACHABLE", message="Node is unreachable from Start.", node_id=node_id
                )
            )

    return issues
