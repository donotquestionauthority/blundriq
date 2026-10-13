"""The report's SQL reduction (core/repertoire/report.py `_REPORT_SQL`) against a Python
reference over the same per-(game, line) rows: statuses in `who_deviated`'s order, one game per
scope by precedence then the (book, chapter, line, id) tie-break, the slip as the most common
whole tuple, the depth histogram, shared, and the gap games with their context.

The fixtures are built to split code-point order from the database's default collation where the
SQL compares text — the line's rank over book, chapter and line titles: sibling chapters `Zebra`,
`apple`, `Éclair`, line names differing only in case. Python compares strings by code point and
`COLLATE "C"` is what makes the SQL agree, so the test refuses to run on a C-collated database,
where it would prove nothing. The equally frequent slips `Nf3` and `a3` exercise the slip's own
tie-break, which is Python's tuple order in `_summary`."""

from __future__ import annotations

from collections import Counter
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from core.constants import PLAYER_ID
from core.repertoire import report
from core.settings import Settings
from tests import repertoire_helpers as h

Row = dict[str, Any]
_PRECEDENCE = {"me": 0, "opponent": 1, "ended": 2, "ran_out": 3}


def _raw_rows(conn: psycopg.Connection[DictRow]) -> list[Row]:
    """One row per (game, attached line) in the window, status and own moves by the Python rules."""
    rows = conn.execute(
        "SELECT grr.chess_game_id AS gid, pg.result, cg.played_at, grr.deviated_at_ply AS m,"
        " jsonb_array_length(cg.moves) AS glen, cg.moves->>grr.deviated_at_ply AS played,"
        " rl.id AS line_id, rl.line_name, jsonb_array_length(rl.moves) AS len, rl.moves->>grr.deviated_at_ply AS expected,"
        " ch.id AS chapter_id, ch.title AS chapter_title, bk.id AS book_id, bk.title AS book_title, bk.color"
        " FROM game_repertoire_results grr"
        " JOIN chess_games cg ON cg.id = grr.chess_game_id"
        " JOIN player_games pg ON pg.chess_game_id = cg.id AND pg.player_id = grr.player_id"
        " JOIN game_result_lines grl ON grl.game_repertoire_result_id = grr.id"
        " JOIN repertoire_lines rl ON rl.id = grl.line_id AND rl.active"
        " JOIN chapters ch ON ch.id = rl.chapter_id AND ch.active"
        " JOIN books bk ON bk.id = ch.book_id AND bk.active AND bk.player_id = %s",
        (PLAYER_ID,),
    ).fetchall()
    out: list[Row] = []
    for r in rows:
        d = dict(r)
        d["status"] = report.status_of(int(d["m"]), int(d["len"]), int(d["glen"]), str(d["color"]))
        d["own"] = report.own_moves(int(d["m"]), str(d["color"]))
        out.append(d)
    return out


def _key(r: Row) -> tuple[int, str, str, str, int]:
    return (
        _PRECEDENCE[r["status"]],
        str(r["book_title"]),
        str(r["chapter_title"]),
        str(r["line_name"]),
        int(r["line_id"]),
    )


