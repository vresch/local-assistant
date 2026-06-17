from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
import shlex

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from assistant.config import get_settings, validate_local_model_settings
from assistant.db import cleanup_database, connect
from assistant.logs.debug import get_debug_logger
from assistant.logs.logger import finish_run, log_event, start_run, update_run_route
from assistant.notes.categorizer import NoteCategory, categorise_notes
from assistant.notes.indexer import index_notes
from assistant.notes.search import SearchResult, get_chunk, search_notes
from assistant.orchestrator import answer_question
from assistant.providers.local import build_local_provider
from assistant.providers.remote import build_remote_provider
from assistant.research import research_question
from assistant.state.tasks import (
    TASK_STATUSES,
    Task,
    TaskEvent,
    add_task_note,
    cancel_task,
    complete_task,
    create_task,
    get_task,
    list_task_events,
    list_tasks,
    update_task,
)
from assistant.tools.registry import ToolSpec, build_command, load_registry
from assistant.tools.runner import run_tool
from assistant.ui import run_ui


app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
app.add_typer(task_app, name="task", help="Track local task state.")
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

TASK_STATUS_STYLES = {
    "open": DASHBOARD_VALUE,
    "active": DASHBOARD_GOOD,
    "blocked": DASHBOARD_WARN,
    "done": DASHBOARD_MUTED,
    "cancelled": DASHBOARD_BAD,
}
OUTPUT_FORMATS = {"text", "json"}


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


