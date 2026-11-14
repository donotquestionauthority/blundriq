"""GET /explorer/position and GET /explorer/lines — the Opening Explorer (core/explorer.py).

Both read only and take the board as a path of SAN moves from the start, comma-separated: at
most EXPLORER_MAX_PLIES moves of at most EXPLORER_SAN_MAX_CHARS characters each. A path over
either bound, or one that is not legal from the start, is 422; nothing is truncated here.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from api import auth
from core import db, explorer
from core.constants import (
    EXPLORER_LINES_MAX_OFFSET,
    EXPLORER_LINES_PAGE,
    EXPLORER_MAX_PLIES,
    EXPLORER_QUERY_MAX_CHARS,
    EXPLORER_SAN_MAX_CHARS,
)

router = APIRouter(prefix="/explorer", tags=["explorer"], dependencies=[auth.Authed])

Colour = Literal["white", "black"]
TimeClass = Literal["focus", "all"]


def _tokens(moves: str) -> list[str]:
    tokens = moves.split(",") if moves else []
    if len(tokens) > EXPLORER_MAX_PLIES:
        raise HTTPException(422, f"a path has at most {EXPLORER_MAX_PLIES} moves")
    if any(not t or len(t) > EXPLORER_SAN_MAX_CHARS for t in tokens):
        raise HTTPException(422, f"a move is 1 to {EXPLORER_SAN_MAX_CHARS} characters")
    return tokens


@router.get("/position")
def position(
    colour: Colour = Query("white"),
    moves: str = Query("", max_length=EXPLORER_QUERY_MAX_CHARS),
    time_class: TimeClass = Query("focus"),
) -> dict[str, Any]:
    tokens = _tokens(moves)
    with db.transaction() as conn:
        scope = explorer.load_scope(conn, time_class)
        try:
            return explorer.position(conn, scope, colour, tokens)
        except explorer.ExplorerPathError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/lines")
def lines(
    colour: Colour = Query("white"),
    moves: str = Query("", max_length=EXPLORER_QUERY_MAX_CHARS),
    offset: int = Query(0, ge=0, le=EXPLORER_LINES_MAX_OFFSET),
) -> dict[str, Any]:
    if offset % EXPLORER_LINES_PAGE:
        raise HTTPException(422, f"offset is a multiple of {EXPLORER_LINES_PAGE}")
    tokens = _tokens(moves)
    with db.transaction() as conn:
        try:
            return explorer.lines(conn, colour, tokens, offset=offset, limit=EXPLORER_LINES_PAGE)
        except explorer.ExplorerPathError as exc:
            raise HTTPException(422, str(exc)) from exc
