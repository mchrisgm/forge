"""SessionManager lifecycle tests with docker fully mocked (PLAN §6.3).

Docker is replaced by the in-memory fakes from conftest; the workspaces
volume's host mountpoint is faked so the sibling bind-mount path is exercised.

Regression note: SessionManager.create() once returned the freshly-added ORM
instance after commit (expired+detached under expire_on_commit=True), raising
DetachedInstanceError on every create. TestCreateAndSpawn pins the fixed
contract. Most tests below drive _spawn / stop / reap / delete through
directly-seeded Session rows for isolation.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import read_session, set_setting, write_session
from app.models import (
    Connector,
    ConnectorKind,
    EngineKind,
    Session,
    SessionState,
    Task,
    User,
)
from app.services.session_manager import (
    SessionError,
    container_name,
    session_manager,
)
from tests.conftest import add_model, wait_for_session_state

pytestmark = pytest.mark.usefixtures("db_ready", "fake_workspace_host")

def add_session_row(
    state: SessionState = SessionState.running,
    minutes_ago: int = 0,
    model_id: int | None = None,
    name: str = "row",
) -> str:
    session = Session(
        name=name,
        state=state,
        model_id=model_id,
        last_active_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    with write_session() as db:
        db.add(session)
        db.flush()
        return session.id


def get_session_row(session_id: str) -> Session | None:
    with read_session() as db:
        return db.get(Session, session_id)


async def spawn_session(
    model_id: int,
    repo_url: str | None = None,
    name: str = "spawned",
    user_id: int | None = None,
) -> Session:
    """Seed a Session row the way create() does, then run the real spawn path."""
    settings = get_settings()
    session = Session(name=name, model_id=model_id, repo_url=repo_url, user_id=user_id)
    session.workspace_path = str(Path(settings.workspaces_dir) / session.id)
    with write_session() as db:
        db.add(session)
        db.flush()
        session_id = session.id
    await session_manager._spawn(session_id)
    return await wait_for_session_state(
        session_id, SessionState.running, SessionState.error
    )


# ── create -> spawn -> running ──────────────────────────────────────────────


class TestCreateAndSpawn:
    async def test_create_returns_creating_session_then_running(self, fake_docker):
        """The intended create() contract (PLAN §6.1 POST /sessions)."""
        model_id = add_model()
        session = await session_manager.create("feature work", model_id)
        assert session.state == SessionState.creating
        assert session.workspace_path.endswith(session.id)
        final = await wait_for_session_state(session.id, SessionState.running)
        assert final.container_id

    async def test_spawn_creates_container_with_correct_kwargs(self, fake_docker):
        with write_session() as db:
            db.add(
                Connector(
                    kind=ConnectorKind.github,
                    enabled=True,
                    config_json=json.dumps({"token": "ghp_spawntest_secret"}),
                )
            )
        model_id = add_model()
        final = await spawn_session(model_id, repo_url="https://example.com/repo.git")
        assert final.state == SessionState.running, final.last_error
        settings = get_settings()

        container = fake_docker.containers.get(container_name(final.id))
        assert final.container_id == container.id
        kwargs = container.run_kwargs

        # Image / network / labels
        assert container.image == settings.session_image
        assert kwargs["network"] == settings.docker_network == "forge-internal"
        assert kwargs["labels"] == {"forge.session": final.id}

        # Mounts: workspace bind (host path under the volume mountpoint) and
        # /skills read-only — and nothing else (no docker socket).
        mounts = {m["Target"]: m for m in kwargs["mounts"]}
        assert set(mounts) == {"/workspace", "/skills"}
        assert mounts["/workspace"]["Type"] == "bind"
        assert (
            mounts["/workspace"]["Source"]
            == f"/var/lib/docker/volumes/{settings.workspaces_volume}/_data/{final.id}"
        )
        assert mounts["/skills"]["Type"] == "volume"
        assert mounts["/skills"]["Source"] == settings.skills_volume
        assert mounts["/skills"].get("ReadOnly") is True
        assert not mounts["/workspace"].get("ReadOnly")

        # Resource limits (PLAN §6.3)
        assert kwargs["mem_limit"] == "4g"
        assert kwargs["nano_cpus"] == int(4e9)
        assert kwargs["pids_limit"] == 512
        assert kwargs["restart_policy"] == {"Name": "no"}
        assert kwargs["security_opt"] == ["no-new-privileges:true"]

        # Hard security floor: no GPU, no privileges, no docker socket.
        assert "privileged" not in kwargs
        assert "device_requests" not in kwargs
        assert not any("docker.sock" in str(m.get("Source", "")) for m in kwargs["mounts"])

        # Environment: session id, repo url, rendered opencode config, and the
        # PAT passed as env only — never inside the config JSON.
        env = kwargs["environment"]
        assert env["FORGE_SESSION_ID"] == final.id
        assert env["FORGE_REPO_URL"] == "https://example.com/repo.git"
        assert env["GITHUB_PAT"] == "ghp_spawntest_secret"
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert "forge-local" in config["provider"]
        assert config["mcp"]["github"]["enabled"] is True
        assert "ghp_spawntest_secret" not in env["OPENCODE_CONFIG_CONTENT"]

        # Workspace dir was created on the orchestrator side.
        assert Path(settings.workspaces_dir, final.id).is_dir()

    async def test_pat_not_injected_when_github_connector_disabled(self, fake_docker):
        """The connector toggle must actually cut access — even with a token
        stored and an env fallback configured (review finding)."""
        with write_session() as db:
            db.add(
                Connector(
                    kind=ConnectorKind.github,
                    enabled=False,
                    config_json=json.dumps({"token": "ghp_should_not_leak"}),
                )
            )
        model_id = add_model()
        final = await spawn_session(model_id)
        assert final.state == SessionState.running, final.last_error
        env = fake_docker.containers.get(container_name(final.id)).run_kwargs[
            "environment"
        ]
        assert "GITHUB_PAT" not in env

    async def test_spawn_uses_the_session_owners_connectors(self, fake_docker):
        """Connectors are per-user now: a session owned by alice must ride
        alice's github token — never bob's, never the legacy NULL row's."""
        with write_session() as db:
            alice = User(username="alice")
            bob = User(username="bob")
            db.add(alice)
            db.add(bob)
            db.flush()
            alice_id = alice.id
            for owner, token in [
                (alice_id, "ghp_alice_token"),
                (bob.id, "ghp_bob_token"),
                (None, "ghp_legacy_token"),
            ]:
                db.add(
                    Connector(
                        user_id=owner,
                        kind=ConnectorKind.github,
                        enabled=True,
                        config_json=json.dumps({"token": token}),
                    )
                )
        model_id = add_model()
        final = await spawn_session(model_id, user_id=alice_id)
        assert final.state == SessionState.running, final.last_error
        env = fake_docker.containers.get(container_name(final.id)).run_kwargs[
            "environment"
        ]
        assert env["GITHUB_PAT"] == "ghp_alice_token"

    async def test_ownerless_session_uses_legacy_null_connectors(self, fake_docker):
        with write_session() as db:
            owner = User(username="someone")
            db.add(owner)
            db.flush()
            db.add(
                Connector(
                    user_id=owner.id,
                    kind=ConnectorKind.github,
                    enabled=True,
                    config_json=json.dumps({"token": "ghp_owned_token"}),
                )
            )
            db.add(
                Connector(
                    user_id=None,
                    kind=ConnectorKind.github,
                    enabled=True,
                    config_json=json.dumps({"token": "ghp_legacy_token"}),
                )
            )
        model_id = add_model()
        final = await spawn_session(model_id, user_id=None)
        assert final.state == SessionState.running, final.last_error
        env = fake_docker.containers.get(container_name(final.id)).run_kwargs[
            "environment"
        ]
        assert env["GITHUB_PAT"] == "ghp_legacy_token"

    async def test_spawn_failure_marks_session_error(self, fake_docker):
        fake_docker.containers.fail_run = RuntimeError("image not found")
        model_id = add_model()
        final = await spawn_session(model_id)
        assert final.state == SessionState.error
        assert "image not found" in final.last_error

    async def test_unknown_model_is_404(self, fake_docker):
        with pytest.raises(SessionError) as excinfo:
            await session_manager.create("nope", model_id=99999)
        assert excinfo.value.status_code == 404


