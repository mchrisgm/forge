import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..db import read_session, write_session
from ..models import Connector, ConnectorKind

router = APIRouter(prefix="/connectors")

# Config keys that hold secrets — echoed back only as a mask
SECRET_KEYS = {"token", "pat", "api_key"}
MASK = "••••••"


def _masked(connector: Connector) -> dict:
    config = json.loads(connector.config_json or "{}")
    masked = {
        key: (MASK if key in SECRET_KEYS and value else value)
        for key, value in config.items()
    }
    return {
        "id": connector.id,
        "kind": connector.kind.value,
        "enabled": connector.enabled,
        "config": masked,
        "has_token": bool(config.get("token")),
    }


class PatchBody(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


@router.get("")
def list_connectors() -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(Connector)).all()
    rows = sorted(rows, key=lambda r: r.kind.value)
    return [_masked(r) for r in rows]


@router.patch("/{kind}")
def patch_connector(kind: ConnectorKind, body: PatchBody) -> dict:
    with write_session() as db:
        connector = db.exec(select(Connector).where(Connector.kind == kind)).first()
        if connector is None:
            raise HTTPException(404, "connector not found")
        if body.enabled is not None:
            connector.enabled = body.enabled
        if body.config is not None:
            current = json.loads(connector.config_json or "{}")
            for key, value in body.config.items():
                # Ignore the mask echoed back by the UI; empty string clears
                if value == MASK:
                    continue
                if value == "" or value is None:
                    current.pop(key, None)
                else:
                    current[key] = value
            connector.config_json = json.dumps(current)
        db.add(connector)
        db.flush()
        db.refresh(connector)
        result = _masked(connector)
    return result
