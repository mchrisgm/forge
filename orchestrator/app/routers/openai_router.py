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

from ..services.engine_manager import engine_manager
from ..services.openai_proxy import proxy_openai_request

router = APIRouter(prefix="/v1")


@router.get("/models")
def list_served_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": lease.model_slug,
                "object": "model",
                "created": int(time.time()),
                "owned_by": f"forge-{lease.engine.value}-gpu{lease.gpu_index}",
            }
            for lease in engine_manager.ready_leases()
        ],
    }


def resolve_lease(model_slug: str | None):
    """Lease serving `model_slug`. The single-ready-lease fallback applies
    ONLY to slug-less requests — an explicit slug that matches nothing is a
    404, never a silent answer from a different model."""
    ready = engine_manager.ready_leases()
    if model_slug:
        lease = engine_manager.lease_for_slug(model_slug)
        if lease:
            return lease
    elif len(ready) == 1:
        return ready[0]
    served = ", ".join(le.model_slug for le in ready) or "(none)"
    raise HTTPException(
        404,
        f"model {model_slug!r} is not being served. Currently serving: {served}. "
        "Load it from the Models page first.",
    )


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.body()
    try:
        model_slug = json.loads(body or b"{}").get("model")
    except json.JSONDecodeError:
        model_slug = None
    lease = resolve_lease(model_slug)
    return await proxy_openai_request(lease.base_url, "chat/completions", body)


@router.post("/completions")
async def completions(request: Request):
    body = await request.body()
    try:
        model_slug = json.loads(body or b"{}").get("model")
    except json.JSONDecodeError:
        model_slug = None
    lease = resolve_lease(model_slug)
    return await proxy_openai_request(lease.base_url, "completions", body)
