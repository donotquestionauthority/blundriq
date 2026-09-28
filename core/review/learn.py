"""Learn-mode commits: one `learn_commits` row per rep the player committed on the per-game
Review page.

The row's identity is `(player_id, attempt_id)` → (game, ply, the canonical committed move),
minted by the client and immutable; `game_move`, `engine_move`, `book_move`, `in_check` and
`ply_classified` are a snapshot of what stood when the rep was first recorded, never refreshed
(a re-analysis or a repertoire edit afterwards changes nothing here), and there is no UPDATE
path at all. Two spellings of the move are kept on purpose: `committed_move` is the canonical
SAN, the chess fact; `submitted_move` is the request's bytes verbatim, so a retry that re-sends
them is recognised without a board (`adjudicate`, rule 2). The route in `api/routes/games.py`
owns the order of the checks; this module owns the SQL and the pure adjudication.
"""

from __future__ import annotations

from typing import Any, Literal

import chess
from psycopg import Connection

from core.constants import PLAYER_ID

Verdict = Literal[200, 409, 410]


def canonical_san(fen: str, submitted: str) -> str | None:
    """The one canonical SAN of `submitted` on the board `fen`, or None for anything that is
    not a legal move there. python-chess parses `--`, `Z0`, `0000` and `@@@@` as the null move
    without complaint, so the parse result is accepted only when it is truthy AND legal; a
    check or mate suffix is corrected, not merely stripped (`Ra8` → `Ra8#`)."""
    try:
        board = chess.Board(fen)
        move = board.parse_san(submitted)
    except Exception:
        return None
    if not move or move not in board.legal_moves:
        return None
    return board.san(move)


def adjudicate(
    row: dict[str, Any], game_id: int, ply: int, submitted: str, canonical: str | None, *, board: bool
) -> Verdict:
    """Whether a request repeats the action `row` already records. A different game or ply is
    409 (the client reused an attempt id — never evidence the player changed their move);
    bytes equal to either stored spelling are 200 with no board; otherwise the board decides,
    and `board` says whether there was one: without it 410 (nothing to compare, nothing
    written), with it `canonical` — the request's move on that board, None when it is not a
    legal move there — is 200 when it equals the stored move and 409 otherwise."""
    if int(row["chess_game_id"]) != game_id or int(row["ply"]) != ply:
        return 409
    if submitted in (row["committed_move"], row["submitted_move"]):
        return 200
    if not board:
        return 410
    return 200 if canonical is not None and canonical == row["committed_move"] else 409


def commit_by_attempt(conn: Connection[Any], attempt_id: str) -> dict[str, Any] | None:
    """The row `attempt_id` stands for, or None. Board-free: a retry resolves from here for as
    long as the row exists."""
    row = conn.execute(
        "SELECT id, chess_game_id, ply, attempt_id::text AS attempt_id, committed_move, submitted_move, game_move,"
        " engine_move, book_move, in_check, ply_classified, elapsed_ms, created_at"
        " FROM learn_commits WHERE player_id = %s AND attempt_id = %s::uuid",
        (PLAYER_ID, attempt_id),
    ).fetchone()
    return dict(row) if row else None


def ply_classified(conn: Connection[Any], game_id: int, ply: int) -> bool:
    """Whether a `blunders` row with a classification stands at this ply — what the
    "inaccuracies+ only" filter selects on, recorded as the durable fact it was at commit."""
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM blunders WHERE player_id = %s AND chess_game_id = %s AND ply = %s"
        " AND classification IS NOT NULL) AS present",
        (PLAYER_ID, game_id, ply),
    ).fetchone()
    return bool(row and row["present"])


def insert_commit(
    conn: Connection[Any],
    game_id: int,
    ply: int,
    attempt_id: str,
    *,
    committed: str,
    submitted: str,
    game_move: str | None,
    engine_move: str | None,
    book_move: str | None,
    in_check: bool,
    ply_classified: bool,
    elapsed_ms: int | None,
) -> dict[str, Any]:
    """`INSERT … ON CONFLICT (player_id, attempt_id) DO NOTHING`, then the read-back:
    `{"row", "created"}`. `created` says only that no row stood before — a lost race between
    two first requests under one attempt id is `created: False` with the winner's row, which
    the caller adjudicates like any repeat. `elapsed_ms` is None exactly when the rep was
    untimed."""
    inserted = conn.execute(
        "INSERT INTO learn_commits (player_id, chess_game_id, ply, attempt_id, committed_move, submitted_move,"
        " game_move, engine_move, book_move, in_check, ply_classified, elapsed_ms)"
        " VALUES (%s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (player_id, attempt_id) DO NOTHING RETURNING id",
        (
            PLAYER_ID,
            game_id,
            ply,
            attempt_id,
            committed,
            submitted,
            game_move,
            engine_move,
            book_move,
            in_check,
            ply_classified,
            elapsed_ms,
        ),
    ).fetchone()
    row = commit_by_attempt(conn, attempt_id)
    assert row is not None  # either this insert's or the winner's
    return {"row": row, "created": inserted is not None}
