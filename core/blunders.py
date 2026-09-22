"""The Blunders page: recurring positions where the player errs, ranked; and their dismissal.

The unit is the **board** (`blunders.canonical_fen`: placement, side, castling, en passant),
and how often it recurs is counted in **distinct games**. `per_game` reduces each
(board, game) to its worst instance; count, score and the classification map are all taken
over `per_game`, so the classification counts on a card always sum to its count. The details
query selects with the same ordering, so a card's example and games are the rows its score
was computed from.

Every filter applies to `per_game`'s input, through one WHERE builder shared by the ranked
query and the details query. The window is either a day count or the player's most recent N
standard games (`eligibility.window_cte`: Chess960 never takes a slot). Time class narrows
*inside* the window, never before it (docs/decisions/005).

The `fen` a card carries is a real occurrence's FEN, never the board key: the key ends in a
fixed `0 1` and is not the position anyone played. Dismissal is by board, so dismiss and
restore accept any FEN and reduce it in SQL.

**New** (`mark_new`): a board the list has never shown — not in `seen_blunder_boards` and not
dismissed. The page acknowledges exactly the boards it rendered (`mark_seen`), so a board that
arrived between the read and the acknowledgement, or that sits on a page never opened, stays
new; the first look ever (`players.blunders_seen_at` still NULL) marks nothing and acknowledges
everything then listed, so history is not news. Home reads the same predicate for its count
and never acknowledges anything. New boards are listed first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString, cast

from psycopg import Connection

from core.chess.eligibility import analysable_sql, evidence_sql, window_cte
from core.constants import BLUNDER_CLASSES, BLUNDER_SCORE_WEIGHTS, PLAYER_ID
from core.settings import Settings

Row = dict[str, Any]

PAGE_SIZE = 50
TIME_CLASSES = ("focus", "all", "bullet", "blitz", "rapid", "classical")


@dataclass(frozen=True)
class BlunderFilters:
    classifications: tuple[str, ...]
    min_occurrences: int = 2
    since_days: int | None = None
    last_n_games: int = 0  # > 0 wins over since_days
    time_class: str = "focus"  # one of TIME_CLASSES
    show_dismissed: bool = False
    mark_new: bool = False  # flag boards never shown; False before the first look


def default_filters(config: Settings, mark_new: bool = False) -> BlunderFilters:
    """The filters the page opens on (ui/src/blunders.ts `defaultFilters` builds the same
    from the settings row, including the fallback for an empty or unknown class list). Home
    counts new boards against exactly these."""
    by_games = config.blunders_default_filter_mode == "games"
    classes = tuple(c for c in config.blunders_default_classifications if c in BLUNDER_CLASSES)
    return BlunderFilters(
        classifications=classes or ("miss", "blunder", "mistake"),
        min_occurrences=config.blunders_default_min_occurrences,
        since_days=None if by_games else config.blunders_default_window_days,
        last_n_games=config.blunders_default_last_n_games if by_games else 0,
        time_class="focus",
        show_dismissed=False,
        mark_new=mark_new,
    )


def _time_class_sql(time_class: str, focus: str) -> str:
    if time_class == "focus":
        return evidence_sql("cg", focus)
    if time_class == "all":
        return "TRUE"
    if time_class == "classical":
        return "cg.time_class IN ('classical', 'correspondence')"
    if time_class in ("bullet", "blitz", "rapid"):
        return f"cg.time_class = '{time_class}'"
    raise ValueError(f"unknown time class {time_class!r}")


def _scope(f: BlunderFilters, focus: str) -> tuple[str, str, dict[str, Any]]:
    """`(WITH prefix, WHERE body over aliases b/cg, params)` — the one definition of which
    blunder rows are in play. Both queries below are built on it."""
    params: dict[str, Any] = {"pid": PLAYER_ID, "cls": list(f.classifications)}
    clauses = [
        "b.player_id = %(pid)s",
        "b.classification = ANY(%(cls)s)",
        analysable_sql("cg"),
        _time_class_sql(f.time_class, focus),
    ]
    prefix = ""
    if f.last_n_games > 0:
        prefix = window_cte() + ", "
        clauses.append("b.chess_game_id IN (SELECT chess_game_id FROM window_games)")
        params["window"] = f.last_n_games
    elif f.since_days:
        clauses.append("cg.played_at >= now() - make_interval(days => %(days)s)")
        params["days"] = f.since_days
    return prefix, " AND ".join(clauses), params


# The survivor of each (board, game): the worst instance. The details query must order by
# exactly this, or a card would show one row and be scored from another.
_SURVIVOR_ORDER = "b.centipawn_loss DESC NULLS LAST, b.ply, b.id"

_WEIGHT_CASE = (
    "CASE b.classification "
    + " ".join(f"WHEN '{k}' THEN {v}" for k, v in BLUNDER_SCORE_WEIGHTS.items())
    + " ELSE 0 END"
)


def _ranked_sql(prefix: str, where: str) -> str:
    return f"""
    WITH {prefix}per_game AS (
        SELECT DISTINCT ON (b.canonical_fen, b.chess_game_id)
               b.canonical_fen, b.classification, pg.player_color, cg.played_at,
               {_WEIGHT_CASE} AS weight
        FROM blunders b
        JOIN player_games pg ON pg.chess_game_id = b.chess_game_id AND pg.player_id = b.player_id
        JOIN chess_games cg ON cg.id = b.chess_game_id
        WHERE {where}
        ORDER BY b.canonical_fen, b.chess_game_id, {_SURVIVOR_ORDER}
    ),
    agg AS (
        SELECT canonical_fen, count(*) AS count, sum(weight) AS score, max(played_at) AS last_played,
               mode() WITHIN GROUP (ORDER BY player_color) AS color
        FROM per_game GROUP BY canonical_fen
        HAVING count(*) >= %(min_occ)s
    ),
    cls AS (
        SELECT canonical_fen, jsonb_object_agg(classification, n) AS classifications
        FROM (SELECT canonical_fen, classification, count(*) AS n FROM per_game GROUP BY 1, 2) c
        GROUP BY canonical_fen
    ),
    marked AS (
        SELECT agg.*, cls.classifications,
               EXISTS (SELECT 1 FROM dismissed_blunder_fens d
                       WHERE d.player_id = %(pid)s AND d.canonical_fen = agg.canonical_fen) AS dismissed,
               EXISTS (SELECT 1 FROM seen_blunder_boards s
                       WHERE s.player_id = %(pid)s AND s.canonical_fen = agg.canonical_fen) AS seen
        FROM agg JOIN cls USING (canonical_fen)
    ),
    flagged AS (
        SELECT marked.*, (%(mark_new)s AND NOT seen AND NOT dismissed) AS is_new FROM marked
    )
    """


Page = tuple[list[Row], int, int, list[str]]  # rows, active count, dismissed count, unseen board keys


def _page_rows(conn: Connection[Any], f: BlunderFilters, focus: str, page: int) -> Page:
    prefix, where, params = _scope(f, focus)
    params |= {
        "min_occ": f.min_occurrences,
        "mark_new": f.mark_new,
        "dismissed": f.show_dismissed,
        "limit": PAGE_SIZE,
        "offset": page * PAGE_SIZE,
    }
    body = _ranked_sql(prefix, where)
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                body
                + """
                SELECT f.*, c.active_count, c.dismissed_count, c.unseen
                FROM (SELECT count(*) FILTER (WHERE NOT dismissed) AS active_count,
                             count(*) FILTER (WHERE dismissed) AS dismissed_count,
                             coalesce(array_agg(canonical_fen) FILTER (WHERE NOT seen AND NOT dismissed), '{}')
                                 AS unseen
                      FROM flagged) c
                LEFT JOIN LATERAL (
                    SELECT * FROM flagged WHERE dismissed = %(dismissed)s
                    ORDER BY is_new DESC, score DESC, last_played DESC NULLS LAST, canonical_fen
                    LIMIT %(limit)s OFFSET %(offset)s
                ) f ON TRUE
                ORDER BY f.is_new DESC, f.score DESC, f.last_played DESC NULLS LAST, f.canonical_fen
                """,
            ),
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    # The counts ride on every row and survive a page past the end as one all-NULL row.
    counts = rows[0]
    return (
        [r for r in rows if r["canonical_fen"] is not None],
        int(counts["active_count"]),
        int(counts["dismissed_count"]),
        [str(x) for x in counts["unseen"]],
    )


def _details(conn: Connection[Any], f: BlunderFilters, focus: str, boards: list[str]) -> dict[str, list[Row]]:
    """One row per (board, game) for the given boards — the same survivors the ranking used —
    with the game, and the repertoire line the game was matched to, if any."""
    if not boards:
        return {}
    prefix, where, params = _scope(f, focus)
    params["boards"] = boards
    with_prefix = f"WITH {prefix.rstrip(', ')}" if prefix else ""
    with conn.cursor() as cur:
        cur.execute(
            cast(
                LiteralString,
                f"""
                {with_prefix}
                SELECT DISTINCT ON (b.canonical_fen, b.chess_game_id)
                       b.canonical_fen, b.fen, b.ply, b.move_played, b.best_move, b.best_line,
                       b.post_blunder_line, b.centipawn_loss AS cp_loss, b.classification, b.phase,
                       b.chess_game_id, cg.moves, cg.url AS game_url, cg.opening_name, cg.played_at,
                       pg.player_color AS color, pg.player_rating, pg.result,
                       bk.title AS book_title, ch.title AS chapter_title,
                       grr.deviated_at_ply, grr.deviation_by,
                       (SELECT array_agg(rl.line_name ORDER BY rl.line_name)
                        FROM game_result_lines grl JOIN repertoire_lines rl ON rl.id = grl.line_id
                        WHERE grl.game_repertoire_result_id = grr.id) AS line_names
                FROM blunders b
                JOIN player_games pg ON pg.chess_game_id = b.chess_game_id AND pg.player_id = b.player_id
                JOIN chess_games cg ON cg.id = b.chess_game_id
                LEFT JOIN game_repertoire_results grr
                       ON grr.chess_game_id = b.chess_game_id AND grr.player_id = b.player_id
                LEFT JOIN books bk ON bk.id = grr.book_id
                LEFT JOIN chapters ch ON ch.id = grr.chapter_id
                WHERE {where} AND b.canonical_fen = ANY(%(boards)s)
                ORDER BY b.canonical_fen, b.chess_game_id, {_SURVIVOR_ORDER}
                """,
            ),
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["canonical_fen"]), []).append(r)
    for group in out.values():
        group.sort(key=lambda d: d["played_at"].timestamp() if d["played_at"] else float("-inf"), reverse=True)
    return out


def in_repertoire(d: dict[str, Any]) -> bool:
    """The blunder happened while the game was still inside a matched repertoire line —
    whoever left the line later."""
    return bool(d.get("book_title")) and d.get("deviated_at_ply") is not None and d["ply"] <= d["deviated_at_ply"]


def pick_example(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The occurrence a card shows: the most severe class present, most recent game first."""
    for cls in BLUNDER_CLASSES:
        for d in group:
            if d["classification"] == cls:
                return d
    return group[0] if group else None


