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


def _validate_pack_url(git_url: str) -> None:
    # Pack imports copy many directories at once, so hold them to the
    # stricter https-only shape (no plaintext http, no ssh/scp forms).
    if not re.match(r"^https://[\w.-]+(:\d+)?/", git_url):
        raise SkillError("only https git URLs are supported")


def _clone(git_url: str, clone_dir: Path) -> None:
    """Shallow-clone git_url into clone_dir (list-args subprocess, no shell)."""
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


def _reject_escaping_symlinks(skill_dir: Path, clone_root: Path) -> None:
    """A malicious repo could ship symlinks to orchestrator files (DB with the
    JWT signing key and PAT); shutil.copytree dereferences them by default,
    which would copy those secrets into the session-readable /skills volume.
    Refuse any symlink that resolves outside the clone."""
    root = clone_root.resolve()
    for path in skill_dir.rglob("*"):
        if path.is_symlink():
            resolved = path.resolve()
            if not str(resolved).startswith(str(root) + "/") and resolved != root:
                raise SkillError(
                    f"skill contains a symlink escaping the repository: "
                    f"{path.relative_to(clone_root)}"
                )


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


def _install_skill_dir(
    skill_dir: Path,
    clone_root: Path,
    git_url: str,
    skills_root: Path,
    *,
    enabled: bool = True,
) -> Skill:
    """Validate a SKILL.md-bearing directory inside a clone, copy it into the
    skills volume, and create the Skill row. Shared by single installs and
    bulk pack imports so both go through the same symlink/name checks."""
    _reject_escaping_symlinks(skill_dir, clone_root)
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
    # symlinks=True copies links verbatim (intra-repo links keep working,
    # anything else dangles harmlessly inside the read-only volume) instead
    # of dereferencing them into the shared volume.
    shutil.copytree(skill_dir, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))

    skill = Skill(
        name=name,
        description=description,
        source_url=git_url,
        path=str(dest),
        enabled=enabled,
    )
    with write_session() as db:
        db.add(skill)
        db.flush()
        db.refresh(skill)
        skill_id = skill.id
    with read_session() as db:
        skill = db.get(Skill, skill_id)
    bus.publish("skill.installed", {"name": name})
    return skill


def install(git_url: str, subdir: str | None = None) -> Skill:
    _validate_git_url(git_url)
    settings = get_settings()
    skills_root = Path(settings.skills_dir)
    skills_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="forge-skill-") as tmp:
        clone_dir = Path(tmp) / "repo"
        _clone(git_url, clone_dir)
        skill_dir = _find_skill_dir(clone_dir, subdir)
        return _install_skill_dir(skill_dir, clone_dir, git_url, skills_root)


PACK_SCAN_CAP = 500
PACK_INSTALL_CAP = 100


def _enumerate_pack(clone_dir: Path) -> list[dict]:
    """List every skill directory (SKILL.md at depth 1 or 2) in a clone."""
    found: list[dict] = []
    paths = sorted(clone_dir.glob("*/SKILL.md")) + sorted(clone_dir.glob("*/*/SKILL.md"))
    for skill_md in paths:
        if len(found) >= PACK_SCAN_CAP:
            log.warning("pack scan capped at %d skills", PACK_SCAN_CAP)
            break
        skill_dir = skill_md.parent
        subdir = str(skill_dir.relative_to(clone_dir))
        if ".git" in skill_dir.relative_to(clone_dir).parts:
            continue
        try:
            meta = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            meta = {}
        entry = {
            "name": str(meta.get("name") or skill_dir.name),
            "description": str(meta.get("description") or ""),
            "subdir": subdir,
        }
        if not meta:
            # Tolerated, not fatal: install falls back to the directory name.
            entry["note"] = "SKILL.md frontmatter missing or malformed"
        found.append(entry)
    return found


def scan_pack(git_url: str) -> list[dict]:
    """Shallow-clone a skill monorepo and list its installable skills as
    [{name, description, subdir}] (plus a 'note' on malformed frontmatter),
    capped at PACK_SCAN_CAP. The temp clone is removed before returning."""
    _validate_pack_url(git_url)
    with tempfile.TemporaryDirectory(prefix="forge-pack-") as tmp:
        clone_dir = Path(tmp) / "repo"
        _clone(git_url, clone_dir)
        return _enumerate_pack(clone_dir)


def install_from_pack(git_url: str, subdirs: list[str]) -> dict:
    """Install a batch of skills from one repo with a single clone. Each
    subdir goes through the same validation as a single install (symlink
    escape, SKILL.md presence, duplicate name). Bulk-imported skills start
    disabled so a large import cannot flood the session tool listing."""
    _validate_pack_url(git_url)
    if not subdirs:
        raise SkillError("no subdirs selected")
    if len(subdirs) > PACK_INSTALL_CAP:
        raise SkillError(f"at most {PACK_INSTALL_CAP} skills per pack install")
    settings = get_settings()
    skills_root = Path(settings.skills_dir)
    skills_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    skipped: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="forge-pack-") as tmp:
        clone_dir = Path(tmp) / "repo"
        _clone(git_url, clone_dir)
        for subdir in subdirs:
            try:
                skill_dir = _find_skill_dir(clone_dir, subdir)
                skill = _install_skill_dir(
                    skill_dir, clone_dir, git_url, skills_root, enabled=False
                )
                installed.append(skill.name)
            except SkillError as exc:
                skipped.append({"subdir": subdir, "reason": exc.detail})
    return {
        "installed": installed,
        "skipped": skipped,
        "note": "bulk-imported skills start disabled — enable the ones you want "
        "sessions to load",
    }


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
