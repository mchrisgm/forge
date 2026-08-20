"""Per-user OAuth sign-in for connectors (GitHub, Hugging Face, …).

Every Forge profile connects its OWN accounts — the same device-code and
PKCE flows Claude Code and Codex use — instead of sharing one server-wide
PAT. A successful flow stores the access token in the user's existing
Connector row under ``config_json["token"]``, which is exactly where a
hand-pasted key would live: everything downstream (session env vars, the
git credential store that clones private repos, MCP auth headers, HF
downloads) picks it up with no further wiring.

Two grant types cover the providers a LAN-hosted app can realistically use:

- **device** (GitHub): no redirect URI needed — Forge shows a short code,
  the user enters it on the provider's device page, Forge polls for the
  token. Perfect for self-hosted boxes with no public URL.
- **code** (Hugging Face): standard authorization-code + PKCE. The UI opens
  the provider's consent page with a redirect back to the SPA route
  ``/oauth/callback``, which posts the code here for the exchange. The
  redirect URI the browser used is stored per flow and replayed verbatim in
  the token call, so Forge never has to guess its own public origin.

Both need a client id the admin creates once with the provider (an OAuth
app pointing at their Forge host); ids live in the Setting table (Settings
page) with env fallbacks. Adding another provider = one PROVIDERS entry —
the flows, storage, and UI are generic. Providers whose MCP servers do
their own OAuth handshake in-session (several catalog entries) don't need
entries here.

Threat model is the LAN (PLAN §7): tokens rest in config_json like every
other connector secret; flows are in-memory, single-use, TTL-bounded, and
scoped to the user who started them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass

import httpx
from sqlmodel import select

from ..config import get_settings
from ..db import get_setting, read_session, write_session
from ..models import Connector

log = logging.getLogger(__name__)

FLOW_TTL_S = 900.0  # both providers expire device/authorization codes by then


class OAuthError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class Provider:
    kind: str  # connector kind the token is stored on
    label: str
    method: str  # "device" | "code"
    scopes: str
    token_url: str
    device_code_url: str = ""  # device only
    authorize_url: str = ""  # code only
    identity_url: str = ""
    identity_field: str = ""  # JSON key holding the account name
    # Setting-table keys (env-fallback values come from Settings fields of
    # the same name).
    client_id_key: str = ""
    client_secret_key: str = ""
    setup_note: str = ""
    setup_url: str = ""


PROVIDERS: dict[str, Provider] = {
    "github": Provider(
        kind="github",
        label="GitHub",
        method="device",
        # repo: list/clone/push private repos; read:user: show who connected.
        scopes="repo read:user",
        device_code_url="https://github.com/login/device/code",
        token_url="https://github.com/login/oauth/access_token",
        identity_url="https://api.github.com/user",
        identity_field="login",
        client_id_key="github_oauth_client_id",
        setup_note=(
            "Create a GitHub OAuth App (any callback URL) with device flow "
            "enabled, then paste its client ID here. No client secret needed."
        ),
        setup_url="https://github.com/settings/developers",
    ),
    "hugging-face": Provider(
        kind="hugging-face",
        label="Hugging Face",
        method="code",
        # read-repos covers gated/private model downloads and the HF MCP.
        scopes="openid profile read-repos",
        authorize_url="https://huggingface.co/oauth/authorize",
        token_url="https://huggingface.co/oauth/token",
        identity_url="https://huggingface.co/api/whoami-v2",
        identity_field="name",
        client_id_key="hf_oauth_client_id",
        client_secret_key="hf_oauth_client_secret",
        setup_note=(
            "Create a Hugging Face OAuth app with redirect URL "
            "http(s)://<your forge host>/oauth/callback and paste its client "
            "ID (and secret, if issued) here."
        ),
        setup_url="https://huggingface.co/settings/applications",
    ),
}


def client_config(provider: Provider) -> tuple[str, str]:
    """(client_id, client_secret) — Setting-table override, env fallback."""
    settings = get_settings()
    client_id = get_setting(provider.client_id_key) or getattr(
        settings, provider.client_id_key, ""
    )
    secret = ""
    if provider.client_secret_key:
        secret = get_setting(provider.client_secret_key) or getattr(
            settings, provider.client_secret_key, ""
        )
    return client_id, secret


def provider_status(kind: str) -> dict:
    """What the UI needs to render a connect button (or its setup hint)."""
    provider = PROVIDERS.get(kind)
    if provider is None:
        return {"supported": False}
    client_id, _ = client_config(provider)
    return {
        "supported": True,
        "method": provider.method,
        "ready": bool(client_id),
        "setup_note": provider.setup_note,
        "setup_url": provider.setup_url,
    }


# ── pending flows (in-memory, TTL-bounded, per-user) ────────────────────────


@dataclass
class PendingFlow:
    id: str
    user_id: int
    kind: str
    created_at: float
    device_code: str = ""  # device
    interval: float = 5.0  # device: min seconds between token polls
    last_poll: float = 0.0
    verifier: str = ""  # code (PKCE)
    redirect_uri: str = ""  # code


_flows: dict[str, PendingFlow] = {}


def _sweep() -> None:
    now = time.time()
    for flow_id in list(_flows):
        if now - _flows[flow_id].created_at > FLOW_TTL_S:
            del _flows[flow_id]


def _flow_for(flow_id: str, user_id: int) -> PendingFlow:
    _sweep()
    flow = _flows.get(flow_id)
    if flow is None or flow.user_id != user_id:
        raise OAuthError("sign-in flow not found or expired — start again", 404)
    return flow


def _provider_for(kind: str) -> Provider:
    provider = PROVIDERS.get(kind)
    if provider is None:
        raise OAuthError(f"connector {kind!r} does not support OAuth sign-in", 404)
    return provider


def _require_client(provider: Provider) -> tuple[str, str]:
    client_id, secret = client_config(provider)
    if not client_id:
        raise OAuthError(
            f"{provider.label} sign-in is not configured yet. "
            f"{provider.setup_note}",
            409,
        )
    return client_id, secret


# ── token storage ───────────────────────────────────────────────────────────


def store_token(
    user_id: int, kind: str, token: str, account: str, scopes: str, method: str
) -> None:
    """Write the minted token into the user's connector row — the same slot a
    pasted key uses — plus an ``oauth`` block so the UI can show the account.
    Enables the connector: signing in IS the configuration."""
    with write_session() as db:
        connector = db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == user_id
            )
        ).first()
        if connector is None:  # catalog seeding predates this kind — self-heal
            connector = Connector(kind=kind, user_id=user_id, enabled=True)
        config = {}
        try:
            config = json.loads(connector.config_json or "{}")
        except json.JSONDecodeError:
            pass
        config["token"] = token
        config["oauth"] = {
            "account": account,
            "scopes": scopes,
            "method": method,
            "connected_at": int(time.time()),
        }
        connector.config_json = json.dumps(config)
        connector.enabled = True
        db.add(connector)


def disconnect(user_id: int, kind: str) -> None:
    """Drop the OAuth token + metadata (a pasted key elsewhere is untouched
    because OAuth and paste share the token slot — disconnecting clears it)."""
    with write_session() as db:
        connector = db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == user_id
            )
        ).first()
        if connector is None:
            raise OAuthError("connector not found", 404)
        try:
            config = json.loads(connector.config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        if "oauth" not in config:
            raise OAuthError("this connector has no OAuth connection", 409)
        config.pop("oauth", None)
        config.pop("token", None)
        connector.config_json = json.dumps(config)
        db.add(connector)


def stored_token(user_id: int, kind: str) -> str:
    """The user's token for a connector kind (OAuth-minted or hand-pasted),
    only while the connector is enabled — the toggle must cut access."""
    with read_session() as db:
        connector = db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == user_id
            )
        ).first()
    if connector is None or not connector.enabled:
        return ""
    try:
        config = json.loads(connector.config_json or "{}")
    except json.JSONDecodeError:
        return ""
    return str(config.get("token") or "")


async def _fetch_identity(provider: Provider, token: str) -> str:
    if not provider.identity_url:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(
                provider.identity_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            return str(resp.json().get(provider.identity_field) or "")
    except (httpx.HTTPError, ValueError) as exc:  # non-fatal: token still works
        log.warning("%s identity fetch failed: %s", provider.kind, exc)
        return ""


# ── device flow (GitHub) ────────────────────────────────────────────────────


async def start_device(kind: str, user_id: int) -> dict:
    provider = _provider_for(kind)
    if provider.method != "device":
        raise OAuthError(f"{provider.label} uses a browser redirect, not a code")
    client_id, _ = _require_client(provider)
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                provider.device_code_url,
                data={"client_id": client_id, "scope": provider.scopes},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError(f"{provider.label} is unreachable: {exc}", 502) from exc
    if "device_code" not in data:
        raise OAuthError(
            f"{provider.label} rejected the sign-in request: "
            f"{data.get('error_description') or data.get('error') or 'unknown error'}",
            502,
        )
    flow = PendingFlow(
        id=secrets.token_urlsafe(16),
        user_id=user_id,
        kind=kind,
        created_at=time.time(),
        device_code=data["device_code"],
        interval=float(data.get("interval", 5)),
    )
    _sweep()
    _flows[flow.id] = flow
    return {
        "flow": "device",
        "flow_id": flow.id,
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", ""),
        "interval": flow.interval,
        "expires_in": data.get("expires_in", int(FLOW_TTL_S)),
    }


async def poll_device(kind: str, flow_id: str, user_id: int) -> dict:
    provider = _provider_for(kind)
    flow = _flow_for(flow_id, user_id)
    if flow.kind != kind:
        raise OAuthError("flow belongs to a different connector", 404)
    now = time.time()
    if now - flow.last_poll < flow.interval:
        return {"status": "pending"}  # too eager — don't hammer the provider
    flow.last_poll = now
    client_id, _ = _require_client(provider)
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                provider.token_url,
                data={
                    "client_id": client_id,
                    "device_code": flow.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError(f"{provider.label} is unreachable: {exc}", 502) from exc

    error = data.get("error", "")
    if error == "authorization_pending":
        return {"status": "pending"}
    if error == "slow_down":
        flow.interval += 5  # per RFC 8628
        return {"status": "pending", "interval": flow.interval}
    if error == "expired_token":
        _flows.pop(flow_id, None)
        raise OAuthError("the code expired before it was entered — start again", 410)
    if error == "access_denied":
        _flows.pop(flow_id, None)
        raise OAuthError("sign-in was declined on the provider page", 403)
    token = data.get("access_token", "")
    if error or not token:
        _flows.pop(flow_id, None)
        raise OAuthError(
            f"{provider.label} sign-in failed: "
            f"{data.get('error_description') or error or 'no token returned'}",
            502,
        )
    _flows.pop(flow_id, None)
    account = await _fetch_identity(provider, token)
    store_token(
        user_id, kind, token, account, data.get("scope", provider.scopes), "device"
    )
    return {"status": "connected", "account": account}


# ── authorization-code flow with PKCE (Hugging Face) ────────────────────────


def _valid_redirect(uri: str) -> bool:
    return bool(uri) and uri.startswith(("http://", "https://")) and (
        uri.endswith("/oauth/callback")
    )


async def start_code(kind: str, user_id: int, redirect_uri: str) -> dict:
    provider = _provider_for(kind)
    if provider.method != "code":
        raise OAuthError(f"{provider.label} uses a device code, not a redirect")
    client_id, _ = _require_client(provider)
    if not _valid_redirect(redirect_uri):
        raise OAuthError("redirect_uri must be <origin>/oauth/callback")
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    flow = PendingFlow(
        id=secrets.token_urlsafe(24),  # doubles as the OAuth `state`
        user_id=user_id,
        kind=kind,
        created_at=time.time(),
        verifier=verifier,
        redirect_uri=redirect_uri,
    )
    _sweep()
    _flows[flow.id] = flow
    params = httpx.QueryParams(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": provider.scopes,
            "state": flow.id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {
        "flow": "code",
        "flow_id": flow.id,
        "authorize_url": f"{provider.authorize_url}?{params}",
    }


async def exchange_code(kind: str, user_id: int, code: str, state: str) -> dict:
    provider = _provider_for(kind)
    flow = _flow_for(state, user_id)  # state IS the flow id — CSRF check built in
    if flow.kind != kind or not flow.verifier:
        raise OAuthError("flow belongs to a different connector", 404)
    _flows.pop(state, None)  # single-use, success or not
    client_id, client_secret = _require_client(provider)
    form = {
        "client_id": client_id,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": flow.redirect_uri,  # must match the authorize call
        "code_verifier": flow.verifier,
    }
    if client_secret:
        form["client_secret"] = client_secret
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                provider.token_url, data=form, headers={"Accept": "application/json"}
            )
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError(f"{provider.label} is unreachable: {exc}", 502) from exc
    token = data.get("access_token", "")
    if not token:
        raise OAuthError(
            f"{provider.label} sign-in failed: "
            f"{data.get('error_description') or data.get('error') or 'no token returned'}",
            502,
        )
    account = await _fetch_identity(provider, token)
    store_token(
        user_id, kind, token, account, data.get("scope", provider.scopes), "code"
    )
    return {"status": "connected", "account": account}
