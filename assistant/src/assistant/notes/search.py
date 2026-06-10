from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
import json


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class SearchResult:
    chunk_id: int
    path: str
    title: str
    heading: str | None
    heading_path: str | None
    chunk_index: int
    snippet: str
    content: str
    rank: float
    score: float
    mtime_ns: int
    modified_at: str
    tags: tuple[str, ...]
    token_count: int
    start_line: int
    end_line: int


def search_notes(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 5,
    tag: str | None = None,
    path: str | None = None,
    since: str | None = None,
) -> list[SearchResult]:
    fts_query = to_fts_query(query)
    if not fts_query:
        return []
    filters = ["chunks_fts MATCH ?"]
    params: list[object] = [fts_query]
    if tag:
        filters.append("documents.tags_json LIKE ?")
        params.append(f'%"{tag.lower()}"%')
    if path:
        filters.append("documents.path LIKE ?")
        params.append(f"%{path}%")
    since_ns = _since_to_ns(since)
    if since_ns is not None:
        filters.append("documents.mtime_ns >= ?")
        params.append(since_ns)
    params.append(max(limit * 5, limit))
    rows = conn.execute(
        f"""
        SELECT
            chunks.id AS chunk_id,
            chunks_fts.path,
            documents.title,
            documents.tags_json,
            documents.mtime_ns,
            NULLIF(chunks_fts.heading, '') AS heading,
            chunks.heading AS plain_heading,
            chunks.heading_path,
            chunks.chunk_index,
            chunks.token_count,
            chunks.start_line,
            chunks.end_line,
            snippet(chunks_fts, 3, '[', ']', ' ... ', 24) AS snippet,
            chunks.content,
            bm25(chunks_fts) AS rank
        FROM chunks_fts
        JOIN chunks ON chunks.id = chunks_fts.chunk_id
        JOIN documents ON documents.id = chunks.document_id
        WHERE {" AND ".join(filters)}
        ORDER BY rank
        LIMIT ?
        """,
        params,
    ).fetchall()
    results = [
        _result_from_row(row, query)
        for row in rows
    ]
    results.sort(key=lambda result: (-result.score, result.rank, result.chunk_id))
    return results[:limit]


def get_chunk(conn: sqlite3.Connection, chunk_id: int) -> SearchResult | None:
    row = conn.execute(
        """
        SELECT
            chunks.id AS chunk_id,
            documents.path,
            documents.title,
            documents.tags_json,
            documents.mtime_ns,
            chunks.heading AS plain_heading,
            chunks.heading_path,
            chunks.chunk_index,
            chunks.token_count,
            chunks.start_line,
            chunks.end_line,
            chunks.content,
            0.0 AS rank
        FROM chunks
        JOIN documents ON documents.id = chunks.document_id
        WHERE chunks.id = ?
        """,
        (chunk_id,),
    ).fetchone()
    if row is None:
        return None
    return _result_from_row(row, "")


def _result_from_row(row: sqlite3.Row, query: str) -> SearchResult:
    tags = tuple(json.loads(row["tags_json"] or "[]"))
    rank = float(row["rank"])
    score = _score(row, query, rank)
    return SearchResult(
            chunk_id=int(row["chunk_id"]),
            path=row["path"],
            title=row["title"] or "",
            heading=row["plain_heading"],
            heading_path=row["heading_path"],
            chunk_index=int(row["chunk_index"]),
            snippet=row["snippet"] if "snippet" in row.keys() else "",
            content=row["content"],
            rank=rank,
            score=score,
            mtime_ns=int(row["mtime_ns"]),
            modified_at=_mtime_to_iso(int(row["mtime_ns"])),
            tags=tags,
            token_count=int(row["token_count"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
    )


def _score(row: sqlite3.Row, query: str, rank: float) -> float:
    score = -rank
    normalized_query = query.strip().lower()
    title = (row["title"] or "").lower()
    heading_text = " ".join(part for part in [row["plain_heading"], row["heading_path"]] if part).lower()
    terms = [term.lower() for term in TOKEN_RE.findall(query)]
    if normalized_query and normalized_query == title:
        score += 5.0
    if terms and any(term in heading_text for term in terms):
        score += 2.0
    score += min(max(int(row["mtime_ns"]), 0) / 10**18, 2.0) * 0.001
    return score


def to_fts_query(query: str) -> str:
    terms = TOKEN_RE.findall(query)
    return " OR ".join(f"{term}*" for term in terms)


def _since_to_ns(since: str | None) -> int | None:
    if not since:
        return None
    parsed = datetime.fromisoformat(since)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return min(int(parsed.timestamp() * 1_000_000_000), 9_223_372_036_854_775_807)


def _mtime_to_iso(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()
