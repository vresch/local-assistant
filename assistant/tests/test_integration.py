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
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    index_result = runner.invoke(cli.app, ["index"], env=env)
    assert index_result.exit_code == 0
    assert "scanned=1 new=1 updated=0 skipped=0 removed=0 failed=0 chunks=2" in index_result.output

    search_result = runner.invoke(cli.app, ["search", "SQLite local", "--limit", "1"], env=env)
    assert search_result.exit_code == 0
    assert "Chunk" in search_result.output
    assert "Score" in search_result.output
    assert "project.md" in search_result.output
    assert "Project Alpha" in search_result.output

    with sqlite3.connect(db_path) as conn:
        chunk_id = conn.execute("SELECT id FROM chunks ORDER BY id LIMIT 1").fetchone()[0]

    show_result = runner.invoke(cli.app, ["show", str(chunk_id)], env=env)
    assert show_result.exit_code == 0
    assert "Chunk Metadata" in show_result.output
    assert "chunk_id" in show_result.output
    assert "Project Alpha" in show_result.output
    assert "SQLite FTS5" in show_result.output

    ask_result = runner.invoke(cli.app, ["ask", "What search should phase one use?"], env=env)
    assert ask_result.exit_code == 0
    assert "Answer:" in ask_result.output
    assert "Supporting notes:" in ask_result.output
    assert "Sources:" in ask_result.output
    assert "SQLite FTS5" in ask_result.output
    assert "project.md" in ask_result.output
    assert "chunk 1" in ask_result.output

    with sqlite3.connect(db_path) as conn:
        runs = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id").fetchall()
        chunks = conn.execute("SELECT heading FROM chunks ORDER BY chunk_index").fetchall()
        ask_events = conn.execute(
            """
            SELECT event_type, message
            FROM run_events
            WHERE run_id = 4
            ORDER BY id
            """
        ).fetchall()
        search_events = conn.execute(
            """
            SELECT event_type, message
            FROM run_events
            WHERE run_id = 2
            ORDER BY id
            """
        ).fetchall()

    assert [run[0] for run in runs] == ["index", "search", "show", "ask"]
    assert [run[1] for run in runs] == ["notes.index", "notes.search", "notes.show", "local_answer"]
    assert all(run[2] == "succeeded" for run in runs)
    assert runs[1][3] == "results=1 llm=none model=none reason=search_only"
    assert runs[3][3] == "results=2 llm=none model=none local_model_used=False"
    assert [chunk[0] for chunk in chunks] == ["Project Alpha", "Decision"]
    assert search_events == [("llm", "llm=none model=none reason=search_only")]
    assert [event[0] for event in ask_events] == [
        "normalized_query",
        "retrieved_sources",
        "llm",
        "synthesis",
        "answer",
        "answer_summary",
    ]
    assert "search*" in ask_events[0][1]
    assert "project.md" in ask_events[1][1]
    assert "Project Alpha" in ask_events[1][1]
    assert ask_events[2][1] == "llm=none model=none"
    assert ask_events[3][1] == "llm=none model=none local_model_used=False"
    assert "The strongest matching note says" in ask_events[4][1]

    debug_log = debug_log_path.read_text(encoding="utf-8")
    assert "command=index status=succeeded" in debug_log
    assert "command=search status=succeeded" in debug_log
    assert "command=show status=succeeded" in debug_log
    assert "command=ask status=succeeded" in debug_log


def test_cli_ask_handles_empty_results_and_no_model_flag(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project\nSQLite only.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["ask", "banana telescope", "--no-model"], env=env)

    assert result.exit_code == 0
    assert "I could not find relevant notes for that question." in result.output
    assert "Sources:" in result.output
    assert "None" in result.output
    assert "assistant index" in result.output

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        events = conn.execute(
            """
            SELECT event_type, message
            FROM run_events
            WHERE run_id = ?
            ORDER BY id
            """,
            (2,),
        ).fetchall()

    assert run == ("ask", "local_answer", "succeeded", "results=0 llm=none model=none local_model_used=False")
    assert events == [
        ("normalized_query", "banana* OR telescope*"),
        ("retrieved_sources", "none"),
        ("llm", "llm=none model=none"),
        ("synthesis", "llm=none model=none local_model_used=False"),
        ("answer", "I could not find relevant notes for that question."),
        ("answer_summary", "no relevant chunks found; local_model_requested=False"),
    ]


def test_cli_ask_alerts_when_configured_llm_model_path_is_invalid(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project\nSQLite FTS5 powers local search.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    missing_model = tmp_path / "missing.gguf"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": str(missing_model),
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["ask", "What powers local search?"], env=env)

    assert result.exit_code == 1
    assert f"Invalid LLM configuration: ASSISTANT_LLAMA_MODEL_PATH does not exist: {missing_model}" in result.output

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        events = conn.execute(
            """
            SELECT event_type, message
            FROM run_events
            WHERE run_id = ?
            ORDER BY id
            """,
            (2,),
        ).fetchall()

    expected_summary = f"invalid_llm_config ASSISTANT_LLAMA_MODEL_PATH does not exist: {missing_model}"
    assert run == ("ask", "local_answer", "failed", expected_summary)
    assert events == [("llm_config", expected_summary)]


