from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static, TabPane, TabbedContent

from assistant.config import Settings
from assistant.db import connect


DEFAULT_LIMIT = 200


@dataclass(frozen=True)
class TableView:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


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


class AssistantUi(App[None]):
    """Read-only Textual browser for the local assistant database."""

    CSS = """
    Screen {
        background: #101820;
    }

    Header {
        background: #1f6f8b;
    }

    TabbedContent {
        height: 1fr;
    }

    .tab-body {
        height: 1fr;
    }

    .filters {
        height: auto;
        padding: 1 2;
        background: #16232e;
        border-bottom: solid #2f5368;
    }

    .filter-input {
        width: 1fr;
        margin-right: 1;
    }

    .limit-input {
        width: 16;
    }

    .meta {
        height: 1;
        padding: 0 2;
        color: #9fb6c4;
        background: #101820;
    }

    DataTable {
        height: 1fr;
        margin: 0 1 1 1;
        background: #0f1720;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.conn: sqlite3.Connection | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="database-tabs"):
            yield from self._tab("Documents", "documents", "Path filter")
            yield from self._tab("Chunks", "chunks", "Path, heading, or content filter")
            yield from self._tab("Ask", "ask", "Question, decision output, or answer filter")
            yield from self._tab("Runs", "runs", "Command, status, route, input, or summary filter")
            yield from self._tab("Events", "events", "Command, event type, or message filter")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Local Assistant"
        self.sub_title = str(self.settings.db_path)
        self.conn = connect(self.settings.db_path)
        self.refresh_tables()

    def on_unmount(self) -> None:
        if self.conn is not None:
            self.conn.close()

    @on(Input.Changed)
    def filter_changed(self, _event: Input.Changed) -> None:
        self.refresh_tables()

    def action_refresh(self) -> None:
        self.refresh_tables()

    def refresh_tables(self) -> None:
        if self.conn is None:
            return
        self._refresh_table("documents", fetch_documents)
        self._refresh_table("chunks", fetch_chunks)
        self._refresh_table("ask", fetch_ask_runs)
        self._refresh_table("runs", fetch_runs)
        self._refresh_table("events", fetch_events)

    def _refresh_table(self, key: str, fetch_view) -> None:
        assert self.conn is not None
        filter_text = self.query_one(f"#{key}-filter", Input).value
        limit = self._limit_value(key)
        view = fetch_view(self.conn, filter_text, limit)
        table = self.query_one(f"#{key}-table", DataTable)
        table.clear(columns=True)
        table.add_columns(*view.columns)
        for row in view.rows:
            table.add_row(*(str(value) for value in row))
        self.query_one(f"#{key}-meta", Static).update(f"{len(view.rows)} rows shown | db={self.settings.db_path}")

    def _limit_value(self, key: str) -> int:
        raw_limit = self.query_one(f"#{key}-limit", Input).value.strip()
        if not raw_limit:
            return DEFAULT_LIMIT
        try:
            return max(1, int(raw_limit))
        except ValueError:
            return DEFAULT_LIMIT

    def _tab(self, title: str, key: str, placeholder: str) -> ComposeResult:
        with TabPane(title, id=f"{key}-tab"):
            with Vertical(classes="tab-body"):
                with Horizontal(classes="filters"):
                    yield Input(placeholder=placeholder, id=f"{key}-filter", classes="filter-input")
                    yield Input(value=str(DEFAULT_LIMIT), placeholder="Limit", id=f"{key}-limit", classes="limit-input")
                yield Static("", id=f"{key}-meta", classes="meta")
                yield DataTable(id=f"{key}-table", zebra_stripes=True, cursor_type="row")


def run_ui(settings: Settings) -> None:
    AssistantUi(settings).run()


def _like(value: str) -> str:
    return f"%{value.strip()}%"


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