class TestAirllmRejected:
    async def test_airllm_model_cannot_power_a_session(self, fake_docker):
        model_id = add_model(engine=EngineKind.airllm, display_name="Llama 70B AirLLM")
        with pytest.raises(SessionError) as excinfo:
            await session_manager.create("chat only", model_id)
        assert excinfo.value.status_code == 400
        assert "chat-only" in excinfo.value.detail

    async def test_imagegen_model_gets_a_lane_accurate_rejection(self, fake_docker):
        model_id = add_model(engine=EngineKind.imagegen, display_name="SDXL Turbo")
        with pytest.raises(SessionError) as excinfo:
            await session_manager.create("art", model_id)
        assert excinfo.value.status_code == 400
        assert "image models" in excinfo.value.detail
        assert "AirLLM" not in excinfo.value.detail
        assert fake_docker.containers.run_calls == []
        # Nothing was spawned and no session row was left behind.
        assert fake_docker.containers.run_calls == []
        with read_session() as db:
            from sqlmodel import select

            assert db.exec(select(Session)).all() == []


class TestMaxParallelSessions:
    @pytest.mark.parametrize(
        "busy_state", [SessionState.running, SessionState.creating]
    )
    async def test_creating_beyond_the_cap_is_409(
        self, fake_docker, monkeypatch, busy_state
    ):
        monkeypatch.setenv("FORGE_MAX_PARALLEL_SESSIONS", "1")
        get_settings.cache_clear()
        model_id = add_model()
        add_session_row(state=busy_state, model_id=model_id)

        with pytest.raises(SessionError) as excinfo:
            await session_manager.create("one too many", model_id)
        assert excinfo.value.status_code == 409
        assert "max parallel sessions" in excinfo.value.detail

    async def test_stopped_sessions_do_not_count_toward_the_cap(
        self, fake_docker, monkeypatch
    ):
        monkeypatch.setenv("FORGE_MAX_PARALLEL_SESSIONS", "1")
        get_settings.cache_clear()
        model_id = add_model()
        add_session_row(state=SessionState.stopped, model_id=model_id)
        add_session_row(state=SessionState.idle, model_id=model_id)
        add_session_row(state=SessionState.error, model_id=model_id)

        try:
            await session_manager.create("fits", model_id)
        except SessionError:
            pytest.fail("stopped/idle/error sessions must not count toward the cap")
        # Drain the spawn task before teardown.
        with read_session() as db:
            from sqlmodel import select

            row = db.exec(select(Session).where(Session.name == "fits")).first()
        assert row is not None, "the cap check must have admitted the new session"
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            current = get_session_row(row.id)
            if current and current.state != SessionState.creating:
                break
            await asyncio.sleep(0.02)


