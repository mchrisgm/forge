"""Column migrations on init_db(): create_all never alters existing tables, so
a database created before the thinking feature must gain task.thinking (with
its 'auto' default) when the orchestrator boots on it."""

import sqlite3
from pathlib import Path

from app import db as db_module
from app.config import get_settings
from app.models import Task, ThinkingLevel

OLD_TASK_TABLE = """
CREATE TABLE task (
    id INTEGER PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    prompt VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    opencode_session_id VARCHAR NOT NULL,
    result VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    finished_at DATETIME
);
"""


def create_old_shape_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(OLD_TASK_TABLE)
        conn.execute(
            "INSERT INTO task (id, session_id, prompt, state, opencode_session_id,"
            " result, created_at) VALUES (1, 'sess-1', 'old task', 'done', '', '',"
            " '2026-01-01 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def task_columns(db_path: str) -> dict[str, tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1]: row for row in conn.execute("PRAGMA table_info(task)")}
    finally:
        conn.close()


class TestThinkingColumnMigration:
    def test_old_task_table_gains_thinking_with_auto_default(self):
        db_path = get_settings().db_path
        create_old_shape_db(db_path)
        assert "thinking" not in task_columns(db_path)

        db_module.init_db()

        columns = task_columns(db_path)
        assert "thinking" in columns
        # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk)
        assert columns["thinking"][4] == "'auto'"

        # The pre-existing row was backfilled and reads back through SQLModel.
        with db_module.read_session() as db:
            task = db.get(Task, 1)
        assert task is not None
        assert task.prompt == "old task"
        assert task.thinking == ThinkingLevel.auto

    def test_migration_is_idempotent(self):
        db_path = get_settings().db_path
        create_old_shape_db(db_path)
        db_module.init_db()
        db_module.init_db()  # second boot must not fail on the existing column
        assert "thinking" in task_columns(db_path)

    def test_fresh_db_needs_no_migration(self):
        db_module.init_db()
        columns = task_columns(get_settings().db_path)
        assert "thinking" in columns
