"""Similar positions: the near neighbourhood of a board inside the effectively-active repertoire.

The corpus is every node of every effectively-active line of the book colour that is to move in
the query (a player-turn node has the book's colour to move, and a same-material node has the
query's side to move, so a line of the other colour cannot contribute). Ply 0 is not a node — it
has no arriving move — and a node is admitted only if its stored move parses against its stored
parent FEN, so every neighbour on the wire says how it arose.

The search runs in a fixed order, and the order is the contract: (1) a GIN probe on
`material_keys` picks candidate lines by the query's material hash; (2) the hash-matched plies of
those lines that SQL cannot prove to be beyond the distance have their material *signature*
computed in SQL and compared as text against the query's — a hash is a prefilter, never an
identity, and the CASE computes the signature for those elements only (for every ply of every
candidate line it takes seconds on a full repertoire); only those plies, with the
elements around them, leave the database; (3) only then the placement distance, dedup by board
text, grouping, ranking and the cap. Nothing downstream ever sees a ply that failed step 2.

Distance is the number of the 64 squares whose occupant differs: a piece moving is 2,
castling 4, an en passant capture 3, so odd distances exist. The query board itself is not
excluded (distance 0 means the position is in the repertoire).

Per board the occurrences are grouped by the canonical move the repertoire plays next —
`read.canonicalise` on the pool, then per occurrence when the pool fails so an unreadable token
is attributed to the line that carries it — plus `end_of_line` for terminal nodes; the groups
are ordered (status, move, tie-break) and enumerated, never reduced to one: this surface shows
what the repertoire says, `project_ply` decides. `board_prep_divergent` is two distinct lines
prescribing different moves at the board. The cap counts boards, and a board is returned with
all of its groups or not at all.
"""

from __future__ import annotations

from typing import Any, cast

import chess
from psycopg import Connection

from core.constants import PLAYER_ID
from core.repertoire.annotations import normalize_fen
from core.repertoire.read import canonicalise, side_to_move, tie_break

Row = dict[str, Any]

# Distance domain: the setting lives in 1..MAX_DISTANCE_CEILING and a caller may only narrow it.
MAX_DISTANCE_CEILING = 8

PREP_STATUS_ORDER = {"move": 0, "end_of_line": 1, "unreadable": 2}

_FILES = "abcdefgh"


# --- the placement metric ------------------------------------------------------------------


def expand_placement(fen: str) -> str:
    """FEN field 1 as exactly 64 characters, rank 8 first, file a first, '.' for empty.
    ValueError on anything else: a malformed placement never becomes a comparable key."""
    field1 = fen.strip().split(" ")[0] if fen and fen.strip() else ""
    ranks = field1.split("/")
    if len(ranks) != 8:
        raise ValueError(f"expected 8 ranks, got {len(ranks)}")
    out: list[str] = []
    for r in ranks:
        row = "".join("." * int(ch) if ch.isdigit() else ch for ch in r)
        if len(row) != 8:
            raise ValueError(f"rank {r!r} expands to {len(row)} squares")
        out.append(row)
    return "".join(out)


def square_name(idx: int) -> str:
    if not 0 <= idx < 64:
        raise ValueError(f"square index {idx} out of range")
    return f"{_FILES[idx % 8]}{8 - idx // 8}"


def placement_distance(a: str, b: str, cutoff: int | None = None) -> int:
    """Squares whose occupant differs. With `cutoff`, `cutoff + 1` is returned as soon as the
    count exceeds it — a lower bound, never a measurement."""
    if len(a) != 64 or len(b) != 64:
        raise ValueError("both placements must be 64 characters")
    n = 0
    for x, y in zip(a, b, strict=True):
        if x != y:
            n += 1
            if cutoff is not None and n > cutoff:
                return cutoff + 1
    return n


def diff_squares(a: str, b: str) -> list[Row]:
    """[{square, from, to}] per differing square; `from` is the query's occupant, `to` the
    neighbour's, None for empty."""
    return [
        {"square": square_name(i), "from": a[i] if a[i] != "." else None, "to": b[i] if b[i] != "." else None}
        for i in range(64)
        if a[i] != b[i]
    ]


