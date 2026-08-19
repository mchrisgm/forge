"""First-run bootstrap (zero-touch setup).

Runs inside the orchestrator's startup on every boot; everything is
idempotent. On a fresh volume it: creates data dirs + DB schema (init_db),
generates the token-signing key, seeds the curated model catalog, and leaves
the instance in "setup required" state — the first person to open the site
creates the admin profile through the setup wizard, after which anyone on the
LAN can register their own profile.
"""

import logging

from sqlmodel import select

from ..auth import user_count
from ..db import read_session

log = logging.getLogger(__name__)


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
    first_run_banner()
