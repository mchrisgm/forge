"""EngineManager — GPU leases (PLAN §2, extended to multiple GPUs).

One engine container per GPU may run at a time; with N GPUs, up to N engines
serve concurrently. Loading onto a busy GPU (or when every GPU is busy)
raises LeaseHeldError (HTTP 409 upstream) with the holders. vLLM can span
multiple free GPUs with tensor parallelism (gpu_count > 1 on load).

Engine containers are started via the host docker socket, pinned to their
GPUs via device_ids, attached to forge-internal, and health-waited by polling
/v1/models; a container that exits during healthwait is a failed load: its
log tail is surfaced and the lease auto-releases (PLAN §14).

Sessions never talk to engine containers directly — the orchestrator's /v1
model router (app/routers/openai_router.py) resolves the request's model slug
to whichever lease serves it, so engine placement is invisible to OpenCode.
"""

import asyncio
import logging
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
GPU_LABEL = "forge.gpus"
ENGINE_CONTAINER_PREFIX = "forge-engine-"


def opencode_model_id_for(model: ModelEntry) -> str:
    """The model id OpenCode sends in requests — engines must serve it."""
    from ..opencode_config import opencode_model_id  # lazy: avoids import cycle

    return opencode_model_id(model)


def detect_gpu_count() -> int:
    """NVML-detected GPU count; FORGE_GPU_COUNT (>0) overrides; fallback 1."""
    settings = get_settings()
    if settings.gpu_count > 0:
        return settings.gpu_count
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            return max(1, pynvml.nvmlDeviceGetCount())
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return 1


class LeaseHeldError(Exception):
    def __init__(self, holders: list[dict[str, Any]]):
        self.holders = holders
        names = ", ".join(h.get("model_name", "?") for h in holders) or "unknown"
        super().__init__(f"GPU lease(s) held by {names}")


@dataclass
class Lease:
    model_id: int
    model_name: str
    model_slug: str
    engine: EngineKind
    gpu_ids: list[int] = field(default_factory=lambda: [0])
    state: str = "starting"  # starting | ready | failed
    container_id: str = ""
    base_url: str = ""
    error: str = ""
    acquired_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def gpu_index(self) -> int:
        return self.gpu_ids[0] if self.gpu_ids else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "model_slug": self.model_slug,
            "engine": self.engine.value,
            "gpu_ids": self.gpu_ids,
            "gpu_index": self.gpu_index,
            "state": self.state,
            "container_id": self.container_id[:12],
            "base_url": self.base_url,
            "error": self.error,
            "acquired_at": self.acquired_at,
        }


def engine_container_name(engine: EngineKind, gpu_index: int = 0) -> str:
    return f"{ENGINE_CONTAINER_PREFIX}{engine.value}-gpu{gpu_index}"


def engine_port(engine: EngineKind, settings: Settings) -> int:
    # Containers have separate network namespaces, so one port per lane is
    # fine even with an instance per GPU.
    return {
        EngineKind.llamacpp: settings.llamacpp_port,
        EngineKind.vllm: settings.vllm_port,
        EngineKind.airllm: settings.airllm_port,
    }[engine]


def engine_base_url(
    engine: EngineKind, settings: Settings | None = None, gpu_index: int = 0
) -> str:
    settings = settings or get_settings()
    return f"http://{engine_container_name(engine, gpu_index)}:{engine_port(engine, settings)}/v1"


def build_llamacpp_command(model: ModelEntry, settings: Settings) -> list[str]:
    n_layers = model.n_layers or estimate_n_layers(model.params_b)
    ctx = min(model.ctx_max or settings.default_ctx, settings.default_ctx)
    ngl = compute_ngl(model.size_gb, n_layers, ctx, settings.vram_budget_gb)
    # No --flash-attn flag: llama.cpp made it value-taking ([on|off|auto],
    # default auto) in Aug 2025; omitting it is valid on every image
    # generation and the modern default already enables FA where supported.
    return [
        "-m", f"/data/models/{model.file_path}",
        "--host", "0.0.0.0",
        "--port", str(settings.llamacpp_port),
        "-c", str(ctx),
        "--n-gpu-layers", str(ngl),
        "--parallel", str(settings.llamacpp_slots),
        "--jinja",
        "--alias", opencode_model_id_for(model),
    ]


