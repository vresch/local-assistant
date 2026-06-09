from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from assistant.notes.search import SearchResult, search_notes, to_fts_query
from assistant.providers.remote import RemoteProvider


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "best",
    "for",
    "how",
    "i",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "with",
}
EXTERNAL_TERMS = {
    "best",
    "current",
    "external",
    "general",
    "latest",
    "market",
    "practices",
    "recommend",
    "research",
    "state",
    "today",
}
BROAD_SYNTHESIS_TERMS = {
    "architecture",
    "architectures",
    "compare",
    "comparison",
    "design",
    "option",
    "options",
    "pattern",
    "patterns",
    "strategy",
    "tradeoff",
    "tradeoffs",
    "versus",
    "vs",
}


@dataclass(frozen=True)
class EscalationDecision:
    should_escalate: bool
    reason: str
    local_context_sufficient: bool


@dataclass(frozen=True)
class ResearchResult:
    text: str
    answer: str
    question: str
    normalized_query: str
    results: list[SearchResult]
    route: str
    remote_used: bool
    provider: str | None
    model: str | None
    escalation_decision: str
    escalation_reason: str
    stored_path: Path
    sources: list[str]
    summary: str
    errors: list[str]


def research_question(
    conn: sqlite3.Connection,
    question: str,
    research_dir: Path,
    *,
    limit: int = 8,
    allow_remote: bool = True,
    force_remote: bool = False,
    remote_provider: RemoteProvider | None = None,
    now: datetime | None = None,
) -> ResearchResult:
    normalized_query = to_fts_query(question)
    results = search_notes(conn, question, limit=limit)
    escalation = assess_escalation(
        question,
        results,
        allow_remote=allow_remote,
        force_remote=force_remote,
    )

    errors: list[str] = []
    remote_response = None
    if escalation.should_escalate and allow_remote:
        if remote_provider is None:
            errors.append("remote model was needed but is not configured")
        else:
            try:
                remote_response = remote_provider.generate(build_research_prompt(question, results))
            except Exception as exc:  # pragma: no cover - exact provider failures vary
                errors.append(f"remote model failed: {exc}")

    remote_used = remote_response is not None
    route = "remote_llm" if remote_used else "local_answer"
    provider = remote_response.provider if remote_response else None
    model = remote_response.model if remote_response else None
    sections = _remote_sections(remote_response.text) if remote_response else _local_sections(results)
    if errors:
        sections = ResearchSections(
            answer=sections.answer,
            local_context_used=sections.local_context_used,
            external_reasoning=sections.external_reasoning,
            recommendation=sections.recommendation,
            risks_tradeoffs=[*sections.risks_tradeoffs, *errors],
            next_step=sections.next_step,
        )
    sources = [_source_reference(result) for result in results]
    timestamp = now or datetime.now().astimezone()
    stored_path = _research_result_path(research_dir, question, timestamp)
    text = _format_output(sections, sources, stored_path)
    stored_path = _store_research_result(
        stored_path,
        question=question,
        timestamp=timestamp,
        route=route,
        remote_used=remote_used,
        provider=provider,
        model=model,
        escalation=escalation,
        sources=sources,
        final_answer=text,
        errors=errors,
    )
    summary = _plain_excerpt(sections.answer, max_chars=220)
    return ResearchResult(
        text=text,
        answer=sections.answer,
        question=question,
        normalized_query=normalized_query,
        results=results,
        route=route,
        remote_used=remote_used,
        provider=provider,
        model=model,
        escalation_decision="yes" if escalation.should_escalate else "no",
        escalation_reason=escalation.reason,
        stored_path=stored_path,
        sources=sources,
        summary=summary,
        errors=errors,
    )


def assess_escalation(
    question: str,
    results: list[SearchResult],
    *,
    allow_remote: bool,
    force_remote: bool,
) -> EscalationDecision:
    if not allow_remote:
        return EscalationDecision(False, "remote disabled by --no-remote", local_context_sufficient=False)
    if force_remote:
        return EscalationDecision(True, "forced by --force-remote", local_context_sufficient=False)

    weak_relevance = _weak_relevance(question, results)
    local_context_sufficient = len(results) >= 2 and not weak_relevance
    if local_context_sufficient:
        return EscalationDecision(False, "local context sufficient", local_context_sufficient=True)

    reasons: list[str] = []
    if len(results) < 2:
        reasons.append(f"fewer than 2 relevant local chunks found ({len(results)})")
    if weak_relevance:
        reasons.append("retrieved chunks have weak relevance")
    if _asks_for_external_knowledge(question):
        reasons.append("question asks for external/general knowledge beyond personal notes")
    if _requires_broad_synthesis(question):
        reasons.append("question requires architectural comparison or broad synthesis")

    if reasons:
        return EscalationDecision(True, "; ".join(reasons), local_context_sufficient=False)
    return EscalationDecision(False, "local context sufficient", local_context_sufficient=True)