@task_app.command("add")
def task_add(
    title: str,
    description: str | None = typer.Option(None, "--description", "-d"),
    priority: int = typer.Option(3, "--priority", "-p", min=1, max=5),
    source: str | None = typer.Option(None, "--source"),
    related_path: str | None = typer.Option(None, "--related-path"),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Add a local task."""
    _validate_output_format(output_format)
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=task.add title=%r priority=%s db_path=%s", title, priority, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "task add", title, "state.tasks")
        try:
            task = create_task(
                conn,
                title,
                description=description,
                priority=priority,
                source=source,
                related_path=related_path,
            )
            summary = f"task_id={task.id} status={task.status} priority={task.priority}"
            log_event(conn, run_id, "task_created", _task_log_summary(task))
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=task.add status=succeeded run_id=%s %s", run_id, summary)
            _print_task_command_result(task, output_format=output_format)
        except Exception as exc:
            if isinstance(exc, (KeyError, ValueError)):
                message = _task_error_message(exc)
                finish_run(conn, run_id, "failed", message)
                err_console.print(message)
                raise typer.Exit(1) from exc
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=task.add status=failed run_id=%s", run_id)
            raise


@task_app.command("list")
def task_list(
    status: str | None = typer.Option(None, "--status", help=f"One of: {', '.join(sorted(TASK_STATUSES))}."),
    limit: int = typer.Option(20, "--limit", min=1),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """List local tasks."""
    _validate_output_format(output_format)
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=task.list status=%r limit=%s db_path=%s", status, limit, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "task list", status or "", "state.tasks")
        try:
            tasks = list_tasks(conn, status=status, limit=limit)
            _print_task_list(tasks, output_format=output_format)
            summary = f"tasks={len(tasks)} status={status or 'any'}"
            log_event(conn, run_id, "task_list", summary)
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=task.list status=succeeded run_id=%s %s", run_id, summary)
        except Exception as exc:
            if isinstance(exc, (KeyError, ValueError)):
                message = _task_error_message(exc)
                finish_run(conn, run_id, "failed", message)
                err_console.print(message)
                raise typer.Exit(1) from exc
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=task.list status=failed run_id=%s", run_id)
            raise


@task_app.command("show")
def task_show(
    task_id: int,
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Show a local task and its notes."""
    _validate_output_format(output_format)
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=task.show task_id=%s db_path=%s", task_id, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "task show", str(task_id), "state.tasks")
        try:
            task = get_task(conn, task_id)
            if task is None:
                summary = f"task_id={task_id} found=false"
                finish_run(conn, run_id, "failed", summary)
                err_console.print(f"Task not found: {task_id}")
                raise typer.Exit(1)
            events = list_task_events(conn, task_id)
            _print_task_detail(task, events, output_format=output_format)
            summary = f"task_id={task.id} found=true notes={len(events)}"
            log_event(conn, run_id, "task_show", summary)
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=task.show status=succeeded run_id=%s %s", run_id, summary)
        except typer.Exit:
            raise
        except Exception as exc:
            if isinstance(exc, (KeyError, ValueError)):
                message = _task_error_message(exc)
                finish_run(conn, run_id, "failed", message)
                err_console.print(message)
                raise typer.Exit(1) from exc
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=task.show status=failed run_id=%s", run_id)
            raise


@task_app.command("set")
def task_set(
    task_id: int,
    status: str | None = typer.Option(None, "--status", help=f"One of: {', '.join(sorted(TASK_STATUSES))}."),
    priority: int | None = typer.Option(None, "--priority", min=1, max=5),
    description: str | None = typer.Option(None, "--description"),
    related_path: str | None = typer.Option(None, "--related-path"),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Update local task fields."""
    _validate_output_format(output_format)
    if status is None and priority is None and description is None and related_path is None:
        raise typer.BadParameter("provide at least one field to update")
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=task.set task_id=%s status=%r priority=%r db_path=%s",
        task_id,
        status,
        priority,
        settings.db_path,
    )
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "task set", str(task_id), "state.tasks")
        try:
            task = update_task(
                conn,
                task_id,
                status=status,
                priority=priority,
                description=description,
                related_path=related_path,
            )
            summary = f"task_id={task.id} status={task.status} priority={task.priority}"
            log_event(conn, run_id, "task_updated", _task_log_summary(task))
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=task.set status=succeeded run_id=%s %s", run_id, summary)
            _print_task_command_result(task, output_format=output_format)
        except Exception as exc:
            if isinstance(exc, (KeyError, ValueError)):
                message = _task_error_message(exc)
                finish_run(conn, run_id, "failed", message)
                err_console.print(message)
                raise typer.Exit(1) from exc
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=task.set status=failed run_id=%s", run_id)
            raise


@task_app.command("note")
def task_note(
    task_id: int,
    note: str,
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Add a note to a local task."""
    _validate_output_format(output_format)
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=task.note task_id=%s db_path=%s", task_id, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "task note", str(task_id), "state.tasks")
        try:
            event = add_task_note(conn, task_id, note)
            summary = f"task_id={task_id} note_id={event.id}"
            log_event(conn, run_id, "task_note", event.message)
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=task.note status=succeeded run_id=%s %s", run_id, summary)
            _print_task_note_result(event, output_format=output_format)
        except Exception as exc:
            if isinstance(exc, (KeyError, ValueError)):
                message = _task_error_message(exc)
                finish_run(conn, run_id, "failed", message)
                err_console.print(message)
                raise typer.Exit(1) from exc
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=task.note status=failed run_id=%s", run_id)
            raise


@task_app.command("done")
def task_done(
    task_id: int,
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Mark a local task done."""
    _validate_output_format(output_format)
    _finish_task_command("done", task_id, complete_task, output_format=output_format)


@task_app.command("cancel")
def task_cancel(
    task_id: int,
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Cancel a local task."""
    _validate_output_format(output_format)
    _finish_task_command("cancel", task_id, cancel_task, output_format=output_format)


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
def ask(
    question: str,
    limit: int = typer.Option(5, "--limit", min=1),
    no_model: bool = typer.Option(False, "--no-model", help="Use extractive local-note answers only."),
    model_provider: str | None = typer.Option(None, "--model-provider", help="Local provider to use for this ask."),
    model_required: bool = typer.Option(False, "--model-required", help="Fail if no local provider is configured."),
) -> None:
    """Answer a question from indexed notes."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=ask question=%r limit=%s no_model=%s model_provider=%r model_required=%s db_path=%s",
        question,
        limit,
        no_model,
        model_provider,
        model_required,
        settings.db_path,
    )
    if model_provider and not no_model:
        settings = replace(settings, local_provider=model_provider)

    local_provider = None
    provider_config_error = None
    if not no_model:
        try:
            local_provider = build_local_provider(settings)
        except Exception as exc:
            provider_config_error = str(exc)

    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "ask", question, "local_answer")
        try:
            local_model_requested = not no_model and (settings.local_provider is not None or model_required)
            if model_required and local_provider is None and provider_config_error is None:
                provider_config_error = "local model required but no local provider is configured"

            config_issues = [] if no_model else validate_local_model_settings(settings)
            if provider_config_error:
                config_issues.append(provider_config_error)

            if config_issues:
                summary = "invalid_local_model_config " + "; ".join(config_issues)
                log_event(conn, run_id, "llm_config", summary)
                finish_run(conn, run_id, "failed", summary)
                debug.error("command=ask status=failed run_id=%s %s", run_id, summary)
                for issue in config_issues:
                    err_console.print(f"Invalid local model configuration: {issue}", soft_wrap=True)
                raise typer.Exit(1)

            with _status("Thinking with local notes..."):
                answer = answer_question(
                    conn,
                    question,
                    limit=limit,
                    use_model=not no_model,
                    local_provider=local_provider,
                )
            log_event(conn, run_id, "normalized_query", answer.normalized_query)
            log_event(conn, run_id, "retrieved_sources", "\n".join(answer.sources) or "none")
            log_event(
                conn,
                run_id,
                "llm",
                (
                    f"llm={answer.llm} "
                    f"model={answer.local_model or 'none'} "
                    f"local_provider={settings.local_provider or 'none'}"
                ),
            )
            log_event(
                conn,
                run_id,
                "synthesis",
                (
                    f"llm={answer.llm} "
                    f"model={answer.local_model or 'none'} "
                    f"local_model_requested={local_model_requested} "
                    f"local_model_used={answer.used_local_model} "
                    f"prompt_chunks={answer.prompt_chunk_count} "
                    f"prompt_chars={answer.prompt_char_count} "
                    f"fallback={'none' if answer.used_local_model else 'extractive'}"
                ),
            )
            log_event(conn, run_id, "answer", answer.answer)
            log_event(conn, run_id, "answer_summary", answer.summary)
            console.print(answer.text)
            summary = (
                f"results={len(answer.results)} "
                f"llm={answer.llm} "
                f"model={answer.local_model or 'none'} "
                f"local_model_requested={local_model_requested} "
                f"local_model_used={answer.used_local_model}"
            )
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=ask status=succeeded run_id=%s %s", run_id, summary)
        except typer.Exit:
            raise
        except Exception as exc:
            if local_provider is not None:
                log_event(conn, run_id, "provider_error", str(exc))
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
def run(
    tool_name: str,
    arg_values: list[str] = typer.Option([], "--arg", help="Tool argument as name=value."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and print the resolved command without executing."),
    approve: bool = typer.Option(False, "--approve", help="Approve medium/high risk or approval-required tools."),
) -> None:
    """Run a registered local tool through uv."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info(
        "command=run tool_name=%r dry_run=%s approve=%s db_path=%s registry_path=%s",
        tool_name,
        dry_run,
        approve,
        settings.db_path,
        settings.registry_path,
    )
    exit_code = 0
    with connect(settings.db_path) as conn:
        parsed_args = _parse_tool_arg_values(arg_values)
        run_input = _tool_run_input(tool_name, parsed_args, dry_run=dry_run, approve=approve)
        run_id = start_run(conn, "run", run_input, "tools.run")
        try:
            registry = load_registry(settings.registry_path)
            tool = registry.get(tool_name)
            if tool is None:
                known = ", ".join(sorted(registry)) or "none"
                raise typer.BadParameter(f"unknown tool {tool_name!r}; known tools: {known}")
            resolved_command = build_command(tool, parsed_args)
            cwd = _tool_working_dir(tool)
            approval_required = _tool_requires_approval(tool)
            metadata = _tool_metadata(tool, resolved_command, parsed_args, dry_run=dry_run, approve=approve)
            if dry_run:
                console.print(_dry_run_text(tool, resolved_command, approval_required))
                summary = f"tool={tool.name} dry_run=true approval_required={str(approval_required).lower()}"
                log_event(conn, run_id, "dry_run", metadata)
                finish_run(conn, run_id, "succeeded", summary)
                debug.info("command=run run_id=%s %s", run_id, summary)
                return
            if approval_required and not approve:
                summary = f"tool={tool.name} blocked approval_required=true risk={tool.risk}"
                log_event(conn, run_id, "approval_required", metadata)
                finish_run(conn, run_id, "failed", summary)
                debug.info("command=run run_id=%s %s", run_id, summary)
                err_console.print(summary)
                raise typer.Exit(1)
            result = run_tool(
                tool,
                command=resolved_command,
                cwd=cwd,
                timeout_seconds=tool.timeout_seconds,
            )
            if result.stdout:
                console.print(result.stdout, end="")
            if result.stderr:
                err_console.print(result.stderr, end="")
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
            debug.info("command=run run_id=%s %s", run_id, summary)
            exit_code = result.returncode
        except typer.Exit:
            raise
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=run status=failed run_id=%s tool_name=%r", run_id, tool_name)
            raise
    raise typer.Exit(exit_code)


def _parse_tool_arg_values(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise typer.BadParameter(f"--arg must be name=value, got {value!r}")
        name, raw_value = value.split("=", 1)
        if not name:
            raise typer.BadParameter("--arg name cannot be empty")
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


def _finish_task_command(
    command_name: str,
    task_id: int,
    action: Callable[..., Task],
    *,
    output_format: str,
) -> None:
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=task.%s task_id=%s db_path=%s", command_name, task_id, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, f"task {command_name}", str(task_id), "state.tasks")
        try:
            task = action(conn, task_id)
            summary = f"task_id={task.id} status={task.status} priority={task.priority}"
            log_event(conn, run_id, "task_updated", _task_log_summary(task))
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=task.%s status=succeeded run_id=%s %s", command_name, run_id, summary)
            _print_task_command_result(task, output_format=output_format)
        except Exception as exc:
            if isinstance(exc, (KeyError, ValueError)):
                message = _task_error_message(exc)
                finish_run(conn, run_id, "failed", message)
                err_console.print(message)
                raise typer.Exit(1) from exc
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=task.%s status=failed run_id=%s", command_name, run_id)
            raise


def _task_summary(task: Task) -> str:
    parts = [
        f"task_id={task.id}",
        f"status={task.status}",
        f"priority={task.priority}",
        f"title={task.title}",
    ]
    if task.related_path:
        parts.append(f"related_path={task.related_path}")
    return " ".join(parts)


def _validate_output_format(output_format: str) -> None:
    if output_format not in OUTPUT_FORMATS:
        allowed = ", ".join(sorted(OUTPUT_FORMATS))
        raise typer.BadParameter(f"invalid output format {output_format!r}; allowed: {allowed}")


def _task_error_message(exc: Exception) -> str:
    return str(exc).strip("'")


def _task_to_dict(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "source": task.source,
        "related_path": task.related_path,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
    }


def _task_event_to_dict(event: TaskEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "task_id": event.task_id,
        "event_type": event.event_type,
        "message": event.message,
        "created_at": event.created_at,
    }


def _print_json(payload: object) -> None:
    console.print(json.dumps(payload, indent=2, sort_keys=True), markup=False)


def _print_task_command_result(task: Task, *, output_format: str) -> None:
    if output_format == "json":
        _print_json({"task": _task_to_dict(task)})
        return
    console.print(_task_summary(task))


def _print_task_note_result(event: TaskEvent, *, output_format: str) -> None:
    if output_format == "json":
        _print_json({"note": _task_event_to_dict(event)})
        return
    console.print(f"task_id={event.task_id} note_id={event.id}")


def _print_task_list(tasks: list[Task], *, output_format: str) -> None:
    if output_format == "json":
        _print_json({"tasks": [_task_to_dict(task) for task in tasks]})
        return
    _print_tasks(tasks)


def _print_task_detail(task: Task, events: list[TaskEvent], *, output_format: str) -> None:
    if output_format == "json":
        _print_json(
            {
                "task": _task_to_dict(task),
                "notes": [_task_event_to_dict(event) for event in events],
            }
        )
        return
    _print_task(task, events)


def _task_log_summary(task: Task) -> str:
    return (
        f"task_id={task.id} title={task.title!r} status={task.status} priority={task.priority} "
        f"source={task.source or 'none'} related_path={task.related_path or 'none'}"
    )


def _print_tasks(tasks: list[Task]) -> None:
    table = _dashboard_table("Tasks")
    table.add_column("ID", justify="right", style=DASHBOARD_MUTED)
    table.add_column("Status")
    table.add_column("Priority", justify="right", style=DASHBOARD_GOOD)
    table.add_column("Updated", style=DASHBOARD_MUTED)
    table.add_column("Title", overflow="fold", style=DASHBOARD_VALUE)
    table.add_column("Related", overflow="fold", style=DASHBOARD_MUTED)
    for task in tasks:
        table.add_row(
            str(task.id),
            _styled_task_status(task.status),
            str(task.priority),
            task.updated_at,
            task.title,
            task.related_path or "",
        )
    console.print(table)


def _print_task(task: Task, events: list[TaskEvent]) -> None:
    details = Text()
    _append_dashboard_kv(details, "task_id", task.id, value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(details, "title", task.title)
    _append_dashboard_kv(details, "status", task.status, value_style=TASK_STATUS_STYLES.get(task.status, DASHBOARD_VALUE))
    _append_dashboard_kv(details, "priority", task.priority, value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(details, "created", task.created_at, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(details, "updated", task.updated_at, value_style=DASHBOARD_MUTED)
    if task.completed_at:
        _append_dashboard_kv(details, "completed", task.completed_at, value_style=DASHBOARD_MUTED)
    if task.source:
        _append_dashboard_kv(details, "source", task.source)
    if task.related_path:
        _append_dashboard_kv(details, "related_path", task.related_path, value_style=DASHBOARD_MUTED)
    if task.description:
        _append_dashboard_kv(details, "description", task.description)
    console.print(Panel(details, title=Text("Task", style="bold white"), border_style=DASHBOARD_BORDER))
    if events:
        table = _dashboard_table("Task Notes")
        table.add_column("ID", justify="right", style=DASHBOARD_MUTED)
        table.add_column("Created", style=DASHBOARD_MUTED)
        table.add_column("Message", overflow="fold", style=DASHBOARD_VALUE)
        for event in events:
            table.add_row(str(event.id), event.created_at, event.message)
        console.print(table)


def _styled_task_status(status: str) -> Text:
    return Text(status, style=TASK_STATUS_STYLES.get(status, DASHBOARD_MUTED))


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
        "tasks": int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
    }


def _dashboard_overview(settings, counts: dict[str, int]) -> Text:
    overview = Text()
    _append_dashboard_kv(overview, "db_path", settings.db_path, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(overview, "notes_dir", settings.notes_dir, value_style=DASHBOARD_MUTED)
    _append_dashboard_kv(overview, "debug_log_path", settings.debug_log_path, value_style=DASHBOARD_MUTED)
    configured_llm = settings.local_provider or "none"
    _append_dashboard_kv(overview, "configured_llm", configured_llm, value_style=DASHBOARD_LLM)
    _append_dashboard_kv(overview, "configured_model", settings.local_model or "none", value_style=DASHBOARD_LLM)
    overview.append("\n")
    _append_dashboard_kv(overview, "documents", counts["documents"], value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(overview, "chunks", counts["chunks"], value_style=DASHBOARD_GOOD)
    _append_dashboard_kv(overview, "tasks", counts["tasks"], value_style=DASHBOARD_WARN)
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
