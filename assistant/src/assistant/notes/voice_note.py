"""Read voice-note transcript JSON into indexable Markdown notes.

The producer (the `voice-note` tool in the python-tools workspace) writes a
transcript file shaped as a ``{meta, data}`` envelope::

    {
      "meta": {
        "schema_version": "1.0",
        "tool": "voice-note",
        "session": "voice_note_2026_06_23-22_15_00",
        "created_at": "2026-06-23T22:15:00"
      },
      "data": [
        {
          "audio": "audio_2026_06_23-22_15_30.wav",
          "text": "hello world",
          "created_at": "2026-06-23T22:15:30"
        }
      ]
    }

This module is the consumer side of that contract: it parses the envelope and
renders a frontmatter Markdown note that the standard indexing pipeline can
ingest. It is pure (no database access) so it is cheap to test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})


@dataclass(frozen=True)
class VoiceNoteEntry:
    text: str
    audio: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class VoiceNoteDocument:
    schema_version: str
    session: str
    tool: str = "voice-note"
    created_at: str | None = None
    entries: tuple[VoiceNoteEntry, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "\n\n".join(entry.text for entry in self.entries if entry.text.strip())


def load_voice_note(json_path: Path) -> VoiceNoteDocument:
    """Parse a ``{meta, data}`` transcript file into a VoiceNoteDocument."""
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return parse_voice_note(payload)


def parse_voice_note(payload: object) -> VoiceNoteDocument:
    if not isinstance(payload, dict):
        raise ValueError("voice-note payload must be a JSON object")

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("voice-note payload is missing a 'meta' object")

    schema_version = _as_text(meta.get("schema_version"))
    if schema_version is None:
        raise ValueError("voice-note meta is missing 'schema_version'")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise ValueError(
            f"unsupported voice-note schema_version {schema_version!r}; supported: {supported}"
        )

    session = _as_text(meta.get("session"))
    if session is None:
        raise ValueError("voice-note meta is missing 'session'")

    raw_data = payload.get("data", [])
    if not isinstance(raw_data, list):
        raise ValueError("voice-note 'data' must be a list")

    entries = tuple(_parse_entry(item) for item in raw_data)

    return VoiceNoteDocument(
        schema_version=schema_version,
        session=session,
        tool=_as_text(meta.get("tool")) or "voice-note",
        created_at=_as_text(meta.get("created_at")),
        entries=entries,
    )


def voice_note_markdown(document: VoiceNoteDocument) -> str:
    """Render a frontmatter Markdown note for the indexing pipeline."""
    tags = ["voice-note"]
    if document.tool and document.tool != "voice-note":
        tags.append(document.tool)

    frontmatter = [
        "---",
        "type: voice-note",
        "status: inbox",
        f"session: {document.session}",
    ]
    if document.created_at:
        frontmatter.append(f"created: {document.created_at}")
    frontmatter.append(f"tags: [{', '.join(tags)}]")
    frontmatter.append("---")

    body = document.text or "_(empty transcript)_"
    return "\n".join([*frontmatter, f"# {document.session}", "", body, ""])


def _parse_entry(item: object) -> VoiceNoteEntry:
    if not isinstance(item, dict):
        raise ValueError("each voice-note data entry must be a JSON object")
    text = _as_text(item.get("text"))
    if text is None:
        raise ValueError("voice-note data entry is missing 'text'")
    return VoiceNoteEntry(
        text=text,
        audio=_as_text(item.get("audio")),
        created_at=_as_text(item.get("created_at")),
    )


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