def build_research_prompt(question: str, results: list[SearchResult]) -> str:
    return "\n".join(
        [
            "You are helping with research for a local-first personal AI assistant.",
            "",
            "Use the provided local notes as primary context.",
            "You may add external/general reasoning, but clearly separate it from what the local notes support.",
            "Do not invent personal facts, existing user decisions, or project state.",
            "",
            "Return:",
            "1. Direct answer",
            "2. Local context used",
            "3. External reasoning",
            "4. Recommendation",
            "5. Risks / tradeoffs",
            "6. Next implementation step",
            "",
            "Question:",
            question,
            "",
            "Local notes:",
            _retrieved_chunks_for_prompt(results),
        ]
    )


@dataclass(frozen=True)
class ResearchSections:
    answer: str
    local_context_used: list[str]
    external_reasoning: list[str]
    recommendation: str
    risks_tradeoffs: list[str]
    next_step: str


def _local_sections(results: list[SearchResult]) -> ResearchSections:
    if not results:
        return ResearchSections(
            answer="I could not find relevant local notes for that research question.",
            local_context_used=["No matching local notes were found."],
            external_reasoning=["None. Remote reasoning was not used."],
            recommendation="Run `assistant index` if notes changed, then retry with a more specific question.",
            risks_tradeoffs=["The answer is limited because no local context was retrieved."],
            next_step="Add or index notes that describe the topic you want researched.",
        )

    excerpts = [_source_excerpt(result) for result in results]
    unique_excerpts = _unique_nonempty(excerpts)
    answer_lines = ["Based on the retrieved local notes:"]
    answer_lines.extend(f"- {excerpt}" for excerpt in unique_excerpts[:5])
    return ResearchSections(
        answer="\n".join(answer_lines),
        local_context_used=[_source_with_snippet(result) for result in results],
        external_reasoning=["None. Remote reasoning was not used."],
        recommendation="Use the cited local notes as the primary basis, then add remote research only if gaps remain.",
        risks_tradeoffs=[
            "Local notes may be incomplete or stale.",
            "Extractive synthesis can miss implications that are not stated directly in notes.",
        ],
        next_step="Review the cited chunks and turn the strongest points into an implementation plan.",
    )


def _remote_sections(text: str) -> ResearchSections:
    parsed = _parse_remote_sections(text)
    return ResearchSections(
        answer=parsed.get("direct answer") or parsed.get("answer") or text.strip(),
        local_context_used=_lines_or_default(parsed.get("local context used"), "See cited local sources below."),
        external_reasoning=_lines_or_default(parsed.get("external reasoning"), "Remote model did not separate this section."),
        recommendation=parsed.get("recommendation") or "Review the remote synthesis against local notes before implementing.",
        risks_tradeoffs=_lines_or_default(parsed.get("risks / tradeoffs"), "Remote output may include unsupported assumptions."),
        next_step=parsed.get("next implementation step") or "Review the stored research result and decide the next code change.",
    )


def _parse_remote_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    heading_re = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:\d+[.)]\s*)?"
        r"(direct answer|answer|local context used|external reasoning|recommendation|risks / tradeoffs|next implementation step)"
        r"\s*:?\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        match = heading_re.match(line)
        if match:
            current = match.group(1).lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def _format_output(sections: ResearchSections, sources: list[str], stored_path: Path) -> str:
    return "\n".join(
        [
            "Answer:",
            sections.answer,
            "",
            "Local context used:",
            *_bullet_lines(sections.local_context_used),
            "",
            "External reasoning:",
            *_bullet_lines(sections.external_reasoning),
            "",
            "Recommendation:",
            sections.recommendation,
            "",
            "Risks / tradeoffs:",
            *_bullet_lines(sections.risks_tradeoffs),
            "",
            "Sources:",
            *(_numbered_lines(sources) if sources else ["None"]),
            "",
            "Stored result:",
            str(stored_path),
        ]
    )


def _research_result_path(research_dir: Path, question: str, timestamp: datetime) -> Path:
    return research_dir / f"{timestamp:%Y-%m-%d}-{_slugify(question)}.md"


