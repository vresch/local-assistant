from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from assistant.config import Settings
from assistant.db import connect
from assistant.logs.logger import finish_run, log_event, start_run
from assistant.notes.indexer import index_notes
from assistant.providers.local import LocalModelResponse
from assistant.ui import (
    AssistantUi,
    UiState,
    fetch_ask_runs,
    fetch_chunk_detail,
    fetch_chunks,
    fetch_documents,
    fetch_events,
    fetch_recent_tool_runs,
    fetch_run_detail,
    fetch_runs,
    fetch_search_results,
    fetch_tool_specs,
)
from textual.widgets import Checkbox, Input


class FakeLocalProvider:
    provider_name = "fake-local"
    model_name = "fake-model"

    def complete(self, prompt: str) -> LocalModelResponse:
        return LocalModelResponse(text="Model-backed UI answer.", provider=self.provider_name, model=self.model_name)


def test_ui_database_views_filter_indexed_content(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    alpha = notes_dir / "alpha.md"
    beta = notes_dir / "beta.md"
    alpha.write_text("# Alpha\nSQLite FTS5 powers local search.", encoding="utf-8")
    beta.write_text("# Beta\nTool registry notes.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)

        documents = fetch_documents(conn, "alpha")
        chunks = fetch_chunks(conn, "FTS5")

    assert documents.columns == ("ID", "Chunks", "Indexed", "Path")
    assert len(documents.rows) == 1
    assert documents.rows[0][3] == str(alpha)
    assert len(chunks.rows) == 1
    assert chunks.rows[0][1] == str(alpha)
    assert chunks.rows[0][3] == "Alpha"
    assert "SQLite FTS5" in chunks.rows[0][4]


def test_ui_database_views_filter_logs(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        run_id = start_run(conn, "search", "SQLite", "notes.search")
        log_event(conn, run_id, "llm", "llm=none model=none reason=search_only")
        finish_run(conn, run_id, "succeeded", "results=1")

        runs = fetch_runs(conn, "succeeded")
        events = fetch_events(conn, "search_only")

    assert runs.columns == ("ID", "Command", "Route", "Status", "Input", "Summary", "Started", "Finished")
    assert len(runs.rows) == 1
    assert runs.rows[0][1] == "search"
    assert runs.rows[0][3] == "succeeded"
    assert len(events.rows) == 1
    assert events.rows[0][2] == "search"
    assert events.rows[0][3] == "llm"
    assert events.rows[0][4] == "llm=none model=none reason=search_only"


def test_ui_database_views_show_ask_query_and_decision_output(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        run_id = start_run(conn, "ask", "What powers local search?", "local_answer")
        log_event(conn, run_id, "normalized_query", "what* powers* local* search*")
        log_event(conn, run_id, "retrieved_sources", "project.md - Project Alpha - chunk 1")
        log_event(conn, run_id, "llm", "llm=none model=none")
        log_event(conn, run_id, "synthesis", "llm=none model=none local_model_used=False")
        log_event(conn, run_id, "answer", "SQLite FTS5 powers local search.")
        log_event(conn, run_id, "answer_summary", "answered from 1 local chunks; llm=none; model=none")
        finish_run(conn, run_id, "succeeded", "results=1 llm=none model=none local_model_used=False")

        asks = fetch_ask_runs(conn, "local search")

    assert asks.columns == ("ID", "Question", "Decision Output", "Answer", "Status", "Started", "Finished")
    assert len(asks.rows) == 1
    assert asks.rows[0][1] == "What powers local search?"
    assert "normalized_query" in asks.rows[0][2]
    assert "retrieved_sources" in asks.rows[0][2]
    assert asks.rows[0][3] == "SQLite FTS5 powers local search."
    assert asks.rows[0][4] == "succeeded"


def test_ui_search_and_chunk_detail_helpers_return_phase_two_metadata(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note = notes_dir / "alpha.md"
    note.write_text("---\ntags: [business]\n---\n# Alpha\nSQLite FTS5 powers local search.", encoding="utf-8")

    with connect(tmp_path / "assistant.db") as conn:
        index_notes(conn, notes_dir)
        results = fetch_search_results(conn, "local search", limit=5, tag="business", path="alpha")
        detail = fetch_chunk_detail(conn, results[0].chunk_id)

    assert len(results) == 1
    assert results[0].title == "Alpha"
    assert results[0].heading_path == "Alpha"
    assert results[0].tags == ("business",)
    assert results[0].start_line == 4
    assert detail is not None
    assert detail.chunk_id == results[0].chunk_id
    assert "SQLite FTS5" in detail.content


def test_ui_run_detail_and_tool_helpers(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  sample:
    description: Sample tool.
    command: ["python", "-V"]
    risk: medium
    permissions: ["read"]
    args:
      - name: path
        type: path
        required: true
        flag: "--path"
""".strip(),
        encoding="utf-8",
    )
    settings = _settings(tmp_path, registry_path)

    with connect(tmp_path / "assistant.db") as conn:
        run_id = start_run(conn, "run", "sample --arg path=.", "tools.run")
        log_event(conn, run_id, "tool", "tool=sample status=succeeded")
        finish_run(conn, run_id, "succeeded", "tool=sample status=succeeded")

        detail = fetch_run_detail(conn, run_id)
        recent_tool_runs = fetch_recent_tool_runs(conn)
        tools = fetch_tool_specs(settings, conn)

    assert detail is not None
    assert detail.run_id == run_id
    assert detail.command == "run"
    assert detail.status == "succeeded"
    assert recent_tool_runs == {"sample": "succeeded"}
    assert len(tools) == 1
    assert tools[0].name == "sample"
    assert tools[0].risk == "medium"
    assert tools[0].requires_approval is True
    assert tools[0].permissions == "read"
    assert "path*:path" in tools[0].args
    assert tools[0].last_status == "succeeded"


def test_ui_state_toggles_selected_sources() -> None:
    state = UiState()

    assert state.toggle_source(10) is True
    assert state.selected_chunk_ids == {10}
    assert state.toggle_source(10) is False
    assert state.selected_chunk_ids == set()

    state.toggle_source(11)
    state.clear_sources()
    assert state.selected_chunk_ids == set()


def test_assistant_ui_mounts_with_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path, tmp_path / "registry.yaml")

    async def run_app() -> None:
        app = AssistantUi(settings)
        async with app.run_test():
            assert app.query_one("#workflow-tabs").active == "ask-tab"
            pass

    asyncio.run(run_app())


def test_assistant_ui_logs_search_actions(tmp_path: Path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "alpha.md").write_text("# Alpha\nSQLite FTS5 powers local search.", encoding="utf-8")
    settings = _settings(tmp_path, tmp_path / "registry.yaml")
    with connect(settings.db_path) as conn:
        index_notes(conn, notes_dir)

    async def run_app() -> None:
        app = AssistantUi(settings)
        async with app.run_test():
            app.query_one("#search-query", Input).value = "local search"
            app.refresh_search(log_action=True)

    asyncio.run(run_app())

    with connect(settings.db_path) as conn:
        runs = fetch_runs(conn, "search")
        events = fetch_events(conn, "search_only")

    assert len(runs.rows) == 1
    assert runs.rows[0][1] == "search"
    assert runs.rows[0][3] == "succeeded"
    assert len(events.rows) == 1


def test_assistant_ui_uses_configured_local_model_by_default(tmp_path: Path, monkeypatch) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "alpha.md").write_text("# Alpha\nSQLite FTS5 powers local search.", encoding="utf-8")
    settings = replace(_settings(tmp_path, tmp_path / "registry.yaml"), local_provider="llama.cpp-server", local_model="local")
    with connect(settings.db_path) as conn:
        index_notes(conn, notes_dir)

    monkeypatch.setattr("assistant.ui.validate_local_model_settings", lambda settings: [])
    monkeypatch.setattr("assistant.ui.build_local_provider", lambda settings: FakeLocalProvider())

    async def run_app() -> None:
        app = AssistantUi(settings)
        async with app.run_test():
            assert app.query_one("#ask-use-local-model", Checkbox).value is True
            assert app.query_one("#ask-use-selected", Checkbox).value is False
            app.query_one("#ask-question", Input).value = "What powers local search?"
            app.run_ask()

    asyncio.run(run_app())

    with connect(settings.db_path) as conn:
        asks = fetch_ask_runs(conn, "Model-backed UI answer")

    assert len(asks.rows) == 1
    assert asks.rows[0][3] == "Model-backed UI answer."
    assert "llm=fake-local" in asks.rows[0][2]


def test_assistant_ui_does_not_run_tool_without_confirmed_selection(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        """
tools:
  sample:
    description: Sample tool.
    command: ["python", "-V"]
    risk: low
""".strip(),
        encoding="utf-8",
    )
    settings = _settings(tmp_path, registry_path)

    async def run_app() -> None:
        app = AssistantUi(settings)
        async with app.run_test():
            app.run_selected_tool(dry_run=True)

    asyncio.run(run_app())

    with connect(settings.db_path) as conn:
        runs = fetch_runs(conn, "sample")

    assert runs.rows == ()


def _settings(tmp_path: Path, registry_path: Path) -> Settings:
    return Settings(
        notes_dir=tmp_path / "notes",
        db_path=tmp_path / "assistant.db",
        registry_path=registry_path,
        debug_log_path=tmp_path / "debug.log",
        llm_summary_path=tmp_path / "summary.md",
        research_dir=tmp_path / "research",
        local_provider=None,
        local_model=None,
        local_context_size=4096,
        local_max_tokens=256,
        local_temperature=0.2,
        local_base_url=None,
        local_timeout=30.0,
        remote_provider=None,
        remote_model=None,
        remote_api_key=None,
        remote_base_url="https://api.openai.com/v1",
        remote_timeout=30.0,
    )
