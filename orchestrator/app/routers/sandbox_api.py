"""REST surface for the smolvm sandbox lane — the chat "run this code" tool.

Two authed endpoints under ``/api/sandbox``:

- ``GET  /api/sandbox/status`` — reachability of the smolvm control API plus the
  configured URL, so the UI can show whether the (opt-in, KVM-gated) lane is up.
- ``POST /api/sandbox/run`` — run one untrusted snippet in a network-less
  microVM overlay and return ``{stdout, stderr, exit_code, timed_out,
  duration_ms}``.

All execution lives in ``app.services.sandbox`` (the single place Forge runs
untrusted code); this module only validates input and maps errors to statuses.

A later phase can expose the same run to *coding sessions* as an MCP tool. The
session container can already reach the orchestrator on forge-internal, so the
tool would POST to an orchestrator endpoint rather than talk to smolvm directly
(keeping the no-auth smolvm API off the session network). That needs an
unauthenticated internal endpoint design decision, so it is intentionally out
of scope here — this phase is the authed REST surface only.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..config import get_settings
from ..models import User
from ..services import sandbox

router = APIRouter(prefix="/sandbox")

# Hard cap on submitted source size (bytes). Guards the request path before the
# code ever reaches a VM; the sandbox itself also caps output.
MAX_CODE_BYTES = 100 * 1024


class RunRequest(BaseModel):
    language: str
    code: str
    stdin: str = ""
    timeout_s: int = sandbox.DEFAULT_TIMEOUT_S


@router.get("/status")
async def sandbox_status(user: User = Depends(current_user)) -> dict:
    """Whether the sandbox lane is reachable, plus where it is expected to be."""
    info = await sandbox.available()
    return {**info, "url": get_settings().sandbox_url}


@router.post("/run")
async def sandbox_run(req: RunRequest, user: User = Depends(current_user)) -> dict:
    """Run one untrusted snippet in the sandbox and return its result."""
    if len(req.code.encode("utf-8")) > MAX_CODE_BYTES:
        raise HTTPException(400, "code exceeds the 100 KB limit")
    try:
        return await sandbox.run_code(
            req.language, req.code, req.stdin, req.timeout_s
        )
    except ValueError as exc:
        # Unsupported language / bad input — the caller's mistake.
        raise HTTPException(400, str(exc)) from exc
    except sandbox.SandboxError as exc:
        # 503 when the lane is down, 502 when smolvm failed the request.
        raise HTTPException(exc.status, exc.message) from exc
