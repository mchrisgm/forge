import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from ..auth import current_user
from ..connector_catalog import CATALOG
from ..db import read_session, write_session
from ..models import Connector, User
from ..services import github_api, oauth_flows, user_service
from ..services.github_api import GitHubApiError
from ..services.oauth_flows import OAuthError

router = APIRouter(prefix="/connectors")

MASK = "••••••"

_CATEGORY_ORDER = {"core": 0, "productivity": 1, "developer": 2, "design": 3, "business": 4}


def _public_view(connector: Connector) -> dict:
    try:
        config = json.loads(connector.config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    oauth_meta = config.get("oauth") if isinstance(config.get("oauth"), dict) else None
    oauth_view = {
        **oauth_flows.provider_status(connector.kind),
        "connected": bool(oauth_meta),
        "account": (oauth_meta or {}).get("account", ""),
        "connected_at": (oauth_meta or {}).get("connected_at"),
    }
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
            "oauth": oauth_view,
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
        "oauth": oauth_view,
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
def list_connectors(user: User = Depends(current_user)) -> list[dict]:
    # Registration seeds the catalog as of that moment — backfill any entries
    # added to the catalog since (idempotent; no-op on the common path).
    user_service.ensure_catalog_connectors(user.id)
    with read_session() as db:
        rows = db.exec(select(Connector).where(Connector.user_id == user.id)).all()
    views = [_public_view(row) for row in rows]
    views.sort(key=lambda v: (_CATEGORY_ORDER.get(v["category"], 9), v["name"].lower()))
    return views


@router.patch("/{kind}")
def patch_connector(
    kind: str, body: PatchBody, user: User = Depends(current_user)
) -> dict:
    with write_session() as db:
        connector = db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == user.id
            )
        ).first()
        if connector is None:
            raise HTTPException(404, "connector not found")
        if body.enabled is not None:
            connector.enabled = body.enabled
        if body.config is not None:
            current = json.loads(connector.config_json or "{}")
            for key, value in body.config.items():
                if value == MASK:
                    continue  # the UI echoed the mask back — keep the stored value
                if key == "token":
                    # A hand-edited token replaces (or clears) an OAuth sign-in:
                    # the account chip must not claim a connection it no longer has.
                    current.pop("oauth", None)
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
def add_custom(body: CustomBody, user: User = Depends(current_user)) -> dict:
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
        if db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == user.id
            )
        ).first():
            raise HTTPException(409, f"connector '{kind}' already exists")
        connector = Connector(
            kind=kind, enabled=True, config_json=config, user_id=user.id
        )
        db.add(connector)
        db.flush()
        db.refresh(connector)
        result = _public_view(connector)
    return result


# ── OAuth sign-in (per-user; GitHub device flow, Hugging Face PKCE) ─────────


class OAuthStartBody(BaseModel):
    # code-flow only: the SPA's callback URL (window.location.origin +
    # "/oauth/callback"), replayed verbatim in the token exchange.
    redirect_uri: str = ""


class OAuthPollBody(BaseModel):
    flow_id: str


class OAuthExchangeBody(BaseModel):
    code: str
    state: str


def _oauth_http(exc: OAuthError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


@router.get("/oauth/providers")
def oauth_providers(user: User = Depends(current_user)) -> dict:
    """Which connector kinds support OAuth sign-in and whether each is ready
    (client id configured by the admin)."""
    return {kind: oauth_flows.provider_status(kind) for kind in oauth_flows.PROVIDERS}


def _request_origins(request: Request) -> set[str]:
    """Origins this browser is actually talking to — the only place a code
    flow may redirect back to. Origin header when present (fetch POSTs send
    it); otherwise the Host header under either scheme (gateway-agnostic)."""
    origins: set[str] = set()
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin:
        origins.add(origin)
    host = request.headers.get("host") or ""
    if host:
        origins.add(f"http://{host}")
        origins.add(f"https://{host}")
    return origins


@router.post("/{kind}/oauth/start")
async def oauth_start(
    kind: str,
    body: OAuthStartBody,
    request: Request,
    user: User = Depends(current_user),
) -> dict:
    """Begin a sign-in: device flow returns a user code to enter on the
    provider's page; code flow returns the authorize URL to open."""
    provider = oauth_flows.PROVIDERS.get(kind)
    try:
        if provider is not None and provider.method == "code":
            return await oauth_flows.start_code(
                kind, user.id, body.redirect_uri, _request_origins(request)
            )
        return await oauth_flows.start_device(kind, user.id)
    except OAuthError as exc:
        raise _oauth_http(exc) from exc


@router.post("/{kind}/oauth/poll")
async def oauth_poll(
    kind: str, body: OAuthPollBody, user: User = Depends(current_user)
) -> dict:
    """Device flow: check whether the user has approved yet. Returns
    {status: pending} until the provider mints the token."""
    try:
        return await oauth_flows.poll_device(kind, body.flow_id, user.id)
    except OAuthError as exc:
        raise _oauth_http(exc) from exc


@router.post("/{kind}/oauth/exchange")
async def oauth_exchange(
    kind: str, body: OAuthExchangeBody, user: User = Depends(current_user)
) -> dict:
    """Code flow: the SPA callback posts the provider's code + state here."""
    try:
        return await oauth_flows.exchange_code(kind, user.id, body.code, body.state)
    except OAuthError as exc:
        raise _oauth_http(exc) from exc


@router.delete("/{kind}/oauth")
def oauth_disconnect(kind: str, user: User = Depends(current_user)) -> dict:
    try:
        oauth_flows.disconnect(user.id, kind)
    except OAuthError as exc:
        raise _oauth_http(exc) from exc
    return {"ok": True}


@router.get("/github/repos")
async def github_repos(
    q: str = "", user: User = Depends(current_user)
) -> list[dict]:
    """The caller's own repos (public and private) for the session-creation
    picker — requires their github connector to be connected (OAuth) or to
    hold a pasted PAT."""
    token = oauth_flows.stored_token(user.id, "github")
    if not token:
        raise HTTPException(
            409,
            "GitHub is not connected. Sign in with GitHub (or paste a PAT) on "
            "the Connectors page to pick from your repositories.",
        )
    try:
        return await github_api.list_repos(token, q)
    except GitHubApiError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.delete("/{kind}")
def delete_connector(kind: str, user: User = Depends(current_user)) -> dict:
    if not kind.startswith("custom-"):
        raise HTTPException(400, "only custom connectors can be removed")
    with write_session() as db:
        connector = db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == user.id
            )
        ).first()
        if connector is None:
            raise HTTPException(404, "connector not found")
        db.delete(connector)
    return {"ok": True}
