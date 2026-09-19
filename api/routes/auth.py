"""POST /login, POST /logout, GET /me."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from api import auth

router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
    if not auth.verify_password(body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong password")
    # `secure` follows the request scheme so the cookie works on http://localhost in dev
    # and is Secure on https in production.
    auth.issue_session(response, secure=request.url.scheme == "https")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    auth.clear_session(response)
    return {"ok": True}


@router.get("/me", dependencies=[auth.Authed])
def me() -> dict[str, bool]:
    return {"authenticated": True}
