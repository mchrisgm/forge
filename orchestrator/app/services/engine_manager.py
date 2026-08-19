"""EngineManager — the single-GPU lease (PLAN §2, §6.2).

Exactly one GPU-resident engine container runs at a time. Loading while a
lease is held raises LeaseHeldError (HTTP 409 upstream). Engine containers are
started via the host docker socket, attached to forge-internal, and health-
waited by polling /v1/models; a container that exits during healthwait is a
failed load: its log tail is surfaced and the lease auto-releases (PLAN §14).
"""

import asyncio
import logging
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import docker
import httpx

from ..config import Settings, get_settings
from ..models import EngineKind, ModelEntry
from . import docker_util
from .events import bus
from .fit_rules import compute_ngl, estimate_n_layers

log = logging.getLogger(__name__)

ENGINE_LABEL = "forge.engine"
ENGINE_CONTAINER_PREFIX = "forge-engine-"


class LeaseHeldError(Exception):
    def __init__(self, holder: dict[str, Any]):
        self.holder = holder
        super().__init__(f"GPU lease held by {holder.get('model_name')}")


@dataclass
class Lease:
    model_id: int
    model_name: str
    engine: EngineKind
    state: str = "starting"  # starting | ready | failed
    container_id: str = ""
    base_url: str = ""
    error: str = ""
    acquired_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "engine": self.engine.value,
            "state": self.state,
            "container_id": self.container_id[:12],
            "base_url": self.base_url,
            "error": self.error,
            "acquired_at": self.acquired_at,
        }


def engine_container_name(engine: EngineKind) -> str:
    return f"{ENGINE_CONTAINER_PREFIX}{engine.value}"


def engine_port(engine: EngineKind, settings: Settings) -> int:
    return {
        EngineKind.llamacpp: settings.llamacpp_port,
        EngineKind.vllm: settings.vllm_port,
        EngineKind.airllm: settings.airllm_port,
    }[engine]


