from __future__ import annotations

import shlex
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Static, TabPane, TabbedContent

from assistant.config import Settings, validate_local_model_settings
from assistant.db import connect
from assistant.logs.logger import finish_run, log_event, start_run
from assistant.notes.search import SearchResult, get_chunk, search_notes
from assistant.orchestrator import AnswerResult, answer_question
from assistant.providers.local import LocalModelProvider, build_local_provider
from assistant.tools.registry import ToolSpec, build_command, load_registry
from assistant.tools.runner import ToolResult, run_tool


DEFAULT_LIMIT = 200
WORKFLOW_LIMIT = 10


@dataclass(frozen=True)
class TableView:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


@dataclass
class UiState:
    active_query: str = ""
    selected_chunk_ids: set[int] = field(default_factory=set)
    active_run_id: int | None = None
    active_tool: str | None = None

    def toggle_source(self, chunk_id: int) -> bool:
        if chunk_id in self.selected_chunk_ids:
            self.selected_chunk_ids.remove(chunk_id)
            return False
        self.selected_chunk_ids.add(chunk_id)
        return True

    def clear_sources(self) -> None:
        self.selected_chunk_ids.clear()


@dataclass(frozen=True)
class RunDetail:
    run_id: int
    command: str
    route: str
    status: str
    input: str
    summary: str
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class EventDetail:
    event_id: int
    run_id: int
    event_type: str
    message: str
    created_at: str


@dataclass(frozen=True)
class ToolRow:
    name: str
    description: str
    risk: str
    permissions: str
    requires_approval: bool
    args: str
    last_status: str


def fetch_documents(conn: sqlite3.Connection, filter_text: str = "", limit: int = DEFAULT_LIMIT) -> TableView:
    rows = conn.execute(
        """
        SELECT documents.id, documents.path, documents.indexed_at, COUNT(chunks.id) AS chunks
        FROM documents
        LEFT JOIN chunks ON chunks.document_id = documents.id
        WHERE lower(documents.path) LIKE lower(?)
        GROUP BY documents.id
        ORDER BY documents.indexed_at DESC, documents.id DESC
        LIMIT ?
        """,
        (_like(filter_text), limit),
    ).fetchall()
    return TableView(
        columns=("ID", "Chunks", "Indexed", "Path"),
        rows=tuple((row["id"], row["chunks"], row["indexed_at"], row["path"]) for row in rows),
    )


def fetch_chunks(conn: sqlite3.Connection, filter_text: str = "", limit: int = DEFAULT_LIMIT) -> TableView:
    rows = conn.execute(
        """
        SELECT
            chunks.id,
            documents.path,
            chunks.chunk_index,
            chunks.heading,
            substr(replace(chunks.content, char(10), ' '), 1, 180) AS preview,
            chunks.created_at
        FROM chunks
        JOIN documents ON documents.id = chunks.document_id
        WHERE
            lower(documents.path) LIKE lower(?)
            OR lower(coalesce(chunks.heading, '')) LIKE lower(?)
            OR lower(chunks.content) LIKE lower(?)
        ORDER BY documents.path, chunks.chunk_index
        LIMIT ?
        """,
        (_like(filter_text), _like(filter_text), _like(filter_text), limit),
    ).fetchall()
    return TableView(
        columns=("ID", "Path", "Chunk", "Heading", "Preview", "Created"),
        rows=tuple(
            (row["id"], row["path"], row["chunk_index"], row["heading"] or "", row["preview"], row["created_at"])
            for row in rows
        ),
    )


