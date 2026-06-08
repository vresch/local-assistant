from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class SearchResult:
    path: str
    heading: str | None
    chunk_index: int
    snippet: str
    content: str
    rank: float


def search_notes(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[SearchResult]:
    fts_query = to_fts_query(query)
    if not fts_query:
        return []
    rows = conn.execute(
        """
        SELECT
            chunks_fts.path,
            NULLIF(chunks_fts.heading, '') AS heading,
            chunks.chunk_index,
            snippet(chunks_fts, 3, '[', ']', ' ... ', 24) AS snippet,
            chunks.content,
            bm25(chunks_fts) AS rank
        FROM chunks_fts
        JOIN chunks ON chunks.id = chunks_fts.chunk_id
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (fts_query, limit),
    ).fetchall()
    return [
        SearchResult(
            path=row["path"],
            heading=row["heading"],
            chunk_index=int(row["chunk_index"]),
            snippet=row["snippet"],
            content=row["content"],
            rank=float(row["rank"]),
        )
        for row in rows
    ]


def to_fts_query(query: str) -> str:
    terms = TOKEN_RE.findall(query)
    return " OR ".join(f"{term}*" for term in terms)
