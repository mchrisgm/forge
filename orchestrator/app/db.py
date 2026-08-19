"""SQLite via SQLModel. Single-user, single-writer: writes go through one lock
(PLAN §14 — SQLite contention). Schema management is create_all; Postgres is the
documented upgrade path if this ever outgrows one user.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine, select

from .config import get_settings

_engine: Engine | None = None
write_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    settings = get_settings()
    for d in (
        settings.models_dir,
        settings.skills_dir,
        settings.workspaces_dir,
        settings.uploads_dir,
    ):
        Path(d).mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(get_engine())
    _apply_column_migrations()


# create_all never alters existing tables; this adds columns introduced after
# a DB was first created. (key: (table, column) -> ADD COLUMN clause)
_COLUMN_MIGRATIONS: dict[tuple[str, str], str] = {
    ("task", "thinking"): "ALTER TABLE task ADD COLUMN thinking VARCHAR DEFAULT 'auto'",
    ("session", "user_id"): "ALTER TABLE session ADD COLUMN user_id INTEGER",
    ("task", "user_id"): "ALTER TABLE task ADD COLUMN user_id INTEGER",
    ("modelentry", "vision"): "ALTER TABLE modelentry ADD COLUMN vision BOOLEAN DEFAULT 0",
    ("upload", "generated"): "ALTER TABLE upload ADD COLUMN generated BOOLEAN DEFAULT 0",
    ("upload", "prompt"): "ALTER TABLE upload ADD COLUMN prompt VARCHAR DEFAULT ''",
}


def _apply_column_migrations() -> None:
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        for (table, column), ddl in _COLUMN_MIGRATIONS.items():
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if existing and column not in existing:
                conn.execute(text(ddl))
                conn.commit()
        _rebuild_connector_table(conn)
        _ensure_memory_fts(conn)


def _rebuild_connector_table(conn) -> None:
    """Connectors became per-user: the old table's UNIQUE(kind) constraint
    would forbid two users holding the same kind, and SQLite cannot drop a
    constraint in place — rebuild the table once, preserving rows (legacy rows
    keep user_id NULL until the first registered user adopts them)."""
    columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(connector)")}
    if not columns or "user_id" in columns:
        return
    conn.exec_driver_sql("ALTER TABLE connector RENAME TO connector_legacy")
    conn.exec_driver_sql(
        """CREATE TABLE connector (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            kind VARCHAR NOT NULL,
            enabled BOOLEAN NOT NULL,
            config_json VARCHAR NOT NULL
        )"""
    )
    conn.exec_driver_sql(
        "INSERT INTO connector (id, user_id, kind, enabled, config_json) "
        "SELECT id, NULL, kind, enabled, config_json FROM connector_legacy"
    )
    conn.exec_driver_sql("DROP TABLE connector_legacy")
    conn.exec_driver_sql("CREATE INDEX ix_connector_user_id ON connector (user_id)")
    conn.exec_driver_sql("CREATE INDEX ix_connector_kind ON connector (kind)")
    conn.commit()


def _ensure_memory_fts(conn) -> None:
    """External-content FTS5 index over memory entries (BM25 retrieval).
    Falls back silently if this SQLite lacks FTS5 — services/memory degrades
    to LIKE search."""
    try:
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "content, content='memoryentry', content_rowid='id', tokenize='porter')"
        )
        conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memoryentry "
            "BEGIN INSERT INTO memory_fts(rowid, content) VALUES (new.id, new.content); END"
        )
        conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memoryentry "
            "BEGIN INSERT INTO memory_fts(memory_fts, rowid, content) "
            "VALUES ('delete', old.id, old.content); END"
        )
        conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE ON memoryentry "
            "BEGIN "
            "INSERT INTO memory_fts(memory_fts, rowid, content) "
            "VALUES ('delete', old.id, old.content); "
            "INSERT INTO memory_fts(rowid, content) VALUES (new.id, new.content); "
            "END"
        )
        conn.commit()
    except Exception:  # pragma: no cover - FTS5-less build
        conn.rollback()


def fts_available() -> bool:
    engine = get_engine()
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql("SELECT count(*) FROM memory_fts")
            return True
        except Exception:
            return False


@contextmanager
def read_session() -> Iterator[DBSession]:
    with DBSession(get_engine()) as session:
        yield session


@contextmanager
def write_session() -> Iterator[DBSession]:
    """Serialized writer. Commits on success, rolls back on error."""
    with write_lock:
        with DBSession(get_engine()) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise


def get_setting(key: str, default: str = "") -> str:
    from .models import Setting

    with read_session() as db:
        row = db.exec(select(Setting).where(Setting.key == key)).first()
        return row.value if row else default


def set_setting(key: str, value: str) -> None:
    from .models import Setting

    with write_session() as db:
        row = db.exec(select(Setting).where(Setting.key == key)).first()
        if row:
            row.value = value
            db.add(row)
        else:
            db.add(Setting(key=key, value=value))


# FastAPI dependency
def db_dependency() -> Iterator[DBSession]:
    with DBSession(get_engine()) as session:
        yield session
