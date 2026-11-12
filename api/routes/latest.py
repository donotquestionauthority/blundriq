"""GET /review/latest?source=chesscom|lichess — the player's most recent finished game on one
site, fetched from the site and parsed by the importer's rules, with what the repertoire says
at every board. Nothing is stored (core/ingest/latest.py says why).

A refusal is `{"detail": {"code", "message"}}`: 422 `no_handle`, 404 `no_games`, 422
`chess960`, 422 `not_reviewable`, 502 `fetch_failed` (the site did not answer, or said no).
Logs carry the refusal code or the failure's class chain, never a handle, a URL or a game.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from api import auth
from api.routes.games import repertoire_projection
from core import db
from core.ingest import latest
from core.ingest.records import FetchError
from core.notify import error_label

router = APIRouter(prefix="/review", tags=["review"], dependencies=[auth.Authed])
_log = logging.getLogger(__name__)

RATE_LIMITED = "The site is rate limiting requests; try again in a minute."
FETCH_FAILED = "The site did not return the game; try again shortly."


def _refuse(status: int, code: str, message: str) -> HTTPException:
    _log.info("review latest refused: %s", code)
    return HTTPException(status, {"code": code, "message": message})


@router.get("/latest")
def review_latest(source: Literal["chesscom", "lichess"] = Query(...)) -> dict[str, Any]:
    try:
        with db.transaction() as conn:
            handle = latest.handle_for(conn, source)
        with latest.client() as http:
            rec = latest.latest_game(source, handle, http)
    except latest.LatestRefused as exc:
        raise _refuse(exc.status, exc.code, exc.detail) from exc
    except FetchError as exc:
        _log.warning("review latest fetch failed: %s", error_label(exc))
        message = RATE_LIMITED if "429" in str(exc) else FETCH_FAILED
        raise HTTPException(502, {"code": "fetch_failed", "message": message}) from exc
    with db.transaction() as conn:
        repertoire = repertoire_projection(conn, rec.player_color, rec.fen_sequence)
    return {
        "game": {
            "source": rec.platform,
            "url": rec.url,
            "played_at": rec.played_at.isoformat() if rec.played_at is not None else None,
            "time_control": rec.time_control,
            "time_class": rec.time_class,
            "opening_name": rec.opening_name or None,
            "opening_eco": rec.opening_eco or None,
            "termination": rec.termination,
            "player_color": rec.player_color,
            "opponent_username": rec.opponent_username,
            "opponent_rating": rec.opponent_rating,
            "player_rating": rec.player_rating,
            "result": rec.result,
            "moves": rec.moves,
            "fen_sequence": rec.fen_sequence,
        },
        "repertoire": repertoire,
    }