# ── stop: stopped vs reaped -> idle ─────────────────────────────────────────


class TestStop:
    async def test_stop_transitions_to_stopped_and_stops_container(self, fake_docker):
        session = await spawn_session(add_model())
        assert session.state == SessionState.running

        stopped = await session_manager.stop(session.id)
        assert stopped.state == SessionState.stopped
        container = fake_docker.containers.by_name[container_name(session.id)]
        assert container.stop_calls == [10]
        assert container.status == "exited"

    async def test_reaped_stop_transitions_to_idle(self, fake_docker):
        session = await spawn_session(add_model())
        reaped = await session_manager.stop(session.id, reaped=True)
        assert reaped.state == SessionState.idle

    async def test_stop_unknown_session_is_404(self, fake_docker):
        with pytest.raises(SessionError) as excinfo:
            await session_manager.stop("no-such-session")
        assert excinfo.value.status_code == 404

    async def test_stop_survives_missing_container(self, fake_docker):
        session_id = add_session_row(state=SessionState.running)
        stopped = await session_manager.stop(session_id)
        assert stopped.state == SessionState.stopped


# ── reaper ──────────────────────────────────────────────────────────────────


class TestReapIdle:
    async def test_reaps_only_sessions_beyond_the_cutoff(self, fake_docker):
        # Default idle timeout is 120 min.
        old_id = add_session_row(state=SessionState.running, minutes_ago=180)
        fresh_id = add_session_row(state=SessionState.running, minutes_ago=5)
        stopped_id = add_session_row(state=SessionState.stopped, minutes_ago=999)

        reaped = await session_manager.reap_idle()
        assert reaped == 1
        assert get_session_row(old_id).state == SessionState.idle
        assert get_session_row(fresh_id).state == SessionState.running
        assert get_session_row(stopped_id).state == SessionState.stopped

    async def test_reaped_session_container_is_stopped_not_removed(self, fake_docker):
        session = await spawn_session(add_model())
        with write_session() as db:
            row = db.get(Session, session.id)
            row.last_active_at = datetime.now(UTC) - timedelta(minutes=999)
            db.add(row)

        assert await session_manager.reap_idle() == 1
        container = fake_docker.containers.by_name[container_name(session.id)]
        assert container.status == "exited"
        assert container.removed is False  # resume must stay possible
        assert get_session_row(session.id).state == SessionState.idle

    async def test_runtime_override_shortens_the_cutoff(self, fake_docker):
        set_setting("session_idle_min", "10")
        session_id = add_session_row(state=SessionState.running, minutes_ago=30)
        assert await session_manager.reap_idle() == 1
        assert get_session_row(session_id).state == SessionState.idle

    async def test_runtime_override_extends_the_cutoff(self, fake_docker):
        set_setting("session_idle_min", "10000")
        session_id = add_session_row(state=SessionState.running, minutes_ago=180)
        assert await session_manager.reap_idle() == 0
        assert get_session_row(session_id).state == SessionState.running

    async def test_touch_resets_the_idle_clock(self, fake_docker):
        session_id = add_session_row(state=SessionState.running, minutes_ago=180)
        session_manager.touch(session_id)
        assert await session_manager.reap_idle() == 0
        assert get_session_row(session_id).state == SessionState.running


