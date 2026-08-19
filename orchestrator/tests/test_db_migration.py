"""Boot-time DB shape: column migrations, the connector table rebuild (the
old UNIQUE(kind) constraint must go now that connectors are per-user), legacy
row adoption by the first registered profile, the FTS5 memory index, and
bootstrap's model-catalog seeding.

create_all never alters existing tables, so databases created before a
feature must gain its columns when the orchestrator boots on them.
"""

import sqlite3
import sys
from pathlib import Path

from sqlmodel import select

from app import db as db_module
from app.auth import register_user
from app.config import get_settings
from app.models import Connector, MemoryEntry, Task, ThinkingLevel, User
from tests.conftest import TEST_PASSWORD

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

# Pre-multi-user shape: no user_id, and UNIQUE(kind) — one row per connector
# globally. SQLite cannot drop a constraint in place, so init_db must rebuild.
OLD_CONNECTOR_TABLE = """
CREATE TABLE connector (
    id INTEGER PRIMARY KEY,
    kind VARCHAR NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL,
    config_json VARCHAR NOT NULL
);
"""

OLD_SESSION_TABLE = """
CREATE TABLE session (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    container_id VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    workspace_path VARCHAR NOT NULL,
    model_id INTEGER,
    created_at DATETIME NOT NULL,
    last_active_at DATETIME NOT NULL,
    repo_url VARCHAR,
    last_error VARCHAR NOT NULL
);
"""

OLD_MODELENTRY_TABLE = """
CREATE TABLE modelentry (
    id INTEGER PRIMARY KEY,
    hf_repo VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL
);
"""


