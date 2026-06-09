from __future__ import annotations

from pathlib import Path

from assistant.db import connect
from assistant.logs.logger import finish_run, log_event, start_run
from assistant.notes.indexer import index_notes
from assistant.ui import fetch_chunks, fetch_documents, fetch_events, fetch_runs


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
