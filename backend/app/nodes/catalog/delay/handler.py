import time
from typing import Any

from app.nodes.base import NodeContext


class DelayNode:
    def execute(
        self, inputs: dict[str, Any], config: dict[str, Any], context: NodeContext
    ) -> dict[str, Any]:
        seconds = min(max(float(config.get("seconds", 1)), 0), 10)
        time.sleep(seconds)
        return inputs
