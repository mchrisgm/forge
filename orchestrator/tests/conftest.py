"""Shared fixtures for the orchestrator test suite.

Every test gets an isolated Settings + SQLite database rooted in tmp_path (env
monkeypatching plus process-global cache resets), and no test ever needs a
docker socket, the network, or a GPU: docker-py is replaced with in-memory
fakes and httpx.AsyncClient is routed through a MockTransport on demand.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

import docker.errors
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app import db as db_module
from app.services import docker_util
from app.services import engine_manager as engine_manager_module
from app.services import events as events_module

TEST_PASSWORD = "forge-test-password"


# ── per-test isolation ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point all Forge paths into tmp_path and reset every process-global
    cache so each test sees pristine Settings, DB, event bus, and lease."""
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "db" / "forge.db"))
    monkeypatch.setenv("FORGE_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("FORGE_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("FORGE_WORKSPACES_DIR", str(tmp_path / "workspaces"))
    monkeypatch.setenv("FORGE_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("FORGE_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)

    config.get_settings.cache_clear()
    db_module._engine = None
    if hasattr(docker_util.client, "cache_clear"):
        docker_util.client.cache_clear()
    # A loop-less bus makes publish() a no-op instead of touching a dead loop.
    events_module.bus._loop = None
    events_module.bus._subscribers.clear()
    # The engine-lease singleton must not leak between tests.
    engine_manager_module.engine_manager._leases = {}
    engine_manager_module.engine_manager._load_tasks = {}
    engine_manager_module.engine_manager._generations = {}
    engine_manager_module.engine_manager._gpu_count = None

    yield

    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    config.get_settings.cache_clear()
    if hasattr(docker_util.client, "cache_clear"):
        docker_util.client.cache_clear()
    events_module.bus._loop = None


# ── docker fakes ────────────────────────────────────────────────────────────


class FakeContainer:
    def __init__(self, name: str, image: str, run_kwargs: dict, status: str = "running"):
        self.id = f"fake-{name}-{id(self):x}"
        self.name = name
        self.image = image
        self.run_kwargs = run_kwargs
        self.status = status
        self.labels = run_kwargs.get("labels") or {}
        self.removed = False
        self.stop_calls: list[int] = []
        self.start_calls = 0
        self.logs_bytes = b""

    def stop(self, timeout: int = 10) -> None:
        self.stop_calls.append(timeout)
        self.status = "exited"

    def start(self) -> None:
        self.start_calls += 1
        self.status = "running"

    def remove(self, force: bool = False) -> None:
        self.removed = True

    def reload(self) -> None:
        pass

    def logs(self, tail: int = 60) -> bytes:
        return self.logs_bytes


class FakeContainers:
    def __init__(self) -> None:
        self.by_name: dict[str, FakeContainer] = {}
        self.run_calls: list[FakeContainer] = []
        self.spawn_status = "running"  # status of newly run() containers
        self.logs_text = b""
        self.fail_run: Exception | None = None

    def run(self, image: str, command=None, **kwargs) -> FakeContainer:
        if self.fail_run is not None:
            raise self.fail_run
        name = kwargs.get("name", f"anon-{len(self.run_calls)}")
        container = FakeContainer(
            name, image, {"command": command, **kwargs}, status=self.spawn_status
        )
        container.logs_bytes = self.logs_text
        self.by_name[name] = container
        self.run_calls.append(container)
        return container

    def get(self, name: str) -> FakeContainer:
        container = self.by_name.get(name)
        if container is None or container.removed:
            raise docker.errors.NotFound(f"no such container: {name}")
        return container

    def list(self, all: bool = False, filters: dict | None = None) -> list[FakeContainer]:
        label = (filters or {}).get("label", "")
        key, _, value = label.partition("=")
        out = []
        for container in self.by_name.values():
            if container.removed:
                continue
            if key and key not in container.labels:
                continue
            if value and container.labels.get(key) != value:
                continue
            out.append(container)
        return out


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()


@pytest.fixture
def fake_docker(monkeypatch) -> FakeDockerClient:
    """Replace docker_util.client() with an in-memory fake."""
    fake = FakeDockerClient()
    monkeypatch.setattr(docker_util, "client", lambda: fake)
    return fake


@pytest.fixture
def fake_workspace_host(monkeypatch) -> str:
    """Pretend the workspaces named volume is mounted on the host."""

    def mountpoint(name: str) -> str:
        return "/var/lib/docker/volumes/" + name + "/_data"

    monkeypatch.setattr(docker_util, "volume_host_mountpoint", mountpoint)
    return "/var/lib/docker/volumes"


def _no_docker() -> None:
    raise RuntimeError("docker is not available in tests")


# ── app / API fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    """TestClient running the real app lifespan against the tmp database.
    Docker is hard-disabled: any accidental client() call raises."""
    monkeypatch.setattr(docker_util, "client", _no_docker)
    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_token(api) -> str:
    resp = api.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture
def auth_headers(auth_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def db_ready() -> None:
    """Create tables + data dirs without booting the FastAPI app."""
    db_module.init_db()


# ── httpx interception ──────────────────────────────────────────────────────


class HttpxMock:
    """Routes every httpx.AsyncClient built during the test through a
    MockTransport; records requests and lets the test swap the handler."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._handler: Callable[[httpx.Request], httpx.Response] = (
            lambda request: httpx.Response(200, json={})
        )

    def set_handler(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


@pytest.fixture
def httpx_mock(monkeypatch) -> HttpxMock:
    mock = HttpxMock()
    transport = httpx.MockTransport(mock._dispatch)
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return mock


# ── small helpers reused across test modules ────────────────────────────────


def add_model(**overrides) -> int:
    """Insert a ready-to-load ModelEntry and return its id."""
    from app.models import EngineKind, ModelEntry, ModelStatus, Quant, ToolCallFormat

    defaults: dict[str, Any] = dict(
        hf_repo="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        display_name="Qwen2.5 Coder 14B Instruct",
        family="qwen",
        params_b=14.0,
        quant=Quant.gguf_q4_k_m,
        file_path="qwen2.5-coder-14b-instruct-q4_k_m.gguf",
        size_gb=9.0,
        engine=EngineKind.llamacpp,
        ctx_max=16384,
        n_layers=40,
        tool_call_format=ToolCallFormat.hermes,
        status=ModelStatus.ready,
    )
    defaults.update(overrides)
    with db_module.write_session() as db:
        model = ModelEntry(**defaults)
        db.add(model)
        db.flush()
        model_id = model.id
    assert model_id is not None
    return model_id


async def wait_for_session_state(session_id: str, *states, timeout: float = 5.0):
    """Poll the DB until the session reaches one of the given states."""
    from app.models import Session

    deadline = time.monotonic() + timeout
    session = None
    while time.monotonic() < deadline:
        with db_module.read_session() as db:
            session = db.get(Session, session_id)
        if session is not None and session.state in states:
            return session
        await asyncio.sleep(0.01)
    pytest.fail(
        f"session {session_id} never reached {states}; "
        f"currently {session.state if session else 'missing'} "
        f"(last_error={getattr(session, 'last_error', '')!r})"
    )
