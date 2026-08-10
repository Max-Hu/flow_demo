from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.nodes.audit import find_missing_published_nodes, require_published_nodes_available


def database_with_versions(*versions: SimpleNamespace) -> Mock:
    db = Mock()
    db.scalars.return_value.all.return_value = list(versions)
    return db


def test_published_flow_node_references_are_checked() -> None:
    version = SimpleNamespace(
        flow_id="flow-1",
        version_number=2,
        content={
            "nodes": [
                {
                    "id": "known",
                    "data": {"nodeType": "start", "nodeVersion": "1.0"},
                },
                {
                    "id": "missing",
                    "data": {"nodeType": "removed_node", "nodeVersion": "2.0"},
                },
            ]
        },
    )
    db = database_with_versions(version)

    missing = find_missing_published_nodes(db)

    assert len(missing) == 1
    assert missing[0].node_type == "removed_node"
    assert missing[0].node_version == "2.0"


def test_startup_fails_with_clear_missing_node_details() -> None:
    version = SimpleNamespace(
        flow_id="flow-1",
        version_number=1,
        content={
            "nodes": [
                {
                    "id": "legacy",
                    "data": {"nodeType": "legacy_node", "nodeVersion": "1.0"},
                }
            ]
        },
    )

    with pytest.raises(RuntimeError, match="legacy_node@1.0.*flow=flow-1"):
        require_published_nodes_available(database_with_versions(version))
