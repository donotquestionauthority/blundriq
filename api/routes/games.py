"""GET /games, /games/filters, /games/export.csv, /games/opponents — the Games page; and the
per-game Review: GET /games/{id}/review, POST /games/{id}/reviewed, POST /games/{id}/learn-commit,
POST /games/{id}/ask (a question about the board at a ply; core/ai.py `ask_review`).

The three per-game routes share one gate, run first: 404 when there is no such game of the
player's, 422 `not_analysable` when it is Chess960 — before anything else about it is read.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import chess
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from api import auth
from api.routes.explore import AlternativeBody
from core import ai, db, games
from core.notify import error_label
from core.repertoire import read as repertoire
from core.review import learn

router = APIRouter(prefix="/games", tags=["games"], dependencies=[auth.Authed])


def _filters(
    since_days: int | None = Query(None, ge=1, le=3650),
    last_n_games: int = Query(0, ge=0, le=20000),
    color: str | None = Query(None, pattern="^(white|black)$"),
    result: str | None = Query(None, pattern="^(win|loss|draw)$"),
    platform: str | None = Query(None, pattern="^(chesscom|lichess)$"),
    variant: str | None = Query(None, pattern="^(standard|chess960)$"),
    book: str | None = Query(None, max_length=200),
    chapter: str | None = Query(None, max_length=200),
    deviation: str | None = Query(None, pattern="^(me|opponent|none|no_match)$"),
    opponent: str | None = Query(None, max_length=50),
) -> games.GameFilters:
    return games.GameFilters(
        since_days=since_days,
        last_n_games=last_n_games,
        color=color,
        result=result,
        platform=platform,
        variant=variant,
        book=book,
        chapter=chapter,
        deviation=deviation,
        opponent=opponent or None,
    )


def _serialise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        g = dict(r)
        if g.get("played_at") is not None:
            g["played_at"] = g["played_at"].isoformat()
        out.append(g)
    return out


@router.get("")
def list_games(page: int = Query(1, ge=1), f: games.GameFilters = Depends(_filters)) -> dict[str, Any]:
    with db.transaction() as conn:
        rows = games.list_games(conn, f, page)
        summary = games.summary(conn, f)
    return {"games": _serialise(rows), "summary": summary, "page": page, "page_size": games.PAGE_SIZE}


@router.get("/filters")
def filters() -> dict[str, Any]:
    with db.transaction() as conn:
        return games.filter_values(conn)


@router.get("/opponents")
def opponents(q: str = Query(..., min_length=1, max_length=50)) -> dict[str, list[str]]:
    with db.transaction() as conn:
        return {"opponents": games.search_opponents(conn, q)}


@router.get("/export.csv")
def export(f: games.GameFilters = Depends(_filters)) -> Response:
    with db.transaction() as conn:
        rows = games.export_games(conn, f)
    return Response(
        games.to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=blundriq_games.csv"},
    )


# --- the per-game review ------------------------------------------------------------------------

_log = logging.getLogger(__name__)
MOVE_MAX_LEN = 32


def _gate(exc: Exception) -> HTTPException:
    if isinstance(exc, games.GameNotFound):
        return HTTPException(404, "game not found")
    return HTTPException(422, "not_analysable")


def _projection(conn: Any, color: str, fen_sequence: Any) -> dict[str, Any] | None:
    """Non-fatal: a failure is `null` on the wire (the page shows no panel) and a class chain
    in the log, never the game's failure."""
    try:
        return repertoire.project_game(conn, color, fen_sequence)
    except Exception as exc:  # noqa: BLE001 — the projection is decoration on the review
        _log.warning("repertoire projection failed: %s", error_label(exc))
        conn.rollback()
        return None


@router.get("/{game_id}/review")
def game_review(game_id: int) -> dict[str, Any]:
    """`{game, blunders, repertoire}`; `game.out_of_window` when the moves are no longer stored."""
    with db.transaction() as conn:
        try:
            g = games.game_for_review(conn, game_id)
        except (games.GameNotFound, games.NotAnalysable) as exc:
            raise _gate(exc) from exc
        blunders = games.blunders_for_game(conn, game_id)
        projection = _projection(conn, g["player_color"], g["fen_sequence"])
    for k in ("played_at", "reviewed_at"):
        if g.get(k) is not None:
            g[k] = g[k].isoformat()
    return {"game": g, "blunders": blunders, "repertoire": projection}


@router.post("/{game_id}/reviewed", status_code=204)
def game_reviewed(game_id: int) -> Response:
    with db.transaction() as conn:
        try:
            games.mark_reviewed(conn, game_id)
        except (games.GameNotFound, games.NotAnalysable) as exc:
            raise _gate(exc) from exc
    return Response(status_code=204)


class LearnCommitBody(BaseModel):
    attempt_id: uuid.UUID
    ply: int = Field(ge=0)
    committed_move: str
    elapsed_ms: int | None = Field(default=None, ge=0)


