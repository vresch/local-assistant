from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import assistant.cli as cli
from assistant.db import connect
from assistant.notes.indexer import index_notes
from assistant.providers.remote import RemoteResponse
from assistant.research import research_question


runner = CliRunner()
NOW = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
QUESTION = "best architecture for local-first AI assistants"


class FakeRemoteProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> RemoteResponse:
        self.prompts.append(prompt)
        return RemoteResponse(
            text="""
1. Direct answer
Use local notes and SQLite FTS5 as the memory layer, then route only complex research to a remote model.

2. Local context used
- The local notes describe SQLite FTS5 and uv tools.

3. External reasoning
- A narrow provider interface keeps remote APIs replaceable.

4. Recommendation
Keep orchestration explicit and inspectable.

5. Risks / tradeoffs
- Remote output can introduce unsupported assumptions.

6. Next implementation step
Add tests around routing and storage.
""".strip(),
            provider=self.provider_name,
            model=self.model_name,
        )


def test_research_searches_local_context_before_remote_provider(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=False)
    provider = FakeRemoteProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(
            conn,
            QUESTION,
            tmp_path / "research",
            remote_provider=provider,
            now=NOW,
        )

    assert result.remote_used is True
    assert len(provider.prompts) == 1
    assert "Local notes:" in provider.prompts[0]
    assert "local-first assistant architecture" in provider.prompts[0]
    assert "architecture.md" in provider.prompts[0]


def test_research_does_not_call_remote_when_local_context_is_sufficient(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=True)
    provider = FakeRemoteProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(
            conn,
            QUESTION,
            tmp_path / "research",
            remote_provider=provider,
            now=NOW,
        )

    assert result.route == "local_answer"
    assert result.remote_used is False
    assert provider.prompts == []
    assert result.escalation_reason == "local context sufficient"


def test_research_calls_remote_when_context_is_insufficient_and_configured(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=False)
    provider = FakeRemoteProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(
            conn,
            QUESTION,
            tmp_path / "research",
            remote_provider=provider,
            now=NOW,
        )

    assert result.route == "remote_llm"
    assert result.remote_used is True
    assert len(provider.prompts) == 1
    assert "question requires architectural comparison or broad synthesis" in result.escalation_reason
    assert "Use local notes and SQLite FTS5" in result.text


def test_research_no_remote_prevents_remote_calls(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=False)
    provider = FakeRemoteProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(
            conn,
            QUESTION,
            tmp_path / "research",
            allow_remote=False,
            remote_provider=provider,
            now=NOW,
        )

    assert result.route == "local_answer"
    assert result.remote_used is False
    assert provider.prompts == []
    assert result.escalation_reason == "remote disabled by --no-remote"


def test_research_force_remote_calls_remote_when_configured(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=True)
    provider = FakeRemoteProvider()

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(
            conn,
            QUESTION,
            tmp_path / "research",
            force_remote=True,
            remote_provider=provider,
            now=NOW,
        )

    assert result.route == "remote_llm"
    assert result.remote_used is True
    assert len(provider.prompts) == 1
    assert result.escalation_reason == "forced by --force-remote"


def test_research_missing_remote_config_falls_back_gracefully(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=False)

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(conn, QUESTION, tmp_path / "research", now=NOW)

    assert result.route == "local_answer"
    assert result.remote_used is False
    assert result.errors == ["remote model was needed but is not configured"]
    assert "remote model was needed but is not configured" in result.text
    assert result.stored_path.is_file()


def test_research_result_markdown_includes_sources_and_route_decision(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=True)

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        result = research_question(
            conn,
            QUESTION,
            tmp_path / "research",
            allow_remote=False,
            now=NOW,
        )

    assert result.stored_path == tmp_path / "research" / "2026-06-09-best-architecture-for-local-first-ai-assistants.md"
    content = result.stored_path.read_text(encoding="utf-8")
    assert "# Research: best architecture for local-first AI assistants" in content
    assert "- Route decision: local_answer" in content
    assert "- Remote model used: no" in content
    assert "- Escalation reason: remote disabled by --no-remote" in content
    assert "## Retrieved Local Sources" in content
    assert "architecture.md - Local-First Assistant Architecture - chunk 1" in content
    assert "## Final Answer" in content
    assert "Stored result:" in content


