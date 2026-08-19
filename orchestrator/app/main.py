"""FastAPI app factory: routers, auth gating, schedulers, boot reconciliation."""

import asyncio
import contextlib
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import select

from .auth import ensure_auth_seeded, require_auth
from .db import get_setting, init_db, write_session
from .models import Connector
from .routers import (
    auth,
    connectors,
    engines,
    events,
    health,
    models_api,
    openai_router,
    sessions,
    settings_api,
    skills,
    system,
)
from .services.engine_manager import engine_manager
from .services.events import bus
from .services.session_manager import session_manager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("forge")

def seed_connectors() -> None:
    """One row per catalog entry: core connectors keep their defaults,
    integrations start disabled until configured from the Connectors page."""
    from .connector_catalog import CATALOG, DEFAULT_ENABLED

    with write_session() as db:
        existing = {c.kind for c in db.exec(select(Connector)).all()}
        for kind in CATALOG:
            if kind not in existing:
                db.add(Connector(kind=kind, enabled=DEFAULT_ENABLED.get(kind, False)))


def reconcile_interrupted_work() -> None:
    """An orchestrator restart kills in-flight downloads and tasks; without
    this, ModelEntry rows sit in 'downloading' and Task rows in 'running'
    forever. Downloads resume from partial files on the next Download press."""
    from datetime import UTC, datetime

    from .models import ModelEntry, ModelStatus, Task, TaskState

    with write_session() as db:
        for entry in db.exec(
            select(ModelEntry).where(ModelEntry.status == ModelStatus.downloading)
        ).all():
            entry.status = ModelStatus.failed
            entry.note = "orchestrator restarted mid-download — press Download to resume"
            db.add(entry)
        for task in db.exec(
            select(Task).where(Task.state.in_([TaskState.queued, TaskState.running]))  # type: ignore[attr-defined]
        ).all():
            task.state = TaskState.failed
            task.result = "orchestrator restarted while this task was in flight"
            task.finished_at = datetime.now(UTC)
            db.add(task)


def _registry_trigger() -> CronTrigger:
    from .config import get_settings

    expr = get_setting("registry_cron") or get_settings().registry_cron
    try:
        return CronTrigger.from_crontab(expr)
    except ValueError:
        log.warning("invalid registry cron %r, falling back to weekly", expr)
        return CronTrigger.from_crontab("0 6 * * 1")


async def _registry_scan_job() -> None:
    from .services.registry import scan

    try:
        result = await asyncio.to_thread(scan)
        log.info("registry scan: %s", result)
    except Exception:
        log.exception("registry scan failed")


async def _reaper_job() -> None:
    try:
        await session_manager.reap_idle()
    except Exception:
        log.exception("session reaper failed")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_auth_seeded()
    seed_connectors()
    reconcile_interrupted_work()
    bus.bind_loop(asyncio.get_running_loop())

    await asyncio.to_thread(engine_manager.reconcile_on_boot)
    await asyncio.to_thread(session_manager.reconcile_on_boot)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_reaper_job, IntervalTrigger(minutes=5), id="session_reaper")
    scheduler.add_job(_registry_scan_job, _registry_trigger(), id="registry_scan")
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("forge orchestrator up")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forge Orchestrator",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # LAN-only deployment; auth is bearer-token
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(auth.router)

    protected = APIRouter(dependencies=[Depends(require_auth)])
    protected.include_router(system.router)
    protected.include_router(engines.router)
    protected.include_router(models_api.router)
    protected.include_router(sessions.router)
    protected.include_router(skills.router)
    protected.include_router(connectors.router)
    protected.include_router(settings_api.router)
    protected.include_router(events.router)
    api.include_router(protected)

    app.include_router(api)
    # OpenAI-compatible model router for session containers. Unauthenticated
    # by design and unreachable from outside forge-internal: the gateway only
    # forwards /api/* and the orchestrator publishes no host ports.
    app.include_router(openai_router.router)
    return app


app = create_app()
