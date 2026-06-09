from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryRule:
    name: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class NoteCategory:
    path: str
    category: str
    score: int
    matched_terms: tuple[str, ...]


CATEGORY_RULES: tuple[CategoryRule, ...] = (
    CategoryRule("project", ("project", "roadmap", "milestone", "launch", "implementation", "sprint", "backlog")),
    CategoryRule("research", ("research", "source", "paper", "study", "reference", "finding", "investigate")),
    CategoryRule("idea", ("idea", "brainstorm", "concept", "opportunity", "proposal", "experiment")),
    CategoryRule("meeting", ("meeting", "agenda", "call", "action item", "follow up", "decision")),
    CategoryRule("task", ("todo", "task", "next action", "checklist", "deadline", "blocked")),
    CategoryRule("journal", ("journal", "daily", "reflection", "gratitude", "mood", "today", "yesterday")),
    CategoryRule("learning", ("learn", "course", "tutorial", "book", "lesson", "practice")),
    CategoryRule("finance", ("budget", "invoice", "revenue", "expense", "pricing", "income")),
    CategoryRule("health", ("health", "workout", "meditation", "sleep", "therapy", "nutrition")),
)


def categorise_notes(conn: sqlite3.Connection) -> list[NoteCategory]:
    """Categorise indexed documents using deterministic local keyword rules."""
    documents = _document_texts(conn)
    return [_categorise_document(path, text) for path, text in documents]


def _document_texts(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT documents.path, chunks.heading, chunks.content
        FROM documents
        LEFT JOIN chunks ON chunks.document_id = documents.id
        ORDER BY documents.path, chunks.chunk_index
        """
    ).fetchall()
    documents: dict[str, list[str]] = {}
    for row in rows:
        parts = documents.setdefault(row["path"], [row["path"]])
        if row["heading"]:
            parts.append(row["heading"])
        if row["content"]:
            parts.append(row["content"])
    return [(path, "\n".join(parts)) for path, parts in documents.items()]


def _categorise_document(path: str, text: str) -> NoteCategory:
    normalized = text.lower()
    scored = [(_score_rule(normalized, rule), rule) for rule in CATEGORY_RULES]
    scored.sort(key=lambda item: (-item[0][0], item[1].name))
    (score, matched_terms), rule = scored[0]
    if score == 0:
        return NoteCategory(path=path, category="uncategorised", score=0, matched_terms=())
    return NoteCategory(path=path, category=rule.name, score=score, matched_terms=tuple(matched_terms))


def _score_rule(text: str, rule: CategoryRule) -> tuple[int, list[str]]:
    score = 0
    matched_terms: list[str] = []
    for term in rule.terms:
        occurrences = len(re.findall(rf"\b{re.escape(term)}\b", text))
        if occurrences:
            score += occurrences
            matched_terms.append(term)
    return score, matched_terms
