"""GET /home — the Home page, which also records the visit."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api import auth
from core import db, home, settings

router = APIRouter(prefix="/home", tags=["home"], dependencies=[auth.Authed])


@router.get("")
def show() -> dict[str, Any]:
    with db.transaction() as conn:
        return home.page(conn, settings.load(conn))
