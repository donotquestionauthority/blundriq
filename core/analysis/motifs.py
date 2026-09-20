"""Deterministic tactical-motif and missed-mate tagger over a game's stored
ply_analysis (the engine's best move at every ply). No second engine pass.

Themes use the Lichess vocabulary so detections line up with the CC0 puzzle
corpus: fork, pin, skewer, hangingPiece, discoveredAttack, plus `mate`
(metric_type 'mate') and `untagged` (metric_type 'positional': a player error
with no tactical classification). An independent geometric implementation;
nothing is copied from Lichess's AGPL tagger.

Rules that matter when editing:
- Player plies only; ply_analysis covers both colours.
- One row per (opportunity, theme): a theme is anchored at the first player ply
  where the engine's best move carries it; continuations emit nothing.
- `found` is eval-tolerance ("kept the advantage": player-POV cp loss <=
  MOTIF_FOUND_CP_TOLERANCE), not move identity. A hangingPiece is also found
  when the played capture won material within `motif_found_material_tolerance`
  pawns of the best capture; such a row keeps its real cp loss.
- Every tactical detector must WIN material after the opponent's best reply
  (`_wins_via_targets`, a motif-scoped static check with a king-guarded SEE),
  at least `motif_min_material_gain` pawns. The move's own entry capture never
  counts for fork/pin/skewer/discovered; hangingPiece is a plain SEE capture.
- Stored evals are White-POV; mate distance comes from `mate_in_moves`
  (signed, in moves) or, for rows without it, from replaying `best_line`.
- An unparseable best move is UNKNOWN: emit nothing at that ply and reset
  anchoring. "Could not classify" is never "no theme".
"""

from __future__ import annotations

import json
from typing import Any

import chess

from core.chess.board import starting_board

POSITIONAL_MIN_CP_LOSS = 100
MOTIF_FOUND_CP_TOLERANCE = 50
TACTICAL_THEMES = ("fork", "pin", "skewer", "hangingPiece", "discoveredAttack")

_PIECE_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
_SLIDERS = (chess.BISHOP, chess.ROOK, chess.QUEEN)
_NO_COLLECTOR = -1

# {target_square: (collector_square | None, survivor_square | None)}
#   collector: a SECONDARY target is credited only when captured by this piece (the pinning slider).
#   survivor:  a PRIMARY target counts only while this piece (the forker / pinner) survives the reply.
TargetSpecs = dict[int, tuple[int | None, int | None]]
Instance = tuple[str, int, frozenset[int]]  # (theme, gain in pawns, target squares won)


def _value(piece_type: int) -> int:
    return _PIECE_VALUE.get(piece_type, 0)


def _piece_value_at(board: chess.Board, sq: int) -> int:
    p = board.piece_at(sq)
    return _value(p.piece_type) if p else 0


# --- static exchange evaluation -----------------------------------------------------


def _see(board: chess.Board, to_sq: int, side: bool) -> int:
    """Net pawns `side` wins by starting captures on to_sq (least valuable attacker
    first, x-ray aware). A king may not recapture a square the enemy still defends."""
    attackers = board.attackers(side, to_sq)
    if not attackers:
        return 0
    from_sq: int | None = None
    for s in sorted(attackers, key=lambda q: _piece_value_at(board, q)):
        p = board.piece_at(s)
        if p is not None and p.piece_type == chess.KING and board.attackers(not side, to_sq):
            continue
        from_sq = s
        break
    if from_sq is None:
        return 0
    captured_val = _piece_value_at(board, to_sq)
    b = board.copy(stack=False)
    mover = b.piece_at(from_sq)
    b.remove_piece_at(from_sq)
    b.set_piece_at(to_sq, mover)
    return max(0, captured_val - _see(b, to_sq, not side))


def _see_capture(board: chess.Board, move: chess.Move) -> int:
    """SEE of a capture from the mover's point of view; > 0 wins material."""
    to_sq = move.to_square
    captured_val = 1 if board.is_en_passant(move) else _piece_value_at(board, to_sq)
    b = board.copy(stack=False)
    mover = b.piece_at(move.from_square)
    b.remove_piece_at(move.from_square)
    if board.is_en_passant(move):
        b.remove_piece_at(to_sq + (-8 if board.turn == chess.WHITE else 8))
    b.set_piece_at(to_sq, mover)
    return captured_val - _see(b, to_sq, not board.turn)


