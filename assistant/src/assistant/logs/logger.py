from __future__ import annotations

import sqlite3


def start_run(conn: sqlite3.Connection, command: str, input_text: str | None, route: str) -> int:
    cursor = conn.execute(
        "INSERT INTO runs(command, input, route, status) VALUES (?, ?, ?, ?)",
        (command, input_text, route, "running"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def log_event(conn: sqlite3.Connection, run_id: int, event_type: str, message: str) -> None:
    conn.execute(
        "INSERT INTO run_events(run_id, event_type, message) VALUES (?, ?, ?)",
        (run_id, event_type, message),
    )
    conn.commit()


def update_run_route(conn: sqlite3.Connection, run_id: int, route: str) -> None:
    conn.execute("UPDATE runs SET route = ? WHERE id = ?", (route, run_id))
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, summary: str) -> None:
    conn.execute(
        """
        UPDATE runs
        SET status = ?, summary = ?, finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, summary, run_id),
    )
    conn.commit()
