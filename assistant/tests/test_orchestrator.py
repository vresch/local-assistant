from __future__ import annotations

from pathlib import Path

from assistant.db import connect
from assistant.notes.indexer import index_notes
from assistant.orchestrator import answer_question
from assistant.providers.local import LocalModelResponse


class FakeProvider:
    provider_name = "fake-local"
    model_name = "fake-model"

    def __init__(self, text: str = "SQLite FTS5 should be used.") -> None:
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LocalModelResponse:
        self.prompts.append(prompt)
        return LocalModelResponse(text=self.text, provider=self.provider_name, model=self.model_name)


def test_answer_question_uses_local_provider_when_configured(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text(
        "# Project Alpha\nSQLite FTS5 powers local note search.",
        encoding="utf-8",
    )
    provider = FakeProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        answer = answer_question(conn, "What powers local search?", local_provider=provider)

    assert answer.used_local_model is True
    assert answer.llm == "fake-local"
    assert answer.local_model == "fake-model"
    assert "llm=fake-local; model=fake-model" in answer.summary
    assert answer.answer == "SQLite FTS5 should be used."
    assert "SQLite FTS5 should be used." in answer.text
    assert "The strongest matching note says" not in answer.text
    assert answer.prompt_chunk_count == 1
    assert answer.prompt_char_count == len(provider.prompts[0])
    assert "Local notes:" in provider.prompts[0]
    assert "SQLite FTS5 powers local note search." in provider.prompts[0]


def test_answer_question_skips_model_without_provider(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text(
        "# Project Alpha\nSQLite FTS5 powers local note search.",
        encoding="utf-8",
    )

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        answer = answer_question(conn, "What powers local search?")

    assert answer.used_local_model is False
    assert answer.llm == "none"
    assert answer.local_model is None
    assert "The strongest matching note says" in answer.answer
    assert "The strongest matching note says" in answer.text


def test_answer_question_does_not_use_provider_without_matching_local_context(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project Alpha\nSQLite FTS5 powers local note search.", encoding="utf-8")
    provider = FakeProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        answer = answer_question(conn, "banana telescope", local_provider=provider)

    assert answer.used_local_model is False
    assert answer.llm == "none"
    assert answer.local_model is None
    assert answer.sources == []
    assert answer.summary == "no relevant chunks found; local_model_requested=True"
    assert provider.prompts == []


def test_answer_question_empty_provider_text_falls_back_to_extractive_answer(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text("# Project Alpha\nSQLite FTS5 powers local note search.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        answer = answer_question(conn, "What powers local search?", local_provider=FakeProvider(text=""))

    assert answer.used_local_model is False
    assert answer.llm == "fake-local"
    assert answer.local_model == "fake-model"
    assert "The strongest matching note says" in answer.answer


def test_answer_question_synthesizes_multiple_business_ideas_without_model(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "business" / "sontera.md").parent.mkdir(parents=True)
    (notes_dir / "business" / "sontera.md").write_text(
        "# Sontera\nI am pursuing a sound healing business called Sontera.",
        encoding="utf-8",
    )
    (notes_dir / "ideas" / "newsletter.md").parent.mkdir(parents=True)
    (notes_dir / "ideas" / "newsletter.md").write_text(
        "# Newsletter\nI am pursuing a paid newsletter business about local productivity systems.",
        encoding="utf-8",
    )
    (notes_dir / "studio.md").write_text(
        "# Studio\nI am pursuing a small design studio business for local companies.",
        encoding="utf-8",
    )

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        answer = answer_question(conn, "What business ideas am I currently pursuing?", use_model=False)

    assert len(answer.results) >= 3
    assert answer.used_local_model is False
    assert "The strongest matching note says" in answer.answer
    assert "Sontera" in answer.answer
    assert "Newsletter" in answer.answer
    assert "Studio" in answer.answer
    assert "sound healing business" in answer.answer.lower()
    assert "paid newsletter business" in answer.answer.lower()
    assert "design studio business" in answer.answer.lower()
    assert "podcast" not in answer.answer.lower()