# ── delete ──────────────────────────────────────────────────────────────────


class TestDelete:
    async def test_delete_removes_container_workspace_and_rows(self, fake_docker):
        session = await spawn_session(add_model())
        workspace = Path(session.workspace_path)
        assert workspace.is_dir()
        with write_session() as db:
            db.add(Task(session_id=session.id, prompt="do a thing"))

        await session_manager.delete(session.id)

        container = fake_docker.containers.run_calls[0]
        assert container.removed is True
        assert not workspace.exists()
        assert get_session_row(session.id) is None
        with read_session() as db:
            from sqlmodel import select

            assert (
                db.exec(select(Task).where(Task.session_id == session.id)).all() == []
            )

    async def test_delete_unknown_session_is_404(self, fake_docker):
        with pytest.raises(SessionError) as excinfo:
            await session_manager.delete("missing")
        assert excinfo.value.status_code == 404


# ── start / resume ──────────────────────────────────────────────────────────


class TestStart:
    async def test_start_restarts_an_existing_stopped_container(self, fake_docker):
        session = await spawn_session(add_model())
        await session_manager.stop(session.id, reaped=True)

        restarted = await session_manager.start(session.id)
        assert restarted.state == SessionState.running
        container = fake_docker.containers.by_name[container_name(session.id)]
        assert container.start_calls == 1
        assert container.status == "running"

    async def test_start_respawns_when_container_was_removed(self, fake_docker):
        session = await spawn_session(add_model())
        # Simulate a host reboot wiping the container.
        fake_docker.containers.by_name.clear()

        restarted = await session_manager.start(session.id)
        assert restarted.state == SessionState.running
        assert container_name(session.id) in fake_docker.containers.by_name
