import logging

import psutil
from fastapi import APIRouter

from ..config import get_settings
from ..services import bootstrap, docker_util
from ..services.engine_manager import engine_manager
from ..services.session_manager import SESSION_LABEL

log = logging.getLogger(__name__)
router = APIRouter(prefix="/system")

# Compose stamps every container it creates with its service name.
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

# The always-on stack: services `docker compose up` starts and keeps running
# (restart: unless-stopped). Engine lanes are deliberately absent — they are
# on-demand, orchestrator-managed, and reported under `engine`; `ui` is a
# one-shot bundle builder whose exited state is healthy; `smolvm` is an
# opt-in profile reported only when its container exists.
ALWAYS_ON_SERVICES = (
    "gateway",
    "orchestrator",
    "searxng",
    "mcp-playwright",
    "mcp-scrapling",
    "headroom",
)
OPTIONAL_SERVICES = ("smolvm",)


def _service_health() -> list[dict]:
    """Live running-state of the compose services Forge expects to be up, so
    the System tab shows exactly which piece is down instead of features
    failing mysteriously. A service with no container at all reports status
    'missing' (typically: the stack was brought up without that service —
    re-run `make up` / the setup script)."""
    by_service: dict[str, str] = {}
    for container in docker_util.find_by_label(COMPOSE_SERVICE_LABEL):
        name = (container.labels or {}).get(COMPOSE_SERVICE_LABEL, "")
        status = container.status or "unknown"
        # Compose replaces containers in place, but keep the healthiest one
        # per service in case a stopped husk lingers next to a running one.
        if by_service.get(name) != "running":
            by_service[name] = status
    services = [
        {
            "service": name,
            "status": by_service.get(name, "missing"),
            "running": by_service.get(name) == "running",
            "optional": False,
        }
        for name in ALWAYS_ON_SERVICES
    ]
    services.extend(
        {
            "service": name,
            "status": by_service[name],
            "running": by_service[name] == "running",
            "optional": True,
        }
        for name in OPTIONAL_SERVICES
        if name in by_service  # opt-in lanes only appear once enabled
    )
    return services


_nvml_warned = False


def _gpu_stats() -> list[dict] | None:
    """Per-GPU stats for every device NVML can see (multi-GPU aware)."""
    global _nvml_warned
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            gpus = []
            for index in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode()
                gpus.append(
                    {
                        "index": index,
                        "name": name,
                        "vram_total_gb": round(mem.total / 1024**3, 2),
                        "vram_used_gb": round(mem.used / 1024**3, 2),
                        "utilization_pct": util.gpu,
                    }
                )
            return gpus
        finally:
            pynvml.nvmlShutdown()
    except Exception as exc:
        if not _nvml_warned:
            _nvml_warned = True
            log.warning(
                "NVML unavailable — GPU stats will be null. On the GPU box the "
                "orchestrator needs the nvidia runtime with driver capability "
                "'utility' (see docker-compose.yml). Cause: %s", exc,
            )
        return None


@router.get("/stats")
def stats() -> dict:
    settings = get_settings()
    ram = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(settings.models_dir)
        disk_stats = {
            "total_gb": round(disk.total / 1024**3, 1),
            "used_gb": round(disk.used / 1024**3, 1),
            "free_gb": round(disk.free / 1024**3, 1),
        }
    except OSError:
        disk_stats = None

    try:
        session_containers = [
            {
                "name": c.name,
                "status": c.status,
                "session_id": (c.labels or {}).get(SESSION_LABEL, ""),
            }
            for c in docker_util.find_by_label(SESSION_LABEL)
        ]
        services = _service_health()
        docker_ok = True
    except Exception as exc:
        log.debug("docker unavailable for stats: %s", exc)
        session_containers = []
        services = []
        docker_ok = False

    gpus = _gpu_stats()
    return {
        "gpu": gpus[0] if gpus else None,  # backcompat single-GPU view
        "gpus": gpus,
        "ram": {
            "total_gb": round(ram.total / 1024**3, 1),
            "used_gb": round(ram.used / 1024**3, 1),
            "pct": ram.percent,
        },
        "cpu_pct": psutil.cpu_percent(interval=None),
        "disk": disk_stats,
        "engine": engine_manager.status(),
        "session_containers": session_containers,
        # Always-on compose services with live running-state (empty when the
        # docker probe above failed — docker_ok already flags that).
        "services": services,
        "docker_ok": docker_ok,
        # Locally-built images currently absent — LIVE (TTL re-probe), so the
        # "run make up" warning clears itself once the images are built.
        "missing_images": bootstrap.current_missing_images(),
        "budgets": {
            "vram_gb": settings.vram_budget_gb,
            "ram_offload_gb": settings.ram_offload_budget_gb,
        },
    }
