import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..connector_catalog import CATALOG
from ..db import read_session, write_session
from ..models import Connector

router = APIRouter(prefix="/connectors")

MASK = "••••••"

_CATEGORY_ORDER = {"core": 0, "productivity": 1, "developer": 2, "design": 3, "business": 4}


def _public_view(connector: Connector) -> dict:
    try:
        config = json.loads(connector.config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    entry = CATALOG.get(connector.kind)
    if entry is not None:
        fields = [
            {
                "key": auth_field.key,
                "label": auth_field.label,
                "secret": auth_field.secret,
                "placeholder": auth_field.placeholder,
                "value": (
                    (MASK if config.get(auth_field.key) else "")
                    if auth_field.secret
                    else config.get(auth_field.key, "")
                ),
                "configured": bool(config.get(auth_field.key)),
            }
            for auth_field in entry.auth_fields
        ]
        return {
            "kind": connector.kind,
            "name": entry.name,
            "description": entry.description,
            "category": entry.category,
            "mcp_type": entry.mcp_type,
            "enabled": connector.enabled,
            "auth_fields": fields,
            "auth_note": entry.auth_note,
            "docs_url": entry.docs_url,
            "is_custom": False,
            # Backcompat for the existing UI (github card)
            "has_token": bool(config.get("token")),
        }
    # Custom connector — definition lives in config_json["mcp"]
    mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    return {
        "kind": connector.kind,
        "name": config.get("display_name") or connector.kind,
        "description": mcp.get("url") or " ".join(mcp.get("command", [])) or "Custom MCP server",
        "category": "custom",
        "mcp_type": mcp.get("type", "remote"),
        "enabled": connector.enabled,
        "auth_fields": [],
        "auth_note": "",
        "docs_url": "",
        "is_custom": True,
        "has_token": False,
    }


class PatchBody(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


class CustomBody(BaseModel):
    name: str
    mcp_type: str = "remote"  # remote | local
    url: str = ""
    command: list[str] = []
    headers: dict[str, str] = {}
    environment: dict[str, str] = {}


@router.get("")
def list_connectors() -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(Connector)).all()
    views = [_public_view(row) for row in rows]
    views.sort(key=lambda v: (_CATEGORY_ORDER.get(v["category"], 9), v["name"].lower()))
    return views


@router.patch("/{kind}")
def patch_connector(kind: str, body: PatchBody) -> dict:
    with write_session() as db:
        connector = db.exec(select(Connector).where(Connector.kind == kind)).first()
        if connector is None:
            raise HTTPException(404, "connector not found")
        if body.enabled is not None:
            connector.enabled = body.enabled
        if body.config is not None:
            current = json.loads(connector.config_json or "{}")
            for key, value in body.config.items():
                if value == MASK:
                    continue  # the UI echoed the mask back — keep the stored value
                if value == "" or value is None:
                    current.pop(key, None)
                else:
                    current[key] = value
            connector.config_json = json.dumps(current)
        db.add(connector)
        db.flush()
        db.refresh(connector)
        result = _public_view(connector)
    return result


@router.post("/custom")
def add_custom(body: CustomBody) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:40]
    if not slug:
        raise HTTPException(400, "name required")
    kind = f"custom-{slug}"
    if body.mcp_type == "remote":
        if not re.match(r"^https?://", body.url):
            raise HTTPException(400, "remote connectors need an http(s) url")
        mcp: dict = {"type": "remote", "url": body.url}
        if body.headers:
            mcp["headers"] = body.headers
    elif body.mcp_type == "local":
        if not body.command:
            raise HTTPException(400, "local connectors need a command")
        mcp = {"type": "local", "command": body.command}
        if body.environment:
            mcp["environment"] = body.environment
    else:
        raise HTTPException(400, "mcp_type must be remote or local")

    config = json.dumps({"display_name": body.name, "mcp": mcp})
    with write_session() as db:
        if db.exec(select(Connector).where(Connector.kind == kind)).first():
            raise HTTPException(409, f"connector '{kind}' already exists")
        connector = Connector(kind=kind, enabled=True, config_json=config)
        db.add(connector)
        db.flush()
        db.refresh(connector)
        result = _public_view(connector)
    return result


@router.delete("/{kind}")
def delete_connector(kind: str) -> dict:
    if not kind.startswith("custom-"):
        raise HTTPException(400, "only custom connectors can be removed")
    with write_session() as db:
        connector = db.exec(select(Connector).where(Connector.kind == kind)).first()
        if connector is None:
            raise HTTPException(404, "connector not found")
        db.delete(connector)
    return {"ok": True}
