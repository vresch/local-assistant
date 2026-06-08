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
    assert "scanned=1 indexed=1 skipped=0 chunks=2" in index_result.output

    search_result = runner.invoke(cli.app, ["search", "SQLite local"], env=env)
    assert search_result.exit_code == 0
    assert "project.md" in search_result.output
    assert "Project Alpha" in search_result.output

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
            WHERE run_id = 3
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

    assert [run[0] for run in runs] == ["index", "search", "ask"]
    assert [run[1] for run in runs] == ["notes.index", "notes.search", "local_answer"]
    assert all(run[2] == "succeeded" for run in runs)
    assert runs[1][3] == "results=1 llm=none model=none reason=search_only"
    assert runs[2][3] == "results=2 llm=none model=none local_model_used=False"
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
    assert ask_events[2][1] == "llm=none model=none"
    assert ask_events[3][1] == "llm=none model=none local_model_used=False"
    assert "The strongest matching note says" in ask_events[4][1]

    debug_log = debug_log_path.read_text(encoding="utf-8")
    assert "command=index status=succeeded" in debug_log
    assert "command=search status=succeeded" in debug_log
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
    assert "Recent Documents" in result.output
    assert "Recent Runs" in result.output
    assert "Last LLM Request Summary" in result.output
    assert "LLM Events" in result.output
    assert "project.md" in result.output
    assert "llm=none model=none reason=search_only" in result.output
    assert "What powers local search?" in result.output
    assert "The strongest matching note says" in result.output
    assert "answered from 1 local chunks" in result.output


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