def _store_research_result(
    stored_path: Path,
    *,
    question: str,
    timestamp: datetime,
    route: str,
    remote_used: bool,
    provider: str | None,
    model: str | None,
    escalation: EscalationDecision,
    sources: list[str],
    final_answer: str,
    errors: list[str],
) -> Path:
    limitations = ["Local notes may be incomplete."]
    if not remote_used:
        limitations.append("Remote model was not used; external reasoning is limited.")
    if errors:
        limitations.extend(errors)
    markdown = "\n".join(
        [
            f"# Research: {question}",
            "",
            f"- Timestamp: {timestamp.isoformat(timespec='seconds')}",
            f"- Original question: {question}",
            f"- Route decision: {route}",
            f"- Remote model used: {'yes' if remote_used else 'no'}",
            f"- Provider: {provider or 'none'}",
            f"- Model: {model or 'none'}",
            f"- Escalation decision: {'yes' if escalation.should_escalate else 'no'}",
            f"- Escalation reason: {escalation.reason}",
            "",
            "## Retrieved Local Sources",
            *(_numbered_lines(sources) if sources else ["None"]),
            "",
            "## Final Answer",
            "",
            final_answer,
            "",
            "## Limitations",
            *_bullet_lines(limitations),
            "",
        ]
    )
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text(markdown, encoding="utf-8")
    return stored_path


def _retrieved_chunks_for_prompt(results: list[SearchResult]) -> str:
    if not results:
        return "No local notes were retrieved."
    blocks = []
    for index, result in enumerate(results, start=1):
        heading = f" - {result.heading}" if result.heading else ""
        blocks.append(
            "\n".join(
                [
                    f"[{index}] {result.path}{heading} - chunk {result.chunk_index + 1}",
                    f"Snippet: {result.snippet}",
                    result.content,
                ]
            )
        )
    return "\n\n".join(blocks)


def _weak_relevance(question: str, results: list[SearchResult]) -> bool:
    if not results:
        return True
    meaningful_terms = _meaningful_terms(question)
    if not meaningful_terms:
        return False
    strong_matches = sum(1 for result in results if _match_count(meaningful_terms, result.content) >= 2)
    return strong_matches < min(2, len(results))


def _asks_for_external_knowledge(question: str) -> bool:
    terms = set(_meaningful_terms(question))
    return bool(terms & EXTERNAL_TERMS)


def _requires_broad_synthesis(question: str) -> bool:
    terms = set(_meaningful_terms(question))
    return bool(terms & BROAD_SYNTHESIS_TERMS)


def _meaningful_terms(text: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9]+", text.lower()) if len(term) > 1 and term not in STOPWORDS]


def _match_count(terms: list[str], content: str) -> int:
    content_terms = set(re.findall(r"[a-z0-9]+", content.lower()))
    return sum(1 for term in set(terms) if term in content_terms)


def _source_reference(result: SearchResult) -> str:
    parts = [result.path]
    if result.heading:
        parts.append(result.heading)
    parts.append(f"chunk {result.chunk_index + 1}")
    return " - ".join(parts)


def _source_with_snippet(result: SearchResult) -> str:
    return f"{_source_reference(result)}: {_plain_excerpt(result.snippet or result.content, max_chars=220)}"


def _source_excerpt(result: SearchResult, max_chars: int = 260) -> str:
    excerpt = _plain_excerpt(_strip_leading_heading(result.content), max_chars=max_chars)
    if result.heading and not excerpt.lower().startswith(result.heading.lower()):
        return f"{result.heading}: {excerpt}"
    return excerpt


def _strip_leading_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and re.match(r"^#{1,6}\s+", lines[0]):
        lines = lines[1:]
    stripped = "\n".join(lines).strip()
    return stripped or text.strip()


def _plain_excerpt(text: str, max_chars: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _slugify(text: str, max_chars: int = 80) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", text.lower()))
    slug = slug[:max_chars].strip("-")
    return slug or "research"


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = value.lower()
        if not value or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(value)
    return unique


def _lines_or_default(text: str | None, default: str) -> list[str]:
    if not text:
        return [default]
    lines = [line.strip().removeprefix("-").strip() for line in text.splitlines() if line.strip()]
    return lines or [default]


def _bullet_lines(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values] if values else ["- None"]


def _numbered_lines(values: list[str]) -> list[str]:
    return [f"{index}. {value}" for index, value in enumerate(values, start=1)]