# --- forced-reply winnability ------------------------------------------------------------


def _wins_via_targets(
    board: chess.Board, move: chess.Move, target_specs: TargetSpecs, count_move_gain: bool = True, min_gain: int = 1
) -> bool:
    """After `move`, does the player net >= min_gain pawns on a motif target against
    EVERY opponent reply? Per reply the best continuation is taken (max inside min);
    targets are followed if the reply moves them; the player's own pieces newly hung
    by the move are debited (the reply's grab plus one free grab after collection)."""
    player = board.turn
    enemy = not player
    after = board.copy(stack=False)
    after.push(move)
    move_gain = _see_capture(board, move) if (count_move_gain and board.is_capture(move)) else 0

    # Player pieces the move itself exposed (net-attacked after, not before). King excluded.
    exposed: dict[int, int] = {}
    for sq, piece in after.piece_map().items():
        if piece.color != player or piece.piece_type == chess.KING:
            continue
        post_see = _see(after, sq, enemy)
        if post_see <= 0:
            continue
        orig_sq = move.from_square if sq == move.to_square else sq
        if _see(board, orig_sq, enemy) > 0:
            continue
        exposed[sq] = post_see

    worst: int | None = None
    for reply in after.legal_moves:
        c = after.copy(stack=False)
        captured_sq = reply.to_square if after.is_capture(reply) else None
        captured_self_sq = captured_sq if (captured_sq is not None and captured_sq in exposed) else None
        c.push(reply)

        continuations: list[tuple[int, int]] = [(0, _NO_COLLECTOR)]  # (gain, collector square)
        for tsq, (collector_sq, survivor_sq) in target_specs.items():
            if survivor_sq is not None and captured_sq == survivor_sq:
                continue
            cur_sq = reply.to_square if reply.from_square == tsq else tsq
            tp = c.piece_at(cur_sq)
            if tp is None or tp.color != enemy:
                continue
            for cap in c.legal_moves:
                if cap.to_square != cur_sq:
                    continue
                if collector_sq is not None and cap.from_square != collector_sq:
                    continue
                continuations.append((_see_capture(c, cap), cap.from_square))

        best_net: int | None = None
        for gain, coll_sq in continuations:
            reply_secured = (
                exposed[captured_self_sq] if (captured_self_sq is not None and captured_self_sq != coll_sq) else 0
            )
            after_secured = 0
            for esq, val in exposed.items():
                if esq == coll_sq or esq == captured_self_sq:
                    continue
                if c.piece_at(esq) is None or _see(c, esq, enemy) <= 0:
                    continue
                after_secured = max(after_secured, val)
            net = move_gain + gain - (reply_secured + after_secured)
            if best_net is None or net > best_net:
                best_net = net
        assert best_net is not None
        if worst is None or best_net < worst:
            worst = best_net
    return worst is not None and worst >= min_gain


def _next_enemy_beyond(board: chess.Board, frm: int, thru: int, enemy: bool) -> int | None:
    """First piece on the ray frm->thru beyond thru, if it is an enemy piece."""
    ff, fr = chess.square_file(frm), chess.square_rank(frm)
    tf, tr = chess.square_file(thru), chess.square_rank(thru)
    df = (tf > ff) - (tf < ff)
    dr = (tr > fr) - (tr < fr)
    f, r = tf + df, tr + dr
    while 0 <= f < 8 and 0 <= r < 8:
        p = board.piece_at(chess.square(f, r))
        if p is not None:
            return chess.square(f, r) if p.color == enemy else None
        f += df
        r += dr
    return None


