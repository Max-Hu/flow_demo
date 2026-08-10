from __future__ import annotations

import re
from typing import Any

FULL_TEMPLATE = re.compile(
    r"^\s*{{\s*(input|variables|run|flowConfig)\.([A-Za-z0-9_.-]+)\s*}}\s*$"
)
INLINE_TEMPLATE = re.compile(
    r"{{\s*(input|variables|run|flowConfig)\.([A-Za-z0-9_.-]+)\s*}}"
)
MISSING = object()


def get_path(data: Any, path: str, default: Any = MISSING) -> Any:
    current = data
    for part in path.split(".") if path else []:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif default is not MISSING:
            return default
        else:
            raise ValueError(f"Template path '{path}' does not exist")
    return current


def _resolve_path(scope: str, path: str, data: dict[str, Any]) -> Any:
    if scope == "variables":
        variables = data[scope]
        if path in variables:
            return variables[path]
        parts = path.split(".")
        for split_at in range(len(parts) - 1, 0, -1):
            name = ".".join(parts[:split_at])
            if name in variables:
                return get_path(variables[name], ".".join(parts[split_at:]))
    return get_path(data[scope], path)


def resolve_templates(value: Any, data: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_templates(item, data) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, data) for item in value]
    if not isinstance(value, str):
        return value
    full = FULL_TEMPLATE.fullmatch(value)
    if full:
        return _resolve_path(full.group(1), full.group(2), data)

    def replace(match: re.Match[str]) -> str:
        resolved = _resolve_path(match.group(1), match.group(2), data)
        return str(resolved)

    return INLINE_TEMPLATE.sub(replace, value)
