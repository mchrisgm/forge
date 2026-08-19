import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..db import read_session
from ..models import Skill
from ..services import skills_service
from ..services.skills_service import SkillError

router = APIRouter(prefix="/skills")


class InstallBody(BaseModel):
    git_url: str
    subdir: str | None = None


class PatchBody(BaseModel):
    enabled: bool


@router.get("")
def list_skills() -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(Skill)).all()
    rows = sorted(rows, key=lambda r: r.name.lower())
    return [r.model_dump(mode="json") for r in rows]


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