def _instance_gain_won(
    board: chess.Board, move: chess.Move, specs: TargetSpecs, min_gain: int
) -> tuple[int, frozenset[int]]:
    """(largest gain the mechanism still wins, the target squares it wins individually)."""
    after = board.copy(stack=False)
    after.push(move)
    hi = max((_piece_value_at(after, t) for t in specs), default=0)
    gain = 0
    g = min_gain
    while g <= hi:
        if _wins_via_targets(board, move, specs, count_move_gain=False, min_gain=g):
            gain = g
            g += 1
        else:
            break
    won = frozenset(
        t
        for t, spec in specs.items()
        if _wins_via_targets(board, move, {t: spec}, count_move_gain=False, min_gain=min_gain)
    )
    return gain, won


# --- detectors (board is the position BEFORE the player's move; board.turn is the player) ---


def _fork_instances(board: chess.Board, move: chess.Move, min_gain: int = 1) -> list[Instance]:
    """The mover threatens >= 2 enemy pieces (the king counts via check) and wins a prong
    against the best reply while surviving it."""
    enemy = not board.turn
    b = board.copy(stack=False)
    b.push(move)
    mover_sq = move.to_square
    if b.piece_at(mover_sq) is None:
        return []
    attacked = b.attacks(mover_sq) & b.occupied_co[enemy]
    if len(attacked) < 2:
        return []
    targets = {t for t in attacked if (p := b.piece_at(t)) is not None and p.piece_type != chess.KING}
    spec: TargetSpecs = {p: (None, mover_sq) for p in targets}
    if not _wins_via_targets(board, move, spec, count_move_gain=False, min_gain=min_gain):
        return []
    gain, won = _instance_gain_won(board, move, spec, min_gain)
    return [("fork", gain, won)]


def _ray_pair(
    board: chess.Board, origin: int, df: int, dr: int, enemy: bool
) -> tuple[tuple[int, chess.Piece], tuple[int, chess.Piece]] | None:
    """The first two occupied squares along a ray, iff both hold enemy pieces."""
    found: list[tuple[int, chess.Piece]] = []
    f = chess.square_file(origin) + df
    r = chess.square_rank(origin) + dr
    while 0 <= f < 8 and 0 <= r < 8:
        sq = chess.square(f, r)
        p = board.piece_at(sq)
        if p is not None:
            found.append((sq, p))
            if len(found) == 2:
                break
        f += df
        r += dr
    if len(found) < 2:
        return None
    (s1, p1), (s2, p2) = found
    if p1.color != enemy or p2.color != enemy:
        return None
    return (s1, p1), (s2, p2)


def _pin_skewer_instances(board: chess.Board, move: chess.Move, min_gain: int = 1) -> list[Instance]:
    """A slider newly attacks a front enemy piece with a second enemy piece behind it.
    Pin: back piece more valuable (front is primary, back secondary). Skewer: front more
    valuable (back secondary). Equal values are an ambiguous label and are skipped; a
    pinned pawn is not a pin tactic; a slider sliding along its existing ray creates nothing."""
    res: list[Instance] = []
    b = board.copy(stack=False)
    b.push(move)
    piece = b.piece_at(move.to_square)
    if piece is None or piece.piece_type not in _SLIDERS:
        return res
    slider_sq = move.to_square
    enemy = not board.turn
    newly = b.attacks(slider_sq) - board.attacks(move.from_square)
    diag = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    straight = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    dirs = diag if piece.piece_type == chess.BISHOP else straight if piece.piece_type == chess.ROOK else diag + straight
    for df, dr in dirs:
        pair = _ray_pair(b, slider_sq, df, dr, enemy)
        if pair is None:
            continue
        (s1, p1), (s2, p2) = pair
        if s1 not in newly:
            continue
        v1, v2 = _value(p1.piece_type), _value(p2.piece_type)
        if v2 > v1:
            if p1.piece_type == chess.PAWN:
                continue
            spec: TargetSpecs = {s1: (None, slider_sq), s2: (slider_sq, None)}
            if _wins_via_targets(board, move, spec, count_move_gain=False, min_gain=min_gain):
                gain, won = _instance_gain_won(board, move, spec, min_gain)
                res.append(("pin", gain, won))
        elif v1 > v2:
            spec = {s2: (slider_sq, None)}
            if _wins_via_targets(board, move, spec, count_move_gain=False, min_gain=min_gain):
                gain, won = _instance_gain_won(board, move, spec, min_gain)
                res.append(("skewer", gain, won))
    return res


