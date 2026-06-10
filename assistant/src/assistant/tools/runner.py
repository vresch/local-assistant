from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.tools.registry import ToolSpec


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    requires_approval: bool
    duration_ms: int = 0
    timed_out: bool = False
    artifacts: tuple[str, ...] = ()
    structured_output: dict[str, Any] | None = None

    @property
    def status(self) -> str:
        return "succeeded" if self.returncode == 0 else "failed"


def run_tool(
    tool: ToolSpec,
    command: list[str] | None = None,
    cwd: Path | None = None,
    timeout_seconds: int | None = None,
) -> ToolResult:
    resolved_command = _uv_command(command or tool.command)
    timeout = timeout_seconds or tool.timeout_seconds
    started = time.monotonic()
    try:
        completed = subprocess.run(
            resolved_command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return ToolResult(
            tool_name=tool.name,
            command=resolved_command,
            returncode=124,
            stdout=_timeout_text(exc.stdout),
            stderr=_timeout_text(exc.stderr) or f"Tool timed out after {timeout} seconds",
            requires_approval=tool.requires_approval,
            duration_ms=duration_ms,
            timed_out=True,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    structured_output = _parse_structured_output(completed.stdout)
    return ToolResult(
        tool_name=tool.name,
        command=resolved_command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        requires_approval=tool.requires_approval,
        duration_ms=duration_ms,
        timed_out=False,
        artifacts=_artifacts(structured_output),
        structured_output=structured_output,
    )


def _uv_command(command: list[str]) -> list[str]:
    if command[:2] == ["uv", "run"]:
        return command
    return ["uv", "run", *command]


def _parse_structured_output(stdout: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "status" not in parsed:
        return None
    return parsed


def _artifacts(structured_output: dict[str, Any] | None) -> tuple[str, ...]:
    if not structured_output:
        return ()
    artifacts = structured_output.get("artifacts", ())
    if not isinstance(artifacts, list | tuple):
        return ()
    return tuple(str(artifact) for artifact in artifacts)


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
