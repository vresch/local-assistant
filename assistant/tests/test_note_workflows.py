from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

import assistant.cli as cli
from assistant.db import connect
from assistant.notes.indexer import index_notes
from assistant.notes.links import extract_links
from assistant.notes.workflows import append_daily_note, capture_note, related_notes, summarize_note


runner = CliRunner()


def test_extract_links_reads_wikilinks_and_markdown_links() -> None:
    links = extract_links("See [[Project Alpha#Decision|the call]] and [Beta](beta.md#Notes). [Web](https://example.com)")

    assert [(link.link_type, link.target_path, link.target_heading, link.alias) for link in links] == [
        ("wikilink", "Project Alpha", "Decision", "the call"),
        ("markdown", "beta.md", "Notes", "Beta"),
    ]


def test_index_resolves_links_by_title_path_stem_and_alias(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    alpha = notes_dir / "alpha.md"
    beta = notes_dir / "beta.md"
    gamma = notes_dir / "gamma-note.md"
    alpha.write_text("# Project Alpha\nLinks to [[Beta Alias]] and [Gamma](gamma-note.md).", encoding="utf-8")
    beta.write_text("---\naliases: [Beta Alias]\n---\n# Beta\nShared #work.", encoding="utf-8")
    gamma.write_text("# Gamma\nShared #work.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        rows = conn.execute(
            """
            SELECT note_links.target_path, target.path AS resolved_path
            FROM note_links
            LEFT JOIN documents AS target ON target.id = note_links.resolved_document_id
            ORDER BY note_links.id
            """
        ).fetchall()

    assert [(row["target_path"], row["resolved_path"]) for row in rows] == [
        ("Beta Alias", str(beta)),
        ("gamma-note.md", str(gamma)),
    ]


def test_index_backfills_links_for_existing_unchanged_documents(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    source = notes_dir / "source.md"
    target = notes_dir / "target.md"
    source.write_text("# Source\nLinks to [[Target]].", encoding="utf-8")
    target.write_text("# Target\nLinked note.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        conn.execute("DELETE FROM note_links")
        conn.execute("UPDATE documents SET links_indexed = 0")
        backfill = index_notes(conn, notes_dir)
        second = index_notes(conn, notes_dir)
        rows = conn.execute(
            """
            SELECT note_links.target_path, target.path AS resolved_path
            FROM note_links
            LEFT JOIN documents AS target ON target.id = note_links.resolved_document_id
            """
        ).fetchall()

    assert backfill.updated == 2
    assert backfill.skipped == 0
    assert second.updated == 0
    assert second.skipped == 2
    assert [(row["target_path"], row["resolved_path"]) for row in rows] == [("Target", str(target))]


def test_workflows_backlinks_related_and_summary(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    alpha = notes_dir / "alpha.md"
    beta = notes_dir / "beta.md"
    alpha.write_text(
        """
---
tags: [work]
---
# Alpha Project
- Keep SQLite search local.

See [[Beta]].
""".strip(),
        encoding="utf-8",
    )
    beta.write_text("# Beta\nRelated #work note linking back to [[Alpha Project]].", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        related = related_notes(conn, notes_dir, alpha, limit=5)
        summary = summarize_note(conn, notes_dir, alpha)
        backlinks = conn.execute(
            """
            SELECT source.path
            FROM note_links
            JOIN documents AS source ON source.id = note_links.source_document_id
            JOIN documents AS target ON target.id = note_links.resolved_document_id
            WHERE target.path = ?
            """,
            (str(alpha),),
        ).fetchall()

    assert related[0].path == str(beta)
    assert "outbound link" in related[0].reasons
    assert summary.title == "Alpha Project"
    assert summary.tags == ("work",)
    assert summary.headings == ("Alpha Project",)
    assert "Keep SQLite search local." in summary.highlights
    assert summary.links[0].resolved_path == str(beta)
    assert [row["path"] for row in backlinks] == [str(beta)]


def test_capture_and_daily_write_markdown_and_reindex(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    with connect(tmp_path / "assistant.db") as conn:
        capture = capture_note(conn, notes_dir, "Remember the local capture flow #inbox/to-read")
        daily = append_daily_note(conn, notes_dir, "Daily entry about SQLite.")
        documents = conn.execute("SELECT path, title, tags_json FROM documents ORDER BY path").fetchall()

    assert capture.path.read_text(encoding="utf-8").startswith("---\ntype: capture\nstatus: inbox")
    assert daily.path.name.endswith(".md")
    assert "Daily entry about SQLite." in daily.path.read_text(encoding="utf-8")
    assert len(documents) == 2
    assert any("inbox/to-read" in row["tags_json"] for row in documents)


def test_capture_uses_unique_paths_for_same_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_timezone = timezone(timedelta(hours=7))

    class FixedDatetime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=local_timezone)

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    monkeypatch.setattr("assistant.notes.workflows.datetime", FixedDatetime)

    with connect(tmp_path / "assistant.db") as conn:
        first = capture_note(conn, notes_dir, "First capture")
        second = capture_note(conn, notes_dir, "Second capture")

    assert first.path.name == "2026-01-02-030405.md"
    assert second.path.name == "2026-01-02-030405-2.md"
    assert first.path.read_text(encoding="utf-8") != second.path.read_text(encoding="utf-8")


def test_cli_note_workflow_commands_log(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    target = notes_dir / "target.md"
    source = notes_dir / "source.md"
    target.write_text("# Target\nUseful local note #work.", encoding="utf-8")
    source.write_text("# Source\nLinks to [[Target]] #work.", encoding="utf-8")
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    capture_result = runner.invoke(cli.app, ["capture", "A quick local thought"], env=env)
    daily_path_result = runner.invoke(cli.app, ["daily"], env=env)
    daily_append_result = runner.invoke(cli.app, ["daily", "--text", "A daily local entry"], env=env)
    backlinks_result = runner.invoke(cli.app, ["backlinks", str(target)], env=env)
    related_result = runner.invoke(cli.app, ["related", str(target)], env=env)
    summary_result = runner.invoke(cli.app, ["summarize", str(source)], env=env)

    assert capture_result.exit_code == 0
    assert "inbox" in capture_result.output
    assert daily_path_result.exit_code == 0
    assert "daily" in daily_path_result.output
    assert daily_append_result.exit_code == 0
    assert backlinks_result.exit_code == 0
    assert "Source" in backlinks_result.output
    assert related_result.exit_code == 0
    assert "Source" in related_result.output
    assert summary_result.exit_code == 0
    assert "Linked Notes" in summary_result.output

    with sqlite3.connect(db_path) as conn:
        commands = [row[0] for row in conn.execute("SELECT command FROM runs ORDER BY id").fetchall()]

    assert commands == ["index", "capture", "daily", "daily", "backlinks", "related", "summarize"]
