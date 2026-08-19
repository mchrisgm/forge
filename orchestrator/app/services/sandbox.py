"""Untrusted code execution via the smolvm microVM sandbox lane.

This is the ONE place Forge runs code it did not write — snippets a user (or a
model on their behalf) hands to the chat "run this code" tool. Every run is
hardware-isolated inside a smolvm microVM (KVM), and the guest is locked down
hard:

- **Network is OFF.** The runner machine is created with ``network: false`` so
  the guest has no egress at all — no exfiltration, no callbacks, no supply of
  a second-stage payload. (The smolvm *host* still reaches a registry to pull
  the base image; only the *guest* is severed.)
- **A hard timeout is enforced** server-side by smolvm (``timeoutSecs``); the
  guest process is SIGKILLed at the deadline and reports exit code 124.
- **Output is capped** (~40 KB per stream) so a run can't flood the caller.
- **One run per call, in a fresh ephemeral overlay.** Each ``/run`` spins a
  throwaway copy-on-write overlay off the pristine base image and tears it down
  when the command exits, so no filesystem state survives a run or leaks
  between users. A single shared, network-less "runner" host machine is reused
  purely as the VM host — user code never executes in *its* rootfs, only in the
  per-call overlay.

Control-plane shape (verified against the smol-machines/smolvm clone, v1.8.3):

- ``POST /api/v1/machines`` — create a machine. Body is camelCase
  ``CreateMachineRequest`` (``src/api/types.rs``); ``network`` defaults false.
  Returns 409 when the name already exists, which we treat as "already there".
- ``POST /api/v1/machines/{id}/run`` — ``run_command`` in
  ``src/api/handlers/exec.rs``: creates a temporary overlay from ``image`` and
  runs ``command`` (a ``Vec<String>`` argv, never a shell string) with ``env``
  and ``timeoutSecs``. Returns ``ExecResponse`` ``{exitCode, stdout, stderr,
  stdoutB64, stderrB64}`` (camelCase).
- ``GET /health`` — liveness, returns 200 ``{status: "ok", ...}``.

Code delivery is injection-safe. The user's code and stdin are passed as the
environment variables ``FORGE_CODE`` / ``FORGE_STDIN`` on the run request; the
command is a fixed per-language ``/bin/sh -c`` bootstrap that writes
``$FORGE_CODE`` to a file with ``printf '%s'`` (which does not interpret the
argument) and pipes ``$FORGE_STDIN`` into the interpreter running that file.
No user-controlled bytes are ever interpolated into the argv, so there is no
command-injection surface — the code only ever runs as data fed to an
interpreter. (``RunRequest`` has no stdin field, so this env+pipe shim is how
stdin reaches the program.)

Failures are defensive: a connection-refused / unreachable smolvm surfaces as a
``SandboxError`` the router maps to 503 ("start it with ``make sandbox``");
smolvm-side errors (non-2xx) map to 502. ``available()`` never raises.
"""

import logging
import time

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

# Fixed name of the shared, network-less VM host machine. It is only ever the
# host for per-call ephemeral overlays (see module docstring); user code never
# runs in its own filesystem, so reuse leaks no state between runs or users.
RUNNER_NAME = "forge-sandbox-runner"

# Exit code smolvm reports when it SIGKILLs a command at the timeout deadline
# (crates/smolvm-agent/src/process.rs: TIMEOUT_EXIT_CODE = 124).
TIMEOUT_EXIT_CODE = 124

# Hard ceiling on the per-run wall-clock budget (seconds). Callers may ask for
# less; anything above is clamped so no single run can hog the lane.
MAX_TIMEOUT_S = 60
MIN_TIMEOUT_S = 1
DEFAULT_TIMEOUT_S = 30

# Per-stream output cap (~40 KB). Longer output is cut with a marker.
MAX_OUTPUT_BYTES = 40 * 1024
_TRUNCATION_MARKER = "\n… [truncated: output exceeded the 40 KB limit]"

# Pinned base image + interpreter per supported language. The images are the
# small Alpine variants; the file extension is cosmetic (the interpreter is
# invoked explicitly, not by extension).
_LANGUAGES: dict[str, tuple[str, str, str]] = {
    # language     ->  (image,               interpreter, ext)
    "python": ("python:3.12-alpine", "python3", "py"),
    "javascript": ("node:22-alpine", "node", "js"),
    "node": ("node:22-alpine", "node", "js"),
    "bash": ("alpine", "sh", "sh"),  # alpine ships busybox sh, not bash
    "sh": ("alpine", "sh", "sh"),
}


class SandboxError(Exception):
    """A sandbox run could not complete.

    ``status`` is the HTTP status the router should surface: 503 when the lane
    itself is down (connection refused / unreachable — the operator needs to
    ``make sandbox``), 502 when smolvm reached but failed the request.
    """

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_LANE_DOWN_MESSAGE = (
    "sandbox lane not running — start it with `make sandbox` (needs /dev/kvm)"
)


