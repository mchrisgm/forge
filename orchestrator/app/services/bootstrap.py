"""First-run bootstrap (zero-touch setup).

Runs inside the orchestrator's startup on every boot; everything is
idempotent. On a fresh volume it: creates data dirs + DB schema (init_db),
generates the token-signing key, seeds the curated model catalog, and leaves
the instance in "setup required" state — the first person to open the site
creates the admin profile through the setup wizard, after which anyone on the
LAN can register their own profile.
"""

import logging
import time

from sqlmodel import select

from ..auth import user_count
from ..db import read_session

log = logging.getLogger(__name__)

# Locally-built images the orchestrator spawns containers from that are
# currently missing — populated by check_required_images() and exposed through
# GET /api/system/stats as `missing_images` so the UI can show a setup warning.
# Kept LIVE (re-probed with a small TTL by current_missing_images), not a boot
# snapshot: running `make up` in another terminal must clear the warning
# without an orchestrator restart.
missing_images: list[str] = []

_IMAGE_CHECK_TTL_S = 20.0
_last_image_check = 0.0


def seed_model_catalog_if_empty() -> int:
    """Insert the curated starter catalog on a fresh install so the Models
    page is immediately actionable (entries arrive as `approved`; downloads
    still require a click — PLAN §1.6)."""
    from ..models import ModelEntry

    with read_session() as db:
        existing = db.exec(select(ModelEntry.id)).first()
    if existing is not None:
        return 0
    try:
        from scripts.seed_models import seed
    except ImportError:  # dev layout: scripts/ lives at the repo root
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        try:
            from scripts.seed_models import seed
        except ImportError:
            log.warning("seed catalog unavailable — skipping model seeding")
            return 0
    created, _skipped = seed()
    log.info("seeded %d starter models", created)
    return created


def check_required_images() -> list[str]:
    """Check that the images the orchestrator spawns containers from exist
    (session-runner + the locally-built engine lanes). Purely advisory: a
    NEWLY missing image logs one prominent warning naming the exact build
    commands and is surfaced via /api/system/stats — the stack still boots.
    Re-runnable: transitions (image built / image gone) are logged once each,
    and the module-level `missing_images` always reflects the latest probe."""
    import docker

    from ..config import get_settings
    from . import docker_util

    settings = get_settings()
    required = [settings.session_image, settings.airllm_image, settings.imagegen_image]
    # The ROCm llama.cpp image is only spawned (and only worth building) on an
    # AMD box — don't warn NVIDIA/CPU hosts about a missing image they never use.
    from .engine_manager import engine_manager

    if engine_manager.gpu_vendor == "amd":
        required.append(settings.llamacpp_rocm_image)
    missing: list[str] = []
    try:
        client = docker_util.client()
        for image in dict.fromkeys(required):  # dedupe, keep order
            try:
                client.images.get(image)
            except docker.errors.ImageNotFound:
                missing.append(image)
    except Exception as exc:  # daemon unreachable (tests, degraded host)
        log.debug("image presence check skipped — docker unavailable: %s", exc)
        missing_images[:] = []
        return []

    newly_missing = [m for m in missing if m not in missing_images]
    now_built = [m for m in missing_images if m not in missing]
    missing_images[:] = missing
    if now_built:
        log.info("previously missing images are now built: %s", ", ".join(now_built))
    if newly_missing:
        log.warning(
            "════════════════════════════════════════════════════════════\n"
            "  Missing local images: %s\n"
            "  Sessions/engines using them will fail to start until built.\n"
            "  Fix: run `make up` (builds all of them), or individually:\n"
            "    docker compose --profile build-only build session-runner\n"
            "    docker compose --profile engines build airllm imagegen\n"
            "════════════════════════════════════════════════════════════",
            ", ".join(missing),
        )
    return missing


def current_missing_images() -> list[str]:
    """Live view for /api/system/stats: re-probe docker (TTL-bounded — the
    System tab polls every 5s) so building the images clears the warning
    within seconds instead of sticking until the next orchestrator restart."""
    global _last_image_check
    now = time.monotonic()
    if now - _last_image_check >= _IMAGE_CHECK_TTL_S:
        _last_image_check = now
        check_required_images()
    return list(missing_images)


def first_run_banner() -> None:
    if user_count() == 0:
        log.info(
            "════════════════════════════════════════════════════════════\n"
            "  Forge is ready for first-time setup!\n"
            "  Open http://<this-host>:8080 from any device on your LAN\n"
            "  and create the first profile (it becomes the admin).\n"
            "════════════════════════════════════════════════════════════"
        )


def run() -> None:
    seed_model_catalog_if_empty()
    from .bundled_skills import seed_bundled_skills

    seed_bundled_skills()
    check_required_images()
    first_run_banner()
