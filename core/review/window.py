"""The review window and the two context reads the detector needs.

The window is the analysis window (`core.chess.eligibility.window_cte`: the most recent
`analysis_game_limit` analysable games), narrowed to the games that are analysed and whose
bulk JSON is intact — the games the detector can replay. Chess960 never takes a slot, so a
Chess960 game is never reviewed. Read-only; the caller owns the transaction and the locks.
"""

from __future__ import annotations

import json
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import window_cte
from core.constants import PLAYER_ID

GameCtx = dict[str, Any]

# The detection knobs, in the order they are read from the settings row into `knobs`. The
# read-side settings (pool floors, the recency half-life) are not here: the writer stores
# every window game's events unfiltered.
KNOB_FIELDS = (
    "review_material_candidate_drop",
    "review_conf_es_drop",
    "review_conf_es_drop_depth12",
    "review_early_k_plies",
    "review_early_ply_cap",
    "review_missed_win_shed",
    "review_faded_peak_es",
    "review_quiesce_max_plies",
)

# The window's rows, shared by the read and by the fingerprint that stands in for it.
_WINDOW_ROWS = cast(
    LiteralString,
    """
    FROM window_games w
    JOIN chess_games cg ON cg.id = w.chess_game_id
    JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = %(pid)s
    WHERE pg.analyzed_at_depth IS NOT NULL
      AND cg.moves IS NOT NULL AND cg.fen_sequence IS NOT NULL AND cg.ply_analysis IS NOT NULL
    """,
)

_WINDOW = cast(
    LiteralString,
    f"""
    WITH {window_cte()}
    SELECT cg.id AS chess_game_id, cg.moves, cg.fen_sequence, cg.ply_analysis,
           cg.ply_analysis_depth AS analysis_depth, pg.player_color, pg.result, cg.termination,
           cg.variant, cg.starting_fen, cg.opening_eco, cg.canonical_family, cg.canonical_variation
    {_WINDOW_ROWS}
    ORDER BY cg.played_at DESC NULLS LAST, cg.id DESC
    """,
)

# Everything the detector is given, digested where it is stored so that no payload leaves the
# server: every column `_WINDOW` selects (the three jsonb columns by the hash of their canonical
# text) as one row literal per game, the repertoire rows `_REPERTOIRE` joins before its
# DISTINCT ON, the motif rows `_MOTIF_MISSED` returns, the schema version — plus the proof the
# published generation carries (`proven`: its fingerprint, only while `fingerprint_at` is the
# generation's own `computed_at`; an older client's upsert moves `computed_at` and not
# `fingerprint_at`, so what it publishes carries no proof). Sums of 64-bit hashes are
# order-independent, which is right: tagging is per game and the delete takes a set. A row
# literal keeps NULLs in their positions, unlike concat_ws.
_FINGERPRINT = cast(
    LiteralString,
    f"""
    WITH {window_cte()},
    w AS MATERIALIZED (
        SELECT cg.id AS chess_game_id,
               hashtextextended(row(cg.id, pg.analyzed_at_depth, cg.ply_analysis_depth, pg.player_color, pg.result,
                                    cg.termination, cg.variant, cg.starting_fen, cg.opening_eco, cg.canonical_family,
                                    cg.canonical_variation, hashtextextended(cg.moves::text, 0),
                                    hashtextextended(cg.fen_sequence::text, 0),
                                    hashtextextended(cg.ply_analysis::text, 0))::text, 0) AS h
        {_WINDOW_ROWS}
    )
    SELECT (SELECT count(*) FROM w) AS games,
           (SELECT sum(h) FROM w) AS games_digest,
           (SELECT sum(hashtextextended(row(grr.chess_game_id, grr.book_id, grr.chapter_id, grr.deviated_at_ply,
                                            grr.deviation_by, grr.expected_move, grl.line_id, grl.matched_ply,
                                            hashtextextended(rl.moves::text, 0))::text, 0))
              FROM game_repertoire_results grr
              JOIN game_result_lines grl ON grl.game_repertoire_result_id = grr.id
              JOIN repertoire_lines rl ON rl.id = grl.line_id
              WHERE grr.player_id = %(pid)s
                AND grr.chess_game_id = ANY(ARRAY(SELECT chess_game_id FROM w))) AS repertoire_digest,
           (SELECT sum(hashtextextended(row(chess_game_id, ply, metric_type, theme, mate_in_moves)::text, 0))
              FROM player_motif_events
              WHERE player_id = %(pid)s AND chess_game_id = ANY(ARRAY(SELECT chess_game_id FROM w))
                AND found IS FALSE AND metric_type IN ('motif', 'mate')) AS motif_digest,
           (SELECT max(version) FROM schema_version) AS schema_version,
           (SELECT fingerprint FROM review_detection_state
             WHERE player_id = %(pid)s AND fingerprint IS NOT NULL AND fingerprint_at = computed_at) AS proven
    """,
)


def fingerprint_row(conn: Connection[Any], window: int) -> dict[str, Any]:
    """One row of scalars: the digests of the window's inputs and the published generation's
    proof, in one statement (the locks are held, and Supabase is a network away)."""
    found = conn.execute(_FINGERPRINT, {"pid": PLAYER_ID, "window": window}).fetchone()
    return dict(found) if found else {}


