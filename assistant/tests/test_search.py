from pathlib import Path

from assistant.db import connect
from assistant.notes.indexer import index_notes
from assistant.notes.search import search_notes


def test_index_and_search_markdown_notes(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note = notes_dir / "alpha.md"
    note.write_text("# Alpha\nSQLite FTS5 powers local search.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        stats = index_notes(conn, notes_dir)
        results = search_notes(conn, "local search")

    assert stats.scanned == 1
    assert stats.indexed == 1
    assert stats.skipped == 0
    assert stats.chunks == 1
    assert len(results) == 1
    assert results[0].path == str(note)
    assert results[0].heading == "Alpha"
    assert results[0].chunk_index == 0
    assert "local" in results[0].snippet.lower()


def test_index_skips_unchanged_files(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "alpha.md").write_text("# Alpha\nOne note.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        first = index_notes(conn, notes_dir)
        second = index_notes(conn, notes_dir)

    assert first.indexed == 1
    assert second.scanned == 1
    assert second.indexed == 0
    assert second.skipped == 1
