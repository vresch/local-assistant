from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from assistant.notes.chunker import chunk_markdown
from assistant.notes.metadata import extract_metadata


@dataclass(frozen=True)
class IndexStats:
    scanned: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0
    chunks: int = 0

    @property
    def indexed(self) -> int:
        return self.new + self.updated


def index_notes(conn: sqlite3.Connection, notes_dir: Path) -> IndexStats:
    notes_dir = notes_dir.expanduser()
    if notes_dir.resolve() == Path.home().resolve():
        raise ValueError("Refusing to index the home directory. Set ASSISTANT_NOTES_DIR to a notes subdirectory.")

    stats = IndexStats()
    current_paths: set[str] = set()
    for path in sorted(notes_dir.rglob("*.md")):
        if not path.is_file():
            continue
        current_paths.add(str(path))
        stats = _add(stats, scanned=1)
        file_size = path.stat().st_size
        file_hash = content_hash(path)
        existing = conn.execute(
            """
            SELECT
                documents.id,
                documents.content_hash,
                documents.title,
                documents.file_size,
                documents.tags_json,
                COUNT(chunks.id) AS chunk_count,
                COALESCE(MIN(chunks.token_count), 0) AS min_token_count
            FROM documents
            LEFT JOIN chunks ON chunks.document_id = documents.id
            WHERE documents.path = ?
            GROUP BY documents.id
            """,
            (str(path),),
        ).fetchone()
        if existing and existing["content_hash"] == file_hash and not _needs_metadata_backfill(existing, file_size):
            stats = _add(stats, skipped=1)
            continue
        try:
            chunk_count = index_file(conn, path, file_hash)
        except Exception:
            stats = _add(stats, failed=1)
            continue
        stats = _add(stats, updated=1 if existing else 0, new=0 if existing else 1, chunks=chunk_count)
    removed = cleanup_stale_documents(conn, notes_dir, current_paths)
    stats = _add(stats, removed=removed)
    conn.commit()
    return stats


def _needs_metadata_backfill(row: sqlite3.Row, file_size: int) -> bool:
    return (
        row["title"] is None
        or row["tags_json"] is None
        or int(row["file_size"] or 0) != file_size
        or (int(row["chunk_count"] or 0) > 0 and int(row["min_token_count"] or 0) == 0)
    )


def index_file(conn: sqlite3.Connection, path: Path, file_hash: str | None = None) -> int:
    file_hash = file_hash or content_hash(path)
    markdown = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(markdown)
    stat = path.stat()
    metadata = extract_metadata(markdown, path)
    mtime_ns = stat.st_mtime_ns
    file_size = stat.st_size
    tags_json = json.dumps(list(metadata.tags), sort_keys=True)

    existing = conn.execute("SELECT id FROM documents WHERE path = ?", (str(path),)).fetchone()
    if existing:
        document_id = int(existing["id"])
        chunk_ids = [row["id"] for row in conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            conn.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})", chunk_ids)
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute(
            """
            UPDATE documents
            SET content_hash = ?,
                mtime_ns = ?,
                title = ?,
                file_size = ?,
                tags_json = ?,
                indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (file_hash, mtime_ns, metadata.title, file_size, tags_json, document_id),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO documents(path, content_hash, mtime_ns, title, file_size, tags_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(path), file_hash, mtime_ns, metadata.title, file_size, tags_json),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("document insert did not return an id")
        document_id = int(cursor.lastrowid)

    for index, chunk in enumerate(chunks):
        cursor = conn.execute(
            """
            INSERT INTO chunks(
                document_id,
                chunk_index,
                heading,
                content,
                heading_path,
                token_count,
                start_line,
                end_line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                index,
                chunk.heading,
                chunk.content,
                chunk.heading_path,
                chunk.token_count,
                chunk.start_line,
                chunk.end_line,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("chunk insert did not return an id")
        chunk_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO chunks_fts(chunk_id, path, heading, content)
            VALUES (?, ?, ?, ?)
            """,
            (chunk_id, str(path), chunk.heading_path or chunk.heading or "", chunk.content),
        )
    return len(chunks)


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cleanup_stale_documents(conn: sqlite3.Connection, notes_dir: Path, current_paths: set[str]) -> int:
    """Remove indexed documents under notes_dir whose Markdown files no longer exist."""
    notes_root = notes_dir.resolve()
    stale_document_ids: list[int] = []
    for row in conn.execute("SELECT id, path FROM documents"):
        document_path = Path(row["path"]).expanduser()
        try:
            resolved_path = document_path.resolve()
        except OSError:
            resolved_path = document_path.absolute()
        if resolved_path.is_relative_to(notes_root) and row["path"] not in current_paths:
            stale_document_ids.append(int(row["id"]))

    for document_id in stale_document_ids:
        chunk_ids = [row["id"] for row in conn.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            conn.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})", chunk_ids)
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return len(stale_document_ids)


def _add(
    stats: IndexStats,
    scanned: int = 0,
    new: int = 0,
    updated: int = 0,
    skipped: int = 0,
    removed: int = 0,
    failed: int = 0,
    chunks: int = 0,
) -> IndexStats:
    return IndexStats(
        scanned=stats.scanned + scanned,
        new=stats.new + new,
        updated=stats.updated + updated,
        skipped=stats.skipped + skipped,
        removed=stats.removed + removed,
        failed=stats.failed + failed,
        chunks=stats.chunks + chunks,
    )
