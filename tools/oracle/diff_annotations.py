"""Line walk-through: the new projection of notes onto every line against the old one.

The old `_project_line_annotations` (pure) is lifted out of the archived source by its AST —
its module cannot be imported outside the old application — and fed the same candidate rows
from the old database; the new `core.repertoire.annotations.line_with_notes` runs on the
migrated scratch database. Compared ply by ply for every line of the player's: the note's
text, source, author, book title and `from_chapter`.

    python tools/oracle/diff_annotations.py --old-src /path/to/old-src   (the extracted archive)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

from common import oracle, report, scratch

from core.constants import PLAYER_ID
from core.repertoire import annotations


def lift(old_src: Path) -> Any:
    """The old `_project_line_annotations`, `_coerce_moves` and `normalize_fen`, compiled
    from their source files alone."""
    tree = ast.parse((old_src / "blundriq-api" / "db" / "repertoire.py").read_text())
    fu = ast.parse((old_src / "blundriq-api" / "fen_utils.py").read_text())
    wanted = {"_project_line_annotations", "_coerce_moves"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    nodes += [n for n in fu.body if isinstance(n, ast.FunctionDef) and n.name == "normalize_fen"]
    assert len(nodes) == 3, "old functions not found"
    module = ast.Module(body=[ast.parse("import json").body[0], *nodes], type_ignores=[])
    ns: dict[str, Any] = {}
    exec(compile(ast.fix_missing_locations(module), "old-projection", "exec"), ns)  # noqa: S102
    return ns["_project_line_annotations"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-src", required=True)
    args = ap.parse_args()
    old_project = lift(Path(args.old_src))
    diffs: list[str] = []
    checked = 0
    with oracle() as old, scratch() as new:
        lines = old.execute(
            "SELECT rl.id, rl.moves, rl.fen_sequence, ch.id AS chapter_id, ch.book_id"
            " FROM repertoire_lines rl JOIN chapters ch ON ch.id = rl.chapter_id JOIN books bk ON bk.id = ch.book_id"
            " WHERE bk.player_id = %s ORDER BY rl.id",
            (PLAYER_ID,),
        ).fetchall()
        candidates_by_book: dict[int, list[dict[str, Any]]] = {}
        for line in lines:
            book_id = int(line["book_id"])
            if book_id not in candidates_by_book:
                candidates_by_book[book_id] = [
                    dict(r)
                    for r in old.execute(
                        "SELECT ra.fen_norm, ra.text, ra.source, ra.author, ra.book_title, ra.line_id AS ann_line_id,"
                        " ra.updated_at, ra.id AS ann_id, rl2.moves, rl2.chapter_id AS ann_chapter_id,"
                        " ch2.title AS ann_chapter_title"
                        " FROM repertoire_annotations ra JOIN repertoire_lines rl2 ON rl2.id = ra.line_id"
                        " JOIN chapters ch2 ON ch2.id = rl2.chapter_id WHERE ra.player_id = %s AND ch2.book_id = %s",
                        (PLAYER_ID, book_id),
                    ).fetchall()
                ]
            expected = old_project(
                list(line["moves"]),
                list(line["fen_sequence"]),
                candidates_by_book[book_id],
                line["id"],
                line["chapter_id"],
            )
            got = annotations.line_with_notes(new, int(line["id"]))
            assert got is not None
            checked += len(expected)
            for e, g in zip(expected, got["positions"], strict=True):
                en = e["annotation"]
                gn = g["annotation"]
                if en is not None:  # the old schema named import sources; here every import is 'course'
                    en = {**en, "source": "manual" if en["source"] == "manual" else "course"}
                if en != gn or e["move"] != g["move"] or e["fen"] != g["fen"]:
                    diffs.append(f"line {line['id']} ply {e['ply']}: old {en} new {gn}")
    return report("line walk-through notes", diffs, checked)


if __name__ == "__main__":
    sys.exit(main())
