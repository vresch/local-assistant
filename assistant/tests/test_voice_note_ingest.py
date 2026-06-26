from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import assistant.cli as cli
from assistant.db import connect
from assistant.notes.voice_note import (
    load_voice_note,
    parse_voice_note,
    voice_note_markdown,
)
from assistant.notes.workflows import capture_voice_note


runner = CliRunner()


SAMPLE = {
    "meta": {
        "schema_version": "1.0",
        "tool": "voice-note",
        "session": "voice_note_2026_06_23-22_15_00",
        "created_at": "2026-06-23T22:15:00",
    },
    "data": [
        {
            "audio": "audio_2026_06_23-22_15_30.wav",
            "text": "first thought",
            "created_at": "2026-06-23T22:15:30",
        },
        {
            "audio": "audio_2026_06_23-22_16_00.wav",
            "text": "second thought",
            "created_at": "2026-06-23T22:16:00",
        },
    ],
}


def test_parse_reads_meta_and_entries() -> None:
    document = parse_voice_note(SAMPLE)

    assert document.schema_version == "1.0"
    assert document.session == "voice_note_2026_06_23-22_15_00"
    assert document.created_at == "2026-06-23T22:15:00"
    assert len(document.entries) == 2
    assert document.text == "first thought\n\nsecond thought"


def test_parse_rejects_unsupported_schema_version() -> None:
    payload = {"meta": {"schema_version": "2.0", "session": "s"}, "data": []}

    with pytest.raises(ValueError, match="unsupported voice-note schema_version"):
        parse_voice_note(payload)


def test_parse_requires_meta() -> None:
    with pytest.raises(ValueError, match="missing a 'meta' object"):
        parse_voice_note({"data": []})


def test_markdown_has_frontmatter_and_body() -> None:
    markdown = voice_note_markdown(parse_voice_note(SAMPLE))

    assert markdown.startswith("---\n")
    assert "type: voice-note" in markdown
    assert "session: voice_note_2026_06_23-22_15_00" in markdown
    assert "created: 2026-06-23T22:15:00" in markdown
    assert "tags: [voice-note]" in markdown
    assert "# voice_note_2026_06_23-22_15_00" in markdown
    assert "first thought" in markdown
    assert "second thought" in markdown


def test_load_voice_note_from_file(tmp_path: Path) -> None:
    json_path = tmp_path / "transcribe.json"
    json_path.write_text(json.dumps(SAMPLE), encoding="utf-8")

    document = load_voice_note(json_path)

    assert document.session == "voice_note_2026_06_23-22_15_00"


def test_capture_voice_note_writes_and_indexes(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    json_path = tmp_path / "transcribe.json"
    json_path.write_text(json.dumps(SAMPLE), encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        result = capture_voice_note(conn, notes_dir, json_path)
        row = conn.execute(
            "SELECT title, note_type, tags_json FROM documents WHERE path = ?",
            (str(result.path),),
        ).fetchone()

    assert result.path == notes_dir / "inbox" / "voice_note_2026_06_23-22_15_00.md"
    assert result.indexed_chunks >= 1
    assert row["note_type"] == "voice-note"
    assert row["title"] == "voice_note_2026_06_23-22_15_00"
    assert "voice-note" in json.loads(row["tags_json"])


def test_capture_voice_note_rejects_empty_transcript(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    json_path = tmp_path / "empty.json"
    payload = {"meta": {"schema_version": "1.0", "session": "s"}, "data": []}
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        with pytest.raises(ValueError, match="no text to capture"):
            capture_voice_note(conn, notes_dir, json_path)


def _cli_env(tmp_path: Path, notes_dir: Path) -> dict[str, str]:
    return {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(tmp_path / "assistant.db"),
        "ASSISTANT_REGISTRY_PATH": str(tmp_path / "registry.yaml"),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }


def test_cli_voice_note_ingests_and_indexes(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    json_path = tmp_path / "transcribe.json"
    json_path.write_text(json.dumps(SAMPLE), encoding="utf-8")

    result = runner.invoke(cli.app, ["voice-note", str(json_path)], env=_cli_env(tmp_path, notes_dir))

    assert result.exit_code == 0
    expected_note = notes_dir / "inbox" / "voice_note_2026_06_23-22_15_00.md"
    assert expected_note.exists()
    assert "voice-note" in expected_note.read_text(encoding="utf-8")


def test_cli_voice_note_reports_bad_payload(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    json_path = tmp_path / "bad.json"
    json_path.write_text(json.dumps({"data": []}), encoding="utf-8")

    result = runner.invoke(cli.app, ["voice-note", str(json_path)], env=_cli_env(tmp_path, notes_dir))

    assert result.exit_code == 1
    assert "missing a 'meta' object" in result.output
