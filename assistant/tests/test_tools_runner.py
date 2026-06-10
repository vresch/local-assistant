from __future__ import annotations

import subprocess
from pathlib import Path

from assistant.tools.registry import ToolSpec
from assistant.tools.runner import _uv_command, run_tool


def test_uv_command_wrapping() -> None:
    assert _uv_command(["python", "tool.py"]) == ["uv", "run", "python", "tool.py"]
    assert _uv_command(["uv", "run", "python", "tool.py"]) == ["uv", "run", "python", "tool.py"]


def test_run_tool_parses_json_stdout(monkeypatch) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout):
        assert command == ["uv", "run", "python", "tool.py"]
        assert cwd == Path("work")
        assert text is True
        assert capture_output is True
        assert check is False
        assert timeout == 5
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status":"succeeded","summary":"done","artifacts":["out.md"]}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_tool(
        ToolSpec(name="sample", command=["python", "tool.py"], timeout_seconds=5),
        cwd=Path("work"),
    )

    assert result.returncode == 0
    assert result.status == "succeeded"
    assert result.structured_output == {"status": "succeeded", "summary": "done", "artifacts": ["out.md"]}
    assert result.artifacts == ("out.md",)
    assert result.timed_out is False
    assert result.duration_ms >= 0


def test_run_tool_handles_timeout(monkeypatch) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout):
        raise subprocess.TimeoutExpired(command, timeout, output="partial", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_tool(ToolSpec(name="slow", command=["python", "slow.py"], timeout_seconds=1))

    assert result.returncode == 124
    assert result.status == "failed"
    assert result.stdout == "partial"
    assert "timed out" in result.stderr
    assert result.timed_out is True


def test_run_tool_preserves_failed_exit_code(monkeypatch) -> None:
    def fake_run(command, cwd, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="bad")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_tool(ToolSpec(name="fail", command=["python", "fail.py"]))

    assert result.returncode == 2
    assert result.status == "failed"
    assert result.stderr == "bad"
