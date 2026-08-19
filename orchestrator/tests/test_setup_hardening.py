"""First-run/setup hardening tests.

A literally fresh box (or one where `docker compose up` ran without `make up`)
must fail friendly, not cryptic:
- bootstrap records locally-built images that are missing at boot and warns
  ONCE with the exact build commands;
- /api/system/stats exposes that list as `missing_images` for the UI banner;
- spawning a session without the session image raises a SessionError naming
  `make up` (instead of docker's "No such image");
- loading an engine whose image is absent pulls it explicitly (visible via an
  `engine.pulling` bus event) before the container starts, so the healthwait
  timeout only begins after the pull.

All docker interaction goes through the in-memory fakes in conftest.
"""

import logging

import httpx
import pytest

from app.config import get_settings
from app.models import SessionState
from app.services import bootstrap, docker_util
from app.services import engine_manager as engine_manager_module
from app.services.session_manager import session_manager
from tests.conftest import add_model, wait_for_session_state
from tests.test_engine_manager import make_manager, make_model, settle


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, kind: str, data: dict | None = None) -> None:
        self.events.append((kind, data or {}))

    def of_kind(self, kind: str) -> list[dict]:
        return [data for k, data in self.events if k == kind]


# ── bootstrap: boot-time image presence check ───────────────────────────────


class TestBootstrapImageCheck:
    def test_all_images_present_records_nothing(self, fake_docker):
        bootstrap.missing_images[:] = ["stale-from-last-boot"]
        assert bootstrap.check_required_images() == []
        assert bootstrap.missing_images == []

    def test_missing_images_recorded_and_warned_once(self, fake_docker, caplog):
        fake_docker.images.present = {"forge-airllm"}  # session + imagegen absent
        with caplog.at_level(logging.WARNING, logger="app.services.bootstrap"):
            missing = bootstrap.check_required_images()

        settings = get_settings()
        assert missing == [settings.session_image, settings.imagegen_image]
        assert bootstrap.missing_images == missing

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "exactly ONE prominent warning"
        message = warnings[0].getMessage()
        assert settings.session_image in message
        assert settings.imagegen_image in message
        assert "make up" in message
        assert "--profile build-only build session-runner" in message
        assert "--profile engines build airllm imagegen" in message

    def test_docker_unreachable_is_graceful_and_clears_state(self, monkeypatch):
        def no_docker():
            raise RuntimeError("docker is not available")

        monkeypatch.setattr(docker_util, "client", no_docker)
        bootstrap.missing_images[:] = ["stale-entry"]
        assert bootstrap.check_required_images() == []
        assert bootstrap.missing_images == []

    def test_run_invokes_the_check(self, fake_docker, db_ready, monkeypatch):
        monkeypatch.setattr(bootstrap, "seed_model_catalog_if_empty", lambda: 0)
        fake_docker.images.present = set()
        bootstrap.run()
        settings = get_settings()
        assert bootstrap.missing_images == [
            settings.session_image,
            settings.airllm_image,
            settings.imagegen_image,
        ]


# ── /api/system/stats exposes missing_images ────────────────────────────────


class TestSystemStatsMissingImages:
    def test_stats_exposes_missing_images(self, api, auth_headers, monkeypatch):
        resp = api.get("/api/system/stats", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["missing_images"] == []  # docker off -> check skipped

        monkeypatch.setattr(
            bootstrap, "missing_images", ["forge-session-runner", "forge-imagegen"]
        )
        resp = api.get("/api/system/stats", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["missing_images"] == [
            "forge-session-runner",
            "forge-imagegen",
        ]


# ── session spawn: friendly failure without the session image ───────────────


@pytest.mark.usefixtures("db_ready", "fake_workspace_host")
class TestSessionMissingImage:
    async def test_spawn_errors_friendly_when_session_image_missing(self, fake_docker):
        fake_docker.images.present = set()  # nothing built on this box
        model_id = add_model()

        session = await session_manager.create("no image yet", model_id)
        final = await wait_for_session_state(session.id, SessionState.error)

        settings = get_settings()
        assert settings.session_image in final.last_error
        assert "make up" in final.last_error
        # No container attempt was made with a missing image.
        assert fake_docker.containers.run_calls == []


# ── engine load: explicit, visible pull when the image is absent ────────────


@pytest.mark.usefixtures("db_ready")
class TestEnginePullWhenMissing:
    async def test_missing_image_pulled_before_start_with_bus_event(
        self, fake_docker, httpx_mock, monkeypatch
    ):
        recorder = RecordingBus()
        monkeypatch.setattr(engine_manager_module, "bus", recorder)
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        fake_docker.images.present = set()

        manager = make_manager(1)
        lease = await manager.load(make_model(id=1))
        await settle(manager)

        settings = get_settings()
        assert lease.state == "ready"
        assert fake_docker.images.pulled == [settings.llamacpp_image]
        assert recorder.of_kind("engine.pulling") == [
            {"lane": "llamacpp", "image": settings.llamacpp_image, "gpu_index": 0}
        ]
        (container,) = fake_docker.containers.run_calls
        assert container.image == settings.llamacpp_image

    async def test_present_image_is_not_pulled(self, fake_docker, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        manager = make_manager(1)
        lease = await manager.load(make_model(id=1))
        await settle(manager)

        assert lease.state == "ready"
        assert fake_docker.images.pulled == []

    async def test_pull_failure_fails_lease_with_build_hint(
        self, fake_docker, httpx_mock
    ):
        fake_docker.images.present = set()
        fake_docker.images.fail_pull = RuntimeError("pull access denied")

        manager = make_manager(1)
        lease = await manager.load(make_model(id=1))
        await settle(manager)

        assert lease.state == "failed"
        assert "could not be pulled" in lease.error
        assert "make up" in lease.error
        assert fake_docker.containers.run_calls == []
