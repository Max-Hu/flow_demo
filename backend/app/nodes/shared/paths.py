from typing import Any


def get_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split(".") if path else []:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"Field '{path}' does not exist in the node input")
    return current
