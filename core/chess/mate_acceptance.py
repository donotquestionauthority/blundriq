"""Forced mate in exactly N: the acceptance map, and the verdict that reads it.

A missed-mate puzzle asks the player to find the mate they missed, and a solve is
correct only if it forces mate in exactly the optimal distance N*. Every competing
optimal line is right; a forced mate one move longer is wrong. A single stored
solution line cannot say that, so generation runs an exhaustive bounded mate solve —
a complete minimax to mate, engine-free and deterministic, not a Stockfish PV — and
flattens the result into a map:

    {"v": 1, "n": N*,
     "p": {"<epd>": ["<uci>", ...]},   player to move -> the competing-optimal moves
     "d": {"<epd>": "<uci>"}}          opponent to move -> the one canonical defence

Keys are `board.epd()` — placement, side, castling, en passant — so transpositions
merge into one node and the move counters cannot fork one. Because every opponent
node carries exactly one defence, a replay reproduces the line the player saw.

The canonical defence is the reply that maximises resistance (the resulting
forced-mate distance), tie-broken by the lexicographically smallest UCI. That rule
is **frozen**: it is what makes a stored map and a fresh rebuild identical, so
changing it means versioning maps (`MAP_VERSION`) before any regeneration.

`build_acceptance_map` returns None rather than a weaker puzzle. A position is
skipped when the exhaustive distance is not the stored `mate_in_moves` (Stockfish
stored a non-optimal mate), when the map would exceed `ACCEPTANCE_MAP_CAP_BYTES`, or
when the solve exceeds `MAX_NODES`. There is no second contract to fall back to.

`walk_acceptance_map` is the verdict, used by the API when grading an attempt. It is
pure: no database, no engine. It re-walks the submitted moves from the puzzle's own
stored map, plays the map's own defences, and never trusts a client's claim or a
client-played reply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import chess

from core.chess.san import parse as parse_san

# Map format version. Bump only together with a versioning migration.
MAP_VERSION = 1

# No forced mate within the search bound.
_INF = 1 << 30

# A map larger than this, or a solve costing more nodes than this, skips the puzzle.
# Both are generous against what the corpus actually needs (the 2026 sizing pass over
# the live own_mate rows peaked at 333 bytes and 39,423 nodes, all at N* <= 3), so a
# deeper missed-mate window does not start skipping positions.
ACCEPTANCE_MAP_CAP_BYTES = 16_384
MAX_NODES = 300_000

AcceptanceMap = dict[str, object]
"""The stored map. Values are heterogeneous (an int, two dicts), so the walker
narrows each one before use rather than trusting the shape of a stored row."""


class BudgetExceeded(Exception):
    """The exhaustive solve visited more nodes than its budget. The node count is
    deterministic, so a position that blows the budget is skipped identically
    everywhere."""


@dataclass
class _Search:
    """One bounded mate solve: its memo and its node budget.

    The memo key carries the bound because each entry is exact only for the budget
    it was computed under.
    """

    max_nodes: int = MAX_NODES
    nodes: int = 0
    memo: dict[tuple[str, str, int], int] = field(default_factory=lambda: {})

    def visit(self) -> None:
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise BudgetExceeded


def _ordered_attacker_moves(board: chess.Board) -> list[chess.Move]:
    """Checks, then captures, then the rest. Ordering only: the distance and the
    accepted set are the same whatever the order, but finding a good candidate early
    lets the bound tightening in `_attacker_dist` cut the remaining branches."""
    checks: list[chess.Move] = []
    captures: list[chess.Move] = []
    rest: list[chess.Move] = []
    for move in board.legal_moves:
        if board.gives_check(move):
            checks.append(move)
        elif board.is_capture(move):
            captures.append(move)
        else:
            rest.append(move)
    return checks + captures + rest


def _attacker_dist(board: chess.Board, bound: int, search: _Search) -> int:
    """Attacker to move: the fewest attacker moves that force mate, or `_INF` if mate
    is not forced within `bound` attacker moves.

    Once a candidate `best` exists, deeper children are searched only to the budget
    that could still beat it, so the search never goes deeper than the minimal mate.
    """
    if bound <= 0:
        return _INF
    key = (board.epd(), "a", bound)
    cached = search.memo.get(key)
    if cached is not None:
        return cached
    search.visit()

    best = _INF
    for move in _ordered_attacker_moves(board):
        board.push(move)
        try:
            if board.is_checkmate():
                best = 1  # immediate mate cannot be beaten
                break
            child_bound = bound - 1 if best == _INF else min(bound - 1, best - 2)
            if child_bound < 0:
                continue
            distance = _defender_dist(board, child_bound, search)
        finally:
            board.pop()
        if distance != _INF and 1 + distance < best:
            best = 1 + distance
    search.memo[key] = best
    return best


def _defender_dist(board: chess.Board, bound: int, search: _Search) -> int:
    """Defender to move: the attacker moves still needed under the defender's
    longest-resisting reply, or `_INF` if any reply escapes — a forced mate has to
    beat every defence."""
    key = (board.epd(), "d", bound)
    cached = search.memo.get(key)
    if cached is not None:
        return cached
    search.visit()

    moves = list(board.legal_moves)
    if not moves:
        # The attacker side returns 1 on checkmate before recursing here, so no legal
        # moves at this point means stalemate: not a forced mate.
        search.memo[key] = _INF
        return _INF

    worst = 0
    for move in moves:
        board.push(move)
        try:
            distance = _attacker_dist(board, bound, search)
        finally:
            board.pop()
        if distance == _INF:
            worst = _INF
            break
        worst = max(worst, distance)
    search.memo[key] = worst
    return worst


def _accepted_player_moves(board: chess.Board, remaining: int, search: _Search) -> list[str]:
    """At a player node whose minimal remaining distance is `remaining` player moves,
    the sorted UCI list of moves that achieve it — the competing-optimal set."""
    accepted: list[str] = []
    for move in board.legal_moves:
        board.push(move)
        try:
            if remaining == 1:
                optimal = board.is_checkmate()
            else:
                optimal = not board.is_checkmate() and _defender_dist(board, remaining - 1, search) == remaining - 1
        finally:
            board.pop()
        if optimal:
            accepted.append(move.uci())
    accepted.sort()
    return accepted


def _canonical_defense(board: chess.Board, bound: int, search: _Search) -> str | None:
    """The frozen tie-break: maximise resistance, then smallest UCI. None if some reply
    escapes mate, which means the node was not forced after all."""
    best_move: str | None = None
    best_resist = -1
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        board.push(move)
        try:
            distance = _attacker_dist(board, bound, search)
        finally:
            board.pop()
        if distance == _INF:
            return None
        # Ascending UCI order plus a strict `>` means the smallest UCI wins a tie.
        if distance > best_resist:
            best_resist = distance
            best_move = move.uci()
    return best_move


@dataclass
class BuildStats:
    """Why a position was or was not turned into a puzzle, for the generator's summary."""

    skip_reason: str = "bad_input"
    nodes: int = 0
    map_bytes: int = 0


