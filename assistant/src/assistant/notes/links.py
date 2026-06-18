from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class NoteLink:
    target_raw: str
    target_path: str | None
    target_heading: str | None
    alias: str | None
    link_type: str


@dataclass(frozen=True)
class LinkRecord:
    source_path: str
    source_title: str
    target_raw: str
    target_path: str | None
    target_heading: str | None
    alias: str | None
    link_type: str
    resolved_path: str | None
    resolved_title: str | None


def extract_links(markdown: str) -> list[NoteLink]:
    links: list[NoteLink] = []
    for match in WIKILINK_RE.finditer(markdown):
        target = match.group(1).strip()
        heading = _clean_optional(match.group(2))
        alias = _clean_optional(match.group(3))
        if target:
            links.append(
                NoteLink(
                    target_raw=match.group(0),
                    target_path=target,
                    target_heading=heading,
                    alias=alias,
                    link_type="wikilink",
                )
            )
    for match in MARKDOWN_LINK_RE.finditer(markdown):
        label = match.group(1).strip()
        raw_url = match.group(2).strip()
        parsed = _parse_markdown_target(raw_url)
        if parsed is None:
            continue
        target, heading = parsed
        links.append(
            NoteLink(
                target_raw=raw_url,
                target_path=target,
                target_heading=heading,
                alias=label or None,
                link_type="markdown",
            )
        )
    return links


def insert_links(conn: sqlite3.Connection, document_id: int, links: list[NoteLink]) -> None:
    conn.execute("DELETE FROM note_links WHERE source_document_id = ?", (document_id,))
    conn.executemany(
        """
        INSERT INTO note_links(
            source_document_id,
            target_raw,
            target_path,
            target_heading,
            alias,
            link_type
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                document_id,
                link.target_raw,
                link.target_path,
                link.target_heading,
                link.alias,
                link.link_type,
            )
            for link in links
        ],
    )


def resolve_note_links(conn: sqlite3.Connection, notes_dir: Path) -> None:
    documents = conn.execute("SELECT id, path, title, aliases_json FROM documents").fetchall()
    by_key: dict[str, int] = {}
    for row in documents:
        path = Path(row["path"])
        keys = {
            str(path),
            path.name,
            path.stem,
            str(path.with_suffix("")),
            row["title"] or "",
        }
        try:
            relative = path.resolve().relative_to(notes_dir.expanduser().resolve())
            keys.update({str(relative), str(relative.with_suffix("")), relative.name, relative.stem})
        except (OSError, ValueError):
            pass
        for alias in json.loads(row["aliases_json"] or "[]"):
            keys.add(str(alias))
        for key in keys:
            normalized = _normalize_key(key)
            if normalized and normalized not in by_key:
                by_key[normalized] = int(row["id"])

    links = conn.execute("SELECT id, target_path FROM note_links").fetchall()
    for link in links:
        target = link["target_path"] or ""
        candidates = _target_candidates(target)
        resolved = next((by_key[candidate] for candidate in candidates if candidate in by_key), None)
        conn.execute("UPDATE note_links SET resolved_document_id = ? WHERE id = ?", (resolved, int(link["id"])))


def backlinks_for_note(conn: sqlite3.Connection, path: Path, notes_dir: Path) -> list[LinkRecord]:
    document = find_document(conn, path, notes_dir)
    if document is None:
        raise KeyError(f"note not indexed: {path}")
    rows = conn.execute(
        """
        SELECT
            source.path AS source_path,
            source.title AS source_title,
            target.path AS resolved_path,
            target.title AS resolved_title,
            note_links.target_raw,
            note_links.target_path,
            note_links.target_heading,
            note_links.alias,
            note_links.link_type
        FROM note_links
        JOIN documents AS source ON source.id = note_links.source_document_id
        LEFT JOIN documents AS target ON target.id = note_links.resolved_document_id
        WHERE note_links.resolved_document_id = ?
        ORDER BY source.title COLLATE NOCASE, source.path
        """,
        (int(document["id"]),),
    ).fetchall()
    return [_link_record(row) for row in rows]


def outbound_links_for_note(conn: sqlite3.Connection, path: Path, notes_dir: Path) -> list[LinkRecord]:
    document = find_document(conn, path, notes_dir)
    if document is None:
        raise KeyError(f"note not indexed: {path}")
    rows = conn.execute(
        """
        SELECT
            source.path AS source_path,
            source.title AS source_title,
            target.path AS resolved_path,
            target.title AS resolved_title,
            note_links.target_raw,
            note_links.target_path,
            note_links.target_heading,
            note_links.alias,
            note_links.link_type
        FROM note_links
        JOIN documents AS source ON source.id = note_links.source_document_id
        LEFT JOIN documents AS target ON target.id = note_links.resolved_document_id
        WHERE note_links.source_document_id = ?
        ORDER BY note_links.id
        """,
        (int(document["id"]),),
    ).fetchall()
    return [_link_record(row) for row in rows]


def find_document(conn: sqlite3.Connection, path: Path, notes_dir: Path) -> sqlite3.Row | None:
    candidates = [path.expanduser()]
    if not path.expanduser().is_absolute():
        candidates.append(notes_dir.expanduser() / path)
    for candidate in candidates:
        row = conn.execute("SELECT * FROM documents WHERE path = ?", (str(candidate),)).fetchone()
        if row is not None:
            return row
    key = _normalize_key(path.as_posix())
    stem_key = _normalize_key(path.stem)
    for row in conn.execute("SELECT * FROM documents"):
        document_path = Path(row["path"])
        keys = {_normalize_key(document_path.name), _normalize_key(document_path.stem), _normalize_key(row["title"] or "")}
        try:
            relative = document_path.resolve().relative_to(notes_dir.expanduser().resolve())
            keys.update({_normalize_key(str(relative)), _normalize_key(str(relative.with_suffix("")))})
        except (OSError, ValueError):
            pass
        if key in keys or stem_key in keys:
            return row
    return None


def _parse_markdown_target(raw_url: str) -> tuple[str, str | None] | None:
    parsed = urlparse(raw_url)
    if parsed.scheme or parsed.netloc or raw_url.startswith("#"):
        return None
    path = unquote(parsed.path).strip()
    if not path:
        return None
    heading = _clean_optional(parsed.fragment)
    return path, heading


def _target_candidates(target: str) -> list[str]:
    path = Path(target)
    values = {target, path.name, path.stem, str(path.with_suffix(""))}
    if not path.suffix:
        values.add(f"{target}.md")
    return [_normalize_key(value) for value in values if _normalize_key(value)]


def _normalize_key(value: str) -> str:
    return value.strip().strip("/").removesuffix(".md").lower()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _link_record(row: sqlite3.Row) -> LinkRecord:
    return LinkRecord(
        source_path=row["source_path"],
        source_title=row["source_title"] or "",
        target_raw=row["target_raw"],
        target_path=row["target_path"],
        target_heading=row["target_heading"],
        alias=row["alias"],
        link_type=row["link_type"],
        resolved_path=row["resolved_path"],
        resolved_title=row["resolved_title"],
    )
