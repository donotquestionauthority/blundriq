"""The Lichess CC0 puzzle corpus: a capped sample of it, and how it is loaded.

Lichess publishes about six million CC0 puzzles as a CSV. Keeping all of them would
cost more than the whole database is allowed, and would not make practice better: what
the motif trainer needs is a deep-enough pool of *good* puzzles at each theme and
rating. So the import keeps the top `cc0_import_cap_per_cell` by popularity in each
(served theme x rating bucket) cell inside the imported rating range, and throws the
rest away while streaming, never holding the file in memory.

The CSV's FEN is the position *before* the opponent's setup move, and `Moves[0]` is
that move. What the solver sees is the position after it, so that is what is stored,
with the remaining moves converted from UCI to SAN. The heavy chess work runs on the
survivors only, never on six million rows.

Two things make a reload safe rather than merely repeatable. An empty sample aborts
before the delete, so a bad CSV cannot wipe a working corpus. And the reload ends with
`VACUUM (ANALYZE)` plus a check that the covering index is still there and still being
used: a wholesale delete and insert leaves the visibility map empty, and without it the
index-only scan the serve path depends on quietly stops happening — measurably worse
than having no index at all.

The corpus is reference data. It is never migrated between databases; it is rebuilt
from the CSV, which is deterministic, and no puzzle depends on it once materialised.
"""

from __future__ import annotations

import csv
import heapq
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import chess
from psycopg import Connection

from core.constants import CC0_SERVE_THEMES
from core.settings import Settings

# Column positions in the published CSV:
# PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,OpeningTags
_ID, _FEN, _MOVES, _RATING, _POPULARITY, _NB_PLAYS, _THEMES = 0, 1, 2, 3, 5, 6, 7

TOPK_INDEX = "ix_lichess_puzzles_topk"

# pg_index.indoption bits. `popularity DESC` means DESC NULLS FIRST, which is what the
# serve's ORDER BY asks for; an index built NULLS LAST cannot satisfy it and the
# index-only scan silently disappears, so the NULLS bit is checked like any other.
_DESC_FIRST = 0x0001 | 0x0002
_ASC_LAST = 0
TOPK_KEY_COLUMNS = (("popularity", _DESC_FIRST), ("nb_plays", _DESC_FIRST), ("puzzle_id", _ASC_LAST))
TOPK_INCLUDED_COLUMNS = ("fen", "rating", "themes", "color", "solution_line")

# Below this many rows a sequential scan is the right plan, so the planner probe at the
# end of a reload is only meaningful on a corpus of a realistic size.
_PLAN_PROBE_MIN_ROWS = 1000


@dataclass
class Candidate:
    puzzle_id: str
    csv_fen: str
    moves: list[str]
    rating: int
    rating_bucket: int
    popularity: int
    nb_plays: int
    themes: list[str]


@dataclass
class Row:
    """A corpus row as stored: the position the solver sees, and the solution in SAN."""

    puzzle_id: str
    fen: str
    solution_line: list[str]
    color: str
    rating: int
    rating_bucket: int
    popularity: int
    nb_plays: int
    themes: list[str]


@dataclass
class ImportStats:
    scanned: int = 0
    sampled: int = 0
    cells: int = 0
    unreplayable: int = 0
    loaded: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=lambda: [])

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "sampled": self.sampled,
            "cells": self.cells,
            "unreplayable": self.unreplayable,
            "loaded": self.loaded,
            "failed": self.failed,
            "notes": self.notes,
        }


