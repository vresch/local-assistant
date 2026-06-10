from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: list[str]
    description: str = ""
    requires_approval: bool = False


def load_registry(path: Path) -> dict[str, ToolSpec]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_tools = data.get("tools", data)
    if isinstance(raw_tools, list):
        items = {item["name"]: item for item in raw_tools}
    elif isinstance(raw_tools, dict):
        items = raw_tools
    else:
        raise ValueError("Tool registry must contain a mapping or list of tools")

    return {name: _parse_tool(name, value) for name, value in items.items()}


def _parse_tool(name: str, value: dict[str, Any]) -> ToolSpec:
    command = value.get("command")
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError(f"Tool {name!r} must define command as a string or list of strings")
    return ToolSpec(
        name=str(value.get("name", name)),
        command=command,
        description=str(value.get("description", "")),
        requires_approval=bool(value.get("requires_approval", False)),
    )