def build_acceptance_map(
    fen: str,
    n_star: int,
    *,
    cap_bytes: int | None = ACCEPTANCE_MAP_CAP_BYTES,
    max_nodes: int = MAX_NODES,
    stats: BuildStats | None = None,
) -> AcceptanceMap | None:
    """The acceptance map for a missed-mate position, or None to skip it.

    `n_star` is the stored `mate_in_moves`, and it is the authority: the exhaustive
    solve must agree with it, or the position is skipped rather than served with a
    map that contradicts its own puzzle.
    """
    if stats is not None:
        stats.skip_reason = "bad_input"
        stats.nodes = 0
        stats.map_bytes = 0
    if isinstance(n_star, bool) or not 1 <= n_star <= 5:
        return None
    try:
        start = chess.Board(fen)
    except (ValueError, AssertionError):
        return None
    if start.is_game_over():
        return None

    search = _Search(max_nodes=max_nodes)
    try:
        built = _build_verified(start, n_star, search, cap_bytes, stats)
    except BudgetExceeded:
        if stats is not None:
            stats.skip_reason = "budget"
            stats.nodes = search.nodes
        return None
    if stats is not None:
        stats.nodes = search.nodes
    return built


def _build_verified(
    start: chess.Board,
    n_star: int,
    search: _Search,
    cap_bytes: int | None,
    stats: BuildStats | None,
) -> AcceptanceMap | None:
    def skip(reason: str) -> None:
        if stats is not None:
            stats.skip_reason = reason
        return None

    # Iterative deepening, so the search never goes past the true minimal distance.
    # A hit below n_star means a shorter forced mate exists and the stored distance was
    # not optimal; no hit at all means the mate is longer than n_star or not forced.
    true_distance: int | None = None
    for depth in range(1, n_star + 1):
        if _attacker_dist(start, depth, search) == depth:
            true_distance = depth
            break
    if true_distance != n_star:
        return skip("distance_mismatch")

    player_nodes: dict[str, list[str]] = {}
    defence_nodes: dict[str, str] = {}

    # Breadth-first over reachable nodes: branch on every accepted move at a player
    # node, follow only the canonical defence at an opponent node.
    frontier: list[tuple[chess.Board, int]] = [(start, n_star)]
    while frontier:
        board, remaining = frontier.pop()
        epd = board.epd()
        if epd in player_nodes:
            continue  # already expanded: a transposition

        accepted = _accepted_player_moves(board, remaining, search)
        if not accepted:
            return skip("inconsistent")
        player_nodes[epd] = accepted

        for uci in accepted:
            board.push(chess.Move.from_uci(uci))
            try:
                if board.is_checkmate():
                    continue  # terminal: no opponent node follows
                opponent_epd = board.epd()
                defence = defence_nodes.get(opponent_epd)
                if defence is None:
                    found = _canonical_defense(board, remaining - 1, search)
                    if found is None:
                        return skip("inconsistent")
                    defence_nodes[opponent_epd] = found
                    defence = found
                board.push(chess.Move.from_uci(defence))
                try:
                    if board.is_game_over():
                        # The mate has to be delivered by the player, so a defence that
                        # ends the game is inconsistent with a forced mate in N*.
                        return skip("inconsistent")
                    frontier.append((board.copy(stack=False), remaining - 1))
                finally:
                    board.pop()
            finally:
                board.pop()

    built: AcceptanceMap = {"v": MAP_VERSION, "n": n_star, "p": player_nodes, "d": defence_nodes}
    size = len(json.dumps(built, separators=(",", ":")).encode("utf-8"))
    if stats is not None:
        stats.map_bytes = size
    if cap_bytes is not None and size > cap_bytes:
        return skip("oversize")
    if stats is not None:
        stats.skip_reason = "ok"
    return built


