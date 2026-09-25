"""The repertoire read side, and its one write.

GET /repertoire, GET /repertoire/{book_id}/sections, PATCH /repertoire/{books|chapters|lines}/{id} — the
Repertoire page. GET|PUT|DELETE /repertoire/annotation, GET /repertoire/lines/{id}/annotated — the note
editor and the line walk-through, on every card and in Practice. GET /repertoire/similar and
GET /repertoire/branch-compare — the two compare surfaces (literal paths, registered before `/{book_id}`).

Both compare routes re-serialise their FENs through python-chess before anything reads them: every
stored FEN is python-chess-generated (the en-passant field is a square only when a capture is legal),
while a client walking a live board writes one after any double push, so a raw client string would
miss stored occurrences on that field alone.
"""

from __future__ import annotations

from typing import Any, Literal

import chess
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api import auth
from core import db, settings
from core.repertoire import annotations, books, branch_compare, neighbourhood

router = APIRouter(prefix="/repertoire", tags=["repertoire"], dependencies=[auth.Authed])


def _board_or_400(fen: str) -> chess.Board:
    try:
        return chess.Board(fen)
    except ValueError as exc:
        raise HTTPException(400, "Not a position") from exc


@router.get("/similar")
def similar(
    fen: str = Query(max_length=100),
    move: str | None = Query(None, max_length=12),
    max_distance: int | None = Query(None, ge=1, le=neighbourhood.MAX_DISTANCE_CEILING),
) -> dict[str, Any]:
    """The near neighbourhood of `fen` in the active repertoire. `move`, when given, must be
    legal in `fen` and only flags `is_queried_move` on matching groups. `max_distance` may narrow
    the setting, never widen it (400, not clamped)."""
    board = _board_or_400(fen)
    canonical_move: str | None = None
    if move and move.strip():
        try:
            canonical_move = board.san(board.parse_san(move.strip()))
        except ValueError as exc:
            raise HTTPException(400, "move is not legal in fen") from exc
    with db.transaction() as conn:
        s = settings.load(conn)
        distance = s.similar_max_distance
        if max_distance is not None:
            if max_distance > distance:
                raise HTTPException(400, "max_distance is above the setting")
            distance = max_distance
        try:
            return neighbourhood.similar_positions(
                conn, board.fen(), canonical_move, max_distance=distance, max_positions=s.similar_max_positions
            )
        except ValueError as exc:
            raise HTTPException(400, "Not a position") from exc


@router.get("/branch-compare")
def compare_branches(fen: str = Query(max_length=100), pre_fen: str = Query(max_length=100)) -> dict[str, Any]:
    """Every opponent option at `pre_fen`, the parent of the puzzle position `fen`. The server
    validates the pair rather than trusting a client move: opposite sides to move, and exactly
    one legal move from `pre_fen` reaches `fen`'s board (promotion and en passant come free)."""
    fen_board = _board_or_400(fen)
    pre_board = _board_or_400(pre_fen)
    if fen_board.turn == pre_board.turn:
        raise HTTPException(400, "pre_fen must be the opponent-to-move parent of fen")
    target = annotations.normalize_fen(fen_board.fen())
    matches: list[chess.Move] = []
    for candidate in pre_board.legal_moves:
        pre_board.push(candidate)
        try:
            if annotations.normalize_fen(pre_board.fen()) == target:
                matches.append(candidate)
        finally:
            pre_board.pop()
    if len(matches) != 1:
        raise HTTPException(400, "fen is not reachable from pre_fen by exactly one legal move")
    arriving = neighbourhood.parse_arriving(pre_board.fen(), pre_board.san(matches[0]))
    assert arriving is not None
    book_color = "white" if fen_board.turn == chess.WHITE else "black"
    with db.transaction() as conn:
        max_boards = settings.load(conn).branch_compare_max_boards
        return branch_compare.branch_compare(
            conn, fen_board.fen(), pre_board.fen(), arriving=arriving, book_color=book_color, max_boards=max_boards
        )


@router.get("")
def list_books() -> dict[str, Any]:
    with db.transaction() as conn:
        return {"books": books.books(conn)}


class ActiveBody(BaseModel):
    active: bool


@router.patch("/{kind}/{id}")
def set_active(kind: Literal["books", "chapters", "lines"], id: int, body: ActiveBody) -> dict[str, Any]:
    with db.transaction() as conn:
        window = settings.load(conn).analysis_game_limit
        result = books.set_active(conn, kind, id, body.active, window)
    if result is None:
        raise HTTPException(404, "Not found")
    return {"detail": "updated", "rematch": result}


def _fen_norm_or_400(fen: str) -> str:
    try:
        return annotations.normalize_fen(fen)
    except ValueError as exc:
        raise HTTPException(400, "Not a position") from exc


@router.get("/annotation")
def get_annotation(fen: str = Query(max_length=100)) -> dict[str, Any] | None:
    _fen_norm_or_400(fen)
    with db.transaction() as conn:
        note = annotations.get(conn, fen)
    if note is None:
        return None
    return {**note, "updated_at": note["updated_at"].isoformat() if note.get("updated_at") else None}


class NoteBody(BaseModel):
    fen: str = Field(max_length=100)
    text: str = Field(max_length=8000)
    line_id: int | None = None


@router.put("/annotation")
def put_annotation(body: NoteBody) -> dict[str, Any]:
    _fen_norm_or_400(body.fen)
    with db.transaction() as conn:
        result = annotations.upsert(conn, [{"fen": body.fen, "text": body.text}], body.line_id, source="manual")
        if result["skipped_no_line"]:
            raise HTTPException(404, "Line not found for this note")
        if result["skipped_off_spine"]:
            raise HTTPException(400, "This position is not part of the selected line")
        if result["blank"]:
            raise HTTPException(400, "Note is empty after cleanup")
        if body.line_id is not None:
            return {"detail": "Note saved", "line_id": body.line_id}
        note = annotations.get(conn, body.fen)
    assert note is not None
    return {**note, "updated_at": note["updated_at"].isoformat() if note.get("updated_at") else None}


@router.delete("/annotation")
def delete_annotation(fen: str = Query(max_length=100), line_id: int | None = Query(None)) -> dict[str, str]:
    _fen_norm_or_400(fen)
    with db.transaction() as conn:
        removed = annotations.delete(conn, fen, line_id)
    if not removed:
        raise HTTPException(404, "No note for this position")
    return {"detail": "Note deleted"}


@router.get("/lines/{line_id}/annotated")
def line_annotated(line_id: int) -> dict[str, Any]:
    with db.transaction() as conn:
        line = annotations.line_with_notes(conn, line_id)
    if line is None:
        raise HTTPException(404, "Line not found")
    return line


@router.get("/{book_id}/sections")
def book_sections(book_id: int) -> dict[str, Any]:
    with db.transaction() as conn:
        result = books.sections(conn, book_id)
    if result is None:
        raise HTTPException(404, "Book not found")
    return {"sections": result}
