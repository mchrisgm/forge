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
    for d in (settings.models_dir, settings.skills_dir, settings.workspaces_dir):
        Path(d).mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(get_engine())


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
