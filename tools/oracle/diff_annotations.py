"""Line walk-through: the new projection of notes onto every line against the old one.

The old `_project_line_annotations` (pure) is lifted out of the archived source by its AST —
its module cannot be imported outside the old application — and fed the same candidate rows
from the old database (read by `core.oracle`, where the reference SQL lives); the new
`core.repertoire.annotations.line_with_notes` runs on the migrated scratch database. Compared ply by ply for every line of the player's: the note's
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

from core import oracle as q
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
        lines = q.old_repertoire_lines(old)
        candidates_by_book: dict[int, list[dict[str, Any]]] = {}
        for line in lines:
            book_id = int(line["book_id"])
            if book_id not in candidates_by_book:
                candidates_by_book[book_id] = q.old_book_notes(old, book_id)
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
