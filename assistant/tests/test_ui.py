from __future__ import annotations

from pathlib import Path

from assistant.db import connect
from assistant.logs.logger import finish_run, log_event, start_run
from assistant.notes.indexer import index_notes
from assistant.ui import fetch_ask_runs, fetch_chunks, fetch_documents, fetch_events, fetch_runs


def test_ui_database_views_filter_indexed_content(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    alpha = notes_dir / "alpha.md"
    beta = notes_dir / "beta.md"
    alpha.write_text("# Alpha\nSQLite FTS5 powers local search.", encoding="utf-8")
    beta.write_text("# Beta\nTool registry notes.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)

        documents = fetch_documents(conn, "alpha")
        chunks = fetch_chunks(conn, "FTS5")

    assert documents.columns == ("ID", "Chunks", "Indexed", "Path")
    assert len(documents.rows) == 1
    assert documents.rows[0][3] == str(alpha)
    assert len(chunks.rows) == 1
    assert chunks.rows[0][1] == str(alpha)
    assert chunks.rows[0][3] == "Alpha"
    assert "SQLite FTS5" in chunks.rows[0][4]


def test_ui_database_views_filter_logs(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        run_id = start_run(conn, "search", "SQLite", "notes.search")
        log_event(conn, run_id, "llm", "llm=none model=none reason=search_only")
        finish_run(conn, run_id, "succeeded", "results=1")

        runs = fetch_runs(conn, "succeeded")
        events = fetch_events(conn, "search_only")

    assert runs.columns == ("ID", "Command", "Route", "Status", "Input", "Summary", "Started", "Finished")
    assert len(runs.rows) == 1
    assert runs.rows[0][1] == "search"
    assert runs.rows[0][3] == "succeeded"
    assert len(events.rows) == 1
    assert events.rows[0][2] == "search"
    assert events.rows[0][3] == "llm"
    assert events.rows[0][4] == "llm=none model=none reason=search_only"


def test_ui_database_views_show_ask_query_and_decision_output(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        run_id = start_run(conn, "ask", "What powers local search?", "local_answer")
        log_event(conn, run_id, "normalized_query", "what* powers* local* search*")
        log_event(conn, run_id, "retrieved_sources", "project.md - Project Alpha - chunk 1")
        log_event(conn, run_id, "llm", "llm=none model=none")
        log_event(conn, run_id, "synthesis", "llm=none model=none local_model_used=False")
        log_event(conn, run_id, "answer", "SQLite FTS5 powers local search.")
        log_event(conn, run_id, "answer_summary", "answered from 1 local chunks; llm=none; model=none")
        finish_run(conn, run_id, "succeeded", "results=1 llm=none model=none local_model_used=False")

        asks = fetch_ask_runs(conn, "local search")

    assert asks.columns == ("ID", "Question", "Decision Output", "Answer", "Status", "Started", "Finished")
    assert len(asks.rows) == 1
    assert asks.rows[0][1] == "What powers local search?"
    assert "normalized_query" in asks.rows[0][2]
    assert "retrieved_sources" in asks.rows[0][2]
    assert asks.rows[0][3] == "SQLite FTS5 powers local search."
    assert asks.rows[0][4] == "succeeded"
