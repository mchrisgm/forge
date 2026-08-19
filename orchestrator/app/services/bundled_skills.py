"""Bundled agent skills: vendored skill directories shipped with Forge.

The repository carries trimmed, license-preserving copies of upstream skills
under ``skills-bundled/`` (one directory per skill, Claude Code SKILL.md
format). On startup (wired from bootstrap) ``seed_bundled_skills`` copies each
one into the shared skills volume and registers a Skill row, exactly like a
git-installed skill — idempotent, so reboots and upgrades are safe: an
already-registered skill is only re-copied when its files vanished from the
volume, and user edits to the row (enabled flag) are never touched.

Reuses skills_service's frontmatter parsing, slugging, and symlink-escape
guard so bundled dirs obey the same safety rules as cloned repos.
"""

import logging
import shutil
from pathlib import Path

from sqlmodel import select

from ..config import get_settings
from ..db import read_session, write_session
from ..models import Skill
from .events import bus
from .skills_service import (
    SkillError,
    _reject_escaping_symlinks,
    parse_frontmatter,
    slugify,
)

log = logging.getLogger(__name__)

# Image layout first (orchestrator Dockerfile: WORKDIR /app, plus
# `COPY skills-bundled ./skills-bundled`), then the dev layout where the
# package runs from the repo checkout and skills-bundled/ sits at the root —
# same two-layout resolution bootstrap uses for scripts/.
_BUNDLED_CANDIDATES = (
    Path("/app/skills-bundled"),
    Path(__file__).resolve().parents[3] / "skills-bundled",
)


def bundled_root() -> Path | None:
    for candidate in _BUNDLED_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


def _seed_one(skill_dir: Path, root: Path, skills_root: Path) -> bool:
    """Copy + register one bundled skill dir. True when a new row was added."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        log.warning("bundled skill %s has no SKILL.md — skipped", skill_dir.name)
        return False
    _reject_escaping_symlinks(skill_dir, root)
    meta = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
    name = str(meta.get("name") or skill_dir.name)
    description = str(meta.get("description") or "")
    dest = skills_root / slugify(name)

    def copy_files(target: Path) -> None:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            skill_dir, target, symlinks=True, ignore=shutil.ignore_patterns(".git")
        )

    with read_session() as db:
        existing = db.exec(select(Skill).where(Skill.name == name)).first()
    if existing is not None:
        # Already registered (this seeder or a manual install). Only restore
        # the files if they vanished from the volume; never touch the row.
        if existing.path and not Path(existing.path).exists():
            copy_files(Path(existing.path))
            log.info("restored missing files for bundled skill %r", name)
        return False

    copy_files(dest)
    skill = Skill(
        name=name,
        description=description,
        source_url=f"bundled://{skill_dir.name}",
        path=str(dest),
    )
    with write_session() as db:
        db.add(skill)
    bus.publish("skill.installed", {"name": name})
    log.info("seeded bundled skill %r", name)
    return True


def seed_bundled_skills() -> int:
    """Idempotently seed every directory under skills-bundled/ into the skills
    volume + Skill table. Returns the number of newly registered skills."""
    root = bundled_root()
    if root is None:
        log.info("no skills-bundled directory found — nothing to seed")
        return 0
    skills_root = Path(get_settings().skills_dir)
    skills_root.mkdir(parents=True, exist_ok=True)

    created = 0
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            if _seed_one(skill_dir, root, skills_root):
                created += 1
        except (SkillError, OSError) as exc:
            # One broken bundle must not block startup or the other bundles.
            detail = getattr(exc, "detail", exc)
            log.warning("bundled skill %s not seeded: %s", skill_dir.name, detail)
    return created
