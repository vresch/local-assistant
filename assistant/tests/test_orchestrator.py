from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from assistant.db import connect
from assistant.notes.indexer import index_notes
from assistant.orchestrator import answer_question


def test_answer_question_uses_llama_when_model_path_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "project.md").write_text(
        "# Project Alpha\nSQLite FTS5 powers local note search.",
        encoding="utf-8",
    )
    model_path = tmp_path / "model.gguf"
    model_path.write_text("fake model", encoding="utf-8")

    class FakeLlama:
        init_kwargs: dict[str, Any] = {}
        prompt = ""
        call_kwargs: dict[str, Any] = {}

        def __init__(self, **kwargs: Any) -> None:
            FakeLlama.init_kwargs = kwargs

        def __call__(self, prompt: str, **kwargs: Any) -> dict[str, object]:
            FakeLlama.prompt = prompt
            FakeLlama.call_kwargs = kwargs
            return {"choices": [{"text": "SQLite FTS5 should be used.\nQuestion: extra prompt text"}]}

    monkeypatch.setattr("assistant.orchestrator._load_llama_class", lambda: FakeLlama)

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        answer = answer_question(
            conn,
            "What powers local search?",
            model_path=model_path,
            context_size=1024,
            max_tokens=64,
            temperature=0.0,
        )

    assert answer.used_local_model is True
    assert answer.llm == "llama-cpp-python"
    assert answer.local_model == str(model_path)
    assert f"llm=llama-cpp-python; model={model_path}" in answer.summary
    assert answer.answer == "SQLite FTS5 should be used."
    assert "SQLite FTS5 should be used." in answer.text
    assert "The strongest matching note says" not in answer.text
    assert FakeLlama.init_kwargs == {
        "model_path": str(model_path),
        "n_ctx": 1024,
        "verbose": False,
    }
    assert FakeLlama.call_kwargs["max_tokens"] == 64
    assert FakeLlama.call_kwargs["temperature"] == 0.0
    assert "\nQuestion:" in FakeLlama.call_kwargs["stop"]
    assert "Local notes:" in FakeLlama.prompt
    assert "SQLite FTS5 powers local note search." in FakeLlama.prompt


def test_answer_question_skips_llama_without_model_path(tmp_path: Path) -> None:
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
