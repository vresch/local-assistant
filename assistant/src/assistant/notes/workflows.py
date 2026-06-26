from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from assistant.notes.chunker import HEADING_RE
from assistant.notes.indexer import content_hash, index_file
from assistant.notes.links import (
    LinkRecord,
    backlinks_for_note,
    find_document,
    outbound_links_for_note,
    resolve_note_links,
)
from assistant.notes.metadata import extract_metadata
from assistant.notes.search import search_notes
from assistant.notes.voice_note import load_voice_note, voice_note_markdown


@dataclass(frozen=True)
class CaptureResult:
    path: Path
    indexed_chunks: int


@dataclass(frozen=True)
class DailyResult:
    path: Path
    indexed_chunks: int | None = None


@dataclass(frozen=True)
class RelatedNote:
    path: str
    title: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class NoteSummary:
    path: Path
    title: str
    tags: tuple[str, ...]
    headings: tuple[str, ...]
    highlights: tuple[str, ...]
    links: tuple[LinkRecord, ...]


def capture_note(conn: sqlite3.Connection, notes_dir: Path, text: str) -> CaptureResult:
    if not text.strip():
        raise ValueError("capture text cannot be empty")
    now = datetime.now().astimezone()
    inbox_dir = notes_dir.expanduser() / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_capture_path(inbox_dir, now)
    title = _first_line_title(text) or f"Capture {now.strftime('%Y-%m-%d %H:%M')}"
    body = text.strip()
    markdown = "\n".join(
        [
            "---",
            "type: capture",
            "status: inbox",
            f"created: {now.isoformat(timespec='seconds')}",
            "tags: [inbox]",
            "---",
            f"# {title}",
            "",
            body,
            "",
        ]
    )
    path.write_text(markdown, encoding="utf-8")
    chunks = _index_touched_note(conn, notes_dir, path)
    return CaptureResult(path=path, indexed_chunks=chunks)


def capture_voice_note(conn: sqlite3.Connection, notes_dir: Path, json_path: Path) -> CaptureResult:
    """Ingest a voice-note ``{meta, data}`` transcript file as a Markdown note."""
    document = load_voice_note(json_path)
    if not document.text.strip():
        raise ValueError("voice-note transcript has no text to capture")
    inbox_dir = notes_dir.expanduser() / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / f"{document.session}.md"
    path.write_text(voice_note_markdown(document), encoding="utf-8")
    chunks = _index_touched_note(conn, notes_dir, path)
    return CaptureResult(path=path, indexed_chunks=chunks)


def daily_note_path(notes_dir: Path, today: datetime | None = None) -> Path:
    date = (today or datetime.now().astimezone()).date().isoformat()
    return notes_dir.expanduser() / "daily" / f"{date}.md"


