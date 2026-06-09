from __future__ import annotations

from pathlib import Path

from assistant.db import connect
from assistant.notes.categorizer import categorise_notes
from assistant.notes.indexer import index_notes


def test_categorise_notes_scores_indexed_documents(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    project_note = notes_dir / "project.md"
    project_note.write_text(
        "# Launch Plan\nProject roadmap with milestones and implementation tasks.",
        encoding="utf-8",
    )
    research_note = notes_dir / "research.md"
    research_note.write_text(
        "# Retrieval Research\nPaper references and study findings for SQLite FTS5.",
        encoding="utf-8",
    )

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        categories = categorise_notes(conn)

    by_path = {Path(category.path).name: category for category in categories}
    assert by_path["project.md"].category == "project"
    assert by_path["project.md"].score >= 3
    assert "roadmap" in by_path["project.md"].matched_terms
    assert by_path["research.md"].category == "research"
    assert by_path["research.md"].score >= 3
    assert "paper" in by_path["research.md"].matched_terms


def test_categorise_notes_marks_unknown_content_uncategorised(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note = notes_dir / "misc.md"
    note.write_text("# Misc\nPlain text without classifier keywords.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        categories = categorise_notes(conn)

    assert categories[0].path == str(note)
    assert categories[0].category == "uncategorised"
    assert categories[0].score == 0
    assert categories[0].matched_terms == ()
