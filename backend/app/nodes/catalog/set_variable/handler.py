from typing import Any

from app.nodes.base import NodeContext


class SetVariableNode:
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any]:
        name = str(config["name"]).strip()
        mode = str(config.get("writeMode", "REPLACE")).upper()
        value = config.get("value")
        stored = context.set_variable(name, value, mode)
        return {**inputs, "variable": {"name": name, "value": stored, "writeMode": mode}}
