"""The user's own memory store: inspect, edit, pin, prune."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..auth import current_user
from ..db import read_session, write_session
from ..models import MemoryEntry, MemoryKind, User
from ..services import memory

router = APIRouter(prefix="/memory")


class MemoryCreate(BaseModel):
    content: str
    kind: MemoryKind = MemoryKind.fact
    pinned: bool = False


class MemoryPatch(BaseModel):
    content: str | None = None
    kind: MemoryKind | None = None
    pinned: bool | None = None
    importance: float | None = None


def _own(entry_id: int, user: User) -> MemoryEntry:
    with read_session() as db:
        entry = db.get(MemoryEntry, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(404, "memory not found")
    return entry


@router.get("")
def list_memories(user: User = Depends(current_user)) -> list[dict]:
    with read_session() as db:
        rows = db.exec(
            select(MemoryEntry).where(MemoryEntry.user_id == user.id)
        ).all()
    rows = sorted(rows, key=lambda r: (not r.pinned, -(r.importance or 0)))
    return [r.model_dump(mode="json") for r in rows]


@router.post("")
def add_memory(body: MemoryCreate, user: User = Depends(current_user)) -> dict:
    if not body.content.strip():
        raise HTTPException(400, "content required")
    entry = MemoryEntry(
        user_id=user.id,
        content=body.content.strip()[:500],
        kind=body.kind,
        pinned=body.pinned,
        importance=1.2,  # user-authored memories start above extracted ones
    )
    with write_session() as db:
        db.add(entry)
        db.flush()
        db.refresh(entry)
        result = entry.model_dump(mode="json")
    return result


@router.patch("/{entry_id}")
def patch_memory(
    entry_id: int, body: MemoryPatch, user: User = Depends(current_user)
) -> dict:
    _own(entry_id, user)
    with write_session() as db:
        row = db.get(MemoryEntry, entry_id)
        if body.content is not None:
            row.content = body.content.strip()[:500]
        if body.kind is not None:
            row.kind = body.kind
        if body.pinned is not None:
            row.pinned = body.pinned
        if body.importance is not None:
            row.importance = max(0.1, min(2.0, body.importance))
        from ..models import utcnow

        row.updated_at = utcnow()
        db.add(row)
        db.flush()
        db.refresh(row)
        result = row.model_dump(mode="json")
    return result


@router.delete("/{entry_id}")
def delete_memory(entry_id: int, user: User = Depends(current_user)) -> dict:
    _own(entry_id, user)
    with write_session() as db:
        row = db.get(MemoryEntry, entry_id)
        if row:
            db.delete(row)
    return {"ok": True}


@router.delete("")
def clear_memories(user: User = Depends(current_user)) -> dict:
    """Forget everything (pinned included) — the user's right."""
    with write_session() as db:
        rows = db.exec(
            select(MemoryEntry).where(MemoryEntry.user_id == user.id)
        ).all()
        for row in rows:
            db.delete(row)
    return {"deleted": True}


@router.post("/consolidate")
async def consolidate(user: User = Depends(current_user)) -> dict:
    return await asyncio.wait_for(memory.consolidate_all(), timeout=120)
