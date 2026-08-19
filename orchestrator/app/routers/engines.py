from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import read_session
from ..models import ModelEntry, ModelStatus
from ..services.engine_manager import LeaseHeldError, engine_manager

router = APIRouter(prefix="/engines")


class LoadBody(BaseModel):
    model_id: int
    force: bool = False


@router.get("")
def engines_status() -> dict:
    return engine_manager.status()


@router.post("/load")
async def load(body: LoadBody) -> dict:
    with read_session() as db:
        model = db.get(ModelEntry, body.model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    if model.status != ModelStatus.ready:
        raise HTTPException(409, f"model is not ready (status: {model.status.value})")
    try:
        lease = await engine_manager.load(model, force=body.force)
    except LeaseHeldError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "GPU lease is held", "holder": exc.holder},
        ) from exc
    return {"lease": lease.as_dict()}


@router.post("/unload")
async def unload() -> dict:
    await engine_manager.unload()
    return {"lease": None}
