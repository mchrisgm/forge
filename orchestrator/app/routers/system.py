import logging

import psutil
from fastapi import APIRouter

from ..config import get_settings
from ..services import docker_util
from ..services.engine_manager import engine_manager
from ..services.session_manager import SESSION_LABEL

log = logging.getLogger(__name__)
router = APIRouter(prefix="/system")


_nvml_warned = False


def _gpu_stats() -> dict | None:
    global _nvml_warned
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            return {
                "name": name,
                "vram_total_gb": round(mem.total / 1024**3, 2),
                "vram_used_gb": round(mem.used / 1024**3, 2),
                "utilization_pct": util.gpu,
            }
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
        docker_ok = True
    except Exception as exc:
        log.debug("docker unavailable for stats: %s", exc)
        session_containers = []
        docker_ok = False

    return {
        "gpu": _gpu_stats(),
        "ram": {
            "total_gb": round(ram.total / 1024**3, 1),
            "used_gb": round(ram.used / 1024**3, 1),
            "pct": ram.percent,
        },
        "cpu_pct": psutil.cpu_percent(interval=None),
        "disk": disk_stats,
        "engine": engine_manager.status(),
        "session_containers": session_containers,
        "docker_ok": docker_ok,
        "budgets": {
            "vram_gb": settings.vram_budget_gb,
            "ram_offload_gb": settings.ram_offload_budget_gb,
        },
    }
