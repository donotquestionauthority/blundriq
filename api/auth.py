"""Single-user login. One password, one signed httpOnly cookie, 30 days.

Replaces the old JWT + refresh-token + RLS design. The password is checked
against PASSWORD_HASH (bcrypt, from core/secrets.py); the session cookie is a
timestamp signed with SESSION_SECRET (itsdangerous), so the server keeps no
session table. Logging out clears the cookie; rotating SESSION_SECRET logs
every device out at once.
"""

from __future__ import annotations

from typing import Annotated

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Response, status
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from core import secrets

COOKIE_NAME = "bq_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600


def _signer() -> TimestampSigner:
    return TimestampSigner(secrets.api().session_secret, salt="session")


def verify_password(password: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), secrets.api().password_hash.encode("utf-8"))


def issue_session(response: Response, *, secure: bool) -> None:
    token = _signer().sign(b"1").decode("utf-8")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def require_session(bq_session: Annotated[str | None, Cookie()] = None) -> None:
    """FastAPI dependency: 401 unless the cookie is present, validly signed, and unexpired."""
    if not bq_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    try:
        _signer().unsign(bq_session, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session invalid or expired") from exc


Authed = Depends(require_session)