def append_daily_note(conn: sqlite3.Connection, notes_dir: Path, text: str) -> DailyResult:
    if not text.strip():
        raise ValueError("daily text cannot be empty")
    now = datetime.now().astimezone()
    path = daily_note_path(notes_dir, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = "\n".join(
            [
                "---",
                "type: daily",
                f"created: {now.date().isoformat()}",
                "tags: [daily]",
                "---",
                f"# {now.date().isoformat()}",
                "",
            ]
        )
    entry = f"\n## {now.strftime('%H:%M')}\n\n{text.strip()}\n"
    path.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")
    chunks = _index_touched_note(conn, notes_dir, path)
    return DailyResult(path=path, indexed_chunks=chunks)


def get_backlinks(conn: sqlite3.Connection, notes_dir: Path, path: Path) -> list[LinkRecord]:
    return backlinks_for_note(conn, path, notes_dir)


def related_notes(conn: sqlite3.Connection, notes_dir: Path, path: Path, limit: int = 10) -> list[RelatedNote]:
    document = find_document(conn, path, notes_dir)
    if document is None:
        raise KeyError(f"note not indexed: {path}")
    source_id = int(document["id"])
    source_path = str(document["path"])
    source_title = document["title"] or ""
    source_tags = set(json.loads(document["tags_json"] or "[]"))
    scores: dict[int, float] = {}
    reasons: dict[int, set[str]] = {}

    def add(document_id: int, amount: float, reason: str) -> None:
        if document_id == source_id:
            return
        scores[document_id] = scores.get(document_id, 0.0) + amount
        reasons.setdefault(document_id, set()).add(reason)

    for row in conn.execute(
        "SELECT resolved_document_id FROM note_links WHERE source_document_id = ? AND resolved_document_id IS NOT NULL",
        (source_id,),
    ):
        add(int(row["resolved_document_id"]), 5.0, "outbound link")
    for row in conn.execute(
        "SELECT source_document_id FROM note_links WHERE resolved_document_id = ?",
        (source_id,),
    ):
        add(int(row["source_document_id"]), 5.0, "backlink")

    for row in conn.execute("SELECT id, title, tags_json FROM documents WHERE id != ?", (source_id,)):
        shared_tags = source_tags.intersection(json.loads(row["tags_json"] or "[]"))
        if shared_tags:
            add(int(row["id"]), 2.0 * len(shared_tags), "shared tags: " + ", ".join(sorted(shared_tags)))
        shared_title_terms = _terms(source_title).intersection(_terms(row["title"] or ""))
        if shared_title_terms:
            add(int(row["id"]), 1.0, "shared title terms")

    if source_title:
        for result in search_notes(conn, source_title, limit=limit * 3):
            if result.path != source_path:
                row = conn.execute("SELECT id FROM documents WHERE path = ?", (result.path,)).fetchone()
                if row is not None:
                    add(int(row["id"]), max(0.5, min(result.score, 3.0)), "FTS overlap")

    if not scores and source_tags:
        query = " ".join(sorted(source_tags))
        for result in search_notes(conn, query, limit=limit * 3):
            if result.path != source_path:
                row = conn.execute("SELECT id FROM documents WHERE path = ?", (result.path,)).fetchone()
                if row is not None:
                    add(int(row["id"]), 0.5, "FTS overlap")

    rows_by_id = {
        int(row["id"]): row
        for row in conn.execute(
            f"SELECT id, path, title FROM documents WHERE id IN ({','.join('?' for _ in scores)})",
            list(scores),
        )
    } if scores else {}
    related = [
        RelatedNote(
            path=rows_by_id[document_id]["path"],
            title=rows_by_id[document_id]["title"] or "",
            score=score,
            reasons=tuple(sorted(reasons.get(document_id, set()))),
        )
        for document_id, score in scores.items()
        if document_id in rows_by_id
    ]
    related.sort(key=lambda item: (-item.score, item.title.lower(), item.path))
    return related[:limit]


def summarize_note(conn: sqlite3.Connection, notes_dir: Path, path: Path) -> NoteSummary:
    document = find_document(conn, path, notes_dir)
    if document is None:
        raise KeyError(f"note not indexed: {path}")
    note_path = Path(document["path"])
    markdown = note_path.read_text(encoding="utf-8", errors="replace")
    metadata = extract_metadata(markdown, note_path)
    headings = tuple(_extract_headings(markdown))
    highlights = tuple(_extract_highlights(markdown))
    links = tuple(outbound_links_for_note(conn, note_path, notes_dir))
    return NoteSummary(path=note_path, title=metadata.title, tags=metadata.tags, headings=headings, highlights=highlights, links=links)


def _index_touched_note(conn: sqlite3.Connection, notes_dir: Path, path: Path) -> int:
    chunks = index_file(conn, path, content_hash(path))
    resolve_note_links(conn, notes_dir)
    conn.commit()
    return chunks


def _unique_capture_path(inbox_dir: Path, now: datetime) -> Path:
    stem = now.strftime("%Y-%m-%d-%H%M%S")
    path = inbox_dir / f"{stem}.md"
    if not path.exists():
        return path
    for suffix in range(2, 10_000):
        candidate = inbox_dir / f"{stem}-{suffix}.md"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not choose a unique capture path in {inbox_dir}")


def _first_line_title(text: str) -> str | None:
    first = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
    if not first:
        return None
    return first[:80]


def _extract_headings(markdown: str) -> list[str]:
    return [match.group(2).strip() for line in markdown.splitlines() if (match := HEADING_RE.match(line))]


def _extract_highlights(markdown: str, limit: int = 6) -> list[str]:
    highlights: list[str] = []
    paragraph: list[str] = []
    in_frontmatter = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped == "---" and not highlights and not paragraph:
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter or stripped.startswith("#"):
            continue
        if stripped.startswith(("- ", "* ", "> ")):
            highlights.append(stripped.lstrip("-*> ").strip())
            paragraph = []
        elif stripped:
            paragraph.append(stripped)
        elif paragraph:
            highlights.append(" ".join(paragraph))
            paragraph = []
        if len(highlights) >= limit:
            return highlights[:limit]
    if paragraph and len(highlights) < limit:
        highlights.append(" ".join(paragraph))
    return highlights[:limit]


def _terms(value: str) -> set[str]:
    return {term.lower() for term in re.findall(r"[A-Za-z0-9_]+", value) if len(term) > 2}