def walk_acceptance_map(fen: str, acceptance_map: object, submitted_player_sans: list[str]) -> bool:
    """True iff the submitted moves are an optimal solve of this puzzle.

    Every submitted move must be accepted at its node, the map's own canonical
    defences interleave, mate is delivered on the N*-th move, and there are exactly
    N* moves. Anything malformed, illegal or missing is False.
    """
    if not isinstance(acceptance_map, dict):
        return False
    stored: dict[str, Any] = {str(k): v for k, v in acceptance_map.items()}  # type: ignore[union-attr]
    player_nodes = stored.get("p")
    defence_nodes = stored.get("d")
    n_star = stored.get("n")
    if not isinstance(player_nodes, dict) or not isinstance(defence_nodes, dict):
        return False
    if isinstance(n_star, bool) or not isinstance(n_star, int) or n_star < 1:
        return False
    if not submitted_player_sans or len(submitted_player_sans) != n_star:
        return False

    try:
        board = chess.Board(fen)
    except (ValueError, AssertionError):
        return False

    for index, san_text in enumerate(submitted_player_sans):
        accepted = player_nodes.get(board.epd())  # type: ignore[union-attr]
        if not isinstance(accepted, list) or not accepted:
            return False
        move = parse_san(board, san_text)
        if move is None or move.uci() not in accepted:
            return False
        board.push(move)

        if board.is_checkmate():
            return index == n_star - 1  # mate must land on the final move
        if index == n_star - 1:
            return False  # the last move did not mate

        defence = defence_nodes.get(board.epd())  # type: ignore[union-attr]
        if not isinstance(defence, str) or not defence:
            return False
        try:
            board.push(chess.Move.from_uci(defence))
        except (ValueError, AssertionError):
            return False
        if board.is_game_over():
            return False

    return False
