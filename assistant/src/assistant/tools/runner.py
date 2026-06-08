from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from assistant.tools.registry import ToolSpec


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    requires_approval: bool

    @property
    def status(self) -> str:
        return "succeeded" if self.returncode == 0 else "failed"


def run_tool(tool: ToolSpec, cwd: Path | None = None) -> ToolResult:
    command = _uv_command(tool.command)
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return ToolResult(
        tool_name=tool.name,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        requires_approval=tool.requires_approval,
    )


def _uv_command(command: list[str]) -> list[str]:
    if command[:2] == ["uv", "run"]:
        return command
    return ["uv", "run", *command]