def _card(row: dict[str, Any], group: list[dict[str, Any]]) -> dict[str, Any]:
    example = pick_example(group)
    labels: list[str] = []
    for d in group:
        if in_repertoire(d):
            label = f"{str(d['book_title']).split(':')[-1].strip()} ({d['chapter_title']})"
            if label not in labels:
                labels.append(label)
    if not labels:
        for d in group:
            name = d.get("opening_name")
            if name and name not in labels:
                labels.append(str(name))
    rep = next((d for d in group if in_repertoire(d)), None)

    def iso(v: Any) -> str | None:
        return v.isoformat() if v is not None else None

    return {
        "fen": example["fen"] if example else None,
        "count": int(row["count"]),
        "score": int(row["score"]),
        "classifications": row["classifications"],
        "color": row["color"],
        "dismissed": bool(row["dismissed"]),
        "is_new": bool(row["is_new"]),
        "last_seen": iso(row["last_played"]),
        "context": ", ".join(labels[:3]) if labels else "Unknown opening",
        "book": rep["book_title"] if rep else None,
        "chapter": rep["chapter_title"] if rep else None,
        "line_names": rep["line_names"] if rep else None,
        "move_played": example["move_played"] if example else None,
        "best_move": example["best_move"] if example else None,
        "best_line": example["best_line"] if example else None,
        "post_blunder_line": example["post_blunder_line"] if example else None,
        "cp_loss": example["cp_loss"] if example else None,
        "ply": example["ply"] if example else None,
        "chess_game_id": example["chess_game_id"] if example else None,
        "moves": example["moves"] if example else None,
        "games": [
            {
                "chess_game_id": d["chess_game_id"],
                "game_url": d["game_url"],
                "played_at": iso(d["played_at"]),
                "result": d["result"],
                "classification": d["classification"],
                "cp_loss": d["cp_loss"],
                "move_played": d["move_played"],
                "best_move": d["best_move"],
                "in_repertoire": in_repertoire(d),
                "deviated_by_me": in_repertoire(d) and d.get("deviation_by") == "me",
            }
            for d in group
        ],
    }


