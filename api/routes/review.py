"""GET /review, GET /review/positions/{colour}/{key} (and /games), GET /review/habits/{habit_id}.

Every route reads only and takes the same two filters. A bad parameter is 422: an opening key
that is not one of the page's options says `unknown opening key` (the client's stale-key
recovery reads it), a position key must be a signed 64-bit decimal, a habit id must have a
habit's shape. A position no counted game reaches is 404.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from api import auth
from core import db, settings
from core.review import habits, mistakes, position, positions, read
from core.review.filters import OPENING_ALL, ReviewParamError

router = APIRouter(prefix="/review", tags=["review"], dependencies=[auth.Authed])

TimeClass = Literal["focus", "all"]
Colour = Literal["white", "black"]
_BIGINT = (-(2**63), 2**63 - 1)


@router.get("")
def review_page(
    time_class: TimeClass = Query("focus"),
    opening: str = Query(OPENING_ALL, max_length=200),
) -> dict[str, Any]:
    with db.transaction() as conn:
        try:
            return read.page(conn, settings.load(conn), time_class=time_class, opening=opening)
        except ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/positions/{colour}/{key}")
def position_page(
    colour: Colour,
    key: str = Path(pattern=r"^-?[0-9]{1,19}$"),
    time_class: TimeClass = Query("focus"),
    opening: str = Query(OPENING_ALL, max_length=200),
    page: int = Query(1, ge=1, le=10000),
) -> dict[str, Any]:
    board = int(key)
    if not _BIGINT[0] <= board <= _BIGINT[1]:
        raise HTTPException(422, "position key out of range")
    with db.transaction() as conn:
        config = settings.load(conn)
        try:
            parsed, _ = read.checked_opening(conn, config, time_class, opening)
        except ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc
        result = position.position_page(conn, positions.Scope(config, time_class, parsed), colour, board, page)
    if result is None:
        raise HTTPException(404, "no counted game reaches this position")
    return result


@router.get("/positions/{colour}/{key}/games")
def position_move_games(
    colour: Colour,
    key: str = Path(pattern=r"^-?[0-9]{1,19}$"),
    time_class: TimeClass = Query("focus"),
    opening: str = Query(OPENING_ALL, max_length=200),
    move: str | None = Query(None, pattern=r"^[A-Za-z0-9+#=\-]{2,10}$"),
    page: int = Query(1, ge=1, le=10000),
) -> dict[str, Any]:
    """Rob's games from the board: every visit with `move`, or with no `move` every costly one."""
    board = int(key)
    if not _BIGINT[0] <= board <= _BIGINT[1]:
        raise HTTPException(422, "position key out of range")
    with db.transaction() as conn:
        config = settings.load(conn)
        try:
            parsed, _ = read.checked_opening(conn, config, time_class, opening)
        except ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc
        result = mistakes.move_games(
            conn, positions.Scope(config, time_class, parsed), colour, board, move=move, page=page
        )
    if result is None:
        raise HTTPException(404, "you never moved from this position under this filter")
    return result


@router.get("/habits/{habit_id}")
def habit_games(
    habit_id: str = Path(max_length=60),
    time_class: TimeClass = Query("focus"),
    opening: str = Query(OPENING_ALL, max_length=200),
    page: int = Query(1, ge=1, le=10000),
) -> dict[str, Any]:
    with db.transaction() as conn:
        config = settings.load(conn)
        try:
            parsed, _ = read.checked_opening(conn, config, time_class, opening)
            return habits.habit_games(conn, config, time_class, parsed, habit_id, page)
        except ReviewParamError as exc:
            raise HTTPException(422, str(exc)) from exc