def _vllm_parser(model: ModelEntry) -> str:
    return {
        "hermes": "hermes",
        "qwen": "hermes",
        "llama3": "llama3_json",
    }.get(model.tool_call_format.value, "hermes")


def build_vllm_command(
    model: ModelEntry, settings: Settings, tensor_parallel: int = 1
) -> list[str]:
    model_path = f"/data/models/{model.file_path}" if model.file_path else model.hf_repo
    ctx = min(model.ctx_max or 16384, 16384)
    # Serve the slug FIRST: OpenCode sends the provider models-map key (the
    # slug from opencode_model_id) as the request's model field, and vLLM
    # 404s any name it does not serve. The display name rides along as an
    # alias for humans.
    cmd = [
        "--model", model_path,
        "--served-model-name", opencode_model_id_for(model), model.display_name,
        "--host", "0.0.0.0",
        "--port", str(settings.vllm_port),
        "--quantization", "awq",
        "--max-model-len", str(ctx),
        "--gpu-memory-utilization", "0.90",
    ]
    if tensor_parallel > 1:
        cmd += ["--tensor-parallel-size", str(tensor_parallel)]
    if model.tool_call_format.value != "none":
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", _vllm_parser(model)]
    return cmd


def build_airllm_env(model: ModelEntry, settings: Settings) -> dict[str, str]:
    path = f"/data/models/{model.file_path}" if model.file_path else model.hf_repo
    return {
        "AIRLLM_MODEL_PATH": path,
        "AIRLLM_MODEL_NAME": opencode_model_id_for(model),
        "AIRLLM_PORT": str(settings.airllm_port),
        "AIRLLM_MAX_TOKENS": "512",
    }


