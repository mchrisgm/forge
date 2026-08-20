"""Runtime-tunable settings (Settings page): reaper timeout and registry
schedule. Stored in the Setting table, overriding env defaults. Per-profile
settings (password, instructions, memory) live under /users/me."""

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import get_settings
from ..db import get_setting, set_setting
from ..services import routing

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
    headroom_enabled: bool | None = None
    # OAuth app credentials for per-user connector sign-in. Empty string
    # clears the Setting-table override (env default applies again).
    github_oauth_client_id: str | None = None
    hf_oauth_client_id: str | None = None
    hf_oauth_client_secret: str | None = None


_SECRET_MASK = "••••••"


def _oauth_view() -> dict:
    """Per-provider client config for the Settings page — ids are shown (they
    are public by OAuth's design), secrets only as configured/not."""
    from ..services import oauth_flows

    view = {}
    for kind, provider in oauth_flows.PROVIDERS.items():
        client_id, secret = oauth_flows.client_config(provider)
        view[kind] = {
            "label": provider.label,
            "method": provider.method,
            "client_id": client_id,
            "needs_secret": bool(provider.client_secret_key),
            "has_secret": bool(secret),
            "setup_note": provider.setup_note,
            "setup_url": provider.setup_url,
        }
    return view


@router.get("")
async def get_all() -> dict:
    settings = get_settings()
    return {
        "session_idle_min": effective_session_idle_min(),
        "registry_cron": effective_registry_cron(),
        "max_parallel_sessions": settings.max_parallel_sessions,
        "vram_budget_gb": settings.vram_budget_gb,
        "ram_offload_budget_gb": settings.ram_offload_budget_gb,
        "llamacpp_slots": settings.llamacpp_slots,
        "headroom": await routing.status(),
        "oauth": _oauth_view(),
    }


@router.patch("")
async def patch(body: PatchBody, request: Request) -> dict:
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
    if body.headroom_enabled is not None:
        set_setting("headroom_enabled", "true" if body.headroom_enabled else "false")
        routing.reset_probe()  # re-probe immediately on next call
    for key in (
        "github_oauth_client_id",
        "hf_oauth_client_id",
        "hf_oauth_client_secret",
    ):
        value = getattr(body, key)
        if value is not None and value != _SECRET_MASK:
            set_setting(key, value.strip())
    return await get_all()

