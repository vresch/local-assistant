from __future__ import annotations

from dataclasses import dataclass
import sqlite3


TASK_STATUSES = {"open", "active", "blocked", "done", "cancelled"}
MIN_PRIORITY = 1
MAX_PRIORITY = 5


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    description: str | None
    status: str
    priority: int
    source: str | None
    related_path: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class TaskEvent:
    id: int
    task_id: int
    event_type: str
    message: str
    created_at: str


def create_task(
    conn: sqlite3.Connection,
    title: str,
    *,
    description: str | None = None,
    priority: int = 3,
    source: str | None = None,
    related_path: str | None = None,
) -> Task:
    title = _validate_title(title)
    _validate_priority(priority)
    cursor = conn.execute(
        """
        INSERT INTO tasks(title, description, status, priority, source, related_path)
        VALUES (?, ?, 'open', ?, ?, ?)
        """,
        (title, _clean_optional(description), priority, _clean_optional(source), _clean_optional(related_path)),
    )
    conn.commit()
    if cursor.lastrowid is None:
        raise RuntimeError("task insert did not return an id")
    return require_task(conn, int(cursor.lastrowid))


def list_tasks(conn: sqlite3.Connection, *, status: str | None = None, limit: int = 20) -> list[Task]:
    if status is not None:
        _validate_status(status)
    if limit < 1:
        raise ValueError("limit must be at least 1")
    params: list[object] = []
    where = ""
    if status is not None:
        where = "WHERE status = ?"
        params.append(status)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT id, title, description, status, priority, source, related_path, created_at, updated_at, completed_at
        FROM tasks
        {where}
        ORDER BY
          CASE status
            WHEN 'active' THEN 1
            WHEN 'blocked' THEN 2
            WHEN 'open' THEN 3
            WHEN 'done' THEN 4
            WHEN 'cancelled' THEN 5
            ELSE 6
          END,
          priority ASC,
          updated_at DESC,
          id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_task_from_row(row) for row in rows]


def get_task(conn: sqlite3.Connection, task_id: int) -> Task | None:
    row = conn.execute(
        """
        SELECT id, title, description, status, priority, source, related_path, created_at, updated_at, completed_at
        FROM tasks
        WHERE id = ?
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return _task_from_row(row)


def require_task(conn: sqlite3.Connection, task_id: int) -> Task:
    task = get_task(conn, task_id)
    if task is None:
        raise KeyError(f"task not found: {task_id}")
    return task


def update_task(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    status: str | None = None,
    priority: int | None = None,
    description: str | None = None,
    related_path: str | None = None,
) -> Task:
    require_task(conn, task_id)
    assignments = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[object] = []
    if status is not None:
        _validate_status(status)
        assignments.append("status = ?")
        params.append(status)
        if status == "done":
            assignments.append("completed_at = CURRENT_TIMESTAMP")
        else:
            assignments.append("completed_at = NULL")
    if priority is not None:
        _validate_priority(priority)
        assignments.append("priority = ?")
        params.append(priority)
    if description is not None:
        assignments.append("description = ?")
        params.append(_clean_optional(description))
    if related_path is not None:
        assignments.append("related_path = ?")
        params.append(_clean_optional(related_path))
    params.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", params)
    conn.commit()
    return require_task(conn, task_id)


def complete_task(conn: sqlite3.Connection, task_id: int) -> Task:
    return update_task(conn, task_id, status="done")


def cancel_task(conn: sqlite3.Connection, task_id: int) -> Task:
    return update_task(conn, task_id, status="cancelled")


def add_task_note(conn: sqlite3.Connection, task_id: int, message: str) -> TaskEvent:
    require_task(conn, task_id)
    message = message.strip()
    if not message:
        raise ValueError("task note cannot be empty")
    cursor = conn.execute(
        "INSERT INTO task_events(task_id, event_type, message) VALUES (?, 'note', ?)",
        (task_id, message),
    )
    conn.execute("UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
    conn.commit()
    if cursor.lastrowid is None:
        raise RuntimeError("task event insert did not return an id")
    row = conn.execute(
        """
        SELECT id, task_id, event_type, message, created_at
        FROM task_events
        WHERE id = ?
        """,
        (int(cursor.lastrowid),),
    ).fetchone()
    return _event_from_row(row)


def list_task_events(conn: sqlite3.Connection, task_id: int) -> list[TaskEvent]:
    require_task(conn, task_id)
    rows = conn.execute(
        """
        SELECT id, task_id, event_type, message, created_at
        FROM task_events
        WHERE task_id = ?
        ORDER BY id
        """,
        (task_id,),
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def _validate_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise ValueError("task title cannot be empty")
    return title


def _validate_status(status: str) -> None:
    if status not in TASK_STATUSES:
        allowed = ", ".join(sorted(TASK_STATUSES))
        raise ValueError(f"invalid task status {status!r}; allowed: {allowed}")


def _validate_priority(priority: int) -> None:
    if priority < MIN_PRIORITY or priority > MAX_PRIORITY:
        raise ValueError(f"priority must be between {MIN_PRIORITY} and {MAX_PRIORITY}")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=int(row["id"]),
        title=str(row["title"]),
        description=row["description"],
        status=str(row["status"]),
        priority=int(row["priority"]),
        source=row["source"],
        related_path=row["related_path"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=row["completed_at"],
    )


def _event_from_row(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        id=int(row["id"]),
        task_id=int(row["task_id"]),
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        created_at=str(row["created_at"]),
    )