def castling_rights(fen: str) -> frozenset[str]:
    parts = fen.strip().split(" ")
    if len(parts) < 3 or parts[2] == "-":
        return frozenset()
    return frozenset(parts[2])


def looks_like_castling(a: str, b: str) -> bool:
    """Is the difference consistent with one side having castled (at least four squares, a
    king and a rook among them)? Its own label because castled-vs-not is otherwise filed as
    "two pieces different"."""
    diffs = [(a[i], b[i]) for i in range(64) if a[i] != b[i]]
    if len(diffs) < 4:
        return False
    kings = any(x in "Kk" or y in "Kk" for x, y in diffs)
    rooks = any(x in "Rr" or y in "Rr" for x, y in diffs)
    return kings and rooks


# --- moves on the wire ---------------------------------------------------------------------


def parse_arriving(parent_fen: str | None, san: str | None) -> Row | None:
    """The stored move parsed against the stored parent position and re-emitted canonically,
    with its squares (the king's, for castling). None when either is missing or the move does
    not parse: the node is then outside the corpus. Never derived from a placement diff, which
    cannot express a promotion piece and mishandles castling."""
    if not parent_fen or not san:
        return None
    try:
        parent = chess.Board(parent_fen)
        move = parent.parse_san(san)
    except ValueError:
        return None
    if not move:
        return None  # python-chess parses '--' / 'Z0' / '0000' as the null move: not an arriving move
    return {
        "san": parent.san(move),
        "from": chess.square_name(move.from_square),
        "to": chess.square_name(move.to_square),
        "promotion": chess.piece_symbol(move.promotion) if move.promotion else None,
        "is_castling": parent.is_castling(move),
        "is_en_passant": parent.is_en_passant(move),
    }


def _tokens(v: Any) -> list[str | None] | None:
    """A stored JSON array as tokens; a null element stays None (a terminal, not a token)."""
    return [None if x is None else str(x) for x in cast(list[Any], v)] if isinstance(v, list) else None


class NoSignature(ValueError):
    """The query FEN has no material signature (an unreadable side-to-move field)."""


# --- SQL: prefilter and signature, one authority ------------------------------------------

_QUERY_SIG_SQL = "SELECT bq_material_sig(%(fen)s) AS sig, bq_material_key(%(fen)s) AS key"

