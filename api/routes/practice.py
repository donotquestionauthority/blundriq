"""The Practice page's API: the play queue, attempts, skips and the deep link.

GET  /practice/puzzles              the queue (srs=due), the browse list (all) or the trophies (retired)
POST /practice/puzzles/{id}/attempt grade and record one attempt
POST /practice/skip                 defer a served item
GET  /practice/puzzles/{id}         the immutable solver payload for `?puzzle=<id>`
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api import auth
from core import db, settings
from core.constants import CC0_SERVE_THEMES
from core.puzzles import attempts, serve, srs, visibility

router = APIRouter(prefix="/practice", tags=["practice"], dependencies=[auth.Authed])

Ptype = Literal["all", "motif", "repertoire", "blunder", "custom"]


@router.get("/puzzles")
def get_puzzles(
    srs_view: Literal["due", "all", "retired"] = Query("due", alias="srs"),
    ptype: Ptype = Query("all"),
    subtype: str | None = Query(None, max_length=100),
    last_n_games: int = Query(0, ge=0, le=20000),
) -> dict[str, Any]:
    with db.transaction() as conn:
        config = settings.load(conn)
        batch_id = scope = threshold = None
        if srs_view == "due":
            served = serve.play_batch(conn, config, last_n_games=last_n_games, ptype=ptype, subtype=subtype)
            rows, batch_id, scope, threshold = served.rows, served.batch_id, served.scope, served.mint_ahead
        elif srs_view == "retired":
            rows = serve.retired(conn)
        else:
            rows = serve.browse(conn, config, last_n_games=last_n_games)
        if ptype != "all" and srs_view != "retired":
            rows = [r for r in rows if serve.matches_type(r, ptype) and serve.matches_subtype(r, ptype, subtype)]
        links = attempts.game_links(conn, [str(r["fen"]) for r in rows])
        correct = attempts.correct_game_links(conn, rows)
        mastered = srs.mastered_count(conn)
    for r in rows:
        r["srs"] = srs.serialise(r.get("srs"))
        r["game_links"] = links.get(str(r["fen"]), [])
        r["correct_game_links"] = correct.get(int(r["id"]), [])
    return {
        "puzzles": rows,
        "total": len(rows),
        "mastered_count": mastered,
        "advance_threshold": config.srs_advance_threshold,
        "batch_id": batch_id,
        "scope": scope,
        "mint_ahead_threshold": threshold,
        "served_themes": list(CC0_SERVE_THEMES),
    }


class AttemptBody(BaseModel):
    solved: bool
    moves_played: str | None = Field(None, max_length=2000)
    attempt_id: UUID | None = None
    session_id: UUID | None = None


@router.post("/puzzles/{puzzle_id}/attempt")
def post_attempt(puzzle_id: int, body: AttemptBody) -> dict[str, Any]:
    with db.transaction() as conn:
        config = settings.load(conn)
        try:
            return attempts.record(
                conn,
                puzzle_id,
                config,
                claimed=body.solved,
                moves_played=body.moves_played,
                attempt_id=str(body.attempt_id) if body.attempt_id else None,
                session_id=str(body.session_id) if body.session_id else None,
            )
        except attempts.NotAttemptable as exc:
            raise HTTPException(404, "puzzle not found") from exc


class SkipBody(BaseModel):
    ptype: Ptype = "all"
    subtype: str | None = Field(None, max_length=100)
    batch_id: int
    puzzle_id: int


@router.post("/skip")
def post_skip(body: SkipBody) -> dict[str, str]:
    with db.transaction() as conn:
        outcome = serve.skip(
            conn, serve.scope_of(body.ptype, body.subtype), body.batch_id, body.puzzle_id, settings.load(conn)
        )
    if outcome == "STATE_MISS":
        raise HTTPException(409, {"status": outcome})
    return {"status": outcome}


@router.get("/puzzles/{puzzle_id}")
def get_puzzle(puzzle_id: int) -> dict[str, Any]:
    with db.transaction() as conn:
        row = visibility.visible_by_id(conn, puzzle_id, lookahead_plies=serve.lookahead_plies(settings.load(conn)))
    if row is None:
        raise HTTPException(404, "puzzle not found")
    return visibility.playable(row)
