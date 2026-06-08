from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from assistant.config import get_settings
from assistant.db import cleanup_database, connect
from assistant.logs.debug import get_debug_logger
from assistant.logs.logger import finish_run, log_event, start_run
from assistant.notes.indexer import index_notes
from assistant.notes.search import search_notes
from assistant.orchestrator import answer_question
from assistant.tools.registry import load_registry
from assistant.tools.runner import run_tool


app = typer.Typer(no_args_is_help=True)
console = Console()


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
                f"scanned={stats.scanned} indexed={stats.indexed} "
                f"skipped={stats.skipped} chunks={stats.chunks}"
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
def search(query: str, limit: int = 5) -> None:
    """Search indexed notes."""
    settings = get_settings()
    debug = get_debug_logger(settings.debug_log_path)
    debug.info("command=search query=%r limit=%s db_path=%s", query, limit, settings.db_path)
    with connect(settings.db_path) as conn:
        run_id = start_run(conn, "search", query, "notes.search")
        try:
            results = search_notes(conn, query, limit=limit)
            _print_results(results)
            finish_run(conn, run_id, "succeeded", f"results={len(results)}")
            debug.info("command=search status=succeeded run_id=%s results=%s", run_id, len(results))
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=search status=failed run_id=%s", run_id)
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
            answer = answer_question(conn, question, limit=limit, use_model=not no_model)
            log_event(conn, run_id, "normalized_query", answer.normalized_query)
            log_event(conn, run_id, "retrieved_sources", "\n".join(answer.sources) or "none")
            log_event(conn, run_id, "synthesis", f"local_model_used={answer.used_local_model}")
            log_event(conn, run_id, "answer_summary", answer.summary)
            console.print(answer.text)
            summary = f"results={len(answer.results)} local_model_used={answer.used_local_model}"
            finish_run(conn, run_id, "succeeded", summary)
            debug.info("command=ask status=succeeded run_id=%s %s", run_id, summary)
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=ask status=failed run_id=%s", run_id)
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
                log_event(conn, run_id, "approval_required", "approval flag present; interactive approval not implemented")
            result = run_tool(tool)
            if result.stdout:
                console.print(result.stdout, end="")
            if result.stderr:
                console.err.print(result.stderr, end="")
            summary = f"tool={tool.name} status={result.status} returncode={result.returncode}"
            log_event(conn, run_id, "tool", summary)
            finish_run(conn, run_id, result.status, summary)
            debug.info("command=run run_id=%s %s", run_id, summary)
            exit_code = result.returncode
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            debug.exception("command=run status=failed run_id=%s tool_name=%r", run_id, tool_name)
            raise
    raise typer.Exit(exit_code)


def _print_results(results: list[object]) -> None:
    table = Table(title="Search Results")
    table.add_column("Path", overflow="fold")
    table.add_column("Heading")
    table.add_column("Snippet")
    for result in results:
        table.add_row(result.path, result.heading or "", result.snippet)
    console.print(table)


def _cleanup_summary(counts: dict[str, int], include_logs: bool) -> str:
    parts = [
        f"documents={counts['documents']}",
        f"chunks={counts['chunks']}",
        f"chunks_fts={counts['chunks_fts']}",
    ]
    if include_logs:
        parts.extend([f"runs={counts['runs']}", f"run_events={counts['run_events']}"])
    return "deleted " + " ".join(parts)
