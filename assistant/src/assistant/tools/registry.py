from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml  # type: ignore[import-untyped]


ALLOWED_ARG_TYPES = {"str", "int", "float", "bool", "path"}
ALLOWED_PERMISSIONS = {"read", "write", "shell", "network"}
ALLOWED_RISKS = {"low", "medium", "high"}
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class ToolArgSpec:
    name: str
    type: str = "str"
    required: bool = False
    default: str | int | float | bool | None = None
    description: str = ""
    flag: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: list[str]
    description: str = ""
    requires_approval: bool = False
    risk: str = "low"
    permissions: tuple[str, ...] = ()
    args: tuple[ToolArgSpec, ...] = ()
    timeout_seconds: int = 60
    working_dir: str | None = None


def load_registry(path: Path) -> dict[str, ToolSpec]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_tools = data.get("tools", data)
    if isinstance(raw_tools, list):
        items = _list_tools(raw_tools)
    elif isinstance(raw_tools, dict):
        items = raw_tools
    else:
        raise ValueError("Tool registry must contain a mapping or list of tools")

    registry = {name: _parse_tool(name, value) for name, value in items.items()}
    if len(registry) != len({tool.name for tool in registry.values()}):
        raise ValueError("Tool registry contains duplicate tool names")
    return registry


def _list_tools(raw_tools: list[Any]) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for index, item in enumerate(raw_tools):
        if not isinstance(item, dict):
            raise ValueError(f"Tool registry list item {index} must be a mapping")
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError(f"Tool registry list item {index} must define name")
        if name in items:
            raise ValueError(f"Tool registry contains duplicate tool name {name!r}")
        items[name] = item
    return items


def _parse_tool(name: str, value: dict[str, Any]) -> ToolSpec:
    if not isinstance(value, dict):
        raise ValueError(f"Tool {name!r} must define a mapping")
    tool_name = str(value.get("name", name))
    _validate_tool_name(tool_name)

    command = value.get("command")
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError(f"Tool {name!r} must define command as a string or list of strings")

    risk = str(value.get("risk", "low"))
    if risk not in ALLOWED_RISKS:
        raise ValueError(f"Tool {tool_name!r} has invalid risk {risk!r}; expected one of {sorted(ALLOWED_RISKS)}")

    raw_permissions = value.get("permissions", ())
    if raw_permissions is None:
        raw_permissions = ()
    if not isinstance(raw_permissions, list | tuple) or not all(isinstance(item, str) for item in raw_permissions):
        raise ValueError(f"Tool {tool_name!r} permissions must be a list of strings")
    permissions = tuple(raw_permissions)
    invalid_permissions = sorted(set(permissions) - ALLOWED_PERMISSIONS)
    if invalid_permissions:
        raise ValueError(
            f"Tool {tool_name!r} has invalid permissions {invalid_permissions}; "
            f"expected one of {sorted(ALLOWED_PERMISSIONS)}"
        )

    timeout_seconds = int(value.get("timeout_seconds", 60))
    if timeout_seconds <= 0:
        raise ValueError(f"Tool {tool_name!r} timeout_seconds must be greater than zero")

    working_dir = value.get("working_dir")
    if working_dir is not None and not isinstance(working_dir, str):
        raise ValueError(f"Tool {tool_name!r} working_dir must be a string")

    return ToolSpec(
        name=tool_name,
        command=command,
        description=str(value.get("description", "")),
        requires_approval=bool(value.get("requires_approval", False)),
        risk=risk,
        permissions=permissions,
        args=tuple(_parse_arg(tool_name, raw_arg) for raw_arg in value.get("args", ()) or ()),
        timeout_seconds=timeout_seconds,
        working_dir=working_dir,
    )


def build_command(tool: ToolSpec, values: dict[str, str]) -> list[str]:
    """Validate CLI arg values and append rendered arguments deterministically."""
    specs = {arg.name: arg for arg in tool.args}
    unknown = sorted(set(values) - set(specs))
    if unknown:
        raise ValueError(f"Unknown args for tool {tool.name!r}: {', '.join(unknown)}")

    command = list(tool.command)
    for arg in tool.args:
        if arg.name in values:
            value = _coerce_arg(tool.name, arg, values[arg.name])
        elif arg.default is not None:
            value = arg.default
        elif arg.required:
            raise ValueError(f"Missing required arg for tool {tool.name!r}: {arg.name}")
        else:
            continue
        rendered = _render_arg(arg, value)
        command.extend(rendered)
    return command


def _parse_arg(tool_name: str, value: Any) -> ToolArgSpec:
    if not isinstance(value, dict):
        raise ValueError(f"Tool {tool_name!r} args must contain mappings")
    name = value.get("name")
    if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
        raise ValueError(f"Tool {tool_name!r} has invalid arg name {name!r}")
    arg_type = str(value.get("type", "str"))
    if arg_type not in ALLOWED_ARG_TYPES:
        raise ValueError(
            f"Tool {tool_name!r} arg {name!r} has invalid type {arg_type!r}; "
            f"expected one of {sorted(ALLOWED_ARG_TYPES)}"
        )
    required = bool(value.get("required", False))
    default = value.get("default")
    if default is not None:
        default = _coerce_arg(tool_name, ToolArgSpec(name=name, type=arg_type), default)
    flag = value.get("flag")
    if flag is not None and (not isinstance(flag, str) or not flag.startswith("-")):
        raise ValueError(f"Tool {tool_name!r} arg {name!r} flag must start with '-'")
    return ToolArgSpec(
        name=name,
        type=arg_type,
        required=required,
        default=default,
        description=str(value.get("description", "")),
        flag=flag,
    )


def _coerce_arg(tool_name: str, arg: ToolArgSpec, value: Any) -> str | int | float | bool:
    try:
        if arg.type == "str":
            return str(value)
        if arg.type == "path":
            return str(value)
        if arg.type == "int":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if arg.type == "float":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if arg.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Tool {tool_name!r} arg {arg.name!r} must be {arg.type}") from exc
    raise ValueError(f"Tool {tool_name!r} arg {arg.name!r} has unsupported type {arg.type!r}")


def _render_arg(arg: ToolArgSpec, value: str | int | float | bool) -> list[str]:
    rendered_value = _arg_value_to_string(value)
    if arg.flag:
        return [arg.flag, rendered_value]
    return [rendered_value]


def _arg_value_to_string(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _validate_tool_name(name: str) -> None:
    if not TOOL_NAME_RE.match(name):
        raise ValueError(f"Tool name {name!r} must be a stable identifier")