# A ply's placement, FEN field 1 with the ranks run together and every digit spelled out as that
# many '.', the way `expand_placement` reads it. Only a key-matched ply other than ply 0 is
# expanded; one is dropped only when its expansion is 64 squares of the standard alphabet and more
# than %(max_distance)s of them differ from the query's (`expand_placement` of it, bound as
# %(qplacement)s). Any placement SQL cannot read that way is kept for `expand_and_verify` to judge,
# so for every ply `expand_and_verify` admits the SQL distance is the same count and the ply is never
# dropped here.
#
# Each surviving line comes back sparse: `moves` and `fen_sequence` hold the stored element at every
# index `expand_and_verify` reads for a kept ply i (fen i - 1 and i, move i - 1 and i), JSON null
# elsewhere, cut after the last kept ply; `material_sigs` holds the signature at the kept plies only;
# `moves_len`, `fens_len` and `sigs_len` are the stored lengths the lists are padded back to. A
# `moves` that is not an array is returned as stored. A line with no kept ply is not returned.
_CANDIDATE_LINES_SQL = r"""
SELECT rl.id AS line_id, rl.line_name, rl.is_alternative, ch.title AS chapter_title, bk.title AS book_title,
       CASE WHEN jsonb_typeof(rl.moves) = 'array' THEN jsonb_array_length(rl.moves) END AS moves_len,
       CASE WHEN jsonb_typeof(rl.fen_sequence) = 'array' THEN jsonb_array_length(rl.fen_sequence) END AS fens_len,
       cardinality(rl.material_keys) AS sigs_len,
       CASE WHEN jsonb_typeof(rl.moves) = 'array' THEN
              COALESCE((SELECT jsonb_agg(CASE WHEN g.n = ANY(kp.plies) OR g.n + 1 = ANY(kp.plies)
                                              THEN rl.moves -> g.n ELSE 'null'::jsonb END ORDER BY g.n)
                          FROM generate_series(0, LEAST(kp.last, jsonb_array_length(rl.moves) - 1)) AS g(n)),
                       '[]'::jsonb)
            ELSE rl.moves END AS moves,
       (SELECT jsonb_agg(CASE WHEN g.n = ANY(kp.plies) OR g.n + 1 = ANY(kp.plies)
                              THEN rl.fen_sequence -> g.n ELSE 'null'::jsonb END ORDER BY g.n)
          FROM generate_series(0, kp.last) AS g(n)) AS fen_sequence,
       (SELECT array_agg(CASE WHEN g.n = ANY(kp.plies) THEN bq_material_sig(rl.fen_sequence->>g.n) END ORDER BY g.n)
          FROM generate_series(0, kp.last) AS g(n)) AS material_sigs
FROM repertoire_lines rl
JOIN chapters ch ON ch.id = rl.chapter_id
JOIN books bk ON bk.id = ch.book_id
CROSS JOIN LATERAL (
    SELECT array_agg((k.ord - 1)::int ORDER BY k.ord) AS plies, max(k.ord - 1)::int AS last
    FROM unnest(rl.material_keys) WITH ORDINALITY AS k(key, ord)
    CROSS JOIN LATERAL (
        SELECT replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(
                 split_part(regexp_replace(rl.fen_sequence->>(k.ord::int - 1), '^\s+', ''), ' ', 1),
                 '/', ''), '0', ''), '1', '.'), '2', '..'), '3', '...'), '4', '....'), '5', '.....'),
                 '6', '......'), '7', '.......'), '8', '........'), '9', '.........') AS placement
        OFFSET 0  -- a fence: the expansion is computed once per ply, not once per square counted
    ) e
    WHERE k.key = %(qkey)s AND k.ord > 1
      AND NOT (e.placement ~ '^[.PNBRQKpnbrqk]{64}$'
               AND (SELECT count(*) FROM generate_series(1, 64) AS s(n)
                     WHERE substr(e.placement, s.n, 1) <> substr(%(qplacement)s, s.n, 1)) > %(max_distance)s)
) kp
WHERE bk.player_id = %(pid)s AND rl.active AND ch.active AND bk.active
  AND bk.color = %(book_color)s
  AND rl.material_keys && ARRAY[%(qkey)s::bigint]
  AND kp.last IS NOT NULL
ORDER BY rl.id
"""


def query_material(conn: Connection[Any], fen: str) -> tuple[str, int]:
    """(signature text, hash) of the query, both from the SQL authority. ValueError when the
    signature is NULL — a FEN with an unreadable side to move — so the route answers 400."""
    with conn.cursor() as cur:
        cur.execute(_QUERY_SIG_SQL, {"fen": fen})
        row = cur.fetchone()
    if row is None or row["sig"] is None or row["key"] is None:
        raise NoSignature("query FEN has no material signature")
    return str(row["sig"]), int(row["key"])


# --- corpus expansion: verify, distance, admission -----------------------------------------


def _padded(items: list[Any] | None, length: int | None) -> list[Any] | None:
    """`items` extended with None to the stored `length` the SQL reports (a sparse list is cut
    after its last needed element); unchanged when there is no length or nothing to add."""
    if items is None or length is None or len(items) >= length:
        return items
    return items + [None] * (length - len(items))