def _reference(rows: list[Row]) -> tuple[dict[str, dict[int, Row]], dict[int, Row]]:
    """The reduction as `_Tally` did it in Python: the first row per (scope, game) under
    precedence then the tie-break, counted; the gap games likewise over every attached line."""
    scopes: dict[str, dict[int, Row]] = {"line": {}, "chapter": {}, "book": {}}
    per_game: dict[tuple[str, int, int], Row] = {}
    for r in sorted(rows, key=lambda r: (int(r["gid"]), *_key(r))):
        for scope, key in (("line", "line_id"), ("chapter", "chapter_id"), ("book", "book_id")):
            per_game.setdefault((scope, int(r[key]), int(r["gid"])), r)
    lines_of_game: dict[tuple[int, int], set[int]] = {}
    for r in rows:
        lines_of_game.setdefault((int(r["chapter_id"]), int(r["gid"])), set()).add(int(r["line_id"]))
    members: dict[tuple[str, int], list[Row]] = {}
    for (scope, sid, _), r in per_game.items():
        members.setdefault((scope, sid), []).append(r)
    for (scope, sid), rs in members.items():
        results = Counter(r["result"] for r in rs)
        statuses = Counter(r["status"] for r in rs)
        wins, draws, losses = results.get("win", 0), results.get("draw", 0), results.get("loss", 0)
        slips = Counter((r["own"] + 1, r["expected"], r["played"]) for r in rs if r["status"] == "me")
        slip = None
        if slips:
            (own, exp, pl), n = min(slips.items(), key=lambda kv: (-kv[1], kv[0]))
            slip = {"own_move": own, "expected": exp, "played": pl, "count": n}
        depth = Counter(int(r["own"]) for r in rs)
        last = max((r["played_at"] for r in rs if r["played_at"] is not None), default=None)
        scopes[scope][sid] = {
            "games": len(rs),
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "score": round((wins + draws / 2) / (wins + draws + losses), 3) if wins + draws + losses else None,
            "left_by_me": statuses.get("me", 0),
            "left_by_opponent": statuses.get("opponent", 0),
            "ended": statuses.get("ended", 0),
            "ran_out": statuses.get("ran_out", 0),
            "faithful": len(rs) - statuses.get("me", 0),
            "depth_reached": {str(k): v for k, v in sorted(depth.items())},
            "slip": slip,
            "last_played": last.isoformat() if last else None,
            "shared": scope == "line"
            and all(len(lines_of_game[(int(r["chapter_id"]), int(r["gid"]))]) > 1 for r in rs),
        }
    gaps: dict[int, Row] = {}
    for gid in {int(r["gid"]) for r in rows}:
        rep = min((r for r in rows if int(r["gid"]) == gid), key=_key)
        if rep["status"] == "opponent":
            gaps[gid] = {
                k: rep[k]
                for k in (
                    "m",
                    "book_id",
                    "book_title",
                    "chapter_id",
                    "chapter_title",
                    "line_id",
                    "line_name",
                    "expected",
                    "played",
                    "color",
                )
            }
    return scopes, gaps


def _plant(
    conn: psycopg.Connection[DictRow],
    gid: int,
    moves: list[str],
    *,
    colour: str,
    m: int,
    book: int,
    chapter: int,
    lines: list[int],
    result: str = "win",
    days_ago: float = 1,
) -> None:
    fens = h.game(conn, gid, moves, color=colour, result=result, days_ago=days_ago)
    mover = "white" if m % 2 == 0 else "black"
    by = "me" if mover == colour else "opponent"
    h.result(
        conn,
        gid,
        book_id=book,
        chapter_id=chapter,
        ply=m,
        by=by,
        expected=None,
        played=moves[m] if m < len(moves) else None,
        fen=fens[min(m, len(fens) - 1)],
        line_ids=lines,
    )


