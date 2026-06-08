from __future__ import annotations

import sqlite3

from assistant.notes.search import SearchResult, search_notes


def answer_question(conn: sqlite3.Connection, question: str, limit: int = 3) -> tuple[str, list[SearchResult]]:
    results = search_notes(conn, question, limit=limit)
    if not results:
        return "I could not find relevant notes for that question.", []

    lines = ["Based on your notes:"]
    for result in results:
        source = f"{result.path}"
        if result.heading:
            source = f"{source}#{result.heading}"
        excerpt = _plain_excerpt(result.content)
        lines.append(f"- {excerpt} ({source})")

    return "\n".join(lines), results


def _plain_excerpt(text: str, max_chars: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
