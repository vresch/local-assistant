from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import assistant.cli as cli
from assistant.db import connect
from assistant.state.tasks import (
    add_task_note,
    cancel_task,
    complete_task,
    create_task,
    list_task_events,
    list_tasks,
    update_task,
)


runner = CliRunner()


def test_task_storage_create_update_filter_and_persist(tmp_path: Path) -> None:
    db_path = tmp_path / "assistant.db"
    with connect(db_path) as conn:
        task = create_task(
            conn,
            "Review local provider tests",
            description="Confirm model fallback behavior.",
            priority=2,
            source="manual",
            related_path="assistant/tests/test_local_providers.py",
        )
        other = create_task(conn, "Write task docs")
        active = update_task(conn, task.id, status="active", priority=1)
        event = add_task_note(conn, active.id, "Blocked on config decision")

    with connect(db_path) as conn:
        active_tasks = list_tasks(conn, status="active")
        open_tasks = list_tasks(conn, status="open")
        events = list_task_events(conn, active.id)

    assert active.status == "active"
    assert active.priority == 1
    assert [task.id for task in active_tasks] == [active.id]
    assert [task.id for task in open_tasks] == [other.id]
    assert event.message == "Blocked on config decision"
    assert events[0].message == "Blocked on config decision"


def test_task_storage_done_cancel_and_validation(tmp_path: Path) -> None:
    with connect(tmp_path / "assistant.db") as conn:
        done = complete_task(conn, create_task(conn, "Finish task state").id)
        cancelled = cancel_task(conn, create_task(conn, "Drop stale task").id)

        with pytest.raises(ValueError, match="invalid task status"):
            update_task(conn, done.id, status="waiting")
        with pytest.raises(ValueError, match="priority must be between"):
            create_task(conn, "Bad priority", priority=6)
        with pytest.raises(ValueError, match="task title cannot be empty"):
            create_task(conn, " ")

    assert done.status == "done"
    assert done.completed_at is not None
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is None


def test_cli_task_commands_persist_and_log(tmp_path: Path) -> None:
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(tmp_path / "notes"),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    add_result = runner.invoke(
        cli.app,
        [
            "task",
            "add",
            "Review local provider tests",
            "--description",
            "Confirm fallback behavior.",
            "--priority",
            "2",
            "--related-path",
            "assistant/tests/test_local_providers.py",
        ],
        env=env,
    )
    assert add_result.exit_code == 0
    assert "task_id=1" in add_result.output
    assert "status=open" in add_result.output

    list_result = runner.invoke(cli.app, ["task", "list", "--status", "open"], env=env)
    assert list_result.exit_code == 0
    assert "Review local" in list_result.output
    assert "viders.py" in list_result.output
    assert "open" in list_result.output

    set_result = runner.invoke(cli.app, ["task", "set", "1", "--status", "active", "--priority", "1"], env=env)
    assert set_result.exit_code == 0
    assert "status=active" in set_result.output
    assert "priority=1" in set_result.output

    note_result = runner.invoke(cli.app, ["task", "note", "1", "Blocked on config decision"], env=env)
    assert note_result.exit_code == 0
    assert "note_id=1" in note_result.output

    show_result = runner.invoke(cli.app, ["task", "show", "1"], env=env)
    assert show_result.exit_code == 0
    assert "Task" in show_result.output
    assert "Review local provider tests" in show_result.output
    assert "Blocked on config decision" in show_result.output

    done_result = runner.invoke(cli.app, ["task", "done", "1"], env=env)
    assert done_result.exit_code == 0
    assert "status=done" in done_result.output

    with sqlite3.connect(db_path) as conn:
        runs = conn.execute("SELECT command, route, status FROM runs ORDER BY id").fetchall()
        task = conn.execute("SELECT title, status, priority, completed_at FROM tasks WHERE id = 1").fetchone()
        events = conn.execute("SELECT event_type, message FROM task_events WHERE task_id = 1").fetchall()

    assert [run[0] for run in runs] == ["task add", "task list", "task set", "task note", "task show", "task done"]
    assert {run[1] for run in runs} == {"state.tasks"}
    assert all(run[2] == "succeeded" for run in runs)
    assert task[0] == "Review local provider tests"
    assert task[1] == "done"
    assert task[2] == 1
    assert task[3] is not None
    assert events == [("note", "Blocked on config decision")]


