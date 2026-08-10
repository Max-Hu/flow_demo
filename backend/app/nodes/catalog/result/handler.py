from typing import Any

from app.nodes.base import NodeContext


class ResultNode:
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any]:
        template = str(config.get("message", "Completed"))
        try:
            message = template.format_map(inputs)
        except KeyError as exc:
            raise ValueError(f"Message variable {exc!s} is missing from the input") from exc
        return {**inputs, "result": config.get("result", "COMPLETED"), "message": message}