def positions(conn: Connection[Any], f: BlunderFilters, focus: str, page: int = 0) -> dict[str, Any]:
    """One page of the ranked list, with the counts of both views. `focus` is the
    `time_class_focus` setting, which the 'focus' time class resolves to."""
    rows, active, dismissed, unseen = _page_rows(conn, f, focus, page)
    details = _details(conn, f, focus, [str(r["canonical_fen"]) for r in rows])
    total = dismissed if f.show_dismissed else active
    # What the page acknowledges once rendered: the boards it marked NEW — or, on the first
    # look, every active board on any page, so that history is known rather than news.
    shown = [str(r["canonical_fen"]) for r in rows if r["is_new"]] if f.mark_new else unseen
    return {
        "positions": [_card(r, details.get(str(r["canonical_fen"]), [])) for r in rows],
        "active_count": active,
        "dismissed_count": dismissed,
        "to_acknowledge": shown,
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": max(1, -(-total // PAGE_SIZE)),
    }


def new_count(conn: Connection[Any], f: BlunderFilters, focus: str) -> int:
    """How many active boards the list has never shown — the Home page's number, from the
    same ranking the page shows. Zero before the first look."""
    if not f.mark_new:
        return 0
    prefix, where, params = _scope(f, focus)
    params |= {"min_occ": f.min_occurrences, "mark_new": True}
    body = _ranked_sql(prefix, where) + "SELECT count(*) AS n FROM flagged WHERE is_new"
    with conn.cursor() as cur:
        cur.execute(cast(LiteralString, body), params)
        row = cur.fetchone()
    assert row is not None
    return int(row["n"])