def fetch_runs(conn: sqlite3.Connection, filter_text: str = "", limit: int = DEFAULT_LIMIT) -> TableView:
    rows = conn.execute(
        """
        SELECT id, command, route, status, input, summary, started_at, finished_at
        FROM runs
        WHERE
            lower(command) LIKE lower(?)
            OR lower(route) LIKE lower(?)
            OR lower(status) LIKE lower(?)
            OR lower(coalesce(input, '')) LIKE lower(?)
            OR lower(coalesce(summary, '')) LIKE lower(?)
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(_like(filter_text) for _ in range(5)) + (limit,),
    ).fetchall()
    return TableView(
        columns=("ID", "Command", "Route", "Status", "Input", "Summary", "Started", "Finished"),
        rows=tuple(
            (
                row["id"],
                row["command"],
                row["route"],
                row["status"],
                row["input"] or "",
                row["summary"] or "",
                row["started_at"],
                row["finished_at"] or "",
            )
            for row in rows
        ),
    )


def fetch_ask_runs(conn: sqlite3.Connection, filter_text: str = "", limit: int = DEFAULT_LIMIT) -> TableView:
    rows = conn.execute(
        """
        SELECT id, input, status, summary, started_at, finished_at
        FROM runs
        WHERE
            command = 'ask'
            AND (
                lower(coalesce(input, '')) LIKE lower(?)
                OR lower(coalesce(summary, '')) LIKE lower(?)
                OR EXISTS (
                    SELECT 1
                    FROM run_events
                    WHERE run_events.run_id = runs.id
                    AND (
                        lower(run_events.event_type) LIKE lower(?)
                        OR lower(run_events.message) LIKE lower(?)
                    )
                )
            )
        ORDER BY id DESC
        LIMIT ?
        """,
        (_like(filter_text), _like(filter_text), _like(filter_text), _like(filter_text), limit),
    ).fetchall()

    rendered_rows: list[tuple[Any, ...]] = []
    for row in rows:
        events = conn.execute(
            """
            SELECT event_type, message
            FROM run_events
            WHERE run_id = ?
            ORDER BY id
            """,
            (row["id"],),
        ).fetchall()
        event_messages = {event["event_type"]: event["message"] for event in events}
        rendered_rows.append(
            (
                row["id"],
                row["input"] or "",
                _ask_decision_output(event_messages),
                event_messages.get("answer", row["summary"] or ""),
                row["status"],
                row["started_at"],
                row["finished_at"] or "",
            )
        )

    return TableView(
        columns=("ID", "Question", "Decision Output", "Answer", "Status", "Started", "Finished"),
        rows=tuple(rendered_rows),
    )


def fetch_events(conn: sqlite3.Connection, filter_text: str = "", limit: int = DEFAULT_LIMIT) -> TableView:
    rows = conn.execute(
        """
        SELECT
            run_events.id,
            run_events.run_id,
            runs.command,
            run_events.event_type,
            run_events.message,
            run_events.created_at
        FROM run_events
        JOIN runs ON runs.id = run_events.run_id
        WHERE
            lower(runs.command) LIKE lower(?)
            OR lower(run_events.event_type) LIKE lower(?)
            OR lower(run_events.message) LIKE lower(?)
        ORDER BY run_events.id DESC
        LIMIT ?
        """,
        (_like(filter_text), _like(filter_text), _like(filter_text), limit),
    ).fetchall()
    return TableView(
        columns=("ID", "Run", "Command", "Type", "Message", "Created"),
        rows=tuple(
            (row["id"], row["run_id"], row["command"], row["event_type"], row["message"], row["created_at"])
            for row in rows
        ),
    )


def fetch_search_results(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = WORKFLOW_LIMIT,
    tag: str | None = None,
    path: str | None = None,
    since: str | None = None,
) -> list[SearchResult]:
    return search_notes(conn, query, limit=limit, tag=_empty_to_none(tag), path=_empty_to_none(path), since=_empty_to_none(since))


def fetch_chunk_detail(conn: sqlite3.Connection, chunk_id: int) -> SearchResult | None:
    return get_chunk(conn, chunk_id)


def fetch_run_detail(conn: sqlite3.Connection, run_id: int) -> RunDetail | None:
    row = conn.execute(
        """
        SELECT id, command, route, status, input, summary, started_at, finished_at
        FROM runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return RunDetail(
        run_id=int(row["id"]),
        command=row["command"],
        route=row["route"],
        status=row["status"],
        input=row["input"] or "",
        summary=row["summary"] or "",
        started_at=row["started_at"],
        finished_at=row["finished_at"] or "",
    )


def fetch_run_events(conn: sqlite3.Connection, run_id: int) -> list[EventDetail]:
    rows = conn.execute(
        """
        SELECT id, run_id, event_type, message, created_at
        FROM run_events
        WHERE run_id = ?
        ORDER BY id
        """,
        (run_id,),
    ).fetchall()
    return [
        EventDetail(
            event_id=int(row["id"]),
            run_id=int(row["run_id"]),
            event_type=row["event_type"],
            message=row["message"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def fetch_tool_specs(settings: Settings, conn: sqlite3.Connection | None = None) -> list[ToolRow]:
    registry = load_registry(settings.registry_path)
    last_runs = fetch_recent_tool_runs(conn) if conn is not None else {}
    rows: list[ToolRow] = []
    for tool in sorted(registry.values(), key=lambda item: item.name):
        rows.append(
            ToolRow(
                name=tool.name,
                description=tool.description,
                risk=tool.risk,
                permissions=", ".join(tool.permissions) or "none",
                requires_approval=_tool_requires_approval(tool),
                args=", ".join(_render_arg_spec(arg) for arg in tool.args) or "none",
                last_status=last_runs.get(tool.name, "never"),
            )
        )
    return rows


def fetch_recent_tool_runs(conn: sqlite3.Connection | None, limit: int = DEFAULT_LIMIT) -> dict[str, str]:
    if conn is None:
        return {}
    rows = conn.execute(
        """
        SELECT input, status
        FROM runs
        WHERE command = 'run'
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    statuses: dict[str, str] = {}
    for row in rows:
        tool_name = (row["input"] or "").split(" ", 1)[0]
        if tool_name and tool_name not in statuses:
            statuses[tool_name] = row["status"]
    return statuses


class AssistantUi(App[None]):
    """Operational Textual surface over the local assistant core."""

    CSS = """
    Screen {
        background: #0f1419;
    }

    Header {
        background: #1f6f8b;
    }

    TabbedContent {
        height: 1fr;
    }

    .toolbar {
        height: auto;
        padding: 1 2;
        background: #15212b;
        border-bottom: solid #2f5368;
    }

    .workflow {
        height: 1fr;
    }

    .left-pane {
        width: 45%;
        height: 1fr;
        border-right: solid #2f5368;
    }

    .main-pane {
        width: 55%;
        height: 1fr;
    }

    .field {
        margin-right: 1;
    }

    .query {
        width: 2fr;
    }

    .small-input {
        width: 14;
    }

    .action {
        width: auto;
        margin-right: 1;
    }

    .meta {
        height: 1;
        padding: 0 2;
        color: #9fb6c4;
        background: #0f1419;
    }

    .preview {
        height: 1fr;
        padding: 1 2;
        background: #101820;
        overflow-y: auto;
    }

    .basket {
        height: 7;
        padding: 1 2;
        background: #151b20;
        border-top: solid #2f5368;
        overflow-y: auto;
    }

    .result-output {
        height: 1fr;
        padding: 1 2;
        background: #101820;
        overflow-y: auto;
    }

    DataTable {
        height: 1fr;
        margin: 0 1 1 1;
        background: #111923;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("/", "focus_search", "Search"),
        ("a", "toggle_source", "Add source"),
        ("o", "open_source", "Show source"),
        ("enter", "preview_selected", "Preview"),
        ("d", "dry_run_tool", "Dry run"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.conn: sqlite3.Connection | None = None
        self.state = UiState()
        self.search_results: list[SearchResult] = []
        self.tool_specs: dict[str, ToolSpec] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="workflow-tabs"):
            with TabPane("Ask", id="ask-tab"):
                yield from self._ask_view()
            with TabPane("Search", id="search-tab"):
                yield from self._search_view()
            with TabPane("Sources", id="sources-tab"):
                yield from self._sources_view()
            with TabPane("Tools", id="tools-tab"):
                yield from self._tools_view()
            with TabPane("Runs", id="runs-tab"):
                yield from self._runs_view()
            with TabPane("Logs", id="logs-tab"):
                yield from self._logs_view()
            with TabPane("Storage", id="storage-tab"):
                yield from self._storage_view()
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Local Assistant"
        self.sub_title = f"DB {self.settings.db_path} | Notes {self.settings.notes_dir}"
        self.conn = connect(self.settings.db_path)
        self._load_tools()
        self._refresh_all()

    def on_unmount(self) -> None:
        if self.conn is not None:
            self.conn.close()

    @on(Button.Pressed, "#search-submit")
    def search_submit(self, _event: Button.Pressed) -> None:
        self.refresh_search(log_action=True)

    @on(Button.Pressed, "#ask-submit")
    def ask_submit(self, _event: Button.Pressed) -> None:
        self.run_ask()

    @on(Button.Pressed, "#clear-sources")
    def clear_sources(self, _event: Button.Pressed) -> None:
        self.state.clear_sources()
        self._refresh_source_basket()

    @on(Button.Pressed, "#remove-source")
    def remove_source(self, _event: Button.Pressed) -> None:
        chunk_id = self._selected_table_int("sources-table")
        if chunk_id is not None:
            self.state.selected_chunk_ids.discard(chunk_id)
            self._refresh_source_basket()

    @on(Button.Pressed, "#tool-dry-run")
    def tool_dry_run_pressed(self, _event: Button.Pressed) -> None:
        self.run_selected_tool(dry_run=True)

    @on(Button.Pressed, "#tool-run")
    def tool_run_pressed(self, _event: Button.Pressed) -> None:
        self.run_selected_tool(dry_run=False)

    @on(DataTable.RowSelected, "#tools-table")
    def tool_row_selected(self, _event: DataTable.RowSelected) -> None:
        tool = self._selected_tool()
        self.state.active_tool = tool.name if tool is not None else None

    def action_focus_search(self) -> None:
        if self._active_tab_id() == "search-tab":
            self.query_one("#search-query", Input).focus()

    def action_refresh(self) -> None:
        self._refresh_all()

    def action_preview_selected(self) -> None:
        active_tab = self._active_tab_id()
        if active_tab == "search-tab":
            self.preview_selected_search_result()
        elif active_tab == "runs-tab":
            self.preview_selected_run()
        elif active_tab == "tools-tab":
            self.preview_selected_tool(confirm_selection=True)

    def action_toggle_source(self) -> None:
        if self._active_tab_id() != "search-tab":
            return
        result = self._selected_search_result()
        if result is None:
            return
        self.state.toggle_source(result.chunk_id)
        self._refresh_source_basket()
        self.preview_selected_search_result()

    def action_open_source(self) -> None:
        if self._active_tab_id() == "search-tab":
            self.preview_selected_search_result(full=True)

    def action_dry_run_tool(self) -> None:
        if self._active_tab_id() == "tools-tab":
            self.run_selected_tool(dry_run=True)

    def refresh_search(self, *, log_action: bool = False) -> None:
        if self.conn is None:
            return
        query = self.query_one("#search-query", Input).value.strip()
        self.state.active_query = query
        if not query:
            self.search_results = []
            self._populate_table("search-results", ("Chunk", "Score", "Title", "Heading", "Modified", "Path"), ())
            self.query_one("#search-meta", Static).update("Enter a query to search indexed notes.")
            self.query_one("#search-preview", Static).update("")
            return
        limit = self._input_int("#search-limit", WORKFLOW_LIMIT)
        tag = self.query_one("#search-tag", Input).value
        path = self.query_one("#search-path", Input).value
        since = self.query_one("#search-since", Input).value
        run_id = start_run(self.conn, "search", query, "notes.search") if log_action else None
        try:
            self.search_results = fetch_search_results(
                self.conn,
                query,
                limit=limit,
                tag=tag,
                path=path,
                since=since,
            )
        except Exception as exc:
            if run_id is not None:
                finish_run(self.conn, run_id, "failed", str(exc))
            raise
        if run_id is not None:
            llm_summary = "llm=none model=none reason=search_only"
            log_event(self.conn, run_id, "llm", llm_summary)
            finish_run(self.conn, run_id, "succeeded", f"results={len(self.search_results)} {llm_summary}")
        rows = [
            (
                result.chunk_id,
                f"{result.score:.3f}",
                result.title,
                result.heading_path or result.heading or "",
                result.modified_at,
                result.path,
            )
            for result in self.search_results
        ]
        self._populate_table("search-results", ("Chunk", "Score", "Title", "Heading", "Modified", "Path"), rows)
        self.query_one("#search-meta", Static).update(f"{len(rows)} results | selected sources={len(self.state.selected_chunk_ids)}")
        self.preview_selected_search_result()

    def preview_selected_search_result(self, *, full: bool = False) -> None:
        result = self._selected_search_result()
        if result is None:
            self.query_one("#search-preview", Static).update("No search result selected.")
            return
        selected = "yes" if result.chunk_id in self.state.selected_chunk_ids else "no"
        body = result.content if full else result.snippet or _excerpt(result.content)
        self.query_one("#search-preview", Static).update(_chunk_text(result, selected=selected, body=body))

    def run_ask(self) -> None:
        if self.conn is None:
            return
        question = self.query_one("#ask-question", Input).value.strip()
        if not question:
            self.query_one("#ask-output", Static).update("Enter a question first.")
            return
        use_selected = self.query_one("#ask-use-selected", Checkbox).value
        use_local_model = self.query_one("#ask-use-local-model", Checkbox).value
        selected_chunk_ids = sorted(self.state.selected_chunk_ids) if use_selected else None
        run_id = start_run(self.conn, "ask", question, "local_answer")
        try:
            local_provider, config_issues = _local_provider_for_ui(self.settings, use_model=use_local_model)
            local_model_requested = use_local_model and self.settings.local_provider is not None
            if config_issues:
                summary = "invalid_local_model_config " + "; ".join(config_issues)
                log_event(self.conn, run_id, "llm_config", summary)
                finish_run(self.conn, run_id, "failed", summary)
                self.query_one("#ask-output", Static).update(f"Invalid local model configuration: {'; '.join(config_issues)}")
                self.refresh_runs()
                return
            answer = answer_question(
                self.conn,
                question,
                limit=self._input_int("#ask-limit", 5),
                use_model=use_local_model,
                local_provider=local_provider,
                chunk_ids=selected_chunk_ids,
            )
            _log_answer(
                self.conn,
                run_id,
                answer,
                local_provider=self.settings.local_provider or "none",
                local_model_requested=local_model_requested,
            )
            summary = (
                f"results={len(answer.results)} llm={answer.llm} model={answer.local_model or 'none'} "
                f"local_model_requested={str(local_model_requested).lower()} "
                f"local_model_used={answer.used_local_model}"
            )
            finish_run(self.conn, run_id, "succeeded", summary)
            self.query_one("#ask-output", Static).update(answer.text)
            self.query_one("#ask-citations", Static).update("\n".join(answer.sources) or "None")
        except Exception as exc:
            finish_run(self.conn, run_id, "failed", str(exc))
            self.query_one("#ask-output", Static).update(f"Ask failed: {exc}")
        self.refresh_runs()

    def refresh_runs(self) -> None:
        if self.conn is None:
            return
        view = fetch_runs(self.conn, self.query_one("#runs-filter", Input).value, self._input_int("#runs-limit", DEFAULT_LIMIT))
        self._populate_table("runs-table", view.columns, view.rows)
        self.query_one("#runs-meta", Static).update(f"{len(view.rows)} runs")
        self.preview_selected_run()

    def preview_selected_run(self) -> None:
        if self.conn is None:
            return
        run_id = self._selected_table_int("runs-table")
        if run_id is None:
            return
        self.state.active_run_id = run_id
        detail = fetch_run_detail(self.conn, run_id)
        if detail is None:
            self.query_one("#run-detail", Static).update("Run not found.")
            return
        events = fetch_run_events(self.conn, run_id)
        self.query_one("#run-detail", Static).update(_run_detail_text(detail, events))

    def refresh_logs(self) -> None:
        if self.conn is None:
            return
        view = fetch_events(self.conn, self.query_one("#logs-filter", Input).value, self._input_int("#logs-limit", DEFAULT_LIMIT))
        self._populate_table("logs-table", view.columns, view.rows)
        self.query_one("#logs-meta", Static).update(f"{len(view.rows)} events")

    def refresh_storage(self) -> None:
        if self.conn is None:
            return
        filter_text = self.query_one("#storage-filter", Input).value
        limit = self._input_int("#storage-limit", DEFAULT_LIMIT)
        documents = fetch_documents(self.conn, filter_text, limit)
        chunks = fetch_chunks(self.conn, filter_text, limit)
        self._populate_table("storage-documents-table", documents.columns, documents.rows)
        self._populate_table("storage-chunks-table", chunks.columns, chunks.rows)
        self.query_one("#storage-meta", Static).update(
            f"{len(documents.rows)} documents | {len(chunks.rows)} chunks | db={self.settings.db_path}"
        )

    def refresh_sources(self) -> None:
        if self.conn is None:
            return
        self._update_source_basket_widgets()
        rows: list[tuple[Any, ...]] = []
        for chunk_id in sorted(self.state.selected_chunk_ids):
            result = fetch_chunk_detail(self.conn, chunk_id)
            if result is not None:
                rows.append((result.chunk_id, result.title, result.heading_path or result.heading or "", result.path))
        self._populate_table("sources-table", ("Chunk", "Title", "Heading", "Path"), rows)

    def refresh_tools(self) -> None:
        if self.conn is None:
            return
        self._load_tools()
        rows = [
            (
                row.name,
                row.risk,
                "yes" if row.requires_approval else "no",
                row.permissions,
                row.args,
                row.last_status,
                row.description,
            )
            for row in fetch_tool_specs(self.settings, self.conn)
        ]
        self._populate_table("tools-table", ("Name", "Risk", "Approval", "Permissions", "Args", "Last", "Description"), rows)
        self.query_one("#tools-meta", Static).update(f"{len(rows)} tools | registry={self.settings.registry_path}")
        self.preview_selected_tool()

    def preview_selected_tool(self, *, confirm_selection: bool = False) -> None:
        tool = self._selected_tool()
        if tool is None:
            self.query_one("#tool-detail", Static).update("No tool selected.")
            return
        if confirm_selection:
            self.state.active_tool = tool.name
        try:
            command = build_command(tool, _parse_tool_arg_input(self.query_one("#tool-args", Input).value))
            command_text = shlex.join(["uv", "run", *command] if command[:2] != ["uv", "run"] else command)
        except Exception as exc:
            command_text = f"Invalid args: {exc}"
        self.query_one("#tool-detail", Static).update(_tool_detail_text(tool, command_text))

    def run_selected_tool(self, *, dry_run: bool) -> None:
        if self.conn is None:
            return
        tool = self._selected_tool()
        if tool is None or self.state.active_tool != tool.name:
            self.query_one("#tool-output", Static).update("Select a tool before running it.")
            return
        try:
            args = _parse_tool_arg_input(self.query_one("#tool-args", Input).value)
            command = build_command(tool, args)
        except Exception as exc:
            self.query_one("#tool-output", Static).update(f"Invalid tool args: {exc}")
            return

        approval_required = _tool_requires_approval(tool)
        approved = self.query_one("#tool-approve", Checkbox).value
        run_input = _tool_run_input(tool.name, args, dry_run=dry_run, approve=approved)
        run_id = start_run(self.conn, "run", run_input, "tools.run")
        metadata = _tool_metadata(tool, command, args, dry_run=dry_run, approve=approved)
        try:
            if dry_run:
                log_event(self.conn, run_id, "dry_run", metadata)
                finish_run(self.conn, run_id, "succeeded", f"tool={tool.name} dry_run=true approval_required={approval_required}")
                self.query_one("#tool-output", Static).update(_dry_run_text(tool, command, approval_required))
                self.refresh_runs()
                self.refresh_tools()
                return
            if approval_required and not approved:
                summary = f"tool={tool.name} blocked approval_required=true risk={tool.risk}"
                log_event(self.conn, run_id, "approval_required", metadata)
                finish_run(self.conn, run_id, "failed", summary)
                self.query_one("#tool-output", Static).update(summary)
                self.refresh_runs()
                self.refresh_tools()
                return
            result = run_tool(tool, command=command, cwd=_tool_working_dir(tool), timeout_seconds=tool.timeout_seconds)
            _log_tool_result(self.conn, run_id, tool, result, metadata)
            self.query_one("#tool-output", Static).update(_tool_result_text(result))
        except Exception as exc:
            finish_run(self.conn, run_id, "failed", str(exc))
            self.query_one("#tool-output", Static).update(f"Tool failed: {exc}")
        self.refresh_runs()
        self.refresh_tools()

    def _refresh_all(self) -> None:
        self.refresh_search()
        self.refresh_sources()
        self.refresh_tools()
        self.refresh_runs()
        self.refresh_logs()
        self.refresh_storage()

    def _search_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Search notes", id="search-query", classes="query field")
                yield Input(value=str(WORKFLOW_LIMIT), placeholder="Limit", id="search-limit", classes="small-input field")
                yield Input(placeholder="Tag", id="search-tag", classes="small-input field")
                yield Input(placeholder="Path", id="search-path", classes="small-input field")
                yield Input(placeholder="Since YYYY-MM-DD", id="search-since", classes="small-input field")
                yield Button("Search", id="search-submit", classes="action")
            yield Static("", id="search-meta", classes="meta")
            with Horizontal(classes="workflow"):
                with Vertical(classes="left-pane"):
                    yield DataTable(id="search-results", zebra_stripes=True, cursor_type="row")
                    yield Static("", id="source-basket-search", classes="basket")
                with Vertical(classes="main-pane"):
                    yield Static("", id="search-preview", classes="preview")

    def _ask_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Ask a question from local notes", id="ask-question", classes="query field")
                yield Input(value="5", placeholder="Limit", id="ask-limit", classes="small-input field")
                yield Checkbox("Use selected sources", False, id="ask-use-selected")
                yield Checkbox("Use local model", True, id="ask-use-local-model")
                yield Label(_local_model_label(self.settings))
                yield Button("Ask", id="ask-submit", classes="action")
            with Horizontal(classes="workflow"):
                with Vertical(classes="left-pane"):
                    yield Static("", id="source-basket-ask", classes="basket")
                    yield Static("", id="ask-citations", classes="preview")
                with Vertical(classes="main-pane"):
                    yield Static("", id="ask-output", classes="result-output")

    def _sources_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Button("Remove Source", id="remove-source", classes="action")
                yield Button("Clear Sources", id="clear-sources", classes="action")
            yield DataTable(id="sources-table", zebra_stripes=True, cursor_type="row")
            yield Static("", id="source-basket-sources", classes="basket")

    def _tools_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="arg=value arg2=value2", id="tool-args", classes="query field")
                yield Checkbox("Approved", False, id="tool-approve")
                yield Button("Dry Run", id="tool-dry-run", classes="action")
                yield Button("Run", id="tool-run", classes="action")
            yield Static("", id="tools-meta", classes="meta")
            with Horizontal(classes="workflow"):
                with Vertical(classes="left-pane"):
                    yield DataTable(id="tools-table", zebra_stripes=True, cursor_type="row")
                with Vertical(classes="main-pane"):
                    yield Static("", id="tool-detail", classes="preview")
                    yield Static("", id="tool-output", classes="preview")

    def _runs_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Command, status, route, input, or summary", id="runs-filter", classes="query field")
                yield Input(value=str(DEFAULT_LIMIT), placeholder="Limit", id="runs-limit", classes="small-input field")
            yield Static("", id="runs-meta", classes="meta")
            with Horizontal(classes="workflow"):
                with Vertical(classes="left-pane"):
                    yield DataTable(id="runs-table", zebra_stripes=True, cursor_type="row")
                with Vertical(classes="main-pane"):
                    yield Static("", id="run-detail", classes="preview")

    def _logs_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Command, event type, or message", id="logs-filter", classes="query field")
                yield Input(value=str(DEFAULT_LIMIT), placeholder="Limit", id="logs-limit", classes="small-input field")
            yield Static("", id="logs-meta", classes="meta")
            yield DataTable(id="logs-table", zebra_stripes=True, cursor_type="row")

    def _storage_view(self) -> ComposeResult:
        with Vertical(classes="workflow"):
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Path, heading, or content", id="storage-filter", classes="query field")
                yield Input(value=str(DEFAULT_LIMIT), placeholder="Limit", id="storage-limit", classes="small-input field")
            yield Static("", id="storage-meta", classes="meta")
            with Horizontal(classes="workflow"):
                with Vertical(classes="left-pane"):
                    yield DataTable(id="storage-documents-table", zebra_stripes=True, cursor_type="row")
                with Vertical(classes="main-pane"):
                    yield DataTable(id="storage-chunks-table", zebra_stripes=True, cursor_type="row")

    def _populate_table(self, table_id: str, columns: tuple[str, ...], rows: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...]) -> None:
        table = self.query_one(f"#{table_id}", DataTable)
        table.clear(columns=True)
        table.add_columns(*columns)
        for row in rows:
            table.add_row(*(str(value) for value in row))

    def _refresh_source_basket(self) -> None:
        self._update_source_basket_widgets()
        self.refresh_sources()

    def _update_source_basket_widgets(self) -> None:
        text = _source_basket_text(self.conn, self.state.selected_chunk_ids)
        for widget_id in ("source-basket-search", "source-basket-ask", "source-basket-sources"):
            try:
                self.query_one(f"#{widget_id}", Static).update(text)
            except Exception:
                pass

    def _selected_search_result(self) -> SearchResult | None:
        table = self.query_one("#search-results", DataTable)
        index = table.cursor_row
        if index is None or index < 0 or index >= len(self.search_results):
            return self.search_results[0] if self.search_results else None
        return self.search_results[index]

    def _selected_table_int(self, table_id: str) -> int | None:
        table = self.query_one(f"#{table_id}", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index < 0:
            return None
        try:
            row = table.get_row_at(row_index)
        except Exception:
            return None
        if not row:
            return None
        try:
            return int(str(row[0]))
        except ValueError:
            return None

    def _selected_tool(self) -> ToolSpec | None:
        table = self.query_one("#tools-table", DataTable)
        row_index = table.cursor_row
        if row_index is None or row_index < 0:
            return None
        else:
            try:
                row = table.get_row_at(row_index)
                name = str(row[0]) if row else None
            except Exception:
                name = None
        if name is None:
            return None
        return self.tool_specs.get(name)

    def _active_tab_id(self) -> str | None:
        tabs = self.query_one("#workflow-tabs", TabbedContent)
        return tabs.active

    def _load_tools(self) -> None:
        self.tool_specs = load_registry(self.settings.registry_path)

    def _input_int(self, selector: str, default: int) -> int:
        raw = self.query_one(selector, Input).value.strip()
        if not raw:
            return default
        try:
            return max(1, int(raw))
        except ValueError:
            return default


def run_ui(settings: Settings) -> None:
    AssistantUi(settings).run()


def _like(value: str) -> str:
    return f"%{value.strip()}%"


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _ask_decision_output(event_messages: dict[str, str]) -> str:
    parts = [
        ("normalized_query", event_messages.get("normalized_query", "")),
        ("retrieved_sources", event_messages.get("retrieved_sources", "")),
        ("llm", event_messages.get("llm", "")),
        ("synthesis", event_messages.get("synthesis", "")),
        ("answer_summary", event_messages.get("answer_summary", "")),
    ]
    cleaned_parts = [f"{label}={value}" for label, value in parts if value]
    if cleaned_parts:
        return " | ".join(cleaned_parts)
    return event_messages.get("answer", "") or event_messages.get("answer_summary", "") or ""


def _chunk_text(result: SearchResult, *, selected: str, body: str) -> str:
    return "\n".join(
        [
            f"chunk_id: {result.chunk_id}",
            f"title: {result.title}",
            f"path: {result.path}",
            f"heading: {result.heading_path or result.heading or ''}",
            f"tags: {', '.join(result.tags) or 'none'}",
            f"modified: {result.modified_at}",
            f"lines: {result.start_line}-{result.end_line}",
            f"tokens: {result.token_count}",
            f"selected: {selected}",
            "",
            body,
        ]
    )


def _source_basket_text(conn: sqlite3.Connection | None, chunk_ids: set[int]) -> str:
    if not chunk_ids:
        return "Selected sources: none"
    lines = ["Selected sources:"]
    for chunk_id in sorted(chunk_ids):
        result = fetch_chunk_detail(conn, chunk_id) if conn is not None else None
        if result is None:
            lines.append(f"- chunk {chunk_id}")
        else:
            lines.append(f"- chunk {chunk_id}: {result.title} | {result.heading_path or result.heading or result.path}")
    return "\n".join(lines)


def _run_detail_text(detail: RunDetail, events: list[EventDetail]) -> str:
    lines = [
        f"run_id: {detail.run_id}",
        f"command: {detail.command}",
        f"route: {detail.route}",
        f"status: {detail.status}",
        f"input: {detail.input}",
        f"summary: {detail.summary}",
        f"started: {detail.started_at}",
        f"finished: {detail.finished_at}",
        "",
        "Events:",
    ]
    lines.extend(f"- [{event.created_at}] {event.event_type}: {event.message}" for event in events)
    return "\n".join(lines)


def _tool_detail_text(tool: ToolSpec, command_text: str) -> str:
    return "\n".join(
        [
            f"name: {tool.name}",
            f"description: {tool.description}",
            f"risk: {tool.risk}",
            f"permissions: {', '.join(tool.permissions) or 'none'}",
            f"requires_approval: {_tool_requires_approval(tool)}",
            f"args: {', '.join(_render_arg_spec(arg) for arg in tool.args) or 'none'}",
            f"command: {command_text}",
        ]
    )


def _render_arg_spec(arg: Any) -> str:
    marker = "*" if arg.required else ""
    default = f"={arg.default}" if arg.default is not None else ""
    return f"{arg.name}{marker}:{arg.type}{default}"


def _parse_tool_arg_input(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in shlex.split(value):
        if "=" not in part:
            raise ValueError(f"tool args must be name=value, got {part!r}")
        name, raw_value = part.split("=", 1)
        if not name:
            raise ValueError("tool arg name cannot be empty")
        parsed[name] = raw_value
    return parsed


def _tool_requires_approval(tool: ToolSpec) -> bool:
    return tool.requires_approval or tool.risk in {"medium", "high"}


def _tool_working_dir(tool: ToolSpec) -> Path | None:
    if tool.working_dir is None:
        return None
    path = Path(tool.working_dir).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _tool_run_input(tool_name: str, args: dict[str, str], *, dry_run: bool, approve: bool) -> str:
    rendered_args = " ".join(f"--arg {name}={args[name]}" for name in sorted(args))
    parts = [tool_name]
    if rendered_args:
        parts.append(rendered_args)
    if dry_run:
        parts.append("--dry-run")
    if approve:
        parts.append("--approve")
    return " ".join(parts)


def _tool_metadata(
    tool: ToolSpec,
    command: list[str],
    args: dict[str, str],
    *,
    dry_run: bool,
    approve: bool,
) -> str:
    rendered_args = ",".join(f"{name}={args[name]}" for name in sorted(args)) or "none"
    return (
        f"tool={tool.name} command={shlex.join(command)} dry_run={str(dry_run).lower()} "
        f"args={rendered_args} risk={tool.risk} permissions={','.join(tool.permissions) or 'none'} "
        f"requires_approval={str(_tool_requires_approval(tool)).lower()} approved={str(approve).lower()}"
    )


def _dry_run_text(tool: ToolSpec, command: list[str], approval_required: bool) -> str:
    return "\n".join(
        [
            f"tool={tool.name}",
            f"command={shlex.join(command)}",
            f"risk={tool.risk}",
            f"permissions={','.join(tool.permissions) or 'none'}",
            f"requires_approval={str(approval_required).lower()}",
        ]
    )


def _local_provider_for_ui(settings: Settings, *, use_model: bool) -> tuple[LocalModelProvider | None, list[str]]:
    if not use_model:
        return None, []
    config_issues = validate_local_model_settings(settings)
    provider: LocalModelProvider | None = None
    provider_config_error: str | None = None
    try:
        provider = build_local_provider(settings)
    except Exception as exc:
        provider_config_error = str(exc)
    if provider_config_error:
        config_issues.append(provider_config_error)
    return provider, config_issues


def _local_model_label(settings: Settings) -> str:
    if settings.local_provider is None:
        return "Model: extractive"
    return f"Model: {settings.local_model or settings.local_provider}"


def _log_answer(
    conn: sqlite3.Connection,
    run_id: int,
    answer: AnswerResult,
    *,
    local_provider: str,
    local_model_requested: bool,
) -> None:
    log_event(conn, run_id, "normalized_query", answer.normalized_query)
    log_event(conn, run_id, "retrieved_sources", "\n".join(answer.sources) or "none")
    log_event(
        conn,
        run_id,
        "llm",
        f"llm={answer.llm} model={answer.local_model or 'none'} local_provider={local_provider}",
    )
    log_event(
        conn,
        run_id,
        "synthesis",
        (
            f"llm={answer.llm} model={answer.local_model or 'none'} "
            f"local_model_requested={local_model_requested} local_model_used={answer.used_local_model} "
            f"prompt_chunks={answer.prompt_chunk_count} prompt_chars={answer.prompt_char_count} fallback=extractive"
        ),
    )
    log_event(conn, run_id, "answer", answer.answer)
    log_event(conn, run_id, "answer_summary", answer.summary)


def _log_tool_result(conn: sqlite3.Connection, run_id: int, tool: ToolSpec, result: ToolResult, metadata: str) -> None:
    summary = (
        f"tool={tool.name} status={result.status} returncode={result.returncode} "
        f"duration_ms={result.duration_ms} timed_out={str(result.timed_out).lower()}"
    )
    log_event(conn, run_id, "tool", f"{metadata} {summary}")
    if result.structured_output:
        structured_summary = str(result.structured_output.get("summary", ""))
        if structured_summary:
            log_event(conn, run_id, "structured_summary", structured_summary)
    if result.artifacts:
        log_event(conn, run_id, "artifacts", "\n".join(result.artifacts))
    finish_run(conn, run_id, result.status, summary)


def _tool_result_text(result: ToolResult) -> str:
    return "\n".join(
        [
            f"status: {result.status}",
            f"returncode: {result.returncode}",
            f"duration_ms: {result.duration_ms}",
            "",
            "stdout:",
            result.stdout or "",
            "",
            "stderr:",
            result.stderr or "",
        ]
    )


def _excerpt(value: str, max_chars: int = 900) -> str:
    compact = value.strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
