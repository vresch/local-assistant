from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from assistant.config import get_settings, validate_llama_settings
from assistant.db import cleanup_database, connect
from assistant.logs.debug import get_debug_logger
from assistant.logs.logger import finish_run, log_event, start_run, update_run_route
from assistant.notes.categorizer import NoteCategory, categorise_notes
from assistant.notes.indexer import index_notes
from assistant.notes.search import SearchResult, get_chunk, search_notes
from assistant.orchestrator import answer_question
from assistant.providers.remote import build_remote_provider
from assistant.research import research_question
from assistant.tools.registry import load_registry
from assistant.tools.runner import run_tool
from assistant.ui import run_ui


app = typer.Typer(no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)

DASHBOARD_BORDER = "cyan"
DASHBOARD_HEADER = "bold cyan"
DASHBOARD_LABEL = "cyan"
DASHBOARD_VALUE = "white"
DASHBOARD_MUTED = "dim"
DASHBOARD_GOOD = "green"
DASHBOARD_WARN = "yellow"
DASHBOARD_BAD = "bold red"
DASHBOARD_LLM = "magenta"

STATUS_STYLES = {
    "succeeded": DASHBOARD_GOOD,
    "failed": DASHBOARD_BAD,
    "running": DASHBOARD_WARN,
}


@app.command()
def index() -> None:
    """Index Markdown notes from ~/notes."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=index notes_dir=%s db_path=%s", settings.notes_dir, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "index", str(settings.notes_dir), "notes.index")
        try:
            stats = index_notes(conn, settings.notes_dir)
            summary = (
                f"scanned={stats.scanned} new={stats.new} updated={stats.updated} "
                f"skipped={stats.skipped} removed={stats.removed} failed={stats.failed} chunks={stats.chunks}"
            )
            log_event(conn, run_id, "index", summary)
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=index status=succeeded run_id=%s %s", run_id, summary)
            console.print(summary)
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=index status=failed run_id=%s", run_id)
            raise


@app.command()
def search(
    query: str,
    limit: int = typer.Option(5, "--limit", min=1),
    tag: str | None = typer.Option(None, "--tag"),
    path: str | None = typer.Option(None, "--path"),
    since: str | None = typer.Option(None, "--since"),
) -> None:
    """Search indexed notes."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=search query=%r limit=%s tag=%r path=%r since=%r db_path=%s",
        query,
        limit,
        tag,
        path,
        since,
        settings.db_path,
    )
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "search", query, "notes.search")
        try:
            with _status("Searching notes..."):
                results = search_notes(conn, query, limit=limit, tag=tag, path=path, since=since)
            _print_results(results)
            llm_summary = "llm=none model=none reason=search_only"
            log_event(conn, run_id, "llm", llm_summary)
            summary = f"results={len(results)} {llm_summary}"
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=search status=succeeded run_id=%s %s", run_id, summary)
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=search status=failed run_id=%s", run_id)
            raise


@app.command()
def show(chunk_id: int) -> None:
    """Show a stored chunk and its metadata."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=show chunk_id=%s db_path=%s", chunk_id, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "show", str(chunk_id), "notes.show")
        try:
            result = get_chunk(conn, chunk_id)
            if result is None:
                summary = f"chunk_id={chunk_id} found=false"
                finish_run(conn, run_id, "failed", summary)
                err_console.print(f"Chunk not found: {chunk_id}")
                raise typer.Exit(1)
            _print_chunk(result)
            summary = f"chunk_id={chunk_id} found=true"
            log_event(conn, run_id, "chunk", summary)
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=show status=succeeded run_id=%s %s", run_id, summary)
        except typer.Exit:
            raise
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=show status=failed run_id=%s", run_id)
            raise


@app.command("categorise-notes")
def categorise_notes_command() -> None:
    """Categorise indexed notes with local keyword rules."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=categorise-notes db_path=%s", settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "categorise-notes", None, "notes.categorise")
        try:
            with _status("Categorising indexed notes..."):
                categories = categorise_notes(conn)
            _print_categories(categories)
            summary = _category_summary(categories)
            log_event(conn, run_id, "categories", summary)
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=categorise-notes status=succeeded run_id=%s %s", run_id, summary)
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=categorise-notes status=failed run_id=%s", run_id)
            raise