def test_cli_task_cancel_and_invalid_status(tmp_path: Path) -> None:
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(tmp_path / "notes"),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    assert runner.invoke(cli.app, ["task", "add", "Drop stale task"], env=env).exit_code == 0
    cancel_result = runner.invoke(cli.app, ["task", "cancel", "1"], env=env)
    invalid_result = runner.invoke(cli.app, ["task", "list", "--status", "waiting"], env=env)

    assert cancel_result.exit_code == 0
    assert "status=cancelled" in cancel_result.output
    assert invalid_result.exit_code != 0
    assert "invalid task status" in invalid_result.output


def test_cli_task_json_output(tmp_path: Path) -> None:
    db_path = tmp_path / "assistant.db"
    env = {
        "ASSISTANT_NOTES_DIR": str(tmp_path / "notes"),
        "ASSISTANT_DB_PATH": str(db_path),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    add_result = runner.invoke(
        cli.app,
        ["task", "add", "Review JSON output", "--priority", "1", "--format", "json"],
        env=env,
    )
    assert add_result.exit_code == 0
    add_payload = json.loads(add_result.output)
    assert add_payload["task"]["id"] == 1
    assert add_payload["task"]["title"] == "Review JSON output"
    assert add_payload["task"]["priority"] == 1

    note_result = runner.invoke(cli.app, ["task", "note", "1", "Machine-readable note", "--format", "json"], env=env)
    assert note_result.exit_code == 0
    note_payload = json.loads(note_result.output)
    assert note_payload["note"]["task_id"] == 1
    assert note_payload["note"]["message"] == "Machine-readable note"

    list_result = runner.invoke(cli.app, ["task", "list", "--format", "json"], env=env)
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.output)
    assert [task["title"] for task in list_payload["tasks"]] == ["Review JSON output"]

    show_result = runner.invoke(cli.app, ["task", "show", "1", "--format", "json"], env=env)
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.output)
    assert show_payload["task"]["title"] == "Review JSON output"
    assert show_payload["notes"][0]["message"] == "Machine-readable note"

    done_result = runner.invoke(cli.app, ["task", "done", "1", "--format", "json"], env=env)
    assert done_result.exit_code == 0
    done_payload = json.loads(done_result.output)
    assert done_payload["task"]["status"] == "done"


def test_cli_task_json_output_does_not_render_rich_markup(tmp_path: Path) -> None:
    env = {
        "ASSISTANT_NOTES_DIR": str(tmp_path / "notes"),
        "ASSISTANT_DB_PATH": str(tmp_path / "assistant.db"),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    result = runner.invoke(cli.app, ["task", "add", "Review [red]markup[/red]", "--format", "json"], env=env)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["task"]["title"] == "Review [red]markup[/red]"
    assert "[red]markup[/red]" in result.output


def test_cli_task_rejects_invalid_output_format(tmp_path: Path) -> None:
    env = {
        "ASSISTANT_NOTES_DIR": str(tmp_path / "notes"),
        "ASSISTANT_DB_PATH": str(tmp_path / "assistant.db"),
        "ASSISTANT_DEBUG_LOG_PATH": str(tmp_path / "debug.log"),
        "ASSISTANT_LLAMA_MODEL_PATH": "",
    }

    result = runner.invoke(cli.app, ["task", "list", "--format", "yaml"], env=env)

    assert result.exit_code != 0
    assert "invalid output format" in result.output
