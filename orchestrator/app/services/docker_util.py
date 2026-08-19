"""Shared docker-py plumbing for engine + session containers."""

import logging
from functools import lru_cache

import docker
from docker.models.containers import Container

from ..config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def client() -> docker.DockerClient:
    return docker.from_env()


def find_by_label(label: str, value: str | None = None) -> list[Container]:
    selector = f"{label}={value}" if value is not None else label
    return client().containers.list(all=True, filters={"label": selector})


def remove_container(container: Container, timeout: int = 10) -> None:
    try:
        if container.status == "running":
            container.stop(timeout=timeout)
        container.remove(force=True)
    except docker.errors.NotFound:
        pass
    except docker.errors.APIError as exc:
        log.warning("removing container %s failed: %s", container.name, exc)


def volume_host_mountpoint(volume_name: str) -> str | None:
    """Host path of a named volume — used to bind-mount per-session workspace
    subdirectories into sibling containers (named volumes can't be sub-mounted
    portably across Docker versions)."""
    try:
        vol = client().volumes.get(volume_name)
        return vol.attrs.get("Mountpoint") or None
    except docker.errors.NotFound:
        return None


def container_logs_tail(container: Container, lines: int = 60) -> str:
    try:
        return container.logs(tail=lines).decode(errors="replace")
    except docker.errors.APIError:
        return ""


def network_exists(name: str) -> bool:
    try:
        client().networks.get(name)
        return True
    except docker.errors.NotFound:
        return False


def ensure_network(name: str) -> None:
    if not network_exists(name):
        client().networks.create(name, driver="bridge")


def settings_network() -> str:
    return get_settings().docker_network