def sample(rows: Iterable[list[str]], config: Settings, stats: ImportStats) -> dict[str, Candidate]:
    """Stream the CSV, keeping the best `cap_per_cell` puzzles per theme and bucket.

    One bounded min-heap per cell, keyed `(popularity, nb_plays, puzzle_id)` so ties
    break deterministically and the same CSV always yields the same sample. A puzzle
    can sit in several theme cells at one rating, so an evicted payload is dropped only
    once no cell still holds it.
    """
    served = set(CC0_SERVE_THEMES)
    cap = config.cc0_import_cap_per_cell
    bucket_size = config.cc0_rating_bucket_size
    heaps: dict[tuple[str, int], list[tuple[int, int, str]]] = {}
    payloads: dict[str, Candidate] = {}

    for index, fields in enumerate(rows):
        if index == 0 and fields and fields[_ID] == "PuzzleId":
            continue
        stats.scanned += 1
        try:
            rating = int(fields[_RATING])
            if not config.cc0_import_rating_min <= rating <= config.cc0_import_rating_max:
                continue
            themes = set(fields[_THEMES].split())
            matched = themes & served
            if not matched:
                continue
            moves = fields[_MOVES].split()
            if len(moves) < 2:
                continue  # the setup move plus at least one move of solution
            candidate = Candidate(
                puzzle_id=fields[_ID],
                csv_fen=fields[_FEN],
                moves=moves,
                rating=rating,
                rating_bucket=(rating // bucket_size) * bucket_size,
                popularity=int(fields[_POPULARITY]),
                nb_plays=int(fields[_NB_PLAYS]),
                themes=sorted(themes),
            )
        except (IndexError, ValueError):
            continue

        for theme in matched:
            cell = heaps.setdefault((theme, candidate.rating_bucket), [])
            heapq.heappush(cell, (candidate.popularity, candidate.nb_plays, candidate.puzzle_id))
            payloads[candidate.puzzle_id] = candidate
            if len(cell) > cap:
                _, _, evicted = heapq.heappop(cell)
                if not _still_held(evicted, payloads, heaps):
                    payloads.pop(evicted, None)

    survivors: dict[str, Candidate] = {}
    for cell_heap in heaps.values():
        for _, _, puzzle_id in cell_heap:
            held = payloads.get(puzzle_id)
            if held is not None:
                survivors[puzzle_id] = held
    stats.sampled = len(survivors)
    stats.cells = len(heaps)
    return survivors


def _still_held(
    puzzle_id: str,
    payloads: dict[str, Candidate],
    heaps: dict[tuple[str, int], list[tuple[int, int, str]]],
) -> bool:
    candidate = payloads.get(puzzle_id)
    if candidate is None:
        return False
    for theme in candidate.themes:
        cell = heaps.get((theme, candidate.rating_bucket))
        if cell and any(entry[2] == puzzle_id for entry in cell):
            return True
    return False


def materialise(survivors: dict[str, Candidate], stats: ImportStats) -> list[Row]:
    """Turn survivors into stored rows: apply the setup move, convert the rest to SAN."""
    rows: list[Row] = []
    for candidate in survivors.values():
        try:
            board = chess.Board(candidate.csv_fen)
            board.push_uci(candidate.moves[0])
            presented = board.fen()
            color = "w" if board.turn == chess.WHITE else "b"
            solution: list[str] = []
            for uci in candidate.moves[1:]:
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(uci)  # python-chess will happily push an illegal move
                solution.append(board.san(move))
                board.push(move)
        except (ValueError, AssertionError, IndexError):
            stats.unreplayable += 1
            continue
        if not solution:
            stats.unreplayable += 1
            continue
        rows.append(
            Row(
                puzzle_id=candidate.puzzle_id,
                fen=presented,
                solution_line=solution,
                color=color,
                rating=candidate.rating,
                rating_bucket=candidate.rating_bucket,
                popularity=candidate.popularity,
                nb_plays=candidate.nb_plays,
                themes=candidate.themes,
            )
        )
    return rows


def read_csv(path: str) -> Iterator[list[str]]:
    """Rows of the published CSV. The operator decompresses the `.zst` first; there is
    no reason to carry a compression dependency for a file that is read once a year."""
    csv.field_size_limit(10 * 1024 * 1024)
    with open(path, newline="", encoding="utf-8") as handle:
        yield from csv.reader(handle)


def load(conn: Connection[Any], rows: list[Row], stats: ImportStats) -> None:
    """Replace the corpus. Refuses to delete anything when the sample is empty."""
    if not rows:
        stats.failed += 1
        stats.notes.append("sample is empty; the existing corpus was left in place")
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM lichess_puzzles")
        for row in rows:
            cur.execute(
                "INSERT INTO lichess_puzzles (puzzle_id, fen, solution_line, color, rating,"
                " rating_bucket, popularity, nb_plays, themes)"
                " VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::text[])",
                (
                    row.puzzle_id,
                    row.fen,
                    json.dumps(row.solution_line),
                    row.color,
                    row.rating,
                    row.rating_bucket,
                    row.popularity,
                    row.nb_plays,
                    row.themes,
                ),
            )
    conn.commit()
    stats.loaded = len(rows)


def verify_topk_index(conn: Connection[Any]) -> None:
    """Vacuum the reloaded table and prove the serve path's index is still usable.

    Read as ordered structure from the catalogue, not as substrings of the DDL: an
    index with the same column names in a different order, or made unique, partial or
    expression-based, contains every substring a naive check would look for and is
    still the wrong index. Raises, so the step is recorded as failed.
    """
    previous = conn.autocommit
    try:
        conn.autocommit = True  # VACUUM cannot run inside a transaction
        conn.execute("VACUUM (ANALYZE) lichess_puzzles")
    finally:
        conn.autocommit = previous

    row = conn.execute(
        """
        SELECT i.indisvalid, i.indisready, i.indislive, i.indisunique,
               i.indnkeyatts, i.indnatts, i.indoption::int[] AS options,
               i.indkey::int[] AS columns, am.amname,
               i.indexprs IS NOT NULL AS has_expressions,
               i.indpred IS NOT NULL AS is_partial,
               i.indrelid
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_am am ON am.oid = c.relam
        WHERE c.relname = %s
        """,
        (TOPK_INDEX,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"corpus index {TOPK_INDEX} is missing")
    if not (row["indisvalid"] and row["indisready"] and row["indislive"]):
        raise RuntimeError(f"corpus index {TOPK_INDEX} is not usable")
    if row["amname"] != "btree" or row["indisunique"] or row["has_expressions"] or row["is_partial"]:
        raise RuntimeError(f"corpus index {TOPK_INDEX} has the wrong shape")

    names = _attribute_names(conn, int(row["indrelid"]), [int(c) for c in row["columns"]])
    options = [int(o) for o in row["options"]]
    keys = tuple(zip(names[: int(row["indnkeyatts"])], options, strict=True))
    included = tuple(names[int(row["indnkeyatts"]) : int(row["indnatts"])])
    if keys != TOPK_KEY_COLUMNS or included != TOPK_INCLUDED_COLUMNS:
        raise RuntimeError(f"corpus index {TOPK_INDEX} does not match the expected columns: {keys} include {included}")

    size = conn.execute("SELECT count(*) AS n FROM lichess_puzzles").fetchone()
    if size is None or int(size["n"]) < _PLAN_PROBE_MIN_ROWS:
        # Below this the planner rightly prefers a sequential scan and the probe would
        # say nothing about the real corpus.
        return
    plan = conn.execute(
        "EXPLAIN SELECT fen, rating, themes, color, solution_line FROM lichess_puzzles"
        " WHERE themes && ARRAY['fork','pin']::text[] AND rating BETWEEN 800 AND 2400"
        " ORDER BY popularity DESC, nb_plays DESC, puzzle_id LIMIT 40"
    ).fetchall()
    text = " ".join(str(line["QUERY PLAN"]) for line in plan)
    if "Index Only Scan" not in text or TOPK_INDEX not in text:
        raise RuntimeError("corpus top-k probe no longer uses an index-only scan")


def _attribute_names(conn: Connection[Any], table_oid: int, attnums: list[int]) -> list[str]:
    rows = conn.execute(
        "SELECT attnum, attname FROM pg_attribute WHERE attrelid = %s AND attnum = ANY(%s)",
        (table_oid, attnums),
    ).fetchall()
    by_num = {int(r["attnum"]): str(r["attname"]) for r in rows}
    return [by_num.get(num, "<expression>") for num in attnums]


def import_corpus(conn: Connection[Any], config: Settings, csv_path: str) -> dict[str, Any]:
    """Rebuild the corpus from the published CSV."""
    stats = ImportStats()
    survivors = sample(read_csv(csv_path), config, stats)
    rows = materialise(survivors, stats)
    load(conn, rows, stats)
    if stats.loaded:
        verify_topk_index(conn)
    return stats.as_dict()
