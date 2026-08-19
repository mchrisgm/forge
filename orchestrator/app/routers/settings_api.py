"""Runtime-tunable settings (Settings page): password, reaper timeout,
registry schedule. Stored in the Setting table, overriding env defaults."""

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth import change_password, verify_password
from ..config import get_settings
from ..db import get_setting, set_setting

router = APIRouter(prefix="/settings")


def effective_session_idle_min() -> int:
    override = get_setting("session_idle_min")
    if override.isdigit():
        return int(override)
    return get_settings().session_idle_min


def effective_registry_cron() -> str:
    return get_setting("registry_cron") or get_settings().registry_cron


class PatchBody(BaseModel):
    session_idle_min: int | None = None
    registry_cron: str | None = None


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.get("")
def get_all() -> dict:
    settings = get_settings()
    return {
        "session_idle_min": effective_session_idle_min(),
        "registry_cron": effective_registry_cron(),
        "max_parallel_sessions": settings.max_parallel_sessions,
        "vram_budget_gb": settings.vram_budget_gb,
        "ram_offload_budget_gb": settings.ram_offload_budget_gb,
        "llamacpp_slots": settings.llamacpp_slots,
    }


@router.patch("")
def patch(body: PatchBody, request: Request) -> dict:
    if body.session_idle_min is not None:
        if body.session_idle_min < 5:
            raise HTTPException(400, "idle timeout must be at least 5 minutes")
        set_setting("session_idle_min", str(body.session_idle_min))
    if body.registry_cron is not None:
        try:
            trigger = CronTrigger.from_crontab(body.registry_cron)
        except ValueError as exc:
            raise HTTPException(400, f"invalid cron expression: {exc}") from exc
        set_setting("registry_cron", body.registry_cron)
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler and scheduler.get_job("registry_scan"):
            scheduler.reschedule_job("registry_scan", trigger=trigger)
    return get_all()


@router.post("/password")
def set_password(body: PasswordBody) -> dict:
    if not verify_password(body.current_password):
        raise HTTPException(401, "current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(400, "new password must be at least 8 characters")
    change_password(body.new_password)
    return {"ok": True}
