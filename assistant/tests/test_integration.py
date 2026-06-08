from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

import assistant.cli as cli
from assistant.tools.runner import ToolResult


runner = CliRunner()


def test_cli_indexes_searches_answers_and_logs(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note = notes_dir / "project.md"
    note.write_text(
        """
# Project Alpha
Use SQLite FTS5 for local-first note search.

## Decision
Avoid remote LLM calls in phase one.
""".strip(),
        encoding="utf-8",
    )
    db_path = tmp_path / "assistant.db"
    debug_log_path = tmp_path / "debug.log"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_REGISTRY_PATH": str(tmp_path / "registry.yaml"),
        "ASSISTANT_DEBUG_LOG_PATH": str(debug_log_path),
    }

    index_result = runner.invoke(cli.app, ["index"], env=env)
    assert index_result.exit_code == 0
    assert "scanned=1 indexed=1 skipped=0 chunks=2" in index_result.output

    search_result = runner.invoke(cli.app, ["search", "SQLite local"], env=env)
    assert search_result.exit_code == 0
    assert "project.md" in search_result.output
    assert "Project Alpha" in search_result.output

    ask_result = runner.invoke(cli.app, ["ask", "What search should phase one use?"], env=env)
    assert ask_result.exit_code == 0
    assert "Based on your notes:" in ask_result.output
    assert "SQLite FTS5" in ask_result.output
    assert "project.md" in ask_result.output

    with sqlite3.connect(db_path) as conn:
        runs = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id").fetchall()
        chunks = conn.execute("SELECT heading FROM chunks ORDER BY chunk_index").fetchall()

    assert [run[0] for run in runs] == ["index", "search", "ask"]
    assert [run[1] for run in runs] == ["notes.index", "notes.search", "notes.ask"]
    assert all(run[2] == "succeeded" for run in runs)
    assert [chunk[0] for chunk in chunks] == ["Project Alpha", "Decision"]

    debug_log = debug_log_path.read_text(encoding="utf-8")
    assert "command=index status=succeeded" in debug_log
    assert "command=search status=succeeded" in debug_log
    assert "command=ask status=succeeded" in debug_log


def test_cli_run_loads_registry_runs_tool_and_logs_approval(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "assistant.db"
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  sample:
    description: Sample integration tool.
    command: ["python", "-c", "print('sample output')"]
    requires_approval: true
""".strip(),
        encoding="utf-8",
    )
    env = {
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_REGISTRY_PATH": str(registry_path),
    }

    def fake_run_tool(tool):
        return ToolResult(
            tool_name=tool.name,
            command=["uv", "run", *tool.command],
            returncode=0,
            stdout="sample output\n",
            stderr="",
            requires_approval=tool.requires_approval,
        )

    monkeypatch.setattr(cli, "run_tool", fake_run_tool)

    result = runner.invoke(cli.app, ["run", "sample"], env=env)
    assert result.exit_code == 0
    assert "sample output" in result.output

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, input, route, status, summary FROM runs").fetchone()
        events = conn.execute("SELECT event_type, message FROM run_events ORDER BY id").fetchall()

    assert run == (
        "run",
        "sample",
        "tools.run",
        "succeeded",
        "tool=sample status=succeeded returncode=0",
    )
    assert events == [
        ("approval_required", "approval flag present; interactive approval not implemented"),
        ("tool", "tool=sample status=succeeded returncode=0"),
    ]


def test_cli_clean_db_removes_indexed_data_but_keeps_logs(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project\nKeep logs while clearing index.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["clean-db"], env=env)

    assert result.exit_code == 0
    assert "deleted documents=1 chunks=1 chunks_fts=1" in result.output
    with sqlite3.connect(db_path) as conn:
        documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        chunks_fts = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        runs = conn.execute("SELECT command, status FROM runs ORDER BY id").fetchall()

    assert documents == 0
    assert chunks == 0
    assert chunks_fts == 0
    assert runs == [("index", "succeeded"), ("clean-db", "succeeded")]


def test_cli_clean_db_include_logs_removes_old_logs_and_records_cleanup(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project\nClear everything.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["clean-db", "--include-logs"], env=env)

    assert result.exit_code == 0
    assert "deleted documents=1 chunks=1 chunks_fts=1 runs=1 run_events=1" in result.output
    with sqlite3.connect(db_path) as conn:
        documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        runs = conn.execute("SELECT command, status, summary FROM runs").fetchall()
        events = conn.execute("SELECT event_type FROM run_events").fetchall()

    assert documents == 0
    assert runs == [("clean-db", "succeeded", "deleted documents=1 chunks=1 chunks_fts=1 runs=1 run_events=1")]
    assert events == [("cleanup",)]
