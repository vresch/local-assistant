from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from assistant.notes.search import SearchResult, search_notes, to_fts_query


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


def answer_question(
    conn: sqlite3.Connection,
    question: str,
    limit: int = 5,
    use_model: bool = True,
    model_path: Path | None = None,
    context_size: int = 4096,
    max_tokens: int = 256,
    temperature: float = 0.2,
) -> AnswerResult:
    normalized_query = to_fts_query(question)
    results = search_notes(conn, question, limit=limit)
    if not results:
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
            summary=f"no relevant chunks found; local_model_requested={use_model}",
            sources=[],
        )

    sources = [_source_reference(result) for result in results]
    supporting_notes = [_plain_excerpt(result.content) for result in results]
    used_local_model = use_model and model_path is not None
    llm = "llama-cpp-python" if used_local_model else "none"
    local_model = str(model_path) if used_local_model else None
    direct_answer = (
        _llama_answer(
            question,
            results,
            model_path,
            context_size=context_size,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if used_local_model
        else _extractive_answer(results)
    )

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
            f"local_model_requested={use_model}; "
            f"local_model_used={used_local_model}"
        ),
        sources=sources,
    )


def _llama_answer(
    question: str,
    results: list[SearchResult],
    model_path: Path | None,
    context_size: int,
    max_tokens: int,
    temperature: float,
) -> str:
    if model_path is None:
        raise ValueError("model_path is required when local model synthesis is enabled")
    if not model_path.is_file():
        raise FileNotFoundError(f"llama model not found: {model_path}")

    llama_cls = _load_llama_class()
    model = llama_cls(model_path=str(model_path), n_ctx=context_size, verbose=False)
    response = model(
        _build_prompt(question, results),
        max_tokens=max_tokens,
        temperature=temperature,
        stop=["\nQuestion:", "\nSupporting notes:", "\nSources:", "\nNext action:"],
    )
    text = _clean_generated_answer(_extract_llama_text(response))
    return text or _extractive_answer(results)


def _load_llama_class() -> Any:
    try:
        return import_module("llama_cpp").Llama
    except ImportError as exc:
        raise RuntimeError(
            "llama-cpp-python is required for local model answers. "
            "Install dependencies with `uv sync` or run `assistant ask --no-model`."
        ) from exc


def _build_prompt(question: str, results: list[SearchResult]) -> str:
    context_blocks = []
    for index, result in enumerate(results, start=1):
        heading = f" ({result.heading})" if result.heading else ""
        context_blocks.append(
            "\n".join(
                [
                    f"[{index}] {result.path}{heading}, chunk {result.chunk_index + 1}",
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


def _extract_llama_text(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    return text.strip()
    return str(response).strip()


def _clean_generated_answer(text: str) -> str:
    cleaned = text.strip()
    for marker in ("\nQuestion:", "\nSupporting notes:", "\nSources:", "\nNext action:"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
    return cleaned


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
    if result.heading:
        parts.append(result.heading)
    parts.append(f"chunk {result.chunk_index + 1}")
    return " - ".join(parts)


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
            return f"{heading}: {excerpt}"
    return excerpt


def _strip_leading_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and re.match(r"^#{1,6}\s+", lines[0]):
        lines = lines[1:]
    stripped = "\n".join(lines).strip()
    return stripped or text.strip()
