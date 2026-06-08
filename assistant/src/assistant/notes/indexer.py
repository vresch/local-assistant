from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from assistant.notes.chunker import chunk_markdown


@dataclass(frozen=True)
class IndexStats:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    chunks: int = 0


def index_notes(conn: sqlite3.Connection, notes_dir: Path) -> IndexStats:
    notes_dir = notes_dir.expanduser()
    if notes_dir.resolve() == Path.home().resolve():
        raise ValueError("Refusing to index the home directory. Set ASSISTANT_NOTES_DIR to a notes subdirectory.")

    stats = IndexStats()
    for path in sorted(notes_dir.rglob("*.md")):
        if not path.is_file():
            continue
        stats = _add(stats, scanned=1)
        file_hash = content_hash(path)
        existing = conn.execute(
            "SELECT id, content_hash FROM documents WHERE path = ?",
            (str(path),),
        ).fetchone()
        if existing and existing["content_hash"] == file_hash:
            stats = _add(stats, skipped=1)
            continue
        chunk_count = index_file(conn, path, file_hash)
        stats = _add(stats, indexed=1, chunks=chunk_count)
    conn.commit()
    return stats


def index_file(conn: sqlite3.Connection, path: Path, file_hash: str | None = None) -> int:
    file_hash = file_hash or content_hash(path)
    markdown = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(markdown)
    mtime_ns = path.stat().st_mtime_ns

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
            SET content_hash = ?, mtime_ns = ?, indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (file_hash, mtime_ns, document_id),
        )
    else:
        cursor = conn.execute(
            "INSERT INTO documents(path, content_hash, mtime_ns) VALUES (?, ?, ?)",
            (str(path), file_hash, mtime_ns),
        )
        document_id = int(cursor.lastrowid)

    for index, chunk in enumerate(chunks):
        cursor = conn.execute(
            """
            INSERT INTO chunks(document_id, chunk_index, heading, content)
            VALUES (?, ?, ?, ?)
            """,
            (document_id, index, chunk.heading, chunk.content),
        )
        chunk_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO chunks_fts(chunk_id, path, heading, content)
            VALUES (?, ?, ?, ?)
            """,
            (chunk_id, str(path), chunk.heading or "", chunk.content),
        )
    return len(chunks)


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _add(stats: IndexStats, scanned: int = 0, indexed: int = 0, skipped: int = 0, chunks: int = 0) -> IndexStats:
    return IndexStats(
        scanned=stats.scanned + scanned,
        indexed=stats.indexed + indexed,
        skipped=stats.skipped + skipped,
        chunks=stats.chunks + chunks,
    )