def expand_and_verify(rows: list[Row], query_fen: str, query_sig: str, max_distance: int) -> list[Row]:
    """Candidate line rows to the verified, distance-qualified occurrences ("carriers"). A row
    whose lists are sparse (`_CANDIDATE_LINES_SQL`) is padded back to its stored lengths first, so
    every check below sees the lengths the line has. The exact-signature test runs first on every
    ply; a NULL signature (a hash-unmatched or prefiltered element, or a malformed stored FEN)
    never equals a real one, so all of those fail here."""
    query_stm = side_to_move(query_fen)
    query_placement = expand_placement(query_fen)
    carriers: list[Row] = []
    for r in rows:
        moves = _padded(_tokens(r["moves"]), r.get("moves_len"))
        fens = _padded(_tokens(r["fen_sequence"]), r.get("fens_len"))
        sigs = _padded(cast(list[str | None] | None, r["material_sigs"]), r.get("sigs_len"))
        if not moves or not fens or sigs is None:
            continue
        if len(fens) != len(moves) + 1 or len(sigs) != len(fens):
            continue  # a row breaking the length invariant is skipped, never repaired
        for i in range(1, len(fens)):
            if sigs[i] != query_sig:
                continue
            fen = fens[i]
            if fen is None:
                continue
            try:
                if side_to_move(fen) != query_stm:
                    continue
                placement = expand_placement(fen)
                board_key = normalize_fen(fen)
            except ValueError:
                continue
            d = placement_distance(query_placement, placement, cutoff=max_distance)
            if d > max_distance:
                continue
            arriving = parse_arriving(fens[i - 1], moves[i - 1])
            if arriving is None:
                continue
            carriers.append(
                {
                    "book": r["book_title"],
                    "chapter": r["chapter_title"],
                    "line_name": r["line_name"],
                    "line_id": r["line_id"],
                    "line_ply": i,
                    "is_alternative": bool(r["is_alternative"]),
                    "expected_move": moves[i] if i < len(moves) else None,
                    "fen": fen,
                    "board_key": board_key,
                    "placement": placement,
                    "distance": d,
                    "arriving": arriving,
                }
            )
    return carriers


# --- grouping: enumerate, never reduce ------------------------------------------------------


def _canonicalise_isolated(rep_fen: str, continuing: list[Row]) -> tuple[list[Row], list[Row]]:
    """(pool, failures): `canonicalise` over the whole pool, and only when that raises, once per
    occurrence so each failure is attributed to the occurrence that caused it."""
    try:
        return canonicalise(rep_fen, continuing), []
    except ValueError:
        pool: list[Row] = []
        failures: list[Row] = []
        for c in continuing:
            try:
                pool.extend(canonicalise(rep_fen, [c]))
            except ValueError:
                failures.append(c)
        return pool, failures


def _group_entry(
    status: str,
    rep: Row,
    members: list[Row],
    *,
    prep_move: str | None = None,
    raw_token: str | None = None,
    canonical_query_move: str | None = None,
) -> Row:
    return {
        "prep_move": prep_move,
        "prep_status": status,
        "prep_raw_token": raw_token,
        "is_queried_move": status == "move" and canonical_query_move is not None and prep_move == canonical_query_move,
        "arriving": rep["arriving"],
        "book_title": rep["book"],
        "chapter_title": rep["chapter"],
        "line_name": rep["line_name"],
        "line_id": rep["line_id"],
        "line_ply": rep["line_ply"],
        "is_alternative": rep["is_alternative"],
        "carried_by_line_count": len({m["line_id"] for m in members}),
        "_rep_key": tie_break(rep),
    }


def prep_groups(rep_fen: str, carriers: list[Row], canonical_query_move: str | None = None) -> tuple[list[Row], bool]:
    """The groups at one board and whether distinct lines disagree there. Continuing
    occurrences group by canonical move, terminal ones as `end_of_line`, unparseable tokens
    as `unreadable` keyed by the raw token (two lines with the same corrupt token collapse,
    distinct tokens stay distinct — surfaced, never dropped). Divergence counts move groups
    only, across distinct lines: a terminal node has no move to disagree with, and one line
    reaching a board twice is two occurrences, not a disagreement."""
    continuing = [c for c in carriers if c["expected_move"] is not None]
    terminal = [c for c in carriers if c["expected_move"] is None]
    pool, failures = _canonicalise_isolated(rep_fen, continuing) if continuing else ([], [])

    groups: list[Row] = []
    by_move: dict[str, list[Row]] = {}
    for c in pool:
        by_move.setdefault(str(c["canonical_move"]), []).append(c)
    for move, members in by_move.items():
        rep = min(members, key=tie_break)
        groups.append(_group_entry("move", rep, members, prep_move=move, canonical_query_move=canonical_query_move))
    if terminal:
        groups.append(_group_entry("end_of_line", min(terminal, key=tie_break), terminal))
    by_token: dict[str, list[Row]] = {}
    for c in failures:
        by_token.setdefault(str(c["expected_move"]), []).append(c)
    for token, members in by_token.items():
        groups.append(_group_entry("unreadable", min(members, key=tie_break), members, raw_token=token))

    divergent = any(
        a["canonical_move"] != b["canonical_move"] and a.get("line_id") != b.get("line_id")
        for i, a in enumerate(pool)
        for b in pool[i + 1 :]
    )
    groups.sort(
        key=lambda g: (PREP_STATUS_ORDER[g["prep_status"]], g["prep_move"] or g["prep_raw_token"] or "", g["_rep_key"])
    )
    for g in groups:
        del g["_rep_key"]
    return groups, divergent


