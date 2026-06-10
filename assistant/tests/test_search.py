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


def test_index_removes_deleted_markdown_files_from_search(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    deleted_note = notes_dir / "deleted.md"
    kept_note = notes_dir / "kept.md"
    deleted_note.write_text("# Deleted\nStale archivedtoken reference.", encoding="utf-8")
    kept_note.write_text("# Kept\nCurrent SQLite reference.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        deleted_note.unlink()
        stats = index_notes(conn, notes_dir)
        stale_results = search_notes(conn, "archivedtoken")
        kept_results = search_notes(conn, "SQLite")
        document_paths = [row["path"] for row in conn.execute("SELECT path FROM documents ORDER BY path")]
        fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    assert stats.scanned == 1
    assert stats.skipped == 1
    assert stale_results == []
    assert len(kept_results) == 1
    assert document_paths == [str(kept_note)]
    assert fts_count == 1


def test_index_removes_renamed_markdown_file_from_search(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    original_note = notes_dir / "original.md"
    renamed_note = notes_dir / "renamed.md"
    original_note.write_text("# Original\nOriginal legacytoken business idea.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        original_note.rename(renamed_note)
        renamed_note.write_text("# Renamed\nRenamed currenttoken business idea.", encoding="utf-8")
        stats = index_notes(conn, notes_dir)
        old_results = search_notes(conn, "legacytoken")
        new_results = search_notes(conn, "currenttoken")
        document_paths = [row["path"] for row in conn.execute("SELECT path FROM documents ORDER BY path")]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    assert stats.scanned == 1
    assert stats.indexed == 1
    assert old_results == []
    assert len(new_results) == 1
    assert new_results[0].path == str(renamed_note)
    assert document_paths == [str(renamed_note)]
    assert chunks == 1
    assert fts_count == 1
