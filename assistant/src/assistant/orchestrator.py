from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from assistant.notes.search import SearchResult, search_notes, to_fts_query


@dataclass(frozen=True)
class AnswerResult:
    text: str
    results: list[SearchResult]
    normalized_query: str
    used_local_model: bool
    summary: str
    sources: list[str]


def answer_question(conn: sqlite3.Connection, question: str, limit: int = 5, use_model: bool = True) -> AnswerResult:
    normalized_query = to_fts_query(question)
    results = search_notes(conn, question, limit=limit)
    if not results:
        text = "\n".join(
            [
                "Answer:",
                "I could not find relevant notes for that question.",
                "",
                "Sources:",
                "None",
                "",
                "Next action:",
                "Try rephrasing the question or run `assistant index` if your notes changed.",
            ]
        )
        return AnswerResult(
            text=text,
            results=[],
            normalized_query=normalized_query,
            used_local_model=False,
            summary=f"no relevant chunks found; local_model_requested={use_model}",
            sources=[],
        )

    used_local_model = False  # Phase 1 has no local provider integration yet.
    sources = [_source_reference(result) for result in results]
    supporting_notes = [_plain_excerpt(result.content) for result in results]
    direct_answer = _extractive_answer(results)

    lines = [
        "Answer:",
        direct_answer,
        "",
        "Supporting notes:",
        *[f"- {note}" for note in supporting_notes],
        "",
        "Sources:",
        *[f"{index}. {source}" for index, source in enumerate(sources, start=1)],
        "",
        "Next action:",
        "Review the cited notes for more context.",
    ]
    return AnswerResult(
        text="\n".join(lines),
        results=results,
        normalized_query=normalized_query,
        used_local_model=used_local_model,
        summary=f"answered from {len(results)} local chunks; local_model_requested={use_model}",
        sources=sources,
    )


def _extractive_answer(results: list[SearchResult]) -> str:
    top_excerpt = _plain_excerpt(results[0].content, max_chars=220)
    return f"The strongest matching note says: {top_excerpt}"


def _source_reference(result: SearchResult) -> str:
    parts = [result.path]
    if result.heading:
        parts.append(result.heading)
    parts.append(f"chunk {result.chunk_index + 1}")
    return " - ".join(parts)


def _plain_excerpt(text: str, max_chars: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