def seen_at(conn: Connection[Any]) -> datetime | None:
    """When the list was last looked at; None before the first look."""
    row = conn.execute("SELECT blunders_seen_at FROM players WHERE id = %s", (PLAYER_ID,)).fetchone()
    if row is None:
        raise RuntimeError("no players row; run `pipeline player set` first")
    return row["blunders_seen_at"]


def mark_seen(conn: Connection[Any], boards: list[str]) -> datetime:
    """The page has shown these boards (board keys, as `to_acknowledge` gave them). Only
    those become known; anything found since the page read its list stays new."""
    row = conn.execute(
        "UPDATE players SET blunders_seen_at = now() WHERE id = %s RETURNING blunders_seen_at", (PLAYER_ID,)
    ).fetchone()
    if row is None:
        raise RuntimeError("no players row; run `pipeline player set` first")
    if boards:
        conn.execute(
            "INSERT INTO seen_blunder_boards (player_id, canonical_fen)"
            " SELECT %s, unnest(%s::text[]) ON CONFLICT DO NOTHING",
            (PLAYER_ID, boards),
        )
    return row["blunders_seen_at"]


def dismiss(conn: Connection[Any], fen: str) -> None:
    """Hide a board from the active list. Dismissing it twice is a no-op."""
    conn.execute(
        "INSERT INTO dismissed_blunder_fens (player_id, fen) VALUES (%s, %s)"
        " ON CONFLICT (player_id, canonical_fen) DO NOTHING",
        (PLAYER_ID, fen),
    )


def restore(conn: Connection[Any], fen: str) -> None:
    """Undo a dismissal, whichever occurrence's FEN is given."""
    conn.execute(
        "DELETE FROM dismissed_blunder_fens WHERE player_id = %s AND canonical_fen = bq_canonical_fen(%s) || ' 0 1'",
        (PLAYER_ID, fen),
    )
