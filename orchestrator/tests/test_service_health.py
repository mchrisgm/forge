"""Always-on service health in /api/system/stats: the System tab must show
live running-state per compose service (gateway, search, scraper, headroom, …)
so a down service is visible instead of features failing mysteriously."""

from types import SimpleNamespace

from app.routers import system as system_module
from app.routers.system import (
    ALWAYS_ON_SERVICES,
    COMPOSE_SERVICE_LABEL,
    _service_health,
)
from app.services import docker_util
from app.services.session_manager import SESSION_LABEL


def compose_container(service: str, status: str = "running"):
    return SimpleNamespace(
        name=f"forge-{service}-1",
        status=status,
        labels={COMPOSE_SERVICE_LABEL: service},
    )


def install_containers(monkeypatch, containers):
    """Fake docker_util.find_by_label, dispatching on the label queried."""

    def find(label, value=None):
        if label == COMPOSE_SERVICE_LABEL:
            return list(containers)
        return []  # SESSION_LABEL etc.

    monkeypatch.setattr(docker_util, "find_by_label", find)
    # system.py imports docker_util as a module, so the patch above covers it.
    assert system_module.docker_util.find_by_label is find


class TestServiceHealth:
    def test_all_up_reports_every_expected_service_running(self, monkeypatch):
        install_containers(
            monkeypatch, [compose_container(s) for s in ALWAYS_ON_SERVICES]
        )
        health = _service_health()
        assert [s["service"] for s in health] == list(ALWAYS_ON_SERVICES)
        assert all(s["running"] for s in health)
        assert all(not s["optional"] for s in health)

    def test_a_stopped_service_is_flagged(self, monkeypatch):
        containers = [compose_container(s) for s in ALWAYS_ON_SERVICES]
        containers[2] = compose_container("searxng", status="exited")
        install_containers(monkeypatch, containers)
        by_name = {s["service"]: s for s in _service_health()}
        assert by_name["searxng"] == {
            "service": "searxng",
            "status": "exited",
            "running": False,
            "optional": False,
        }

    def test_a_service_with_no_container_reports_missing(self, monkeypatch):
        # e.g. the stack was brought up before headroom existed in compose.
        install_containers(
            monkeypatch,
            [compose_container(s) for s in ALWAYS_ON_SERVICES if s != "headroom"],
        )
        by_name = {s["service"]: s for s in _service_health()}
        assert by_name["headroom"]["status"] == "missing"
        assert by_name["headroom"]["running"] is False

    def test_running_container_wins_over_a_stopped_husk(self, monkeypatch):
        install_containers(
            monkeypatch,
            [
                compose_container("gateway", status="exited"),
                compose_container("gateway", status="running"),
            ],
        )
        by_name = {s["service"]: s for s in _service_health()}
        assert by_name["gateway"]["running"] is True

    def test_optional_smolvm_appears_only_when_present(self, monkeypatch):
        install_containers(
            monkeypatch, [compose_container(s) for s in ALWAYS_ON_SERVICES]
        )
        assert "smolvm" not in {s["service"] for s in _service_health()}

        install_containers(
            monkeypatch,
            [compose_container(s) for s in ALWAYS_ON_SERVICES]
            + [compose_container("smolvm")],
        )
        by_name = {s["service"]: s for s in _service_health()}
        assert by_name["smolvm"]["running"] is True
        assert by_name["smolvm"]["optional"] is True

    def test_engine_lanes_and_one_shot_ui_are_not_listed(self, monkeypatch):
        install_containers(
            monkeypatch,
            [compose_container(s) for s in ALWAYS_ON_SERVICES]
            + [
                compose_container("imagegen"),  # on-demand engine lane
                compose_container("ui", status="exited"),  # one-shot builder
            ],
        )
        names = {s["service"] for s in _service_health()}
        assert "imagegen" not in names  # reported under `engine`, not here
        assert "ui" not in names  # exited is its healthy state


class TestStatsEndpoint:
    def test_stats_carries_the_services_block(self, api, auth_headers, monkeypatch):
        install_containers(
            monkeypatch,
            [compose_container(s) for s in ALWAYS_ON_SERVICES]
            + [
                SimpleNamespace(
                    name="session-x", status="running", labels={SESSION_LABEL: "sx"}
                )
            ],
        )
        body = api.get("/api/system/stats", headers=auth_headers).json()
        assert body["docker_ok"] is True
        assert [s["service"] for s in body["services"]] == list(ALWAYS_ON_SERVICES)
        assert all(s["running"] for s in body["services"])

    def test_docker_down_yields_empty_services_not_an_error(self, api, auth_headers):
        # The api fixture hard-disables docker: services must degrade to [].
        body = api.get("/api/system/stats", headers=auth_headers).json()
        assert body["docker_ok"] is False
        assert body["services"] == []