def _is_hanging_capture(board: chess.Board, move: chess.Move, min_gain: int = 1) -> bool:
    return board.is_capture(move) and _see_capture(board, move) >= min_gain


def _is_discovered_attack(board: chess.Board, move: chess.Move, min_gain: int = 1) -> bool:
    """Moving the piece uncovers a friendly slider's line onto an enemy piece, and the
    uncovered mechanism wins material. A discovered check counts only if the moved
    piece's targets or the slider's skewer beyond the king win something."""
    player = board.turn
    enemy = not player
    from_sq = move.from_square
    b = board.copy(stack=False)
    b.push(move)
    sliders = b.pieces(chess.BISHOP, player) | b.pieces(chess.ROOK, player) | b.pieces(chess.QUEEN, player)
    sliders.discard(move.to_square)
    for s in sliders:
        newly = b.attacks(s) - board.attacks(s)
        for tsq in newly:
            target = b.piece_at(tsq)
            if target is None or target.color != enemy:
                continue
            if from_sq not in chess.SquareSet(chess.between(s, tsq)):
                continue
            if target.piece_type == chess.KING:
                moved_targets = {
                    t
                    for t in (b.attacks(move.to_square) & b.occupied_co[enemy])
                    if (p := b.piece_at(t)) is not None and p.piece_type != chess.KING
                }
                specs: TargetSpecs = {t: (None, move.to_square) for t in moved_targets}
                back = _next_enemy_beyond(b, s, tsq, enemy)
                if back is not None:
                    specs[back] = (s, None)
                if specs and _wins_via_targets(board, move, specs, count_move_gain=False, min_gain=min_gain):
                    return True
                continue
            if _wins_via_targets(board, move, {tsq: (s, None)}, count_move_gain=False, min_gain=min_gain):
                return True
    return False


def _suppress_parasites(instances: list[Instance]) -> list[Instance]:
    """Drop an instance when another wins strictly more material and already wins every
    square it wins. Ties and partial overlaps are kept; an empty won-set is never dominated."""
    survivors: list[Instance] = []
    for i, (th_i, g_i, won_i) in enumerate(instances):
        dominated = any(
            j != i and g_j > g_i and bool(won_i) and won_i <= won_j for j, (_, g_j, won_j) in enumerate(instances)
        )
        if not dominated:
            survivors.append((th_i, g_i, won_i))
    return survivors


def _geometry(board: chess.Board, best: chess.Move, min_gain: int) -> list[Instance]:
    return _suppress_parasites(_fork_instances(board, best, min_gain) + _pin_skewer_instances(board, best, min_gain))


def detect_motifs(board: chess.Board, best: chess.Move, min_gain: int = 1) -> set[str]:
    """All tactical themes the engine's best move wins at this position."""
    themes = {theme for theme, _, _ in _geometry(board, best, min_gain)}
    if _is_hanging_capture(board, best, min_gain):
        themes.add("hangingPiece")
    if _is_discovered_attack(board, best, min_gain):
        themes.add("discoveredAttack")
    return themes


def won_target_squares(board: chess.Board, best: chess.Move, min_gain: int = 1) -> frozenset[int]:
    """Target squares the best move's tactics win (puzzle solution truncation)."""
    squares: set[int] = set()
    for _, _, won in _geometry(board, best, min_gain):
        squares |= set(won)
    if _is_hanging_capture(board, best, min_gain):
        squares.add(best.to_square)
    return frozenset(squares)


# --- mate distance and cp loss -----------------------------------------------------------


def _replay_mate_distance(board: chess.Board, best_line_san: str | None, player_is_white: bool) -> int | None:
    """Moves to a player-delivered mate by replaying the PV, else None (unknown)."""
    if not best_line_san:
        return None
    b = board.copy(stack=False)
    plies = 0
    for tok in best_line_san.split():
        try:
            b.push_san(tok)
        except Exception:
            return None
        plies += 1
        if b.is_checkmate():
            return (plies + 1) // 2 if plies % 2 == 1 else None
    return None