@app.command()
def ask(question: str, limit: int = 5, no_model: bool = False) -> None:
    """Answer a question from indexed notes."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=ask question=%r limit=%s no_model=%s db_path=%s",
        question,
        limit,
        no_model,
        settings.db_path,
    )
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "ask", question, "local_answer")
        try:
            llm_config_issues = [] if no_model else validate_llama_settings(settings)
            if llm_config_issues:
                summary = "invalid_llm_config " + "; ".join(llm_config_issues)
                log_event(conn, run_id, "llm_config", summary)
                finish_run(conn, run_id, "failed", summary)
                debug.error("command=ask status=failed run_id=%s %s", run_id, summary)
                for issue in llm_config_issues:
                    err_console.print(f"Invalid LLM configuration: {issue}", soft_wrap=True)
                raise typer.Exit(1)

            with _status("Thinking with local notes..."):
                answer = answer_question(
                    conn,
                    question,
                    limit=limit,
                    use_model=not no_model,
                    model_path=settings.llama_model_path,
                    context_size=settings.llama_context_size,
                    max_tokens=settings.llama_max_tokens,
                    temperature=settings.llama_temperature,
                )
            log_event(conn, run_id, "normalized_query", answer.normalized_query)
            log_event(conn, run_id, "retrieved_sources", "\n".join(answer.sources) or "none")
            log_event(
                conn,
                run_id,
                "llm",
                f"llm={answer.llm} model={answer.local_model or 'none'}",
            )
            log_event(
                conn,
                run_id,
                "synthesis",
                (
                    f"llm={answer.llm} "
                    f"model={answer.local_model or 'none'} "
                    f"local_model_used={answer.used_local_model}"
                ),
            )
            log_event(conn, run_id, "answer", answer.answer)
            log_event(conn, run_id, "answer_summary", answer.summary)
            console.print(answer.text)
            summary = (
                f"results={len(answer.results)} "
                f"llm={answer.llm} "
                f"model={answer.local_model or 'none'} "
                f"local_model_used={answer.used_local_model}"
            )
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=ask status=succeeded run_id=%s %s", run_id, summary)
        except typer.Exit:
            raise
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=ask status=failed run_id=%s", run_id)
            raise


@app.command()
def research(
    question: str,
    no_remote: bool = typer.Option(False, "--no-remote", help="Use local notes only."),
    force_remote: bool = typer.Option(False, "--force-remote", help="Use the configured remote model after local search."),
    limit: int = typer.Option(8, "--limit", min=1, help="Maximum number of local chunks to retrieve."),
) -> None:
    """Research a question using local notes first and optional remote reasoning."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=research question=%r limit=%s no_remote=%s force_remote=%s db_path=%s",
        question,
        limit,
        no_remote,
        force_remote,
        settings.db_path,
    )
    remote_provider = None
    provider_config_error = None
    if not no_remote:
        try:
            remote_provider = build_remote_provider(settings)
        except Exception as exc:
            provider_config_error = str(exc)

    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "research", question, "routing")
        try:
            if provider_config_error:
                log_event(conn, run_id, "error", provider_config_error)
            with _status("Researching with local notes first..."):
                result = research_question(
                    conn,
                    question,
                    settings.research_dir,
                    limit=limit,
                    allow_remote=not no_remote,
                    force_remote=force_remote,
                    remote_provider=remote_provider,
                )
            update_run_route(conn, run_id, result.route)
            log_event(conn, run_id, "original_question", question)
            log_event(conn, run_id, "normalized_query", result.normalized_query)
            log_event(conn, run_id, "route", result.route)
            log_event(conn, run_id, "retrieved_sources", "\n".join(result.sources) or "none")
            log_event(conn, run_id, "escalation_decision", result.escalation_decision)
            log_event(conn, run_id, "escalation_reason", result.escalation_reason)
            log_event(
                conn,
                run_id,
                "llm",
                (
                    f"route={result.route} "
                    f"remote_used={result.remote_used} "
                    f"provider={result.provider or 'none'} "
                    f"model={result.model or 'none'}"
                ),
            )
            log_event(conn, run_id, "stored_result_path", str(result.stored_path))
            log_event(conn, run_id, "answer_summary", result.summary)
            for error in result.errors:
                log_event(conn, run_id, "error", error)
            console.print(result.text)
            summary = (
                f"results={len(result.results)} "
                f"route={result.route} "
                f"remote_used={result.remote_used} "
                f"stored={result.stored_path}"
            )
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=research status=succeeded run_id=%s %s", run_id, summary)
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=research status=failed run_id=%s", run_id)
            raise


