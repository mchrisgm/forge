"""Multi-user auth: per-profile username+password -> signed bearer token.

Anyone on the LAN can create a profile (open registration, admin-toggleable);
the first registered profile becomes the admin. Tokens are HS256 JWTs signed
with FORGE_SECRET_KEY (or a generated key persisted in Settings so tokens
survive restarts). SSE endpoints can't set headers from EventSource, so
`?token=` is accepted as an alternative to the Authorization header.

Legacy note: v1 was single-password (FORGE_PASSWORD -> "owner" token). Those
tokens and that login shape are no longer accepted — the first-run setup
wizard creates the first real profile instead.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlmodel import select

from .config import get_settings
from .db import get_setting, read_session, set_setting, write_session
from .models import User

TOKEN_TTL_DAYS = 30
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
MIN_PASSWORD_LEN = 6

_hasher = PasswordHasher()


def ensure_auth_seeded() -> None:
    """Idempotent startup seeding of the token-signing key."""
    settings = get_settings()
    if not get_setting("secret_key"):
        set_setting("secret_key", settings.secret_key or secrets.token_hex(32))


def _signing_key() -> str:
    key = get_setting("secret_key")
    if not key:
        ensure_auth_seeded()
        key = get_setting("secret_key")
    return key


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_user_password(user: User, password: str) -> bool:
    if not user.password_hash:
        return False
    try:
        return _hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        return False


def user_count() -> int:
    with read_session() as db:
        return len(db.exec(select(User.id)).all())


def registration_allowed() -> bool:
    """Open on a fresh install; afterwards the admin can close it."""
    if user_count() == 0:
        return True
    return get_setting("allow_registration", "true") != "false"


def validate_new_credentials(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(
            400,
            "username must be 3-32 chars: lowercase letters, digits, '-', '_' "
            "(starting with a letter or digit)",
        )
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD_LEN} characters")


def register_user(username: str, password: str, display_name: str = "") -> User:
    username = username.strip().lower()
    validate_new_credentials(username, password)
    if not registration_allowed():
        raise HTTPException(403, "registration is disabled — ask the admin to enable it")

    first = user_count() == 0
    with write_session() as db:
        if db.exec(select(User).where(User.username == username)).first():
            raise HTTPException(409, "that username is taken")
        user = User(
            username=username,
            display_name=display_name.strip() or username,
            password_hash=hash_password(password),
            is_admin=first,
        )
        db.add(user)
        db.flush()
        db.refresh(user)
        user_id = user.id

    from .services.user_service import on_user_created

    on_user_created(user_id, adopt_legacy=first)
    with read_session() as db:
        return db.get(User, user_id)


def issue_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "name": user.username,
        "iat": now,
        "exp": now + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def _token_from_request(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:]
    return request.query_params.get("token", "")


def user_id_from_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    try:
        return int(payload.get("sub", ""))
    except (TypeError, ValueError):
        return None  # legacy "owner" tokens


def current_user(request: Request) -> User:
    """FastAPI dependency: the authenticated profile making this request."""
    token = _token_from_request(request)
    user_id = user_id_from_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    with read_session() as db:
        user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="profile no longer exists")
    return user


def require_auth(user: User = Depends(current_user)) -> None:
    """Route guard for endpoints that need a valid login but not the profile."""


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "admin only")
    return user