def fingerprint(row: dict[str, Any], knobs: dict[str, int], window: int, code: str) -> str:
    """The digests with the knobs, the window size, the schema version and the code stamp, as one
    sorted JSON string: what a generation is a function of."""
    return json.dumps(
        {
            "code": code,
            "schema_version": row.get("schema_version"),
            "window": window,
            "knobs": knobs,
            "games": row.get("games"),
            "games_digest": str(row.get("games_digest")),
            "repertoire_digest": str(row.get("repertoire_digest")),
            "motif_digest": str(row.get("motif_digest")),
        },
        sort_keys=True,
    )


# One row per game: the result joined to its deepest matched line (max matched_ply, then the
# lowest line_id, the matcher's own tie-break), with the line's length in plies.
_REPERTOIRE = """
    SELECT DISTINCT ON (grr.chess_game_id)
           grr.chess_game_id, grr.book_id, grr.chapter_id, grr.deviated_at_ply, grr.deviation_by,
           grr.expected_move, grl.line_id, grl.matched_ply, rl.moves AS line_moves
    FROM game_repertoire_results grr
    JOIN game_result_lines grl ON grl.game_repertoire_result_id = grr.id
    JOIN repertoire_lines rl ON rl.id = grl.line_id
    WHERE grr.player_id = %(pid)s AND grr.chess_game_id = ANY(%(ids)s)
    ORDER BY grr.chess_game_id, grl.matched_ply DESC, grl.line_id ASC
"""

# The missed opportunities the tagger recorded: found IS FALSE (never NULL), and only the
# two metric types that are review evidence — 'positional' and 'endgame' rows are not.
_MOTIF_MISSED = """
    SELECT chess_game_id, ply, metric_type, theme, mate_in_moves
    FROM player_motif_events
    WHERE player_id = %(pid)s AND chess_game_id = ANY(%(ids)s)
      AND found IS FALSE AND metric_type IN ('motif', 'mate')
    ORDER BY chess_game_id, ply, metric_type, theme
"""


def _as_list(value: Any) -> Any:
    """A jsonb column comes back as a list; JSON text is tolerated; anything undecodable is
    None, which the detector fails closed on."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except TypeError, ValueError:
            return None
    return value


def window_games(conn: Connection[Any], window: int) -> list[GameCtx]:
    """The window's games as detector contexts, newest first, without their repertoire and
    motif context (`repertoire` is None, `motif_missed` is empty until attached)."""
    rows = conn.execute(_WINDOW, {"pid": PLAYER_ID, "window": window}).fetchall()
    return [
        {
            "chess_game_id": int(r["chess_game_id"]),
            "moves": _as_list(r["moves"]),
            "fen_sequence": _as_list(r["fen_sequence"]),
            "ply_analysis": _as_list(r["ply_analysis"]),
            "analysis_depth": r["analysis_depth"],
            "player_color": r["player_color"],
            "result": r["result"],
            "termination": r["termination"],
            "variant": r["variant"],
            "starting_fen": r["starting_fen"],
            "opening_eco": r["opening_eco"],
            "canonical_family": r["canonical_family"],
            "canonical_variation": r["canonical_variation"],
            "repertoire": None,
            "motif_missed": [],
        }
        for r in rows
    ]


def repertoire_ctx(conn: Connection[Any], ids: list[int]) -> dict[int, dict[str, Any] | None]:
    """{game id: repertoire context | None} for every id given. `line_len` is the matched
    line's length in plies, the unit `matched_ply` is measured in."""
    ctx: dict[int, dict[str, Any] | None] = {gid: None for gid in ids}
    if not ids:
        return ctx
    for row in conn.execute(_REPERTOIRE, {"pid": PLAYER_ID, "ids": ids}).fetchall():
        moves: Any = _as_list(row["line_moves"])
        length = len(cast(list[Any], moves)) if isinstance(moves, list) else 0
        ctx[int(row["chess_game_id"])] = {
            "book_id": row["book_id"],
            "chapter_id": row["chapter_id"],
            "line_id": row["line_id"],
            "deviated_at_ply": row["deviated_at_ply"],
            "deviation_by": row["deviation_by"],
            "expected_move": row["expected_move"],
            "matched_ply": row["matched_ply"],
            "line_len": length,
        }
    return ctx


def motif_missed(conn: Connection[Any], ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """{game id: missed-opportunity rows} for every id given (an empty list when none)."""
    out: dict[int, list[dict[str, Any]]] = {gid: [] for gid in ids}
    if not ids:
        return out
    for row in conn.execute(_MOTIF_MISSED, {"pid": PLAYER_ID, "ids": ids}).fetchall():
        out[int(row["chess_game_id"])].append(
            {
                "ply": row["ply"],
                "metric_type": row["metric_type"],
                "theme": row["theme"],
                "mate_in_moves": row["mate_in_moves"],
            }
        )
    return out


def contexts(conn: Connection[Any], window: int) -> list[GameCtx]:
    """The window's games with their repertoire and motif context attached."""
    games = window_games(conn, window)
    ids = [g["chess_game_id"] for g in games]
    reps = repertoire_ctx(conn, ids)
    missed = motif_missed(conn, ids)
    for g in games:
        g["repertoire"] = reps[g["chess_game_id"]]
        g["motif_missed"] = missed[g["chess_game_id"]]
    return games
