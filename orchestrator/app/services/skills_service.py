"""Git-based skill installs + SKILL.md parsing (PLAN §6.5).

A skill is a directory with SKILL.md (YAML frontmatter: name, description) —
the Claude Code / Agent Skills format. Installs shallow-clone a repo into a
temp dir, locate the skill directory (repo root, given subdir, or a unique
SKILL.md-bearing child), and copy it into the shared skills volume.

Clones use subprocess with list arguments and no shell, and the URL is
scheme-validated first — no shell interpolation anywhere.
"""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
from sqlmodel import select

from ..config import get_settings
from ..db import read_session, write_session
from ..models import Skill
from .events import bus

log = logging.getLogger(__name__)


class SkillError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between leading --- markers."""
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "skill"


def _validate_git_url(git_url: str) -> None:
    if not re.match(r"^https?://[\w.-]+(:\d+)?/", git_url):
        raise SkillError("only http(s) git URLs are supported")


def _find_skill_dir(repo_root: Path, subdir: str | None) -> Path:
    if subdir:
        candidate = (repo_root / subdir).resolve()
        if not str(candidate).startswith(str(repo_root.resolve())):
            raise SkillError("subdir escapes the repository")
        if not (candidate / "SKILL.md").is_file():
            raise SkillError(f"no SKILL.md in subdir '{subdir}'")
        return candidate
    if (repo_root / "SKILL.md").is_file():
        return repo_root
    candidates = sorted(p.parent for p in repo_root.glob("*/SKILL.md")) + sorted(
        p.parent for p in repo_root.glob("*/*/SKILL.md")
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SkillError("repository contains no SKILL.md")
    rels = [str(c.relative_to(repo_root)) for c in candidates[:20]]
    raise SkillError(
        "repository contains multiple skills — pass one of these as subdir: " + ", ".join(rels)
    )


def install(git_url: str, subdir: str | None = None) -> Skill:
    _validate_git_url(git_url)
    settings = get_settings()
    skills_root = Path(settings.skills_dir)
    skills_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="forge-skill-") as tmp:
        clone_dir = Path(tmp) / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", git_url, str(clone_dir)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            raise SkillError(
                f"git clone failed: {exc.stderr.decode(errors='replace')[-400:]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillError("git clone timed out") from exc

        skill_dir = _find_skill_dir(clone_dir, subdir)
        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        meta = parse_frontmatter(skill_md)
        name = str(meta.get("name") or skill_dir.name)
        description = str(meta.get("description") or "")
        slug = slugify(name)

        with read_session() as db:
            if db.exec(select(Skill).where(Skill.name == name)).first():
                raise SkillError(f"skill '{name}' is already installed", 409)

        dest = skills_root / slug
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skill_dir, dest, ignore=shutil.ignore_patterns(".git"))

    skill = Skill(name=name, description=description, source_url=git_url, path=str(dest))
    with write_session() as db:
        db.add(skill)
        db.flush()
        db.refresh(skill)
        skill_id = skill.id
    with read_session() as db:
        skill = db.get(Skill, skill_id)
    bus.publish("skill.installed", {"name": name})
    return skill


def remove(skill_id: int) -> None:
    with read_session() as db:
        skill = db.get(Skill, skill_id)
    if skill is None:
        raise SkillError("skill not found", 404)
    path = Path(skill.path)
    settings = get_settings()
    if path.exists() and str(path.resolve()).startswith(str(Path(settings.skills_dir).resolve())):
        shutil.rmtree(path, ignore_errors=True)
    with write_session() as db:
        row = db.get(Skill, skill_id)
        if row:
            db.delete(row)
    bus.publish("skill.removed", {"id": skill_id})


def set_enabled(skill_id: int, enabled: bool) -> Skill:
    with write_session() as db:
        skill = db.get(Skill, skill_id)
        if skill is None:
            raise SkillError("skill not found", 404)
        skill.enabled = enabled
        db.add(skill)
    with read_session() as db:
        return db.get(Skill, skill_id)
