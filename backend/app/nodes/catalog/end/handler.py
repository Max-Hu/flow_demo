from typing import Any

from app.nodes.base import NodeContext


class EndNode:
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any]:
        return inputs
