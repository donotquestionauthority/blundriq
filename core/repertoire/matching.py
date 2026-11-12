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

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, window_cte
from core.constants import LOCK_REPERTOIRE, PLAYER_ID


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


# The lines a game is matched against: the whole active chain, with a FEN sequence to match on.
_ACTIVE_LINES = """
    FROM repertoire_lines rl
    JOIN chapters ch ON ch.id = rl.chapter_id
    JOIN books bk ON bk.id = ch.book_id
    WHERE rl.active AND ch.active AND bk.active AND bk.player_id = %s AND rl.fen_sequence IS NOT NULL
"""


def active_lines(conn: Connection[Any], colours: Iterable[str] | None = None) -> list[Line]:
    """Every active line, or only those of `colours` — exact for matching, since `match_game`
    compares a game only with lines of the player's colour in it."""
    query = (
        "SELECT rl.id AS line_id, bk.id AS book_id, ch.id AS chapter_id, bk.color, rl.moves, rl.fen_sequence"
        f" {_ACTIVE_LINES}"
    )
    params: tuple[Any, ...] = (PLAYER_ID,)
    if colours is not None:
        query += " AND bk.color = ANY(%s)"
        params = (PLAYER_ID, sorted(set(colours)))
    rows = conn.execute(query + " ORDER BY rl.id", params).fetchall()
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
    """One transaction: upsert result rows, replace their line rows, flag the rest. A fixed
    number of statements whatever the number of games; each game appears at most once."""
    if len({r.chess_game_id for r in results}) != len(results):
        raise ValueError("a game appears twice in one write")
    with conn.transaction():
        if no_match_ids:
            conn.execute(
                "UPDATE player_games SET no_repertoire_match = TRUE WHERE player_id = %s AND chess_game_id = ANY(%s)",
                (PLAYER_ID, no_match_ids),
            )
        if not results:
            return
        rows = conn.execute(
            """
            INSERT INTO game_repertoire_results
                (chess_game_id, player_id, book_id, chapter_id, deviated_at_ply,
                 deviation_by, expected_move, played_move, deviation_fen)
            SELECT u.game, %(pid)s, u.book, u.chapter, u.ply, u.by, u.expected, u.played, u.fen
            FROM unnest(%(game)s::bigint[], %(book)s::int[], %(chapter)s::int[], %(ply)s::int[],
                        %(by)s::text[], %(expected)s::text[], %(played)s::text[], %(fen)s::text[])
                 AS u(game, book, chapter, ply, by, expected, played, fen)
            ON CONFLICT (chess_game_id, player_id) DO UPDATE
            SET book_id = EXCLUDED.book_id, chapter_id = EXCLUDED.chapter_id,
                deviated_at_ply = EXCLUDED.deviated_at_ply, deviation_by = EXCLUDED.deviation_by,
                expected_move = EXCLUDED.expected_move, played_move = EXCLUDED.played_move,
                deviation_fen = EXCLUDED.deviation_fen
            RETURNING id, chess_game_id
            """,
            {
                "pid": PLAYER_ID,
                "game": [r.chess_game_id for r in results],
                "book": [r.book_id for r in results],
                "chapter": [r.chapter_id for r in results],
                "ply": [r.deviated_at_ply for r in results],
                "by": [r.deviation_by for r in results],
                "expected": [r.expected_move for r in results],
                "played": [r.played_move for r in results],
                "fen": [r.deviation_fen for r in results],
            },
        ).fetchall()
        result_id = {int(row["chess_game_id"]): int(row["id"]) for row in rows}
        conn.execute(
            "DELETE FROM game_result_lines WHERE game_repertoire_result_id = ANY(%s)", (list(result_id.values()),)
        )
        grr: list[int] = []
        line: list[int] = []
        ply: list[int] = []
        for r in results:
            for line_id in r.line_ids:
                grr.append(result_id[r.chess_game_id])
                line.append(line_id)
                ply.append(r.deviated_at_ply)
        conn.execute(
            "INSERT INTO game_result_lines (game_repertoire_result_id, line_id, matched_ply)"
            " SELECT * FROM unnest(%s::bigint[], %s::int[], %s::int[]) ON CONFLICT DO NOTHING",
            (grr, line, ply),
        )