def _build_board(query_placement: str, query_fen: str, canonical_query_move: str | None, carriers: list[Row]) -> Row:
    rep = min(carriers, key=tie_break)
    placement = str(carriers[0]["placement"])
    groups, divergent = prep_groups(str(rep["fen"]), carriers, canonical_query_move)
    return {
        "fen": rep["fen"],
        "distance": carriers[0]["distance"],
        "same_material": True,  # always, by step 2; on the wire because the shape names it
        "castling_delta": sorted(castling_rights(query_fen) ^ castling_rights(str(rep["fen"]))),
        "is_castle_shape": looks_like_castling(query_placement, placement),
        "diff_squares": diff_squares(query_placement, placement),
        "board_prep_divergent": divergent,
        "groups": groups,
        "_rep_key": tie_break(rep),
    }


def assemble_response(
    query_fen: str, canonical_query_move: str | None, carriers: list[Row], max_distance: int, max_positions: int
) -> Row:
    """Carriers to the wire shape: one entry per board (dedup by board text), each with all of
    its groups, ordered (distance, tie-break of the board's representative), capped in boards."""
    query_placement = expand_placement(query_fen)
    by_board: dict[str, list[Row]] = {}
    for c in carriers:
        by_board.setdefault(str(c["board_key"]), []).append(c)
    boards = [_build_board(query_placement, query_fen, canonical_query_move, m) for m in by_board.values()]
    boards.sort(key=lambda b: (b["distance"], b["_rep_key"]))
    for b in boards:
        del b["_rep_key"]
    retained = boards[:max_positions]
    omitted = len(boards) - len(retained)
    return {
        "query": {
            "fen": query_fen,
            "move": canonical_query_move,
            "max_distance": max_distance,
            "max_positions": max_positions,
        },
        "truncated": omitted > 0,
        "positions_omitted": omitted,
        "neighbours": retained,
    }


def candidate_rows(conn: Connection[Any], fen: str, max_distance: int) -> tuple[str, list[Row]]:
    """(the query's signature, `_CANDIDATE_LINES_SQL`'s rows) for `fen`: the candidate lines of
    the book colour to move, sparse, ready for `expand_and_verify`."""
    query_sig, query_key = query_material(conn, fen)
    book_color = "white" if side_to_move(fen) == "w" else "black"
    params = {
        "pid": PLAYER_ID,
        "qkey": query_key,
        "book_color": book_color,
        "qplacement": expand_placement(fen),
        "max_distance": max_distance,
    }
    with conn.cursor() as cur:
        cur.execute(_CANDIDATE_LINES_SQL, params)
        rows = [dict(r) for r in cur.fetchall()]
    return query_sig, rows


def similar_positions(
    conn: Connection[Any], fen: str, canonical_query_move: str | None, *, max_distance: int, max_positions: int
) -> Row:
    """The whole search for one query. `fen` is python-chess-serialised by the route; the
    query move is already canonical SAN or None; the bounds are the route-resolved values."""
    if not 1 <= max_distance <= MAX_DISTANCE_CEILING or max_positions < 1:
        raise ValueError("search bounds out of domain")
    query_sig, rows = candidate_rows(conn, fen, max_distance)
    carriers = expand_and_verify(rows, fen, query_sig, max_distance)
    return assemble_response(fen, canonical_query_move, carriers, max_distance, max_positions)
