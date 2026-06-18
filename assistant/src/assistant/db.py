from __future__ import annotations

import sqlite3
from pathlib import Path


TableCounts = dict[str, int]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    initialize(conn)
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            heading TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(document_id, chunk_index)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            path UNINDEXED,
            heading,
            content
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            command TEXT NOT NULL,
            input TEXT,
            route TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS run_events (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 3,
            source TEXT,
            related_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS note_links (
            id INTEGER PRIMARY KEY,
            source_document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            target_raw TEXT NOT NULL,
            target_path TEXT,
            target_heading TEXT,
            alias TEXT,
            link_type TEXT NOT NULL,
            resolved_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL
        );
        """
    )
    _ensure_columns(
        conn,
        "documents",
        {
            "title": "TEXT",
            "file_size": "INTEGER NOT NULL DEFAULT 0",
            "tags_json": "TEXT NOT NULL DEFAULT '[]'",
            "aliases_json": "TEXT NOT NULL DEFAULT '[]'",
            "note_type": "TEXT",
            "note_status": "TEXT",
            "created": "TEXT",
            "updated": "TEXT",
            "links_indexed": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_columns(
        conn,
        "chunks",
        {
            "heading_path": "TEXT",
            "token_count": "INTEGER NOT NULL DEFAULT 0",
            "start_line": "INTEGER NOT NULL DEFAULT 1",
            "end_line": "INTEGER NOT NULL DEFAULT 1",
        },
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_note_links_source ON note_links(source_document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_note_links_resolved ON note_links(resolved_document_id)")
    conn.commit()


def cleanup_database(conn: sqlite3.Connection, include_logs: bool = False) -> TableCounts:
    counts = {
        "documents": _count(conn, "documents"),
        "chunks": _count(conn, "chunks"),
        "chunks_fts": _count(conn, "chunks_fts"),
        "runs": _count(conn, "runs") if include_logs else 0,
        "run_events": _count(conn, "run_events") if include_logs else 0,
    }
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM documents")
    if include_logs:
        conn.execute("DELETE FROM run_events")
        conn.execute("DELETE FROM runs")
    conn.commit()
    conn.execute("VACUUM")
    return counts


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
