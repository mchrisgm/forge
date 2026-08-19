from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import issue_token, require_auth, verify_password

router = APIRouter(prefix="/auth")


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody) -> dict:
    if not verify_password(body.password):
        raise HTTPException(status_code=401, detail="wrong password")
    return {"token": issue_token()}


@router.get("/check", dependencies=[Depends(require_auth)])
def check() -> dict:
    return {"ok": True}