@app.command()
def dashboard(limit: int = 10) -> None:
    """Show a read-only dashboard of local assistant storage and logs."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=dashboard limit=%s db_path=%s", limit, settings.db_path)
    with connect(settings.db_path) as conn:
        counts = _dashboard_counts(conn)
        console.print(
            Panel(
                _dashboard_overview(settings, counts),
                title=Text("Assistant Dashboard", style="bold white"),
                border_style=DASHBOARD_BORDER,
            )
        )
        _print_recent_documents(conn, limit=limit)
        _print_recent_runs(conn, limit=limit)
        _print_last_llm_summary(conn)
        _print_llm_events(conn, limit=limit)


@app.command()
def ui() -> None:
    """Open the Textual database browser."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=ui db_path=%s", settings.db_path)
    run_ui(settings)


@app.command("save-llm-summary")
def save_llm_summary(output: Path | None = typer.Option(None, "--output", "-o")) -> None:
    """Save a markdown summary of the most recent ask run."""
    settings = get_settings()
    output_path = output or settings.llm_summary_path
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=save-llm-summary output=%s db_path=%s", output_path, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "save-llm-summary", str(output_path), "logs.llm_summary")
        try:
            summary = _last_llm_summary(conn)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(summary, encoding="utf-8")
            message = f"saved last_llm_summary={output_path}"
            log_event(conn, run_id, "llm_summary_saved", message)
            finish_run(conn, run_id, "succeeded", message)
            debug.info("command=save-llm-summary status=succeeded run_id=%s output=%s", run_id, output_path)
            console.print(message)
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=save-llm-summary status=failed run_id=%s", run_id)
            raise


@app.command("clean-db")
def clean_db(include_logs: bool = False) -> None:
    """Clear indexed note data from the SQLite database."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=clean-db include_logs=%s db_path=%s", include_logs, settings.db_path)
    with connect(settings.db_path) as conn:
        if include_logs:
            counts = cleanup_database(conn, include_logs=True)
            run_id = start_run(conn, "clean-db", "include_logs=true", "db.cleanup")
            summary = _cleanup_summary(counts, include_logs=True)
            log_event(conn, run_id, "cleanup", summary)
            finish_run(conn, run_id, "succeeded", summary)
        else:
            run_id = start_run(conn, "clean-db", "include_logs=false", "db.cleanup")
            try:
                counts = cleanup_database(conn, include_logs=False)
                summary = _cleanup_summary(counts, include_logs=False)
                log_event(conn, run_id, "cleanup", summary)
                finish_run(conn, run_id, "succeeded", summary)
            except Exception as exc:
                finish_run(conn, run_id, "failed", str(exc))
                debug.exception("command=clean-db status=failed run_id=%s", run_id)
                raise
        debug.info("command=clean-db status=succeeded run_id=%s %s", run_id, summary)
        console.print(summary)


@app.command()
def run(tool_name: str) -> None:
    """Run a registered local tool through uv."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=run tool_name=%r db_path=%s registry_path=%s",
        tool_name,
        settings.db_path,
        settings.registry_path,
    )
    exit_code = 0
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "run", tool_name, "tools.run")
        try:
            registry = load_registry(settings.registry_path)
            tool = registry.get(tool_name)
            if tool is None:
                known = ", ".join(sorted(registry)) or "none"
                raise typer.BadParameter(f"unknown tool {tool_name!r}; known tools: {known}")
            if tool.requires_approval:
                summary = f"tool={tool.name} blocked approval_required=true"
                log_event(conn, run_id, "approval_required", "execution blocked; interactive approval not implemented")
                finish_run(conn, run_id, "failed", summary)
                debug.info("command=run run_id=%s %s", run_id, summary)
                err_console.print(summary)
                raise typer.Exit(1)
            result = run_tool(tool)
            if result.stdout:
                console.print(result.stdout, end="")
            if result.stderr:
                err_console.print(result.stderr, end="")
            summary = f"tool={tool.name} status={result.status} returncode={result.returncode}"
            log_event(conn, run_id, "tool", summary)
            finish_run(conn, run_id, result.status, summary)
            debug.info("command=run run_id=%s %s", run_id, summary)
            exit_code = result.returncode
        except typer.Exit:
            raise
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=run status=failed run_id=%s tool_name=%r", run_id, tool_name)
            raise
    raise typer.Exit(exit_code)