def test_cli_research_logs_escalation_reason_and_stored_path(tmp_path: Path, monkeypatch: Any) -> None:
    notes_dir = tmp_path / "notes"
    _write_research_notes(notes_dir, include_full_context=False)
    db_path = tmp_path / "assistant.db"
    research_dir = tmp_path / "research"
    env = {
        "ASSISTANT_NOTES_DIR": str(notes_dir),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_RESEARCH_DIR": str(research_dir),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
    }

    monkeypatch.setattr(
        cli,
        "research_question",
        lambda *args, **kwargs: _research_question_with_fixed_time(*args, now=NOW, **kwargs),
    )

    assert runner.invoke(cli.app, ["index"], env=env).exit_code == 0
    result = runner.invoke(cli.app, ["research", QUESTION, "--no-remote"], env=env)

    assert result.exit_code == 0
    assert "Stored result:" in result.output
    stored_path = research_dir / "2026-06-09-best-architecture-for-local-first-ai-assistants.md"
    assert stored_path.is_file()
    assert stored_path.name in result.output.replace("\n", "")

    with sqlite3.connect(db_path) as conn:
        run = conn.execute("SELECT command, route, status, summary FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        events = conn.execute(
            """
            SELECT event_type, message
            FROM run_events
            WHERE run_id = 2
            ORDER BY id
            """
        ).fetchall()

    assert run[0] == "research"
    assert run[1] == "local_answer"
    assert run[2] == "succeeded"
    assert str(stored_path) in run[3]
    event_messages = {event[0]: event[1] for event in events}
    assert event_messages["escalation_reason"] == "remote disabled by --no-remote"
    assert event_messages["stored_result_path"] == str(stored_path)
    assert "architecture.md" in event_messages["retrieved_sources"]


def _research_question_with_fixed_time(*args: Any, now: datetime, **kwargs: Any):
    return research_question(*args, now=now, **kwargs)


def _write_research_notes(notes_dir: Path, *, include_full_context: bool) -> None:
    notes_dir.mkdir(parents=True)
    (notes_dir / "architecture.md").write_text(
        """
# Local-First Assistant Architecture
The local-first assistant architecture should keep notes in SQLite FTS5 and make local retrieval the first step before any model routing.
""".strip(),
        encoding="utf-8",
    )
    (notes_dir / "memory.md").write_text(
        """
# Notes As Memory
Markdown notes act as memory for the assistant. The search layer should cite path, heading, chunk index, and snippet.
""".strip(),
        encoding="utf-8",
    )
    (notes_dir / "tools.md").write_text(
        """
# Python Tools Through uv
Python tools should run through uv so execution stays local, explicit, and inspectable.
""".strip(),
        encoding="utf-8",
    )
    (notes_dir / "routing.md").write_text(
        """
# Provider Routing
Provider routing should prefer a local answer when notes are sufficient and escalate only for justified research gaps.
""".strip(),
        encoding="utf-8",
    )
    (notes_dir / "garden.md").write_text(
        """
# Garden
Tomatoes need sun and regular watering. Mulch helps soil retain moisture.
""".strip(),
        encoding="utf-8",
    )
    if include_full_context:
        (notes_dir / "comparison.md").write_text(
            """
# Architecture Tradeoffs
For local-first AI assistants, the best architecture is a CLI-first orchestrator with SQLite FTS5 retrieval, simple provider routing, local logs, and optional remote synthesis for broad architecture comparison.
""".strip(),
            encoding="utf-8",
        )
