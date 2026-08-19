"""SessionManager — spawn/stop/reap OpenCode session containers (PLAN §6.3).

Sessions are sibling containers on forge-internal: no docker socket, no GPU,
non-root, resource-limited. Workspace dirs live in the forge-workspaces named
volume; because sibling containers can only bind host paths, the per-session
subdirectory is bound via the volume's host mountpoint.
"""

import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import docker

from ..config import get_settings
from ..db import read_session, write_session
from ..models import (
    Connector,
    ConnectorKind,
    ModelEntry,
    Session,
    SessionState,
)
from ..opencode_config import render_opencode_config_json
from . import docker_util
from .events import bus

log = logging.getLogger(__name__)

SESSION_LABEL = "forge.session"
OPENCODE_PORT = 4096


class SessionError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def container_name(session_id: str) -> str:
    return f"forge-session-{session_id[:12]}"


def opencode_base_url(session_id: str) -> str:
    return f"http://{container_name(session_id)}:{OPENCODE_PORT}"


def _publish_state(session: Session) -> None:
    bus.publish(
        "session.state",
        {
            "session_id": session.id,
            "user_id": session.user_id,
            "state": session.state.value,
            "name": session.name,
        },
    )


class SessionManager:
    def _workspace_paths(self, session_id: str) -> tuple[Path, str]:
        """(orchestrator-visible path, host path for sibling bind mount)."""
        settings = get_settings()
        local = Path(settings.workspaces_dir) / session_id
        host_root = docker_util.volume_host_mountpoint(settings.workspaces_volume)
        host = f"{host_root}/{session_id}" if host_root else str(local)
        return local, host

    async def create(
        self,
        name: str,
        model_id: int,
        repo_url: str | None = None,
        user_id: int | None = None,
    ) -> Session:
        import asyncio

        settings = get_settings()
        with read_session() as db:
            model = db.get(ModelEntry, model_id)
        if model is None:
            raise SessionError("model not found", 404)
        from ..opencode_config import airllm_blocked

        if airllm_blocked(model):
            raise SessionError("AirLLM models are chat-only and cannot power sessions", 400)

        with read_session() as db:
            from sqlmodel import select

            rows = db.exec(select(Session)).all()
            active = sum(
                1 for s in rows if s.state in (SessionState.creating, SessionState.running)
            )
        if active >= settings.max_parallel_sessions:
            raise SessionError(
                f"max parallel sessions reached ({settings.max_parallel_sessions})", 409
            )

        session = Session(
            name=name, model_id=model_id, repo_url=repo_url, user_id=user_id
        )
        local_ws, _ = self._workspace_paths(session.id)
        session.workspace_path = str(local_ws)
        with write_session() as db:
            db.add(session)
            db.flush()
            session_id = session.id
        # Re-fetch: the added instance is expired+detached after commit.
        with read_session() as db:
            session = db.get(Session, session_id)
        _publish_state(session)

        asyncio.get_running_loop().create_task(self._spawn(session_id))
        return session

    async def _spawn(self, session_id: str) -> None:
        import asyncio

        try:
            container = await asyncio.to_thread(self._spawn_blocking, session_id)
            with write_session() as db:
                session = db.get(Session, session_id)
                if session:
                    session.container_id = container.id
                    session.state = SessionState.running
                    session.last_active_at = datetime.now(UTC)
                    db.add(session)
            with read_session() as db:
                session = db.get(Session, session_id)
            if session:
                _publish_state(session)
        except Exception as exc:
            log.exception("session spawn failed")
            with write_session() as db:
                session = db.get(Session, session_id)
                if session:
                    session.state = SessionState.error
                    session.last_error = str(exc)
                    db.add(session)
            with read_session() as db:
                session = db.get(Session, session_id)
            if session:
                _publish_state(session)

    def _spawn_blocking(self, session_id: str) -> Any:
        settings = get_settings()
        with read_session() as db:
            session = db.get(Session, session_id)
            model = db.get(ModelEntry, session.model_id) if session else None
            from sqlmodel import select

            owner_id = session.user_id if session else None
            connectors = list(
                db.exec(
                    select(Connector).where(Connector.user_id == owner_id)
                ).all()
            )
        if session is None or model is None:
            raise RuntimeError("session or model vanished during spawn")

        local_ws, host_ws = self._workspace_paths(session_id)
        local_ws.mkdir(parents=True, exist_ok=True)

        config_json = render_opencode_config_json(model, connectors, settings)

        env: dict[str, str] = {
            "FORGE_SESSION_ID": session_id,
            "OPENCODE_CONFIG_CONTENT": config_json,
            "OPENCODE_PORT": str(OPENCODE_PORT),
        }
        if session.repo_url:
            env["FORGE_REPO_URL"] = session.repo_url
        # Connector secrets travel as env vars only (never inside the config
        # JSON), and ONLY while the connector is enabled — the toggle must
        # actually cut access.
        from ..connector_catalog import CATALOG, secret_env_for

        for connector in connectors:
            if not connector.enabled:
                continue
            entry = CATALOG.get(connector.kind)
            if entry is None:
                continue
            try:
                conn_config = json.loads(connector.config_json or "{}")
            except json.JSONDecodeError:
                conn_config = {}
            env.update(secret_env_for(entry, conn_config))
            if connector.kind == ConnectorKind.github.value:
                # Legacy var consumed by the github MCP env template and the
                # entrypoint's git credential store.
                pat = conn_config.get("token", "") or settings.github_pat
                if pat:
                    env["GITHUB_PAT"] = pat

        # Disabled skills are filtered out by the skills MCP server inside the
        # container (the /skills volume mount itself is shared and read-only).
        from sqlmodel import select as sql_select

        from ..models import Skill

        with read_session() as db:
            disabled = [
                Path(s.path).name
                for s in db.exec(sql_select(Skill).where(Skill.enabled == False)).all()  # noqa: E712
            ]
        if disabled:
            env["FORGE_DISABLED_SKILLS"] = ",".join(sorted(disabled))

        mounts = [
            docker.types.Mount(target="/workspace", source=host_ws, type="bind"),
            docker.types.Mount(
                target="/skills",
                source=settings.skills_volume,
                type="volume",
                read_only=True,
            ),
        ]

        # Replace any stale container with the same name (e.g. after error retry)
        try:
            stale = docker_util.client().containers.get(container_name(session_id))
            docker_util.remove_container(stale)
        except docker.errors.NotFound:
            pass

        return docker_util.client().containers.run(
            settings.session_image,
            name=container_name(session_id),
            labels={SESSION_LABEL: session_id},
            network=settings.docker_network,
            environment=env,
            mounts=mounts,
            mem_limit=settings.session_mem_limit,
            nano_cpus=int(settings.session_cpus * 1e9),
            pids_limit=settings.session_pids_limit,
            security_opt=["no-new-privileges:true"],
            detach=True,
            restart_policy={"Name": "no"},
        )

    def _get_container(self, session: Session):
        try:
            return docker_util.client().containers.get(container_name(session.id))
        except docker.errors.NotFound:
            return None

    async def stop(self, session_id: str, reaped: bool = False) -> Session:
        import asyncio

        with read_session() as db:
            session = db.get(Session, session_id)
        if session is None:
            raise SessionError("session not found", 404)
        container = await asyncio.to_thread(self._get_container, session)
        if container is not None:
            await asyncio.to_thread(lambda: container.stop(timeout=10))
        new_state = SessionState.idle if reaped else SessionState.stopped
        with write_session() as db:
            session = db.get(Session, session_id)
            session.state = new_state
            db.add(session)
        with read_session() as db:
            session = db.get(Session, session_id)
        _publish_state(session)
        return session

    async def start(self, session_id: str) -> Session:
        import asyncio

        with read_session() as db:
            session = db.get(Session, session_id)
        if session is None:
            raise SessionError("session not found", 404)
        container = await asyncio.to_thread(self._get_container, session)
        if container is not None:
            await asyncio.to_thread(container.start)
            with write_session() as db:
                session = db.get(Session, session_id)
                session.state = SessionState.running
                session.last_active_at = datetime.now(UTC)
                db.add(session)
            with read_session() as db:
                session = db.get(Session, session_id)
            _publish_state(session)
            return session
        # Container was removed (host reboot, manual cleanup) — respawn it.
        with write_session() as db:
            session = db.get(Session, session_id)
            session.state = SessionState.creating
            db.add(session)
        await self._spawn(session_id)
        with read_session() as db:
            return db.get(Session, session_id)

    async def delete(self, session_id: str) -> None:
        import asyncio

        with read_session() as db:
            session = db.get(Session, session_id)
        if session is None:
            raise SessionError("session not found", 404)
        owner_id = session.user_id
        container = await asyncio.to_thread(self._get_container, session)
        if container is not None:
            await asyncio.to_thread(docker_util.remove_container, container)
        local_ws, _ = self._workspace_paths(session_id)
        if local_ws.exists():
            await asyncio.to_thread(shutil.rmtree, local_ws, True)
        with write_session() as db:
            from sqlmodel import col
            from sqlmodel import delete as sql_delete

            from ..models import Task

            db.exec(sql_delete(Task).where(col(Task.session_id) == session_id))
            session = db.get(Session, session_id)
            if session:
                db.delete(session)
        bus.publish(
            "session.deleted", {"session_id": session_id, "user_id": owner_id}
        )

    def touch(self, session_id: str) -> None:
        with write_session() as db:
            session = db.get(Session, session_id)
            if session:
                session.last_active_at = datetime.now(UTC)
                db.add(session)

    async def reap_idle(self) -> int:
        """APScheduler job: stop session containers idle beyond the timeout.
        The timeout can be overridden at runtime from the Settings page."""
        from ..db import get_setting

        settings = get_settings()
        override = get_setting("session_idle_min")
        idle_min = int(override) if override.isdigit() else settings.session_idle_min
        cutoff = datetime.now(UTC) - timedelta(minutes=idle_min)
        with read_session() as db:
            from sqlmodel import select

            sessions = list(
                db.exec(select(Session).where(Session.state == SessionState.running)).all()
            )
        # Sessions with a task still in flight are active regardless of
        # last_active_at (a long agent turn does not touch the timestamp).
        from .task_runner import inflight_session_ids

        busy = inflight_session_ids()
        reaped = 0
        for session in sessions:
            if session.id in busy:
                continue
            last = session.last_active_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if last < cutoff:
                try:
                    await self.stop(session.id, reaped=True)
                    reaped += 1
                except SessionError:
                    pass
        if reaped:
            log.info("reaped %d idle session(s)", reaped)
        return reaped

    def reconcile_on_boot(self) -> None:
        """Sync DB session states with reality after an orchestrator restart."""
        with read_session() as db:
            from sqlmodel import select

            sessions = list(db.exec(select(Session)).all())
        for session in sessions:
            try:
                container = self._get_container(session)
            except Exception:
                return  # docker unavailable; leave states alone
            if container is None:
                actual = SessionState.stopped
            elif container.status == "running":
                actual = SessionState.running
            else:
                actual = SessionState.stopped
            if session.state in (SessionState.creating, SessionState.running) and (
                session.state != actual
            ):
                with write_session() as db:
                    row = db.get(Session, session.id)
                    if row:
                        row.state = actual
                        db.add(row)


session_manager = SessionManager()
