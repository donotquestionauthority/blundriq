"""The analysis step: pick games needing analysis, run Stockfish in parallel,
write blunders, motif events and the per-ply series.

Worklist: the player's most recent `analysis_game_limit` games (the window),
analysable variants only, with moves, not yet analysed at the target depth.
Each game is written in one transaction behind a monotonic-depth gate (a row
already analysed deeper is left alone), replacing the game's blunders and
non-endgame motif events, then stamping player_games.analyzed_at_depth (even
when there are no blunders, so clean games are not re-analysed every hour)
and the shared chess_games row (depth-guarded).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from multiprocessing import Pool
from typing import Any, LiteralString, cast

from psycopg import Connection

from core import db
from core.analysis.engine import find_stockfish, open_engine
from core.analysis.game import GameAnalysis, analyze_game
from core.analysis.motifs import tag_game
from core.chess.eligibility import analysable_sql, window_cte
from core.constants import PLAYER_ID, STOCKFISH_DEPTH, STOCKFISH_VERSION
from core.notify import error_label
from core.settings import Settings


@dataclass(frozen=True)
class Task:
    chess_game_id: int
    player_color: str
    moves: list[str]
    opening_eco: str | None
    variant: str
    starting_fen: str | None
    depth: int
    settings: Settings
    stockfish: str


def worklist(
    conn: Connection[Any], window: int, depth: int = STOCKFISH_DEPTH, game_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """Games to analyse, newest first. `game_ids` restricts to an explicit set (still in-window)."""
    query = cast(
        LiteralString,
        f"""
        WITH {window_cte()}
        SELECT cg.id AS chess_game_id, pg.player_color, cg.moves, cg.opening_eco, cg.variant, cg.starting_fen
        FROM player_games pg
        JOIN chess_games cg ON cg.id = pg.chess_game_id
        WHERE pg.player_id = %(pid)s
          AND cg.moves IS NOT NULL
          AND {analysable_sql("cg")}
          AND cg.id IN (SELECT chess_game_id FROM window_games)
          AND (pg.analyzed_at_depth IS NULL OR pg.analyzed_at_depth < %(depth)s)
          AND (%(ids)s::bigint[] IS NULL OR cg.id = ANY(%(ids)s::bigint[]))
        ORDER BY cg.played_at DESC NULLS LAST
        """,
    )  # literal SQL plus the eligibility fragment; values are bound
    return conn.execute(query, {"pid": PLAYER_ID, "window": window, "depth": depth, "ids": game_ids}).fetchall()


def save_analysis(conn: Connection[Any], task: Task, result: GameAnalysis) -> bool:
    """Write one game's results. False if a deeper analysis already exists (nothing written)."""
    s = task.settings
    with conn.transaction():
        row = conn.execute(
            "SELECT analyzed_at_depth FROM player_games WHERE player_id = %s AND chess_game_id = %s FOR UPDATE",
            (PLAYER_ID, task.chess_game_id),
        ).fetchone()
        stored = row["analyzed_at_depth"] if row else None
        if stored is not None and stored > task.depth:
            return False

        motif_rows = tag_game(
            result.ply_analysis,
            task.moves,
            task.player_color,
            task.variant,
            task.starting_fen,
            motif_min_material_gain=s.motif_min_material_gain,
            motif_found_material_tolerance=s.motif_found_material_tolerance,
        )
        themes_by_ply: dict[int, set[str]] = {}
        for r in motif_rows:
            if r["found"] is False:
                themes_by_ply.setdefault(int(r["ply"]), set()).add(str(r["theme"]))

        conn.execute(
            "DELETE FROM blunders WHERE player_id = %s AND chess_game_id = %s", (PLAYER_ID, task.chess_game_id)
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO blunders (player_id, chess_game_id, ply, phase, fen, move_played, best_move, best_line,
                                      post_blunder_line, centipawn_loss, classification, opening_eco, themes,
                                      engine_version, analysis_depth)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        PLAYER_ID,
                        task.chess_game_id,
                        b.ply,
                        b.phase,
                        b.fen,
                        b.move_played,
                        b.best_move,
                        b.best_line,
                        b.post_blunder_line,
                        b.centipawn_loss,
                        b.classification,
                        task.opening_eco or "",
                        sorted(themes_by_ply[b.ply]) if b.ply in themes_by_ply else None,
                        f"stockfish_{STOCKFISH_VERSION}",
                        task.depth,
                    )
                    for b in result.blunders
                ],
            )
            cur.execute(
                "DELETE FROM player_motif_events WHERE player_id = %s AND chess_game_id = %s"
                " AND metric_type <> 'endgame'",
                (PLAYER_ID, task.chess_game_id),
            )
            cur.executemany(
                """
                INSERT INTO player_motif_events
                    (player_id, chess_game_id, ply, metric_type, theme, found, mate_in_moves,
                     cp_loss, player_color, engine_version, analysis_depth)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        PLAYER_ID,
                        task.chess_game_id,
                        r["ply"],
                        r["metric_type"],
                        r["theme"],
                        r["found"],
                        r["mate_in_moves"],
                        r["cp_loss"],
                        r["player_color"],
                        f"stockfish_{STOCKFISH_VERSION}",
                        task.depth,
                    )
                    for r in motif_rows
                ],
            )
        conn.execute(
            "UPDATE player_games SET analyzed_at_depth = %s WHERE player_id = %s AND chess_game_id = %s",
            (task.depth, PLAYER_ID, task.chess_game_id),
        )
        conn.execute(
            """
            UPDATE chess_games
            SET analysis_status = 'completed', analysis_engine = %s, analysis_depth = %s, peak_advantage = %s,
                final_eval = %s, ply_analysis = %s::jsonb, ply_analysis_depth = %s
            WHERE id = %s AND %s >= COALESCE(GREATEST(analysis_depth, ply_analysis_depth), 0)
            """,
            (
                f"stockfish_{STOCKFISH_VERSION}",
                task.depth,
                result.peak_advantage,
                result.final_eval,
                json.dumps(result.ply_analysis),
                task.depth,
                task.chess_game_id,
                task.depth,
            ),
        )
    return True


def analyze_one(task: Task) -> dict[str, Any]:
    """Worker body: one engine, one connection, one game."""
    try:
        if not task.moves:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE chess_games SET analysis_status = 'failed_permanent' WHERE id = %s",
                    (task.chess_game_id,),
                )
                conn.execute(
                    "UPDATE player_games SET analyzed_at_depth = %s WHERE player_id = %s AND chess_game_id = %s",
                    (task.depth, PLAYER_ID, task.chess_game_id),
                )
                conn.commit()
            return {"chess_game_id": task.chess_game_id, "ok": True, "issues": 0}
        with open_engine(task.stockfish) as engine:
            result = analyze_game(
                engine,
                task.moves,
                task.player_color,
                task.settings,
                task.depth,
                variant=task.variant,
                starting_fen=task.starting_fen,
            )
        with db.connect() as conn:
            written = save_analysis(conn, task, result)
            conn.commit()
        return {"chess_game_id": task.chess_game_id, "ok": True, "issues": len(result.blunders), "written": written}
    except Exception as exc:  # the run continues; the summary carries the failure
        return {"chess_game_id": task.chess_game_id, "ok": False, "error": error_label(exc)}


def analyze_pending(
    conn: Connection[Any],
    settings: Settings,
    *,
    workers: int | None = None,
    game_ids: list[int] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The step. Reads the worklist on `conn`, then closes it while the pool runs
    (each worker opens its own connection). Returns counts for the run log."""
    stockfish = find_stockfish()
    rows = worklist(conn, settings.analysis_game_limit, STOCKFISH_DEPTH, game_ids)
    if limit is not None:
        rows = rows[:limit]
    tasks = [
        Task(
            chess_game_id=int(r["chess_game_id"]),
            player_color=str(r["player_color"]),
            moves=list(r["moves"]),
            opening_eco=r["opening_eco"],
            variant=str(r["variant"]),
            starting_fen=r["starting_fen"],
            depth=STOCKFISH_DEPTH,
            settings=settings,
            stockfish=stockfish,
        )
        for r in rows
    ]
    conn.commit()
    summary: dict[str, Any] = {
        "pending": len(tasks),
        "analyzed": 0,
        "failed": 0,
        "issues": 0,
        "workers": workers or (os.cpu_count() or 2),
    }
    if not tasks:
        return summary
    failures: list[str] = []
    with Pool(processes=summary["workers"]) as pool:
        for res in pool.imap_unordered(analyze_one, tasks):
            if res["ok"]:
                summary["analyzed"] += 1
                summary["issues"] += int(res.get("issues", 0))
            else:
                summary["failed"] += 1
                failures.append(f"{res['chess_game_id']}: {res['error']}")
            print(
                f"  {summary['analyzed'] + summary['failed']}/{len(tasks)} game {res['chess_game_id']}"
                f" {'ok' if res['ok'] else 'FAILED'}",
                flush=True,
            )
    if failures:
        summary["failures"] = failures[:20]
    return summary
