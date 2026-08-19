"""OpenAI-compatible model router at /v1 (NOT under /api).

Session containers point their forge-local provider at
http://orchestrator:8000/v1; requests are routed by the body's `model` field
(the OpenCode slug) to whichever GPU lease serves that model, so engine
placement is invisible to OpenCode and multi-GPU "one engine per GPU" just
works.

Security: this router is intentionally unauthenticated, exactly like the
engine containers' own OpenAI endpoints. It is reachable ONLY on the
forge-internal docker network — the gateway forwards /api/* alone, and the
orchestrator publishes no host ports (PLAN §4, §7).
"""

import json
import time

from fastapi import APIRouter, HTTPException, Request

from ..config import get_settings
from ..services import routing
from ..services.engine_manager import engine_manager
from ..services.openai_proxy import proxy_openai_request

router = APIRouter(prefix="/v1")

# Headroom's upstream target: identical routing WITHOUT the compression hop.
# The public /v1 forwards to headroom when it is enabled and healthy; if
# headroom then called /v1 back, requests would loop — so it targets this
# path instead (see docker-compose.yml OPENAI_TARGET_API_URL).
direct_router = APIRouter(prefix="/v1-direct")


def _models_payload() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": lease.model_slug,
                "object": "model",
                "created": int(time.time()),
                "owned_by": f"forge-{lease.engine.value}-gpu{lease.gpu_index}",
            }
            for lease in engine_manager.ready_text_leases()
        ],
    }


@router.get("/models")
def list_served_models() -> dict:
    return _models_payload()


@direct_router.get("/models")
def list_served_models_direct() -> dict:
    return _models_payload()


def resolve_lease(model_slug: str | None):
    """TEXT lease serving `model_slug` (imagegen leases never answer chat).
    The single-ready-lease fallback applies ONLY to slug-less requests — an
    explicit slug that matches nothing is a 404, never a silent answer from a
    different model."""
    ready = engine_manager.ready_text_leases()
    if model_slug:
        lease = engine_manager.lease_for_slug(model_slug)
        if lease and lease.engine.value != "imagegen":
            return lease
    elif len(ready) == 1:
        return ready[0]
    served = ", ".join(le.model_slug for le in ready) or "(none)"
    raise HTTPException(
        404,
        f"model {model_slug!r} is not being served. Currently serving: {served}. "
        "Load it from the Models page first.",
    )


async def _route(request: Request, path: str, chain_headroom: bool):
    body = await request.body()
    if chain_headroom and await routing.active():
        # Compression hop: headroom forwards to /v1-direct, which resolves
        # the slug below. A downed proxy fails the health probe and traffic
        # degrades to the direct path automatically.
        return await proxy_openai_request(get_settings().headroom_url, path, body)
    try:
        model_slug = json.loads(body or b"{}").get("model")
    except json.JSONDecodeError:
        model_slug = None
    lease = resolve_lease(model_slug)
    return await proxy_openai_request(lease.base_url, path, body)


@router.post("/chat/completions")
async def chat_completions(request: Request):
    return await _route(request, "chat/completions", chain_headroom=True)


@router.post("/completions")
async def completions(request: Request):
    return await _route(request, "completions", chain_headroom=True)


@direct_router.post("/chat/completions")
async def chat_completions_direct(request: Request):
    return await _route(request, "chat/completions", chain_headroom=False)


@direct_router.post("/completions")
async def completions_direct(request: Request):
    return await _route(request, "completions", chain_headroom=False)
