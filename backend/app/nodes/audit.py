from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FlowVersion
from app.nodes.registry import REGISTRY


@dataclass(frozen=True, order=True)
class MissingNodeReference:
    node_type: str
    node_version: str
    flow_id: str
    flow_version: int
    node_id: str

    def describe(self) -> str:
        return (
            f"{self.node_type}@{self.node_version} "
            f"(flow={self.flow_id}, version={self.flow_version}, node={self.node_id})"
        )


def find_missing_published_nodes(db: Session) -> list[MissingNodeReference]:
    missing: set[MissingNodeReference] = set()
    versions = db.scalars(select(FlowVersion)).all()
    for flow_version in versions:
        content = flow_version.content if isinstance(flow_version.content, dict) else {}
        for node in content.get("nodes", []):
            if not isinstance(node, dict):
                continue
            data = node.get("data", {})
            if not isinstance(data, dict):
                continue
            node_type = str(data.get("nodeType", ""))
            node_version = str(data.get("nodeVersion", "1.0"))
            if node_type and (node_type, node_version) not in REGISTRY.entries:
                missing.add(
                    MissingNodeReference(
                        node_type=node_type,
                        node_version=node_version,
                        flow_id=flow_version.flow_id,
                        flow_version=flow_version.version_number,
                        node_id=str(node.get("id", "<unknown>")),
                    )
                )
    return sorted(missing)


def require_published_nodes_available(db: Session) -> None:
    missing = find_missing_published_nodes(db)
    if missing:
        details = "; ".join(item.describe() for item in missing)
        raise RuntimeError(f"Published flows reference unavailable node types: {details}")
