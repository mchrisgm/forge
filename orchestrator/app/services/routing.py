"""Completion-path routing through the Headroom context-compression proxy.

The deployment chains ALL chat-completion traffic — PWA chats, memory model
calls, and OpenCode sessions (via the /v1 router) — through the `headroom`
compose service, which compresses tool outputs/JSON/logs/code before they
reach the engines. Two softeners bound the blast radius:

- a runtime toggle (Setting "headroom_enabled", surfaced on the Settings
  page; defaults from FORGE_HEADROOM_ENABLED), and
- an automatic health fallback: when the proxy doesn't answer, callers get
  the direct engine path — a downed headroom container degrades to plain
  Forge instead of breaking every chat.

Loop safety: headroom's upstream is the orchestrator's /v1-direct path,
which never consults this module.
"""

import logging
import time

import httpx

from ..config import get_settings
from ..db import get_setting

log = logging.getLogger(__name__)

HEALTH_TTL_S = 15.0
_probe: dict[str, float | bool] = {"ok": False, "at": 0.0}


def enabled() -> bool:
    """The runtime toggle: Setting override first, env default second."""
    raw = get_setting("headroom_enabled", "")
    if raw in ("true", "false"):
        return raw == "true"
    return get_settings().headroom_enabled


async def healthy() -> bool:
    """Cached probe of the full compression chain (headroom → /v1-direct).
    GET {headroom}/models exercises proxy + router in one round trip."""
    now = time.monotonic()
    if now - float(_probe["at"]) < HEALTH_TTL_S:
        return bool(_probe["ok"])
    ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as http:
            resp = await http.get(f"{get_settings().headroom_url}/models")
            ok = resp.status_code == 200
    except httpx.HTTPError as exc:
        log.debug("headroom probe failed: %s", exc)
    if ok != _probe["ok"]:
        log.info("headroom compression %s", "active" if ok else "unavailable — direct path")
    _probe.update(ok=ok, at=now)
    return ok


def reset_probe() -> None:
    """Forget the cached health state (tests, and after toggling)."""
    _probe.update(ok=False, at=0.0)


async def active() -> bool:
    return enabled() and await healthy()


async def completion_base_url(direct_base_url: str) -> str:
    """Base URL a chat-completion call should target: the headroom proxy when
    active, else the caller's direct engine/router URL. The request body's
    model slug survives the hop — headroom forwards it to /v1-direct, whose
    router resolves it to the right lease."""
    if await active():
        return get_settings().headroom_url
    return direct_base_url


async def status() -> dict:
    """Settings-page status card."""
    is_enabled = enabled()
    return {
        "enabled": is_enabled,
        "healthy": (await healthy()) if is_enabled else None,
        "url": get_settings().headroom_url,
    }