def _match_all(
    conn: Connection[Any], games: list[dict[str, Any]], timings: dict[str, float] | None = None
) -> dict[str, int]:
    """Match and write these games. `lines` in the counts is the active lines of the games'
    colours, the lines they were matched against."""
    started = time.perf_counter()
    lines = active_lines(conn, {str(g["player_color"]) for g in games})
    results: list[MatchResult] = []
    no_match: list[int] = []
    for g in games:
        r = match_game(g["chess_game_id"], g["player_color"], list(g["moves"]), list(g["fen_sequence"]), lines)
        if r is None:
            no_match.append(g["chess_game_id"])
        else:
            results.append(r)
    matched_at = time.perf_counter()
    write_results(conn, results, no_match)
    if timings is not None:
        timings["match"] = matched_at - started
        timings["write"] = time.perf_counter() - matched_at
    return {"candidates": len(games), "matched": len(results), "no_match": len(no_match), "lines": len(lines)}


def lock(conn: Connection[Any]) -> None:
    """Serialise everything that reads the repertoire to publish results, or changes it:
    the hourly matcher, a rematch, a toggle, an import, the puzzle generators. Held to the
    end of the current transaction, so a matcher that read the lines before a toggle cannot
    publish results computed from them after it."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_REPERTOIRE, PLAYER_ID))


def match_player(conn: Connection[Any], window: int) -> dict[str, int]:
    """The step: match every unmatched in-window game. Returns counts for the run log. The
    lines (every FEN of every active line) are read only when there is a game to match;
    with none, the count alone keeps the log's shape."""
    with conn.transaction():
        lock(conn)
        games = unmatched_games(conn, window)
        if not games:
            row = conn.execute(f"SELECT count(*) AS lines {_ACTIVE_LINES}", (PLAYER_ID,)).fetchone()
            return {"candidates": 0, "matched": 0, "no_match": 0, "lines": int(row["lines"]) if row else 0}
        return _match_all(conn, games)


def rematch_window(conn: Connection[Any], window: int) -> dict[str, int]:
    """Forget every result and no-match flag and match the whole window again — after the
    repertoire itself changed (an import, a toggle at book level). Runs inside the caller's
    transaction, so the window is never seen empty."""
    with conn.transaction():
        lock(conn)
        conn.execute("DELETE FROM game_repertoire_results WHERE player_id = %s", (PLAYER_ID,))
        conn.execute("UPDATE player_games SET no_repertoire_match = FALSE WHERE player_id = %s", (PLAYER_ID,))
        return match_player(conn, window)


def games_touched_by(conn: Connection[Any], line_ids: list[int], *, activated: bool, window: int) -> list[int]:
    """The in-window games whose result can change when these lines flip. Lines going
    inactive can only take a result away, so only games whose result cites one of them are
    affected. Lines coming active can only be matched by a game that contains their first
    position after the start, which the GIN index finds."""
    if not line_ids:
        return []
    if not activated:
        rows = conn.execute(
            "SELECT DISTINCT grr.chess_game_id FROM game_repertoire_results grr"
            " JOIN game_result_lines grl ON grl.game_repertoire_result_id = grr.id"
            " WHERE grr.player_id = %s AND grl.line_id = ANY(%s)",
            (PLAYER_ID, line_ids),
        ).fetchall()
        return [int(r["chess_game_id"]) for r in rows]
    query = cast(
        LiteralString,
        f"""
        WITH {window_cte()},
        keys AS (SELECT array_agg(DISTINCT rl.position_keys[2]) AS ks FROM repertoire_lines rl
                 WHERE rl.id = ANY(%(lines)s) AND array_length(rl.position_keys, 1) >= 2)
        SELECT cg.id FROM chess_games cg, keys
        WHERE cg.id IN (SELECT chess_game_id FROM window_games) AND cg.position_keys && keys.ks
        """,
    )
    rows = conn.execute(query, {"pid": PLAYER_ID, "window": window, "lines": line_ids}).fetchall()
    return [int(r["id"]) for r in rows]