def _missed_mate_distance(pa: dict[str, Any], board: chess.Board, player_is_white: bool) -> int | None:
    if "mate_in_moves" in pa:
        m = pa["mate_in_moves"]
        if m is None:
            return None
        player_mate = int(m) if player_is_white else -int(m)
        return player_mate if player_mate > 0 else None
    return _replay_mate_distance(board, pa.get("best_line"), player_is_white)


def _cp_loss(pa_by_ply: dict[int, dict[str, Any]], ply: int, player_is_white: bool) -> int | None:
    e0 = pa_by_ply.get(ply)
    e1 = pa_by_ply.get(ply + 1)
    if not e0 or not e1:
        return None
    v0, v1 = e0.get("eval"), e1.get("eval")
    if v0 is None or v1 is None:
        return None
    loss = (int(v0) - int(v1)) if player_is_white else (int(v1) - int(v0))
    return max(0, loss)


# --- entry point ---------------------------------------------------------------------------


def tag_game(
    ply_analysis: list[dict[str, Any]],
    moves: list[str] | str,
    player_color: str,
    variant: str,
    starting_fen: str | None,
    *,
    motif_min_material_gain: int = 1,
    motif_found_material_tolerance: int = 3,
) -> list[dict[str, Any]]:
    """Event rows for one game: {ply, metric_type, theme, found, mate_in_moves, cp_loss, player_color}."""
    if isinstance(moves, str):
        moves = json.loads(moves)
    if not moves or not ply_analysis:
        return []
    pa_by_ply: dict[int, dict[str, Any]] = {int(e["ply"]): e for e in ply_analysis if "ply" in e}
    try:
        board = starting_board(starting_fen, variant or "standard")
    except ValueError:
        return []
    player_is_white = player_color == "white"

    rows: list[dict[str, Any]] = []
    prev_available: set[str] = set()
    for ply, san in enumerate(moves):
        try:
            move = board.parse_san(san)
        except Exception:
            break
        if (ply % 2 == 0) != player_is_white:
            board.push(move)
            continue

        pa = pa_by_ply.get(ply)
        best: chess.Move | None = None
        if pa is not None and pa.get("best_move"):
            try:
                best = board.parse_san(str(pa["best_move"]))
            except Exception:
                best = None
        if pa is None or best is None:
            prev_available = set()
            board.push(move)
            continue

        cp = _cp_loss(pa_by_ply, ply, player_is_white)
        now_available = detect_motifs(board, best, motif_min_material_gain)
        mate_dist = _missed_mate_distance(pa, board, player_is_white)
        if mate_dist is not None:
            now_available.add("mate")
        found = (cp <= MOTIF_FOUND_CP_TOLERANCE) if cp is not None else None

        for theme in now_available - prev_available:
            if found is None:
                continue
            theme_found = found
            material_found = False
            if theme == "hangingPiece" and not theme_found and board.is_capture(move) and board.is_capture(best):
                target_value = _see_capture(board, best)
                won = _see_capture(board, move)
                if won > 0 and (target_value - won) <= motif_found_material_tolerance:
                    theme_found = True
                    material_found = True
            row_cp = 0 if (theme_found and not material_found) else cp
            rows.append(
                {
                    "ply": ply,
                    "metric_type": "mate" if theme == "mate" else "motif",
                    "theme": theme,
                    "found": theme_found,
                    "mate_in_moves": mate_dist if theme == "mate" else None,
                    "cp_loss": row_cp,
                    "player_color": player_color,
                }
            )

        if not now_available and cp is not None and cp > 0 and cp >= POSITIONAL_MIN_CP_LOSS:
            rows.append(
                {
                    "ply": ply,
                    "metric_type": "positional",
                    "theme": "untagged",
                    "found": None,
                    "mate_in_moves": None,
                    "cp_loss": cp,
                    "player_color": player_color,
                }
            )
        prev_available = now_available
        board.push(move)
    return rows