def _print_results(results: list[SearchResult]) -> None:
    console.print("Search Results (Chunk IDs shown)")
    if not results:
        console.print("No results.")
        return
    for index, result in enumerate(results, start=1):
        console.print(f"{index}. [chunk {result.chunk_id}] {result.path}", markup=False)
        console.print(f"   title: {result.title}", markup=False)
        if result.heading_path or result.heading:
            console.print(f"   heading: {result.heading_path or result.heading}", markup=False)
        console.print(f"   modified: {result.modified_at}", markup=False)
        console.print(f"   tags: {', '.join(result.tags) or 'none'}", markup=False)
        console.print(f"   Score: {result.score:.3f}", markup=False)
        console.print(f"   snippet: {result.snippet}", markup=False)


def _print_chunk(result: SearchResult) -> None:
    details = Text()
    _append_dashboard_kv(details, "chunk_id", result.chunk_id, value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(details, "title", result.title)
    _append_dashboard_kv(details, "path", result.path, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(details, "heading_path", result.heading_path or result.heading or "")
    _append_dashboard_kv(details, "modified", result.modified_at, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(details, "tags", ", ".join(result.tags) or "none")
    _append_dashboard_kv(details, "lines", f"{result.start_line}-{result.end_line}")
    _append_dashboard_kv(details, "tokens", result.token_count)
    console.print(Panel(details, title=Text("Chunk Metadata", style="bold white"), border_style=DASHBOARD_BORDER))
    console.print(result.content)


def _print_categories(categories: list[NoteCategory]) -> None:
    table = Table(title="Note Categories")
    table.add_column("Path", overflow="fold")
    table.add_column("Category")
    table.add_column("Score", justify="right")
    table.add_column("Matched Terms", overflow="fold")
    for category in categories:
        table.add_row(
            category.path,
            category.category,
            str(category.score),
            ", ".join(category.matched_terms) or "",
        )
    console.print(table)


def _category_summary(categories: list[NoteCategory]) -> str:
    counts: dict[str, int] = {}
    for category in categories:
        counts[category.category] = counts.get(category.category, 0) + 1
    parts = [f"notes={len(categories)}"]
    parts.extend(f"{name}={counts[name]}" for name in sorted(counts))
    return " ".join(parts)


def _status(message: str):
    if console.is_terminal:
        return console.status(message, spinner="dots")
    return nullcontext()


def _dashboard_counts(conn) -> dict[str, int]:
    return {
        "documents": int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]),
        "chunks": int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]),
        "runs": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
        "run_events": int(conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]),
    }