def rematch_games(
    conn: Connection[Any], game_ids: list[int], timings: dict[str, float] | None = None
) -> dict[str, int]:
    """Match these games again from scratch against the current active lines. `timings`, when
    given, receives the seconds spent matching and writing."""
    if not game_ids:
        return {"candidates": 0, "matched": 0, "no_match": 0, "lines": 0}
    with conn.transaction():
        lock(conn)
        conn.execute(
            "DELETE FROM game_repertoire_results WHERE player_id = %s AND chess_game_id = ANY(%s)",
            (PLAYER_ID, game_ids),
        )
        conn.execute(
            "UPDATE player_games SET no_repertoire_match = FALSE WHERE player_id = %s AND chess_game_id = ANY(%s)",
            (PLAYER_ID, game_ids),
        )
        query = cast(
            LiteralString,
            f"""
            SELECT cg.id AS chess_game_id, pg.player_color, cg.moves, cg.fen_sequence
            FROM player_games pg JOIN chess_games cg ON cg.id = pg.chess_game_id
            WHERE pg.player_id = %(pid)s AND cg.id = ANY(%(ids)s)
              AND cg.moves IS NOT NULL AND cg.fen_sequence IS NOT NULL AND {analysable_sql("cg")}
            ORDER BY cg.played_at ASC NULLS LAST
            """,
        )
        games = conn.execute(query, {"pid": PLAYER_ID, "ids": game_ids}).fetchall()
        return _match_all(conn, games, timings)


def games_a_new_line_can_change(
    conn: Connection[Any], line_id: int, colour: str, line_fens: list[str], window: int
) -> tuple[list[int], int]:
    """The in-window games holding the line's first position after the start whose rows a
    rematch can change now that this active line exists, and how many games were candidates.
    A game of the line's colour whose result matched some line to ply b keeps it unless the new
    line matches it at least as far (m >= b: it ties or wins, and the unchanged matcher
    decides); one with no result row, flagged or not, is rematched. A game of the other colour
    never consults the line, so a result row it holds stands; one without a row is rematched as
    any rematch would — pending (imported since the last match), or flagged by a match that may
    predate lines added while the game was outside the window. Relies on what every repertoire
    change keeps true of a stored result: it is `match_game` over the active lines as they were
    before this one."""
    query = cast(
        LiteralString,
        f"""
        WITH {window_cte()},
        keys AS (SELECT array_agg(DISTINCT rl.position_keys[2]) AS ks FROM repertoire_lines rl
                 WHERE rl.id = %(line)s AND array_length(rl.position_keys, 1) >= 2)
        SELECT cg.id, cg.fen_sequence, grr.deviated_at_ply
        FROM chess_games cg
        CROSS JOIN keys
        JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
        LEFT JOIN game_repertoire_results grr ON grr.chess_game_id = cg.id AND grr.player_id = %(pid)s
        WHERE cg.id IN (SELECT chess_game_id FROM window_games) AND cg.position_keys && keys.ks
          AND (pg.player_color = %(colour)s OR grr.id IS NULL)
        """,
    )
    rows = conn.execute(query, {"pid": PLAYER_ID, "window": window, "line": line_id, "colour": colour}).fetchall()
    changed: list[int] = []
    for r in rows:
        fens = r["fen_sequence"]
        stored = r["deviated_at_ply"]
        if fens is None or stored is None or subsequence_match_length(list(fens), line_fens) >= int(stored):
            changed.append(int(r["id"]))
    return changed, len(rows)
