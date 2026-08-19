"""Single-password login -> signed bearer token (PLAN §7).

- The password hash (argon2) lives in the Setting table; it is seeded from
  FORGE_PASSWORD on first boot and can be changed from the Settings page.
- Tokens are HS256 JWTs signed with FORGE_SECRET_KEY (or a generated key
  persisted in Settings so tokens survive restarts).
- SSE endpoints can't set headers from EventSource, so `?token=` is accepted
  as an alternative to the Authorization header.
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request

from .config import get_settings
from .db import get_setting, set_setting

TOKEN_TTL_DAYS = 30
_hasher = PasswordHasher()


def ensure_auth_seeded() -> None:
    """Idempotent startup seeding of password hash + signing key."""
    settings = get_settings()
    if not get_setting("password_hash"):
        set_setting("password_hash", _hasher.hash(settings.password))
    if not get_setting("secret_key"):
        set_setting("secret_key", settings.secret_key or secrets.token_hex(32))


def _signing_key() -> str:
    key = get_setting("secret_key")
    if not key:
        ensure_auth_seeded()
        key = get_setting("secret_key")
    return key


def verify_password(password: str) -> bool:
    stored = get_setting("password_hash")
    if not stored:
        return False
    try:
        return _hasher.verify(stored, password)
    except VerifyMismatchError:
        return False


def change_password(new_password: str) -> None:
    set_setting("password_hash", _hasher.hash(new_password))


def issue_token() -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": "owner", "iat": now, "exp": now + timedelta(days=TOKEN_TTL_DAYS)}
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def validate_token(token: str) -> bool:
    try:
        jwt.decode(token, _signing_key(), algorithms=["HS256"])
        return True
    except jwt.PyJWTError:
        return False


def require_auth(request: Request) -> None:
    """FastAPI dependency guarding every route except /health and /auth/login."""
    token = ""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:]
    if not token:
        token = request.query_params.get("token", "")
    if not token or not validate_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