def test_cli_ask_no_model_ignores_invalid_llm_model_path(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project\nSQLite FTS5 powers local search.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": str(tmp_path / "missing.gguf"),
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["ask", "What powers local search?", "--no-model"], env=env)

    assert result.exit_code == 0
    assert "Invalid LLM configuration" not in result.output
    assert "The strongest matching note says" in result.output


def test_cli_categorise_notes_prints_and_logs_categories(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text(
        "# Project Alpha\nRoadmap and launch milestones.",
        encoding="utf-8",
    )
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["categorise-notes"], env=env)

    assert result.exit_code == 0
    assert "Note Categories" in result.output
    assert "project.md" in result.output
    assert "project" in result.output
    assert "roadmap" in result.output

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        events = conn.execute("SELECT event_type, message FROM run_events WHERE run_id = 2").fetchall()

    assert run == ("categorise-notes", "notes.categorise", "succeeded", "notes=1 project=1")
    assert events == [("categories", "notes=1 project=1")]


def test_cli_save_llm_summary_writes_latest_ask_summary(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text(
        "# Project\nSQLite FTS5 powers local search.",
        encoding="utf-8",
    )
    db_path = tmp_path / "assistant.db"
    summary_path = tmp_path / "last-llm-request.md"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    assert runner.invoke(cli.app, ["ask", "What powers local search?"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["save-llm-summary", "--output", str(summary_path)], env=env)

    assert result.exit_code == 0
    assert f"saved last_llm_summary={summary_path}" in result.output.replace("\n", "")
    content = summary_path.read_text(encoding="utf-8")
    assert "# Last LLM Request Summary" in content
    assert "- Run ID: 2" in content
    assert "- Question: What powers local search?" in content
    assert "- LLM: llm=none model=none" in content
    assert "## Answer Summary" in content
    assert "The strongest matching note says" in content
    assert "## Run Summary" in content
    assert "answered from 1 local chunks" in content
    assert "project.md" in content

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        events = conn.execute("SELECT event_type, message FROM run_events WHERE run_id = 3").fetchall()

    assert run == (
        "save-llm-summary",
        "logs.llm_summary",
        "succeeded",
        f"saved last_llm_summary={summary_path}",
    )
    assert events == [("llm_summary_saved", f"saved last_llm_summary={summary_path}")]


def test_cli_dashboard_shows_storage_runs_and_llm_events(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project\nSQLite FTS5 powers local search.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    assert runner.invoke(cli.app, ["search", "SQLite"], env=env).exit_code == 0
    assert runner.invoke(cli.app, ["ask", "What powers local search?"], env=env).exit_code == 0

    result = runner.invoke(cli.app, ["dashboard"], env=env)

    assert result.exit_code == 0
    assert "Assistant Dashboard" in result.output
    assert "documents=1" in result.output
    assert "chunks=1" in result.output
    assert "configured_llm=none" in result.output
    assert "configured_model=none" in result.output
    assert "Recent Documents" in result.output
    assert "Recent Runs" in result.output
    assert "Last LLM Request Summary" in result.output
    assert "LLM Events" in result.output
    assert "project.md" in result.output
    assert "llm=none model=none reason=search_only" in result.output
    assert "What powers local search?" in result.output
    assert "The strongest matching note says" in result.output
    assert "answered from 1 local chunks" in result.output


def test_cli_run_loads_registry_and_runs_tool(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "assistant.db"
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  sample:
    description: Sample integration tool.
    command: python -c "print('sample output')"
    requires_approval: false
""".strip(),
        encoding="utf-8",
    )
    env = {
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_REGISTRY_PATH": str(registry_path),
    }

    def fake_run_tool(tool, command=None, cwd=None, timeout_seconds=None):
        assert command == ["python", "-c", "print('sample output')"]
        assert cwd is None
        assert timeout_seconds == 60
        return ToolResult(
            tool_name=tool.name,
            command=["uv", "run", *command],
            returncode=0,
            stdout="sample output\n",
            stderr="",
            requires_approval=tool.requires_approval,
            duration_ms=7,
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
        "tool=sample status=succeeded returncode=0 duration_ms=7 timed_out=false",
    )
    assert len(events) == 1
    assert events[0][0] == "tool"
    assert "tool=sample" in events[0][1]
    assert "command=python -c 'print('\"'\"'sample output'\"'\"')'" in events[0][1]
    assert "risk=low" in events[0][1]
    assert "approved=false" in events[0][1]
    assert "returncode=0" in events[0][1]


def test_cli_run_blocks_tools_that_require_approval(tmp_path: Path, monkeypatch) -> None:
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

    def fail_run_tool(tool, command=None, cwd=None, timeout_seconds=None):
        raise AssertionError("approval-required tools must not execute")

    monkeypatch.setattr(cli, "run_tool", fail_run_tool)

    result = runner.invoke(cli.app, ["run", "sample"], env=env)
    assert result.exit_code == 1
    assert "tool=sample blocked approval_required=true risk=low" in result.output

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, input, route, status, summary FROM runs").fetchone()
        events = conn.execute("SELECT event_type, message FROM run_events ORDER BY id").fetchall()

    assert run == (
        "run",
        "sample",
        "tools.run",
        "failed",
        "tool=sample blocked approval_required=true risk=low",
    )
    assert len(events) == 1
    assert events[0][0] == "approval_required"
    assert "tool=sample" in events[0][1]
    assert "requires_approval=true" in events[0][1]


def test_cli_run_supports_args_dry_run_and_logs_metadata(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "assistant.db"
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  sample:
    description: Sample integration tool.
    command: ["python", "tool.py"]
    risk: low
    permissions: ["read"]
    args:
      - name: month
        type: str
        required: true
        flag: "--month"
""".strip(),
        encoding="utf-8",
    )
    env = {
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_REGISTRY_PATH": str(registry_path),
    }

    def fail_run_tool(tool, command=None, cwd=None, timeout_seconds=None):
        raise AssertionError("dry-run must not execute")

    monkeypatch.setattr(cli, "run_tool", fail_run_tool)

    result = runner.invoke(cli.app, ["run", "sample", "--arg", "month=2026-06", "--dry-run"], env=env)

    assert result.exit_code == 0
    assert "tool=sample" in result.output
    assert "command=python tool.py --month 2026-06" in result.output
    assert "risk=low" in result.output
    assert "permissions=read" in result.output
    assert "requires_approval=false" in result.output

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, input, route, status, summary FROM runs").fetchone()
        events = conn.execute("SELECT event_type, message FROM run_events ORDER BY id").fetchall()

    assert run == (
        "run",
        "sample --arg month=2026-06 --dry-run",
        "tools.run",
        "succeeded",
        "tool=sample dry_run=true approval_required=false",
    )
    assert events[0][0] == "dry_run"
    assert "args=month=2026-06" in events[0][1]


def test_cli_run_blocks_medium_risk_without_approve_and_executes_with_approve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "assistant.db"
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  sample:
    command: ["python", "tool.py"]
    risk: medium
    permissions: ["write"]
    args:
      - name: x
        type: str
        required: true
        flag: "--x"
""".strip(),
        encoding="utf-8",
    )
    env = {
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_REGISTRY_PATH": str(registry_path),
    }
    calls = []

    def fake_run_tool(tool, command=None, cwd=None, timeout_seconds=None):
        calls.append(command)
        return ToolResult(
            tool_name=tool.name,
            command=["uv", "run", *command],
            returncode=0,
            stdout="ok\n",
            stderr="",
            requires_approval=tool.requires_approval,
            duration_ms=3,
            structured_output={"status": "succeeded", "summary": "ok", "artifacts": ["artifact.txt"]},
            artifacts=("artifact.txt",),
        )

    monkeypatch.setattr(cli, "run_tool", fake_run_tool)

    blocked = runner.invoke(cli.app, ["run", "sample", "--arg", "x=y"], env=env)
    approved = runner.invoke(cli.app, ["run", "sample", "--arg", "x=y", "--approve"], env=env)

    assert blocked.exit_code == 1
    assert "approval_required=true risk=medium" in blocked.output
    assert approved.exit_code == 0
    assert "ok" in approved.output
    assert calls == [["python", "tool.py", "--x", "y"]]

    with sqlite3.connect(db_path) as conn:
        runs = conn.execute("SELECT status, summary FROM runs ORDER BY id").fetchall()
        events = conn.execute("SELECT event_type, message FROM run_events ORDER BY id").fetchall()

    assert runs[0] == ("failed", "tool=sample blocked approval_required=true risk=medium")
    assert runs[1] == ("succeeded", "tool=sample status=succeeded returncode=0 duration_ms=3 timed_out=false")
    assert events[0][0] == "approval_required"
    assert events[1][0] == "tool"
    assert "approved=true" in events[1][1]
    assert events[2] == ("structured_summary", "ok")
    assert events[3] == ("artifacts", "artifact.txt")


def test_cli_run_note_create_confines_paths_with_real_runner(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    db_path = tmp_path / "assistant.db"
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  note-create:
    command: ["python", "-m", "assistant.tools.note_create"]
    risk: medium
    permissions: ["write"]
    args:
      - name: path
        type: path
        required: true
        flag: "--path"
      - name: title
        type: str
        required: true
        flag: "--title"
""".strip(),
        encoding="utf-8",
    )
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_REGISTRY_PATH": str(registry_path),
    }

    created = runner.invoke(
        cli.app,
        ["run", "note-create", "--arg", "path=inbox/idea", "--arg", "title=Idea", "--approve"],
        env=env,
    )
    escaped = runner.invoke(
        cli.app,
        ["run", "note-create", "--arg", f"path={tmp_path / 'outside'}", "--arg", "title=Bad", "--approve"],
        env=env,
    )

    assert created.exit_code == 0
    assert (notes_dir / "inbox" / "idea.md").read_text(encoding="utf-8") == "# Idea\n"
    assert escaped.exit_code == 1
    assert "--path must be relative to ASSISTANT_NOTES_DIR" in escaped.output
    assert not (tmp_path / "outside.md").exists()


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
