"""Profiles: registration, login, and the current user's own settings."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..auth import (
    current_user,
    hash_password,
    issue_token,
    register_user,
    registration_allowed,
    require_admin,
    user_count,
    verify_user_password,
)
from ..db import read_session, set_setting, write_session
from ..models import User
from ..services.user_service import public_profile

auth_router = APIRouter(prefix="/auth")
users_router = APIRouter(prefix="/users")


class RegisterBody(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginBody(BaseModel):
    username: str = ""
    password: str


class ProfilePatch(BaseModel):
    display_name: str | None = None
    personal_instructions: str | None = None
    memory_enabled: bool | None = None
    avatar_color: str | None = None


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


class RegistrationToggle(BaseModel):
    allow_registration: bool


@auth_router.get("/status")
def status() -> dict:
    """Public: drives the first-run setup wizard and the register screen."""
    count = user_count()
    return {
        "setup_required": count == 0,
        "allow_registration": registration_allowed(),
        "user_count": count,
    }


@auth_router.post("/register")
def register(body: RegisterBody) -> dict:
    user = register_user(body.username, body.password, body.display_name)
    return {"token": issue_token(user), "user": public_profile(user)}


@auth_router.post("/login")
def login(body: LoginBody) -> dict:
    if not body.username:
        raise HTTPException(
            400,
            "Forge is multi-user now — log in with your profile's username and "
            "password (or create a profile first).",
        )
    with read_session() as db:
        user = db.exec(
            select(User).where(User.username == body.username.strip().lower())
        ).first()
    if user is None or not verify_user_password(user, body.password):
        raise HTTPException(401, "wrong username or password")
    return {"token": issue_token(user), "user": public_profile(user)}


@auth_router.get("/check")
def check(user: User = Depends(current_user)) -> dict:
    return {"ok": True, "user": public_profile(user)}


@users_router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return public_profile(user)


@users_router.patch("/me")
def patch_me(body: ProfilePatch, user: User = Depends(current_user)) -> dict:
    with write_session() as db:
        row = db.get(User, user.id)
        if body.display_name is not None:
            row.display_name = body.display_name.strip() or row.username
        if body.personal_instructions is not None:
            row.personal_instructions = body.personal_instructions[:4000]
        if body.memory_enabled is not None:
            row.memory_enabled = body.memory_enabled
        if body.avatar_color is not None:
            row.avatar_color = body.avatar_color[:16]
        db.add(row)
        db.flush()
        db.refresh(row)
        result = public_profile(row)
    return result


@users_router.post("/me/password")
def change_password(body: PasswordBody, user: User = Depends(current_user)) -> dict:
    if not verify_user_password(user, body.current_password):
        raise HTTPException(401, "current password is wrong")
    if len(body.new_password) < 6:
        raise HTTPException(400, "new password must be at least 6 characters")
    with write_session() as db:
        row = db.get(User, user.id)
        row.password_hash = hash_password(body.new_password)
        db.add(row)
    return {"ok": True}


@users_router.get("")
def list_users(user: User = Depends(current_user)) -> list[dict]:
    """Who else is on this Forge — public profile info only."""
    with read_session() as db:
        rows = db.exec(select(User)).all()
    return [
        {
            "id": row.id,
            "username": row.username,
            "display_name": row.display_name,
            "is_admin": row.is_admin,
            "avatar_color": row.avatar_color,
        }
        for row in sorted(rows, key=lambda r: r.id or 0)
    ]


@users_router.post("/registration")
def toggle_registration(
    body: RegistrationToggle, admin: User = Depends(require_admin)
) -> dict:
    set_setting("allow_registration", "true" if body.allow_registration else "false")
    return {"allow_registration": body.allow_registration}