def test_the_sql_reduction_equals_the_python_reference_where_collations_disagree(
    clean: psycopg.Connection[DictRow],
) -> None:
    collation = clean.execute("SELECT datcollate FROM pg_database WHERE datname = current_database()").fetchone()
    assert collation is not None
    if str(collation["datcollate"]).upper() in ("C", "POSIX", "C.UTF-8", "C.UTF8"):
        pytest.fail(f"the scratch database collates under {collation['datcollate']}: this test proves nothing there")

    h.player(clean)
    h.book(clean, 1, "Italian", "white")
    h.chapter(clean, 3, 1, "Zebra")
    h.chapter(clean, 1, 1, "apple")
    h.chapter(clean, 2, 1, "Éclair")
    h.line(clean, 1, 3, "Z", ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"])
    h.line(clean, 2, 3, "Z2", ["e4", "e5", "a3", "Nc6", "Bc4"])
    h.line(clean, 3, 1, "b-line", ["e4", "e5", "a3", "Nc6", "Nf3"])
    h.line(clean, 4, 1, "B-line", ["e4", "e5", "a3", "Nc6", "Nc3"])
    h.line(clean, 5, 2, "É", ["e4", "e5", "Nc3", "Nf6"])
    # Game 1: the player leaves at ply 2 with h3, tied to Z (expects Nf3) and É (expects Nc3):
    # the book's slip is the first chapter's by code point, "Zebra" before "Éclair".
    _plant(
        clean,
        1,
        ["e4", "e5", "h3", "Nc6", "d4"],
        colour="white",
        m=2,
        book=1,
        chapter=3,
        lines=[1, 5],
        result="loss",
        days_ago=3,
    )
    # Game 2: the opponent leaves at ply 1 (d5), tied to every line: a gap game whose context is "Zebra" › Z.
    _plant(
        clean,
        2,
        ["e4", "d5", "exd5"],
        colour="white",
        m=1,
        book=1,
        chapter=3,
        lines=[1, 2, 3, 4, 5],
        result="draw",
        days_ago=2,
    )
    # Game 3: in "apple", tied to b-line (Nf3) and B-line (Nc3), left at ply 4: the chapter's
    # representative is "B-line" by code point ("B" < "b"), so its slip is Nc3.
    _plant(
        clean,
        3,
        ["e4", "e5", "a3", "Nc6", "h3", "d5"],
        colour="white",
        m=4,
        book=1,
        chapter=1,
        lines=[3, 4],
        result="win",
    )
    # Game 4: in "Zebra", tied to Z2 only, left at ply 2 (a3 expected): with game 1's (2, Nf3, h3)
    # the chapter's two slips are equally frequent and "Nf3" sorts before "a3" by code point.
    _plant(
        clean,
        4,
        ["e4", "e5", "h3", "Nf6"],
        colour="white",
        m=2,
        book=1,
        chapter=3,
        lines=[2],
        result="win",
        days_ago=0.5,
    )
    # Game 5 follows Z to its end and plays on (ran out); game 6 ends inside Z (ended in book).
    _plant(
        clean,
        5,
        ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6"],
        colour="white",
        m=6,
        book=1,
        chapter=3,
        lines=[1],
        result="win",
    )
    _plant(clean, 6, ["e4", "e5", "Nf3", "Nc6"], colour="white", m=4, book=1, chapter=3, lines=[1], result="draw")
    # A Black book, for the own-move arithmetic on the other parity.
    h.book(clean, 2, "Scandi", "black")
    h.chapter(clean, 4, 2, "Main")
    h.line(clean, 6, 4, "Qa5", ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qa5"])
    h.line(clean, 7, 4, "Qd8", ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd8"])
    _plant(
        clean,
        7,
        ["e4", "d5", "exd5", "Qxd5", "Nc3", "Qd6", "d4"],
        colour="black",
        m=5,
        book=2,
        chapter=4,
        lines=[6, 7],
        result="loss",
    )
    _plant(
        clean,
        8,
        ["e4", "d5", "exd5", "Qxd5", "Nf3", "Qa5"],
        colour="black",
        m=4,
        book=2,
        chapter=4,
        lines=[6, 7],
        result="win",
    )
    clean.commit()

    scopes, gap_games = report._reduced(clean, Settings(), "all")
    ref_scopes, ref_gaps = _reference(_raw_rows(clean))
    for scope in scopes:
        for sid in sorted(set(scopes[scope]) | set(ref_scopes[scope])):
            assert scopes[scope].get(sid) == ref_scopes[scope].get(sid), (scope, sid)
    assert {int(g["id"]): {k: g[k] for k in ref_gaps[int(g["id"])]} for g in gap_games} == ref_gaps
    # The cases the fixtures were built for, stated outright.
    assert ref_scopes["book"][1]["slip"] == {"own_move": 2, "expected": "Nf3", "played": "h3", "count": 1}
    assert ref_scopes["chapter"][1]["slip"] == {"own_move": 3, "expected": "Nc3", "played": "h3", "count": 1}
    assert ref_scopes["chapter"][3]["slip"] == {"own_move": 2, "expected": "Nf3", "played": "h3", "count": 1}
    assert ref_gaps[2]["chapter_title"] == "Zebra" and ref_gaps[2]["line_name"] == "Z"
    assert ref_gaps[8]["line_name"] == "Qa5" and ref_scopes["chapter"][4]["slip"]["own_move"] == 3
    assert ref_scopes["line"][1] == {**ref_scopes["line"][1], "games": 4, "ran_out": 1, "ended": 1, "shared": False}
    assert ref_scopes["line"][7]["shared"] is True
