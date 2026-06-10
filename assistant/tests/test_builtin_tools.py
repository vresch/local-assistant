from __future__ import annotations

from pathlib import Path

import pytest

from assistant.tools.note_append_daily import _parse_day
from assistant.tools.note_create import _confined_note_path


def test_note_create_confines_paths_to_notes_dir(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    assert _confined_note_path(notes_dir.resolve(), "inbox/idea") == notes_dir.resolve() / "inbox" / "idea.md"

    with pytest.raises(SystemExit, match="relative"):
        _confined_note_path(notes_dir.resolve(), str(tmp_path / "outside"))
    with pytest.raises(SystemExit, match="inside"):
        _confined_note_path(notes_dir.resolve(), "../outside")


def test_note_append_daily_rejects_non_iso_dates() -> None:
    assert _parse_day("2026-06-10").isoformat() == "2026-06-10"
    with pytest.raises(SystemExit, match="YYYY-MM-DD"):
        _parse_day("../outside")