def _verdict(verdict: int, row: dict[str, Any]) -> JSONResponse:
    if verdict == 200:
        return JSONResponse(status_code=200, content={"id": row["id"], "created": False})
    if verdict == 409:
        raise HTTPException(409, {"error": "attempt_conflict", "id": row["id"]})
    raise HTTPException(410, "position_unavailable")


def _eligibility(g: dict[str, Any], ply: int) -> str | None:
    """Why `ply` cannot be committed at, or None. Turn parity is read from the FEN."""
    moves, fens = g["moves"], g["fen_sequence"]
    if not isinstance(moves, list) or not isinstance(fens, list):
        return "no_position_data"
    if len(fens) != len(moves) + 1:
        return "length_drift"
    if not 0 <= ply < len(moves):
        return "ply_out_of_range"
    parts = str(fens[ply]).split(" ")
    if len(parts) < 2 or parts[1] not in ("w", "b"):
        return "unparseable_position"
    if ("white" if parts[1] == "w" else "black") != g["player_color"]:
        return "not_your_turn"
    return None


@router.post("/{game_id}/learn-commit", status_code=201)
def learn_commit(game_id: int, body: LearnCommitBody) -> Any:
    """Record one Learn rep. The steps run in this order, and the order is the contract:
    the gate (404 / 422) → the attempt lookup, and an existing row is adjudicated without the
    board (409 / 200, or 410 only when the board is needed and the moves are gone) → the
    position (410 when the moves are no longer stored) → the move's bytes (422) → the ply's
    eligibility (422 with the reason) → the canonical move (422 `illegal_move`; a null-move
    spelling never reaches a row) → the snapshots → the insert; a lost `ON CONFLICT` race
    re-enters the adjudication with the winner's row."""
    attempt_id = str(body.attempt_id)
    submitted = body.committed_move
    with db.transaction() as conn:
        try:
            g = games.game_for_review(conn, game_id)
        except (games.GameNotFound, games.NotAnalysable) as exc:
            raise _gate(exc) from exc

        existing = learn.commit_by_attempt(conn, attempt_id)
        if existing is not None:
            # The board is consulted only for a spelling matching neither stored form, and
            # only when the moves are still stored.
            fens = g["fen_sequence"]
            board = isinstance(fens, list) and 0 <= body.ply < len(fens)
            needs_board = board and submitted not in (existing["committed_move"], existing["submitted_move"])
            canonical = learn.canonical_san(str(fens[body.ply]), submitted) if needs_board else None
            return _verdict(learn.adjudicate(existing, game_id, body.ply, submitted, canonical, board=board), existing)

        if g["moves"] is None or g["fen_sequence"] is None:
            raise HTTPException(410, "position_unavailable")
        if not submitted:
            raise HTTPException(422, "committed_move_required")
        if len(submitted) > MOVE_MAX_LEN:
            raise HTTPException(422, "committed_move_too_long")
        if not submitted.isprintable():
            raise HTTPException(422, "committed_move_not_printable")
        reason = _eligibility(g, body.ply)
        if reason is not None:
            raise HTTPException(422, reason)
        fen = str(g["fen_sequence"][body.ply])
        canonical_move = learn.canonical_san(fen, submitted)
        if canonical_move is None:
            raise HTTPException(422, "illegal_move")

        engine_move: str | None = None
        analysis = g["ply_analysis"]
        if isinstance(analysis, list) and body.ply < len(analysis) and isinstance(analysis[body.ply], dict):
            bm = analysis[body.ply].get("best_move")
            engine_move = str(bm) if bm else None
        book_move: str | None = None
        entry = _projection(conn, g["player_color"], [fen])
        if entry is not None:
            book_move = entry["by_ply"][0].get("book_move")
        result = learn.insert_commit(
            conn,
            game_id,
            body.ply,
            attempt_id,
            committed=canonical_move,
            submitted=submitted,
            game_move=str(g["moves"][body.ply]),
            engine_move=engine_move,
            book_move=book_move,
            in_check=chess.Board(fen).is_check(),
            ply_classified=learn.ply_classified(conn, game_id, body.ply),
            elapsed_ms=body.elapsed_ms,
        )
        if not result["created"]:
            row = result["row"]
            return _verdict(learn.adjudicate(row, game_id, body.ply, submitted, canonical_move, board=True), row)
        return {"id": result["row"]["id"], "created": True}


class AskBody(BaseModel):
    ply: int = Field(ge=0, le=2000)
    # Trimmed and limited to ai.QUESTION_MAX_CHARS in core; this bound only caps the body.
    question: str = Field(default="", max_length=4 * ai.QUESTION_MAX_CHARS)
    alternative: AlternativeBody | None = None
    dry_run: bool = False


@router.post("/{game_id}/ask")
def game_ask(game_id: int, body: AskBody) -> dict[str, Any]:
    alternative = body.alternative.model_dump() if body.alternative else None
    try:
        return ai.ask_review(db.transaction, game_id, body.ply, body.question, alternative, dry_run=body.dry_run)
    except ai.ExplainError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