def _resolve_language(language: str) -> tuple[str, str, str]:
    """(image, interpreter, ext) for a supported language, else ValueError.

    The ValueError is 400-worthy: it means the caller asked for a language the
    sandbox does not offer, not that anything went wrong server-side.
    """
    key = (language or "").strip().lower()
    resolved = _LANGUAGES.get(key)
    if resolved is None:
        supported = ", ".join(sorted(set(_LANGUAGES)))
        raise ValueError(
            f"unsupported language {language!r}; supported: {supported}"
        )
    return resolved


def _clamp_timeout(timeout_s: int) -> int:
    try:
        value = int(timeout_s)
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_S
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, value))


def _wrapper_command(interpreter: str, ext: str) -> list[str]:
    """Fixed argv that runs the user's code without interpolating it.

    ``$FORGE_CODE`` / ``$FORGE_STDIN`` are read from the environment (set on the
    run request), never spliced into this string, so there is no injection
    surface. ``printf '%s'`` emits its argument verbatim — no format or escape
    processing — so code containing ``%``, backslashes, or quotes is safe.
    """
    path = f"/tmp/forge_main.{ext}"
    bootstrap = (
        f"printf '%s' \"$FORGE_CODE\" > {path} && "
        f"printf '%s' \"$FORGE_STDIN\" | {interpreter} {path}"
    )
    return ["/bin/sh", "-c", bootstrap]


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return text
    clipped = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", "ignore")
    return clipped + _TRUNCATION_MARKER


async def _ensure_runner(client: httpx.AsyncClient) -> None:
    """Idempotently create the shared network-less runner host machine.

    ``network: false`` is the load-bearing security control — it denies the
    guest all egress. A 409 means the machine already exists (a prior call made
    it); any other non-2xx is an unexpected smolvm-side failure.
    """
    resp = await client.post(
        "/api/v1/machines",
        json={
            "name": RUNNER_NAME,
            "network": False,  # NO egress for untrusted code — do not change
            "cpus": 2,
            "memoryMb": 1024,
        },
    )
    if resp.status_code in (200, 201) or resp.status_code == 409:
        return
    raise SandboxError(
        f"smolvm could not provision the sandbox runner (HTTP {resp.status_code})",
        status=502,
    )


async def run_code(
    language: str,
    code: str,
    stdin: str = "",
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """Run one untrusted snippet in a network-less microVM overlay.

    Returns ``{stdout, stderr, exit_code, timed_out, duration_ms}`` with each
    stream truncated to ~40 KB. Raises ``ValueError`` for an unsupported
    language (400-worthy) and ``SandboxError`` when the lane is down (503) or
    smolvm fails the request (502).
    """
    image, interpreter, ext = _resolve_language(language)
    timeout = _clamp_timeout(timeout_s)
    command = _wrapper_command(interpreter, ext)

    body = {
        "image": image,
        "command": command,
        "env": [
            {"name": "FORGE_CODE", "value": code},
            {"name": "FORGE_STDIN", "value": stdin or ""},
        ],
        "timeoutSecs": timeout,
    }

    settings = get_settings()
    # The client read budget must outlast the guest timeout so smolvm's own
    # deadline (exit 124) wins the race; the extra headroom also covers a
    # first-time base-image pull on the host side.
    client_timeout = httpx.Timeout(timeout + 120.0, connect=5.0)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=settings.sandbox_url, timeout=client_timeout
        ) as client:
            await _ensure_runner(client)
            resp = await client.post(
                f"/api/v1/machines/{RUNNER_NAME}/run", json=body
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise SandboxError(_LANE_DOWN_MESSAGE, status=503) from exc
    except httpx.HTTPError as exc:
        raise SandboxError(f"sandbox request failed: {exc}", status=502) from exc

    duration_ms = int((time.monotonic() - start) * 1000)

    if resp.status_code != 200:
        detail = resp.text[:500]
        raise SandboxError(
            f"smolvm run failed (HTTP {resp.status_code}): {detail}", status=502
        )

    try:
        data = resp.json()
        exit_code = int(data["exitCode"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SandboxError(
            f"smolvm sent a malformed run response: {exc}", status=502
        ) from exc

    return {
        "stdout": _truncate(str(data.get("stdout", ""))),
        "stderr": _truncate(str(data.get("stderr", ""))),
        "exit_code": exit_code,
        "timed_out": exit_code == TIMEOUT_EXIT_CODE,
        "duration_ms": duration_ms,
    }


async def available() -> dict:
    """Cheap health probe of the smolvm control API. Never raises.

    Returns ``{enabled, healthy, detail}``. ``enabled`` reports that the lane is
    configured (a ``sandbox_url`` is always set); ``healthy`` reports whether
    smolvm answered ``GET /health`` with 200.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            base_url=settings.sandbox_url, timeout=5.0
        ) as client:
            resp = await client.get("/health")
    except httpx.HTTPError as exc:
        return {
            "enabled": True,
            "healthy": False,
            "detail": f"sandbox unreachable ({exc.__class__.__name__})",
        }
    if resp.status_code == 200:
        return {"enabled": True, "healthy": True, "detail": "ok"}
    return {
        "enabled": True,
        "healthy": False,
        "detail": f"smolvm /health returned HTTP {resp.status_code}",
    }