class EngineManager:
    def __init__(self) -> None:
        self._leases: dict[int, Lease] = {}  # keyed by primary gpu index
        self._lock = asyncio.Lock()
        self._load_tasks: dict[int, asyncio.Task] = {}
        # Per-GPU generation counters: the blocking container-create thread
        # checks them after docker returns — if stale (an unload/force-load
        # won the race), it removes its own container. asyncio.to_thread
        # cancellation cannot stop the thread itself.
        self._generations: dict[int, int] = {}
        self._gpu_count: int | None = None

    # ── inventory ───────────────────────────────────────────────────────────

    @property
    def gpu_count(self) -> int:
        if self._gpu_count is None:
            self._gpu_count = detect_gpu_count()
        return self._gpu_count

    def _occupied(self) -> set[int]:
        occupied: set[int] = set()
        for lease in self._leases.values():
            if lease.state != "failed":
                occupied.update(lease.gpu_ids)
        return occupied

    def active_leases(self) -> list[Lease]:
        return [lease for lease in self._leases.values() if lease.state != "failed"]

    def ready_leases(self) -> list[Lease]:
        return [lease for lease in self._leases.values() if lease.state == "ready"]

    def lease_for_slug(self, slug: str) -> Lease | None:
        for lease in self._leases.values():
            if lease.model_slug == slug and lease.state == "ready":
                return lease
        return None

    @property
    def lease(self) -> Lease | None:
        """Backcompat single-lease view: the first active lease, if any."""
        active = self.active_leases()
        return active[0] if active else None

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "gpu_count": self.gpu_count,
            "lease": self.lease.as_dict() if self.lease else None,  # backcompat
            "leases": [lease.as_dict() for lease in self._leases.values()],
            "gpus": [
                {
                    "index": i,
                    # A tensor-parallel lease appears on EVERY GPU it spans.
                    "lease": next(
                        (
                            lease.as_dict()
                            for lease in self._leases.values()
                            if i in lease.gpu_ids and lease.state != "failed"
                        ),
                        None,
                    ),
                }
                for i in range(self.gpu_count)
            ],
            "engines": {
                kind.value: {
                    "port": engine_port(kind, settings),
                    "active_on": [
                        lease.gpu_index
                        for lease in self.active_leases()
                        if lease.engine == kind
                    ],
                }
                for kind in EngineKind
            },
        }

    # ── boot reconciliation ─────────────────────────────────────────────────

    def reconcile_on_boot(self) -> None:
        """After an orchestrator restart, adopt still-running engine containers."""
        try:
            containers = docker_util.find_by_label(ENGINE_LABEL)
        except Exception as exc:  # docker socket unavailable — surfaced in /system
            log.warning("engine reconcile skipped: %s", exc)
            return
        settings = get_settings()
        for container in containers:
            if container.status != "running":
                continue
            labels = container.labels or {}
            try:
                engine = EngineKind(labels.get(ENGINE_LABEL, ""))
            except ValueError:
                continue
            try:
                gpu_ids = [int(g) for g in (labels.get(GPU_LABEL) or "0").split(",")]
            except ValueError:
                gpu_ids = [0]
            lease = Lease(
                model_id=int(labels.get("forge.model_id", 0) or 0),
                model_name=labels.get("forge.model_name", "unknown"),
                model_slug=labels.get("forge.model_slug", ""),
                engine=engine,
                gpu_ids=gpu_ids,
                state="ready",
                container_id=container.id,
                base_url=engine_base_url(engine, settings, gpu_ids[0]),
            )
            self._leases[lease.gpu_index] = lease
            log.info("adopted running engine %s on gpu %s", container.name, gpu_ids)

    # ── load / unload ───────────────────────────────────────────────────────

    def _pick_gpus(
        self, gpu_index: int | None, gpu_count: int
    ) -> list[int] | None:
        free = sorted(set(range(self.gpu_count)) - self._occupied())
        if gpu_index is not None:
            wanted = list(range(gpu_index, gpu_index + gpu_count))
            if any(g >= self.gpu_count for g in wanted):
                return None
            return wanted if all(g in free for g in wanted) else None
        if len(free) < gpu_count:
            return None
        return free[:gpu_count]

    async def load(
        self,
        model: ModelEntry,
        force: bool = False,
        gpu_index: int | None = None,
        gpu_count: int = 1,
    ) -> Lease:
        if gpu_count < 1:
            gpu_count = 1
        if gpu_count > 1 and model.engine != EngineKind.vllm:
            raise ValueError("only the vLLM lane supports tensor-parallel multi-GPU loads")
        async with self._lock:
            # Drop failed leases; they hold nothing.
            self._leases = {
                i: lease for i, lease in self._leases.items() if lease.state != "failed"
            }
            # A model already serving is never loaded twice.
            slug = opencode_model_id_for(model)
            for lease in self._leases.values():
                if lease.model_slug == slug:
                    return lease

            gpus = self._pick_gpus(gpu_index, gpu_count)
            if gpus is None:
                if not force:
                    raise LeaseHeldError([le.as_dict() for le in self.active_leases()])
                # force: evict to make room — the named GPU, or everything.
                if gpu_index is not None:
                    await self._unload_gpus_locked(
                        list(range(gpu_index, gpu_index + gpu_count))
                    )
                else:
                    await self._unload_gpus_locked(None)
                gpus = self._pick_gpus(gpu_index, gpu_count)
                if gpus is None:
                    raise LeaseHeldError([le.as_dict() for le in self.active_leases()])

            settings = get_settings()
            lease = Lease(
                model_id=model.id or 0,
                model_name=model.display_name,
                model_slug=slug,
                engine=model.engine,
                gpu_ids=gpus,
                base_url=engine_base_url(model.engine, settings, gpus[0]),
            )
            self._leases[lease.gpu_index] = lease
            for gpu in gpus:
                self._generations[gpu] = self._generations.get(gpu, 0) + 1
            snapshot = {gpu: self._generations[gpu] for gpu in gpus}
            bus.publish("engine.state", {"lease": lease.as_dict()})
            self._load_tasks[lease.gpu_index] = asyncio.create_task(
                self._start_and_healthwait(model, lease, snapshot)
            )
            return lease

    async def unload(self, gpu_index: int | None = None) -> None:
        async with self._lock:
            if gpu_index is None:
                await self._unload_gpus_locked(None)
            else:
                await self._unload_gpus_locked([gpu_index])

    async def _unload_gpus_locked(self, gpus: list[int] | None) -> None:
        victims = [
            lease
            for lease in self._leases.values()
            if gpus is None or set(lease.gpu_ids) & set(gpus)
        ]
        for lease in victims:
            for gpu in lease.gpu_ids:
                self._generations[gpu] = self._generations.get(gpu, 0) + 1
            task = self._load_tasks.pop(lease.gpu_index, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await asyncio.to_thread(self._remove_engine_container, lease)
            self._leases.pop(lease.gpu_index, None)
            bus.publish("engine.state", {"lease": None, "gpu_index": lease.gpu_index})

    def _remove_engine_container(self, lease: Lease) -> None:
        for container in docker_util.find_by_label(ENGINE_LABEL):
            labels = container.labels or {}
            gpus = labels.get(GPU_LABEL, "0")
            try:
                container_gpus = {int(g) for g in gpus.split(",")}
            except ValueError:
                container_gpus = {0}
            if container_gpus & set(lease.gpu_ids):
                docker_util.remove_container(container)

    def _remove_all_engine_containers(self) -> None:
        for container in docker_util.find_by_label(ENGINE_LABEL):
            docker_util.remove_container(container)

    # ── container lifecycle ─────────────────────────────────────────────────

    def _generation_stale(self, snapshot: dict[int, int]) -> bool:
        return any(self._generations.get(gpu, 0) != gen for gpu, gen in snapshot.items())

    def _create_container(
        self, model: ModelEntry, lease: Lease, snapshot: dict[int, int]
    ) -> Any:
        settings = get_settings()
        self._remove_engine_container(lease)
        name = engine_container_name(model.engine, lease.gpu_index)
        labels = {
            ENGINE_LABEL: model.engine.value,
            GPU_LABEL: ",".join(str(g) for g in lease.gpu_ids),
            "forge.model_id": str(model.id or 0),
            "forge.model_name": model.display_name,
            "forge.model_slug": lease.model_slug,
        }
        mounts = [
            docker.types.Mount(
                target="/data/models", source=settings.models_volume, type="volume"
            )
        ]
        device_requests = [
            docker.types.DeviceRequest(
                device_ids=[str(g) for g in lease.gpu_ids], capabilities=[["gpu"]]
            )
        ]
        common: dict[str, Any] = {
            "name": name,
            "labels": labels,
            "network": settings.docker_network,
            "mounts": mounts,
            "detach": True,
            "device_requests": device_requests,
            "restart_policy": {"Name": "no"},
            "shm_size": "8g" if len(lease.gpu_ids) > 1 else "2g",
        }
        env: dict[str, str] = {}
        if settings.hf_token:
            env["HF_TOKEN"] = settings.hf_token

        if model.engine == EngineKind.llamacpp:
            image = settings.llamacpp_image
            command = build_llamacpp_command(model, settings)
        elif model.engine == EngineKind.vllm:
            image = settings.vllm_image
            command = build_vllm_command(model, settings, len(lease.gpu_ids))
            env["VLLM_NO_USAGE_STATS"] = "1"
        else:
            image = settings.airllm_image
            command = None
            env.update(build_airllm_env(model, settings))

        log.info(
            "starting engine %s on gpu(s) %s: %s",
            name, lease.gpu_ids, shlex.join(command) if command else "(env-configured)",
        )
        container = docker_util.client().containers.run(
            image, command=command, environment=env, **common
        )
        if self._generation_stale(snapshot):
            # An unload/force-load superseded this load while docker was
            # creating the container — clean up our own orphan.
            docker_util.remove_container(container)
            raise RuntimeError("engine load superseded by a newer load/unload")
        return container

    async def _start_and_healthwait(
        self, model: ModelEntry, lease: Lease, snapshot: dict[int, int]
    ) -> None:
        settings = get_settings()
        try:
            container = await asyncio.to_thread(
                self._create_container, model, lease, snapshot
            )
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
