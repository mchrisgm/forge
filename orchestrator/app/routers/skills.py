import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..db import read_session
from ..models import Skill
from ..services import skills_service
from ..services.skills_service import PACK_INSTALL_CAP, SkillError
from ..skill_catalog import CATALOG, get_entry

router = APIRouter(prefix="/skills")


class InstallBody(BaseModel):
    git_url: str
    subdir: str | None = None


class PatchBody(BaseModel):
    enabled: bool


class CatalogInstallBody(BaseModel):
    name: str


class PackScanBody(BaseModel):
    git_url: str


class PackInstallBody(BaseModel):
    git_url: str
    subdirs: list[str]


@router.get("")
def list_skills() -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(Skill)).all()
    rows = sorted(rows, key=lambda r: r.name.lower())
    return [r.model_dump(mode="json") for r in rows]


@router.get("/catalog")
def catalog() -> list[dict]:
    """Curated suggested skills, flagged with whether each is installed."""
    with read_session() as db:
        installed = set(db.exec(select(Skill.name)).all())
    return [
        {
            "name": entry.name,
            "description": entry.description,
            "category": entry.category,
            "repo": entry.repo,
            "subdir": entry.subdir,
            "installed": entry.name in installed,
        }
        for entry in CATALOG
    ]


@router.post("/catalog/install")
async def catalog_install(body: CatalogInstallBody) -> dict:
    entry = get_entry(body.name)
    if entry is None:
        raise HTTPException(404, f"unknown catalog skill '{body.name}'")
    with read_session() as db:
        if db.exec(select(Skill).where(Skill.name == entry.name)).first():
            raise HTTPException(409, f"skill '{entry.name}' is already installed")
    try:
        skill = await asyncio.to_thread(skills_service.install, entry.repo, entry.subdir)
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return skill.model_dump(mode="json")


@router.post("/pack/scan")
async def pack_scan(body: PackScanBody) -> list[dict]:
    """Shallow-clone a skill monorepo and list its installable skills."""
    try:
        return await asyncio.to_thread(skills_service.scan_pack, body.git_url)
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/pack/install")
async def pack_install(body: PackInstallBody) -> dict:
    """Bulk-install selected subdirs from one repo (skills start disabled)."""
    if len(body.subdirs) > PACK_INSTALL_CAP:
        raise HTTPException(400, f"at most {PACK_INSTALL_CAP} skills per pack install")
    try:
        return await asyncio.to_thread(
            skills_service.install_from_pack, body.git_url, body.subdirs
        )
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/install")
async def install(body: InstallBody) -> dict:
    try:
        skill = await asyncio.to_thread(skills_service.install, body.git_url, body.subdir)
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return skill.model_dump(mode="json")


@router.delete("/{skill_id}")
async def remove(skill_id: int) -> dict:
    try:
        await asyncio.to_thread(skills_service.remove, skill_id)
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"ok": True}


@router.patch("/{skill_id}")
def patch(skill_id: int, body: PatchBody) -> dict:
    try:
        skill = skills_service.set_enabled(skill_id, body.enabled)
    except SkillError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return skill.model_dump(mode="json")
