from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from assistant.notes.search import SearchResult, get_chunk, search_notes, to_fts_query
from assistant.providers.local import LocalModelProvider


@dataclass(frozen=True)
class AnswerResult:
    text: str
    answer: str
    results: list[SearchResult]
    normalized_query: str
    llm: str
    used_local_model: bool
    local_model: str | None
    summary: str
    sources: list[str]
    prompt_chunk_count: int
    prompt_char_count: int


def answer_question(
    conn: sqlite3.Connection,
    question: str,
    limit: int = 5,
    local_provider: LocalModelProvider | None = None,
    use_model: bool = True,
    chunk_ids: list[int] | None = None,
) -> AnswerResult:
    normalized_query = to_fts_query(question)
    results = _selected_chunks(conn, chunk_ids) if chunk_ids is not None else search_notes(conn, question, limit=limit)
    if not results:
        model_requested = use_model and local_provider is not None
        direct_answer = "I could not find relevant notes for that question."
        text = "\n".join(
            [
                "Answer:",
                direct_answer,
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
            answer=direct_answer,
            results=[],
            normalized_query=normalized_query,
            llm="none",
            used_local_model=False,
            local_model=None,
            summary=f"no relevant chunks found; local_model_requested={model_requested}",
            sources=[],
            prompt_chunk_count=0,
            prompt_char_count=0,
        )

    sources = _grouped_sources(results)
    supporting_notes = [_supporting_note(result) for result in results]
    model_requested = use_model and local_provider is not None
    prompt = _build_prompt(question, results) if model_requested else ""
    if model_requested and local_provider is not None:
        response = local_provider.complete(prompt)
        direct_answer = response.text or _extractive_answer(results)
        used_local_model = bool(response.text)
        llm = response.provider
        local_model = response.model
    else:
        direct_answer = _extractive_answer(results)
        used_local_model = False
        llm = "none"
        local_model = None

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
        answer=direct_answer,
        results=results,
        normalized_query=normalized_query,
        llm=llm,
        used_local_model=used_local_model,
        local_model=local_model,
        summary=(
            f"answered from {len(results)} local chunks; "
            f"llm={llm}; "
            f"model={local_model or 'none'}; "
            f"local_model_requested={model_requested}; "
            f"local_model_used={used_local_model}"
        ),
        sources=sources,
        prompt_chunk_count=len(results),
        prompt_char_count=len(prompt),
    )


def _build_prompt(question: str, results: list[SearchResult]) -> str:
    context_blocks = []
    for index, result in enumerate(results, start=1):
        heading = f" ({result.heading_path or result.heading})" if result.heading_path or result.heading else ""
        context_blocks.append(
            "\n".join(
                [
                    f"[{index}] {result.title}: {result.path}{heading}, chunk {result.chunk_index + 1}",
                    result.content,
                ]
            )
        )
    context = "\n\n".join(context_blocks)
    return "\n".join(
        [
            "You answer questions using only the local notes below.",
            "If the notes do not contain the answer, say that the notes do not say.",
            "Keep the answer concise and do not invent details.",
            "",
            "Local notes:",
            context,
            "",
            f"Question: {question}",
            "Answer:",
        ]
    )


def _selected_chunks(conn: sqlite3.Connection, chunk_ids: list[int] | None) -> list[SearchResult]:
    if not chunk_ids:
        return []
    results: list[SearchResult] = []
    seen: set[int] = set()
    for chunk_id in chunk_ids:
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        result = get_chunk(conn, chunk_id)
        if result is not None:
            results.append(result)
    return results


def _extractive_answer(results: list[SearchResult]) -> str:
    top_excerpt = _source_excerpt(results[0])
    if len(results) == 1:
        return f"The strongest matching note says: {top_excerpt}"

    lines = [
        f"The strongest matching note says: {top_excerpt}",
        "",
        "Other matching notes suggest:",
    ]
    seen = {top_excerpt.lower()}
    appended = False
    for result in results[1:]:
        excerpt = _source_excerpt(result)
        normalized = excerpt.lower()
        if not excerpt or normalized in seen:
            continue
        seen.add(normalized)
        appended = True
        lines.append(f"- {excerpt}")

    if not appended:
        return lines[0]
    return "\n".join(lines)


def _source_reference(result: SearchResult) -> str:
    parts = [result.path]
    if result.heading_path or result.heading:
        parts.append(result.heading_path or result.heading or "")
    parts.append(f"chunk {result.chunk_index + 1}")
    return " - ".join(parts)


def _grouped_sources(results: list[SearchResult]) -> list[str]:
    grouped: dict[str, list[SearchResult]] = {}
    for result in results:
        grouped.setdefault(result.path, []).append(result)

    sources: list[str] = []
    for path, path_results in grouped.items():
        title = path_results[0].title
        chunks = ", ".join(f"chunk {result.chunk_index + 1}" for result in path_results)
        headings = [
            heading
            for heading in (result.heading_path or result.heading for result in path_results)
            if heading is not None
        ]
        heading_text = f" - {'; '.join(dict.fromkeys(headings))}" if headings else ""
        sources.append(f"{path} - {title}{heading_text} - {chunks}")
    return sources


def _supporting_note(result: SearchResult) -> str:
    label = result.title
    if result.heading_path or result.heading:
        label = f"{label} > {result.heading_path or result.heading}"
    return f"{label}: {_plain_excerpt(result.content)}"


def _plain_excerpt(text: str, max_chars: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _source_excerpt(result: SearchResult, max_chars: int = 220) -> str:
    text = _strip_leading_heading(result.content)
    excerpt = _plain_excerpt(text, max_chars=max_chars)
    if result.heading:
        heading = result.heading.strip()
        if excerpt and not excerpt.lower().startswith(heading.lower()):
            label = result.heading_path or heading
            return f"{label}: {excerpt}"
    return excerpt


def _strip_leading_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and re.match(r"^#{1,6}\s+", lines[0]):
        lines = lines[1:]
    stripped = "\n".join(lines).strip()
    return stripped or text.strip()