def engine_base_url(engine: EngineKind, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return f"http://{engine_container_name(engine)}:{engine_port(engine, settings)}/v1"


def build_llamacpp_command(model: ModelEntry, settings: Settings) -> list[str]:
    n_layers = model.n_layers or estimate_n_layers(model.params_b)
    ctx = min(model.ctx_max or settings.default_ctx, settings.default_ctx)
    ngl = compute_ngl(model.size_gb, n_layers, ctx, settings.vram_budget_gb)
    return [
        "-m", f"/data/models/{model.file_path}",
        "--host", "0.0.0.0",
        "--port", str(settings.llamacpp_port),
        "-c", str(ctx),
        "--n-gpu-layers", str(ngl),
        "--parallel", str(settings.llamacpp_slots),
        "--jinja",
        "--flash-attn",
        "--alias", model.display_name,
    ]


def _vllm_parser(model: ModelEntry) -> str:
    return {
        "hermes": "hermes",
        "qwen": "hermes",
        "llama3": "llama3_json",
    }.get(model.tool_call_format.value, "hermes")


def build_vllm_command(model: ModelEntry, settings: Settings) -> list[str]:
    model_path = f"/data/models/{model.file_path}" if model.file_path else model.hf_repo
    ctx = min(model.ctx_max or 16384, 16384)
    cmd = [
        "--model", model_path,
        "--served-model-name", model.display_name,
        "--host", "0.0.0.0",
        "--port", str(settings.vllm_port),
        "--quantization", "awq",
        "--max-model-len", str(ctx),
        "--gpu-memory-utilization", "0.90",
    ]
    if model.tool_call_format.value != "none":
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", _vllm_parser(model)]
    return cmd


def build_airllm_env(model: ModelEntry, settings: Settings) -> dict[str, str]:
    return {
        "AIRLLM_MODEL_PATH": f"/data/models/{model.file_path}" if model.file_path else model.hf_repo,
        "AIRLLM_MODEL_NAME": model.display_name,
        "AIRLLM_PORT": str(settings.airllm_port),
        "AIRLLM_MAX_TOKENS": "512",
    }


class EngineManager:
    def __init__(self) -> None:
        self._lease: Lease | None = None
        self._lock = asyncio.Lock()
        self._load_task: asyncio.Task | None = None

    @property
    def lease(self) -> Lease | None:
        return self._lease

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "lease": self._lease.as_dict() if self._lease else None,
            "engines": {
                kind.value: {
                    "port": engine_port(kind, settings),
                    "container": engine_container_name(kind),
                    "active": bool(self._lease and self._lease.engine == kind),
                }
                for kind in EngineKind
            },
        }

    def reconcile_on_boot(self) -> None:
        """After an orchestrator restart, adopt a still-running engine container
        or clear orphaned state."""
        try:
            containers = docker_util.find_by_label(ENGINE_LABEL)
        except Exception as exc:  # docker socket unavailable — surfaced in /system
            log.warning("engine reconcile skipped: %s", exc)
            return
        for container in containers:
            if container.status == "running":
                labels = container.labels or {}
                try:
                    engine = EngineKind(labels.get(ENGINE_LABEL, ""))
                except ValueError:
                    continue
                settings = get_settings()
                self._lease = Lease(
                    model_id=int(labels.get("forge.model_id", 0) or 0),
                    model_name=labels.get("forge.model_name", "unknown"),
                    engine=engine,
                    state="ready",
                    container_id=container.id,
                    base_url=engine_base_url(engine, settings),
                )
                log.info("adopted running engine %s", container.name)
                return

    async def load(self, model: ModelEntry, force: bool = False) -> Lease:
        async with self._lock:
            if self._lease and self._lease.state != "failed":
                if not force:
                    raise LeaseHeldError(self._lease.as_dict())
                await self._unload_locked()
            settings = get_settings()
            lease = Lease(
                model_id=model.id or 0,
                model_name=model.display_name,
                engine=model.engine,
                base_url=engine_base_url(model.engine, settings),
            )
            self._lease = lease
            bus.publish("engine.state", {"lease": lease.as_dict()})
            self._load_task = asyncio.create_task(self._start_and_healthwait(model, lease))
            return lease

    async def unload(self) -> None:
        async with self._lock:
            await self._unload_locked()

    async def _unload_locked(self) -> None:
        if self._load_task and not self._load_task.done():
            self._load_task.cancel()
            try:
                await self._load_task
            except (asyncio.CancelledError, Exception):
                pass
        await asyncio.to_thread(self._remove_engine_containers)
        self._lease = None
        self._load_task = None
        bus.publish("engine.state", {"lease": None})

    def _remove_engine_containers(self) -> None:
        for container in docker_util.find_by_label(ENGINE_LABEL):
            docker_util.remove_container(container)

    def _create_container(self, model: ModelEntry) -> Any:
        settings = get_settings()
        self._remove_engine_containers()
        name = engine_container_name(model.engine)
        labels = {
            ENGINE_LABEL: model.engine.value,
            "forge.model_id": str(model.id or 0),
            "forge.model_name": model.display_name,
        }
        mounts = [
            docker.types.Mount(
                target="/data/models", source=settings.models_volume, type="volume"
            )
        ]
        common: dict[str, Any] = {
            "name": name,
            "labels": labels,
            "network": settings.docker_network,
            "mounts": mounts,
            "detach": True,
            "device_requests": docker_util.gpu_device_requests(),
            "restart_policy": {"Name": "no"},
            "shm_size": "2g",
        }
        env: dict[str, str] = {}
        if settings.hf_token:
            env["HF_TOKEN"] = settings.hf_token

        if model.engine == EngineKind.llamacpp:
            image = settings.llamacpp_image
            command = build_llamacpp_command(model, settings)
        elif model.engine == EngineKind.vllm:
            image = settings.vllm_image
            command = build_vllm_command(model, settings)
            env["VLLM_NO_USAGE_STATS"] = "1"
        else:
            image = settings.airllm_image
            command = None
            env.update(build_airllm_env(model, settings))

        log.info(
            "starting engine %s: %s", name, shlex.join(command) if command else "(env-configured)"
        )
        return docker_util.client().containers.run(
            image, command=command, environment=env, **common
        )

    async def _start_and_healthwait(self, model: ModelEntry, lease: Lease) -> None:
        settings = get_settings()
        try:
            container = await asyncio.to_thread(self._create_container, model)
        except Exception as exc:
            lease.state = "failed"
            lease.error = f"container start failed: {exc}"
            bus.publish("engine.state", {"lease": lease.as_dict()})
            return
        lease.container_id = container.id
        bus.publish("engine.state", {"lease": lease.as_dict()})

        health_url = f"{lease.base_url}/models"
        deadline = asyncio.get_running_loop().time() + settings.engine_load_timeout_s
        async with httpx.AsyncClient(timeout=5) as http:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    await asyncio.to_thread(container.reload)
                except Exception:
                    pass
                if container.status in ("exited", "dead"):
                    tail = await asyncio.to_thread(
                        docker_util.container_logs_tail, container
                    )
                    lease.state = "failed"
                    lease.error = f"engine exited during load\n{tail}"
                    bus.publish("engine.state", {"lease": lease.as_dict()})
                    await asyncio.to_thread(docker_util.remove_container, container)
                    return
                try:
                    resp = await http.get(health_url)
                    if resp.status_code == 200:
                        lease.state = "ready"
                        bus.publish("engine.state", {"lease": lease.as_dict()})
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(3)

        tail = await asyncio.to_thread(docker_util.container_logs_tail, container)
        lease.state = "failed"
        lease.error = f"healthcheck timed out after {settings.engine_load_timeout_s}s\n{tail}"
        bus.publish("engine.state", {"lease": lease.as_dict()})
        await asyncio.to_thread(docker_util.remove_container, container)


engine_manager = EngineManager()