def _dashboard_overview(settings, counts: dict[str, int]) -> Text:
    overview = Text()
    _append_dashboard_kv(overview, "db_path", settings.db_path, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(overview, "notes_dir", settings.notes_dir, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(overview, "debug_log_path", settings.debug_log_path, value_style=DASHBOARD_MUTED)
    configured_llm = "llama-cpp-python" if settings.llama_model_path else "none"
    _append_dashboard_kv(overview, "configured_llm", configured_llm, value_style=DASHBOARD_LLM)
    _append_dashboard_kv(overview, "configured_model", settings.llama_model_path or "none", value_style=DASHBOARD_LLM)
    overview.append("\n")
    _append_dashboard_kv(overview, "documents", counts["documents"], value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(overview, "chunks", counts["chunks"], value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(overview, "runs", counts["runs"], value_style=DASHBOARD_WARN)
    _append_dashboard_kv(overview, "run_events", counts["run_events"], value_style=DASHBOARD_WARN)
    return overview


def _append_dashboard_kv(text: Text, label: str, value: object, *, value_style: str = DASHBOARD_VALUE) -> None:
    if text.plain:
        text.append("\n")
    text.append(label, style=DASHBOARD_LABEL)
    text.append("=", style=DASHBOARD_MUTED)
    text.append(str(value), style=value_style)


def _dashboard_table(title: str, *, border_style: str = DASHBOARD_BORDER) -> Table:
    return Table(
        title=title,
        title_style=DASHBOARD_HEADER,
        header_style=DASHBOARD_HEADER,
        border_style=border_style,
        row_styles=["none", DASHBOARD_MUTED],
    )


def _styled_status(status: str) -> Text:
    return Text(status, style=STATUS_STYLES.get(status, DASHBOARD_MUTED))


def _print_recent_documents(conn, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT documents.id, documents.path, documents.indexed_at, COUNT(chunks.id) AS chunks
        FROM documents
        LEFT JOIN chunks ON chunks.document_id = documents.id
        GROUP BY documents.id
        ORDER BY documents.indexed_at DESC, documents.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    table = _dashboard_table("Recent Documents")
    table.add_column("ID", justify="right", style=DASHBOARD_MUTED)
    table.add_column("Chunks", justify="right", style=DASHBOARD_GOOD)
    table.add_column("Indexed", style=DASHBOARD_MUTED)
    table.add_column("Path", overflow="fold", style=DASHBOARD_VALUE)
    for row in rows:
        table.add_row(str(row["id"]), str(row["chunks"]), row["indexed_at"], row["path"])
    console.print(table)


def _print_recent_runs(conn, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT id, command, route, status, summary, started_at
        FROM runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    table = _dashboard_table("Recent Runs")
    table.add_column("ID", justify="right", style=DASHBOARD_MUTED)
    table.add_column("Command", style=DASHBOARD_VALUE)
    table.add_column("Status")
    table.add_column("Route", style=DASHBOARD_LLM)
    table.add_column("Started", style=DASHBOARD_MUTED)
    table.add_column("Summary", overflow="fold", style=DASHBOARD_MUTED)
    for row in rows:
        table.add_row(
            str(row["id"]),
            row["command"],
            _styled_status(row["status"]),
            row["route"],
            row["started_at"],
            row["summary"] or "",
        )
    console.print(table)


def _print_llm_events(conn, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT runs.id AS run_id, runs.command, run_events.message, run_events.created_at
        FROM run_events
        JOIN runs ON runs.id = run_events.run_id
        WHERE run_events.event_type = 'llm'
        ORDER BY run_events.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    table = _dashboard_table("LLM Events", border_style=DASHBOARD_LLM)
    table.add_column("Run", justify="right", style=DASHBOARD_MUTED)
    table.add_column("Command", style=DASHBOARD_VALUE)
    table.add_column("Created", style=DASHBOARD_MUTED)
    table.add_column("Message", overflow="fold", style=DASHBOARD_LLM)
    for row in rows:
        table.add_row(str(row["run_id"]), row["command"], row["created_at"], row["message"])
    console.print(table)


def _print_last_llm_summary(conn) -> None:
    try:
        summary = _last_llm_summary(conn)
    except typer.BadParameter:
        summary = "No ask runs found."
    console.print(
        Panel(
            Text(summary, style=DASHBOARD_VALUE),
            title=Text("Last LLM Request Summary", style="bold white"),
            border_style=DASHBOARD_LLM,
        )
    )


def _last_llm_summary(conn) -> str:
    run = conn.execute(
        """
        SELECT id, input, status, summary, started_at, finished_at
        FROM runs
        WHERE command = 'ask'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if run is None:
        raise typer.BadParameter("no ask runs found")

    events = conn.execute(
        """
        SELECT event_type, message
        FROM run_events
        WHERE run_id = ?
        ORDER BY id
        """,
        (run["id"],),
    ).fetchall()
    event_messages = {event["event_type"]: event["message"] for event in events}

    return "\n".join(
        [
            "# Last LLM Request Summary",
            "",
            f"- Run ID: {run['id']}",
            f"- Question: {run['input'] or ''}",
            f"- Status: {run['status']}",
            f"- Started: {run['started_at']}",
            f"- Finished: {run['finished_at'] or ''}",
            f"- Run summary: {run['summary'] or ''}",
            f"- LLM: {event_messages.get('llm', 'none')}",
            f"- Synthesis: {event_messages.get('synthesis', 'none')}",
            f"- Normalized query: {event_messages.get('normalized_query', '')}",
            "",
            "## Answer Summary",
            "",
            event_messages.get("answer", "not stored for this run"),
            "",
            "## Run Summary",
            "",
            event_messages.get("answer_summary", ""),
            "",
            "## Retrieved Sources",
            "",
            event_messages.get("retrieved_sources", "none"),
            "",
        ]
    )


def _cleanup_summary(counts: dict[str, int], include_logs: bool) -> str:
    parts = [
        f"documents={counts['documents']}",
        f"chunks={counts['chunks']}",
        f"chunks_fts={counts['chunks_fts']}",
    ]
    if include_logs:
        parts.extend([f"runs={counts['runs']}", f"run_events={counts['run_events']}"])
    return "deleted " + " ".join(parts)
