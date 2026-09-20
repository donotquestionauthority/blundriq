"""Match games against the active repertoire lines.

A game matches a line position by position: every line position must occur
in the game, in order, so in practice this is a prefix match and a game that
reaches a line's positions by another move order does not match (see
docs/decisions/003). The best match is the longest one; ties keep every line at that length (game_result_lines). A match
is recorded only when the player COMMITTED to the line — followed at least one
of their own moves into it (white: best_ply >= 1, black: best_ply >= 2).
`who_deviated` names the first mover off the line: 'me', 'opponent', or 'none'
when the whole line (or whole game) was followed.

Results are one row per game in game_repertoire_results plus the tied lines in
game_result_lines; unmatched games get player_games.no_repertoire_match = TRUE
so they are not re-tried every hour. Both are replaced, never accumulated, on
a rerun.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, window_cte
from core.constants import PLAYER_ID


@dataclass(frozen=True)
class Line:
    line_id: int
    book_id: int
    chapter_id: int
    color: str
    moves: list[str]
    fen_sequence: list[str]


@dataclass(frozen=True)
class MatchResult:
    chess_game_id: int
    book_id: int
    chapter_id: int
    deviated_at_ply: int
    deviation_by: str
    expected_move: str | None
    played_move: str | None
    deviation_fen: str
    line_ids: list[int]


def player_committed(best_ply: int, player_color: str) -> bool:
    own = (best_ply + 1) // 2 if player_color == "white" else best_ply // 2
    return own >= 1


def subsequence_match_length(game_fens: list[str], rep_fens: list[str]) -> int:
    """How many line positions (after the start) appear in the game, in order."""
    i = 0
    matched = 0
    for rep_fen in rep_fens[1:]:
        while i < len(game_fens):
            if game_fens[i] == rep_fen:
                matched += 1
                i += 1
                break
            i += 1
    return matched


def who_deviated(
    game_moves: list[str], rep_moves: list[str], matched_ply: int, player_color: str
) -> tuple[str, str | None, str | None]:
    if matched_ply >= len(rep_moves) or matched_ply >= len(game_moves):
        return "none", None, None
    mover = "white" if matched_ply % 2 == 0 else "black"
    return ("me" if mover == player_color else "opponent"), rep_moves[matched_ply], game_moves[matched_ply]


def match_game(
    chess_game_id: int, player_color: str, game_moves: list[str], game_fens: list[str], lines: list[Line]
) -> MatchResult | None:
    best_ply = 0
    best: list[Line] = []
    for line in lines:
        if line.color != player_color:
            continue
        ply = subsequence_match_length(game_fens, line.fen_sequence)
        if ply > best_ply:
            best_ply, best = ply, [line]
        elif ply == best_ply and ply > 0:
            best.append(line)
    if not best or not player_committed(best_ply, player_color):
        return None
    ref = best[0]
    by, expected, played = who_deviated(game_moves, ref.moves, best_ply, player_color)
    return MatchResult(
        chess_game_id=chess_game_id,
        book_id=ref.book_id,
        chapter_id=ref.chapter_id,
        deviated_at_ply=best_ply,
        deviation_by=by,
        expected_move=expected,
        played_move=played,
        deviation_fen=game_fens[best_ply],  # len(fens) == len(moves)+1, so always in bounds
        line_ids=[ln.line_id for ln in best],
    )


# --- database -------------------------------------------------------------------


def active_lines(conn: Connection[Any]) -> list[Line]:
    rows = conn.execute(
        """
        SELECT rl.id AS line_id, bk.id AS book_id, ch.id AS chapter_id, bk.color, rl.moves, rl.fen_sequence
        FROM repertoire_lines rl
        JOIN chapters ch ON ch.id = rl.chapter_id
        JOIN books bk ON bk.id = ch.book_id
        WHERE rl.active AND ch.active AND bk.active AND bk.player_id = %s AND rl.fen_sequence IS NOT NULL
        ORDER BY rl.id
        """,
        (PLAYER_ID,),
    ).fetchall()
    return [
        Line(r["line_id"], r["book_id"], r["chapter_id"], r["color"], list(r["moves"]), list(r["fen_sequence"]))
        for r in rows
    ]


def unmatched_games(conn: Connection[Any], window: int) -> list[dict[str, Any]]:
    """In-window analysable games with no result row and no no-match flag."""
    query = cast(
        LiteralString,
        f"""
        WITH {window_cte()}
        SELECT cg.id AS chess_game_id, pg.player_color, cg.moves, cg.fen_sequence
        FROM player_games pg
        JOIN chess_games cg ON cg.id = pg.chess_game_id
        LEFT JOIN game_repertoire_results grr ON grr.chess_game_id = cg.id AND grr.player_id = pg.player_id
        WHERE pg.player_id = %(pid)s
          AND grr.id IS NULL
          AND NOT pg.no_repertoire_match
          AND cg.moves IS NOT NULL AND cg.fen_sequence IS NOT NULL
          AND {analysable_sql("cg")}
          AND cg.id IN (SELECT chess_game_id FROM window_games)
        ORDER BY cg.played_at ASC NULLS LAST
        """,
    )  # literal SQL plus the eligibility fragment; values are bound
    return conn.execute(query, {"pid": PLAYER_ID, "window": window}).fetchall()


def write_results(conn: Connection[Any], results: list[MatchResult], no_match_ids: list[int]) -> None:
    """One transaction: upsert result rows, replace their line rows, flag the rest."""
    with conn.transaction():
        if no_match_ids:
            conn.execute(
                "UPDATE player_games SET no_repertoire_match = TRUE WHERE player_id = %s AND chess_game_id = ANY(%s)",
                (PLAYER_ID, no_match_ids),
            )
        ids: list[int] = []
        for r in results:
            row = conn.execute(
                """
                INSERT INTO game_repertoire_results
                    (chess_game_id, player_id, book_id, chapter_id, deviated_at_ply,
                     deviation_by, expected_move, played_move, deviation_fen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chess_game_id, player_id) DO UPDATE
                SET book_id = EXCLUDED.book_id, chapter_id = EXCLUDED.chapter_id,
                    deviated_at_ply = EXCLUDED.deviated_at_ply, deviation_by = EXCLUDED.deviation_by,
                    expected_move = EXCLUDED.expected_move, played_move = EXCLUDED.played_move,
                    deviation_fen = EXCLUDED.deviation_fen
                RETURNING id
                """,
                (
                    r.chess_game_id,
                    PLAYER_ID,
                    r.book_id,
                    r.chapter_id,
                    r.deviated_at_ply,
                    r.deviation_by,
                    r.expected_move,
                    r.played_move,
                    r.deviation_fen,
                ),
            ).fetchone()
            assert row is not None
            grr_id = int(row["id"])
            ids.append(grr_id)
            conn.execute("DELETE FROM game_result_lines WHERE game_repertoire_result_id = %s", (grr_id,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply)"
                    " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    [(grr_id, line_id, r.deviated_at_ply) for line_id in r.line_ids],
                )


def match_player(conn: Connection[Any], window: int) -> dict[str, int]:
    """The step: match every unmatched in-window game. Returns counts for the run log."""
    lines = active_lines(conn)
    games = unmatched_games(conn, window)
    results: list[MatchResult] = []
    no_match: list[int] = []
    for g in games:
        r = match_game(g["chess_game_id"], g["player_color"], list(g["moves"]), list(g["fen_sequence"]), lines)
        if r is None:
            no_match.append(g["chess_game_id"])
        else:
            results.append(r)
    write_results(conn, results, no_match)
    return {"candidates": len(games), "matched": len(results), "no_match": len(no_match), "lines": len(lines)}