def run_script(db_path: str, script: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()


def create_old_shape_db(db_path: str) -> None:
    run_script(db_path, OLD_TASK_TABLE)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO task (id, session_id, prompt, state, opencode_session_id,"
            " result, created_at) VALUES (1, 'sess-1', 'old task', 'done', '', '',"
            " '2026-01-01 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def create_old_connector_db(db_path: str) -> None:
    run_script(db_path, OLD_CONNECTOR_TABLE)
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO connector (kind, enabled, config_json) VALUES (?, ?, ?)",
            [
                ("github", 1, '{"token": "ghp_legacy_secret"}'),
                ("fetch", 1, "{}"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def table_columns(db_path: str, table: str) -> dict[str, tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def task_columns(db_path: str) -> dict[str, tuple]:
    return table_columns(db_path, "task")


# ── ADD COLUMN migrations ───────────────────────────────────────────────────


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


class TestMultiUserColumnMigrations:
    def test_old_task_table_gains_user_id(self):
        db_path = get_settings().db_path
        create_old_shape_db(db_path)
        db_module.init_db()
        assert "user_id" in task_columns(db_path)
        # Legacy tasks read back with no owner.
        with db_module.read_session() as db:
            task = db.get(Task, 1)
        assert task.user_id is None

    def test_old_session_table_gains_user_id(self):
        db_path = get_settings().db_path
        run_script(db_path, OLD_SESSION_TABLE)
        assert "user_id" not in table_columns(db_path, "session")
        db_module.init_db()
        assert "user_id" in table_columns(db_path, "session")

    def test_old_modelentry_table_gains_vision_defaulting_off(self):
        db_path = get_settings().db_path
        run_script(db_path, OLD_MODELENTRY_TABLE)
        assert "vision" not in table_columns(db_path, "modelentry")
        db_module.init_db()
        columns = table_columns(db_path, "modelentry")
        assert "vision" in columns
        assert columns["vision"][4] == "0"


# ── connector table rebuild ─────────────────────────────────────────────────


class TestConnectorTableRebuild:
    def test_rebuild_adds_user_id_and_keeps_legacy_rows(self):
        db_path = get_settings().db_path
        create_old_connector_db(db_path)
        assert "user_id" not in table_columns(db_path, "connector")

        db_module.init_db()

        assert "user_id" in table_columns(db_path, "connector")
        with db_module.read_session() as db:
            rows = {row.kind: row for row in db.exec(select(Connector)).all()}
        assert set(rows) == {"github", "fetch"}
        for row in rows.values():
            assert row.user_id is None  # unadopted until the first user registers
        assert rows["github"].config_json == '{"token": "ghp_legacy_secret"}'
        assert rows["github"].enabled is True

    def test_unique_kind_constraint_is_gone(self):
        """Two users must be able to hold the same connector kind."""
        db_path = get_settings().db_path
        create_old_connector_db(db_path)
        db_module.init_db()

        conn = sqlite3.connect(db_path)
        try:
            conn.executemany(
                "INSERT INTO connector (user_id, kind, enabled, config_json)"
                " VALUES (?, ?, ?, ?)",
                [(1, "notion", 1, "{}"), (2, "notion", 1, "{}")],
            )
            conn.commit()
            count = conn.execute(
                "SELECT count(*) FROM connector WHERE kind = 'notion'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 2

    def test_rebuild_is_idempotent_and_skips_new_shape_tables(self):
        db_path = get_settings().db_path
        create_old_connector_db(db_path)
        db_module.init_db()
        db_module.init_db()  # second boot: table already has user_id — no-op
        with db_module.read_session() as db:
            rows = db.exec(select(Connector)).all()
        assert len(rows) == 2  # nothing duplicated or dropped
        # No leftover scratch table from the rename dance.
        conn = sqlite3.connect(db_path)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        assert "connector_legacy" not in names


class TestLegacyConnectorAdoption:
    def test_first_registered_user_adopts_legacy_rows(self):
        from app.connector_catalog import CATALOG

        create_old_connector_db(get_settings().db_path)
        db_module.init_db()

        first = register_user("admin", TEST_PASSWORD)
        assert first.is_admin is True

        with db_module.read_session() as db:
            rows = db.exec(
                select(Connector).where(Connector.user_id == first.id)
            ).all()
        by_kind = {row.kind: row for row in rows}
        # The legacy github row (with its configured token) now belongs to the
        # first user; the rest of the catalog was seeded around it.
        assert by_kind["github"].config_json == '{"token": "ghp_legacy_secret"}'
        assert set(by_kind) == set(CATALOG)
        with db_module.read_session() as db:
            orphans = db.exec(
                select(Connector).where(Connector.user_id == None)  # noqa: E711
            ).all()
        assert orphans == []

    def test_second_user_does_not_adopt(self):
        create_old_connector_db(get_settings().db_path)
        db_module.init_db()
        first = register_user("admin", TEST_PASSWORD)
        second = register_user("other", TEST_PASSWORD)
        assert second.is_admin is False

        with db_module.read_session() as db:
            github_rows = db.exec(
                select(Connector).where(Connector.kind == "github")
            ).all()
        owners = {row.user_id: row.config_json for row in github_rows}
        assert owners[first.id] == '{"token": "ghp_legacy_secret"}'
        assert owners[second.id] == "{}"  # fresh, unconfigured row


# ── FTS5 memory index + triggers ────────────────────────────────────────────


def fts_match_ids(term: str) -> list[int]:
    engine = db_module.get_engine()
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?", (term,)
        ).all()
    return [row[0] for row in rows]


class TestMemoryFts:
    def _add_entry(self, content: str) -> int:
        with db_module.write_session() as db:
            user = db.exec(select(User)).first()
            if user is None:
                user = User(username="fts-user")
                db.add(user)
                db.flush()
            entry = MemoryEntry(user_id=user.id, content=content)
            db.add(entry)
            db.flush()
            return entry.id

    def test_fts_table_and_triggers_exist_after_init(self):
        db_module.init_db()
        assert db_module.fts_available() is True
        conn = sqlite3.connect(get_settings().db_path)
        try:
            triggers = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
        finally:
            conn.close()
        assert {"memory_fts_ai", "memory_fts_ad", "memory_fts_au"} <= triggers

    def test_insert_is_indexed(self):
        db_module.init_db()
        entry_id = self._add_entry("the user plays accordion on weekends")
        assert fts_match_ids("accordion") == [entry_id]

    def test_update_reindexes(self):
        db_module.init_db()
        entry_id = self._add_entry("the user plays accordion on weekends")
        with db_module.write_session() as db:
            row = db.get(MemoryEntry, entry_id)
            row.content = "the user plays theremin on weekends"
            db.add(row)
        assert fts_match_ids("accordion") == []
        assert fts_match_ids("theremin") == [entry_id]

    def test_delete_removes_from_index(self):
        db_module.init_db()
        entry_id = self._add_entry("the user plays accordion on weekends")
        with db_module.write_session() as db:
            row = db.get(MemoryEntry, entry_id)
            db.delete(row)
        assert fts_match_ids("accordion") == []
        assert entry_id  # (kept for clarity: the row existed before delete)


# ── bootstrap: model catalog seeding on an empty DB ─────────────────────────


def _reload_seed_module():
    """scripts/seed_models keeps its ModelEntry instances at module level; a
    fresh import gives pristine (unpersisted) instances for this test's DB."""
    sys.modules.pop("scripts.seed_models", None)
    sys.modules.pop("scripts", None)
    root = str(Path(db_module.__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)


class TestBootstrapModelSeeding:
    def test_seeds_the_catalog_on_an_empty_db_only(self):
        from app.models import ModelEntry, ModelStatus
        from app.services import bootstrap

        db_module.init_db()
        _reload_seed_module()
        created = bootstrap.seed_model_catalog_if_empty()

        from scripts.seed_models import SEED_MODELS

        assert created == len(SEED_MODELS) == 6
        with db_module.read_session() as db:
            rows = db.exec(select(ModelEntry)).all()
        assert len(rows) == 6
        # Seeds arrive approved (visible, not yet downloaded), never ready.
        assert {row.status for row in rows} == {ModelStatus.approved}
        assert all(row.hf_repo for row in rows)

        # Second boot: the table is non-empty, so seeding is a no-op.
        assert bootstrap.seed_model_catalog_if_empty() == 0
        with db_module.read_session() as db:
            assert len(db.exec(select(ModelEntry)).all()) == 6

    def test_a_single_manual_entry_suppresses_seeding(self):
        from app.models import ModelEntry
        from app.services import bootstrap

        db_module.init_db()
        with db_module.write_session() as db:
            db.add(ModelEntry(hf_repo="me/my-model", display_name="Mine"))
        _reload_seed_module()
        assert bootstrap.seed_model_catalog_if_empty() == 0
        with db_module.read_session() as db:
            rows = db.exec(select(ModelEntry)).all()
        assert [row.hf_repo for row in rows] == ["me/my-model"]
