"""The review detector: one game's stored analysis in, its review events out.

Pure: python-chess and arithmetic over `moves`, `fen_sequence`, `ply_analysis`, the game's
missed-opportunity rows and its repertoire match, all supplied by the caller. No database,
no engine, no clock, no randomness — identical inputs always give identical output. The
writer (`core/review/write.py`) owns persistence; this module owns detection, pricing,
merging and the stored base route.

The contract, in the terms the rest of the system uses:

- An event is a player DECISION: one per (game, anchor ply). Detectors contribute evidence
  that merges at the anchor; a mixed anchor is classed once by precedence missed-mate >
  material > missed-material > faded, the suppressed kinds kept in `evidence.secondary`.
- Cost is charged once per decision: `max(0, ES[a] - ES[a+1])` across the anchor move,
  capped at 100. `evidence.game_priced_loss` (the floored sum of every player-move drop)
  is a display diagnostic only.
- The anchor is the last moment the loss was avoidable: the player ply with the largest
  single-move expected-score drop in the eight plies before the detector's hit, never the
  capture ply itself.
- A material candidate (a drop of `review_material_candidate_drop` pawns against the best
  of the previous three positions) is confirmed only when both legs hold: the stored PV
  from that position replays to a settled endpoint (the game ends, or a bounded
  capture-resolution extension reaches a position with no material-gaining capture) where
  the deficit still holds, AND the anchor move's drop is at least `review_conf_es_drop`
  (`_depth12` when the game was analysed at depth <= 12). A missing or unreplayable PV, or
  an endpoint that never settles, is UNKNOWN: never an event, counted. A cliff (a
  player-move drop of 20 points) with no candidate nearby is probed the same way and becomes
  a "forced" material event when the PV proves the loss.
- Missed wins are the game's `player_motif_events` rows with `found IS FALSE` and
  `metric_type IN ('motif', 'mate')` at player plies, plus a replay clause for untagged
  geometry (the PV wins at least a minor piece settled and the played move shed at least
  `review_missed_win_shed` points).
- Faded advantage is one game-level event when the win-probability curve has papercut
  shape, its peak was at least `review_faded_peak_es`, no material event exists and the
  game was not won.
- Expected score prices each position by forced mate (`mate_in_moves`, signed, White's
  point of view, in moves: 100 for the player, 0 against), else by the win-probability
  sigmoid over the White-POV `eval` clamped to ±1000 and flipped to the player's side. Nothing
  prices a position from the board itself (docs/decisions/001); `es_authority_*` is `mate` or
  `sigmoid`.
- `base_route` is `endgame_technique` (a material event in the endgame phase at a board
  with at most five men, or a pawn ending), `lapse_defense`, `lapse_offense` or `faded`.
  `opening_candidate` is a material-classed event at or before the book exit plus
  `review_early_k_plies` (or `review_early_ply_cap` with no repertoire match) that carries a
  `pool_key` (`line:{book}:{chapter}:{line}` > `canon:{family}::{variation}` > `eco:{eco}`).
  `book_relation` is one of deviation_before | opponent_left | post_book | inside_line |
  no_repertoire. The displayed route (pool floors, filters) is a read-time concern.
- A clock-decided termination (`core.constants.CLOCK_DECIDED_TERMINATIONS`) returns
  `([], 0)` before anything else is read. Missing or misaligned `moves` / `fen_sequence` /
  `ply_analysis`, an unbuildable start position or a move that does not parse return
  `None`: the game cannot be tagged, and the writer leaves its rows alone.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, cast

import chess

from core.analysis.motifs import see_capture
from core.chess.board import starting_board
from core.constants import CLOCK_DECIDED_TERMINATIONS

# Bumped on any semantic change to detection, pricing or routing. A lineage stamp on
# review_events.config_version, never an identity: the replace is unconditional.
# 1 was the old detector, whose ladder had a first rung over the board (docs/decisions/001);
# 2 prices by mate and sigmoid only.
REVIEW_CONFIG_VERSION = 2

# --- constants the calibration fixed; the tunables arrive in `knobs` ----------------------
_WIN_SIGMOID_K = 0.00368208  # the win-probability constant
_CP_CLAMP = 1000
_CANDIDATE_REF_LOOKBACK = 3  # plies of pre-window reference for a material candidate
_CANDIDATE_GAP_PLIES = 4  # minimum plies between distinct material candidates
_ANCHOR_WINDOW_PLIES = 8  # how far back the anchor is looked for
_CLIFF_ES_DROP = 20.0  # the trapped/forced clause's trigger, in ES points
_MISSED_GAIN_MINOR = 3  # "wins at least a minor", in pawns
_PRICE_FLOOR_ES = 5.0  # game_priced_loss jitter floor
_PIECE_LABEL_WINDOW = 3  # plies around the capture that label the piece lost
# Shape thresholds (classification only, never severity).
_CLEAN_DD = 12.0
_CLIFF_MDD = 0.7
_CLIFF_M = 20.0
_PAPERCUT_MDD = 0.5
_PAPERCUT_DD = 20.0
# Phase-boundary heuristics.
_MIN_OPENING_PLY = 6
_MAX_OPENING_PLY = 24
_DEV_THRESHOLD = 6
_ENDGAME_PHASE_UNITS = 6
_ENDGAME_MAX_PIECES = 7
# A material event in the endgame is `endgame_technique` at a board this small (or a pawn
# ending): a material count, nothing more.
_DRILLABLE_MAX_MEN = 5

_VAL: dict[chess.PieceType, int] = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}

# Merge precedence at one anchor. Faded is game-level and folds beneath everything.
_PRECEDENCE = {"missed_mate": 0, "material": 1, "missed_material": 2, "faded": 3}

Evidence = dict[str, Any]
Event = dict[str, Any]


# --- win probability (player's point of view; stored evals are White's) ---------------------


def _win_pct(cp_white: Any, player_is_white: bool) -> float | None:
    if cp_white is None:
        return None
    cp = float(cp_white) if player_is_white else -float(cp_white)
    cp = max(-_CP_CLAMP, min(_CP_CLAMP, cp))
    return 100.0 / (1.0 + math.exp(-_WIN_SIGMOID_K * cp))


# --- FEN and phase helpers -----------------------------------------------------------------


def _placement(fen: str | None) -> str:
    return fen.split(" ", 1)[0] if fen else ""


def _piece_phase_units(placement: str) -> int:
    w = {"n": 1, "b": 1, "r": 2, "q": 4}
    return sum(w[c.lower()] for c in placement if c.lower() in w)


def _total_pieces(placement: str) -> int:
    return sum(1 for c in placement if c.isalpha())


def _queens_off(placement: str) -> bool:
    return ("Q" not in placement) and ("q" not in placement)


def _is_endgame(placement: str) -> bool:
    """Queens off, or phase units <= 6, or at most 7 pieces."""
    return (
        _queens_off(placement)
        or _piece_phase_units(placement) <= _ENDGAME_PHASE_UNITS
        or _total_pieces(placement) <= _ENDGAME_MAX_PIECES
    )


def _developed_count(placement: str) -> int:
    ranks = placement.split("/")
    if len(ranks) != 8:
        return 0
    white_dev = sum(1 for r in ranks[0:7] for c in r if c in "NBRQ")
    black_dev = sum(1 for r in ranks[1:8] for c in r if c in "nbrq")
    return white_dev + black_dev


def _heuristic_opening_exit(fens: list[str]) -> int:
    """Development-count book exit, used when the repertoire context supplies none."""
    n = len(fens)
    cap = min(_MAX_OPENING_PLY, n - 1)
    for i in range(n):
        if i < _MIN_OPENING_PLY:
            continue
        if i >= cap:
            return cap
        if _developed_count(_placement(fens[i])) >= _DEV_THRESHOLD:
            return i
    return cap


def _endgame_start_ply(fens: list[str]) -> int | None:
    """First position that is (and stays) an endgame, else None."""
    n = len(fens)
    for i in range(n):
        if _is_endgame(_placement(fens[i])):
            if i + 1 >= n or _is_endgame(_placement(fens[i + 1])):
                return i
    return None


# --- material and replay -------------------------------------------------------------------


def _material_balance(board: chess.Board, me: chess.Color) -> int:
    """Material in pawns from the player's side, kings excluded."""
    return sum(_VAL[p] * len(board.pieces(p, me)) for p in _VAL) - sum(
        _VAL[p] * len(board.pieces(p, not me)) for p in _VAL
    )


Replay = tuple[list[chess.Board], list[chess.PieceType | None], list[str | None], list[str]]


def _legal_move(board: chess.Board, token: str, *, uci: bool = False) -> chess.Move | None:
    """The legal move `token` names on `board`, or None. python-chess parses `--`, `Z0`, `0000`
    and `@@@@` as the null move without complaint; a pass is never a move here, in a game or
    in a PV."""
    try:
        move = board.parse_uci(token) if uci else board.parse_san(token)
    except Exception:
        return None
    return move if move and move in board.legal_moves else None


def _replay_moves(moves: list[str], board: chess.Board) -> Replay | None:
    """Replay the game's SAN on `board` (mutated). Per-position snapshots plus a 1-based
    capture log (`caps[k]` = the piece type move k-1 captured; `capturer[k]` = 'w'/'b') and
    the mover per ply — or None when a move does not parse or is a pass (fail closed)."""
    boards = [board.copy(stack=False)]
    caps: list[chess.PieceType | None] = [None]
    capturer: list[str | None] = [None]
    mover: list[str] = []
    for san in moves:
        mv = _legal_move(board, san)
        if mv is None:
            return None
        captured: chess.PieceType | None = None
        if board.is_capture(mv):
            if board.is_en_passant(mv):
                captured = chess.PAWN
            else:
                pc = board.piece_at(mv.to_square)
                captured = pc.piece_type if pc else None
        side = "w" if board.turn == chess.WHITE else "b"
        mover.append(side)
        capturer.append(side)
        board.push(mv)
        caps.append(captured)
        boards.append(board.copy(stack=False))
    return boards, caps, capturer, mover


def _best_gaining_capture(board: chess.Board) -> chess.Move | None:
    """The side to move's best immediately material-gaining capture: the legal capture with
    the largest positive king-guarded SEE, UCI string as the tie-break. None when no capture
    gains material — the settled-quiet condition."""
    best_mv: chess.Move | None = None
    best_gain = 0
    for mv in board.legal_moves:
        if not board.is_capture(mv):
            continue
        gain = see_capture(board, mv)
        if gain > best_gain or (gain == best_gain and gain > 0 and best_mv is not None and mv.uci() < best_mv.uci()):
            best_mv, best_gain = mv, gain
    return best_mv


Settled = tuple[str, chess.Board | None, int, int]

# A move number in a PV ("12." / "12..."), the one digit-led token that is skipped. `0000` is
# not one: it reaches the legality guard and ends the PV like the other null spellings.
_MOVE_NUMBER = re.compile(r"^\d+\.+$")


def _replay_pv_settled(board: chess.Board, pv_san: Any, quiesce_max: int) -> Settled:
    """Replay the stored SAN PV from `board` to a settled endpoint. The game's own
    continuation is never consulted.

    Returns (state, end_board, pv_plies, extension_plies), state one of settled_terminal |
    settled_quiet | unknown, end_board None when unknown. Move-number tokens ("12.", "12...")
    are skipped; any other token that is not a legal move in SAN or UCI — a null move in every
    spelling included — ends the replayable PV and the extension runs from there. Zero
    replayable plies is unknown. The extension is bounded: after `quiesce_max` plies the
    endpoint is settled only if the side to move has no gaining capture, else unknown
    (compensation beyond the bound is never confirmed)."""
    if not pv_san:
        return "unknown", None, 0, 0
    b = board.copy(stack=False)
    played = 0
    for tok in str(pv_san).split():
        tok = tok.strip()
        if not tok or _MOVE_NUMBER.match(tok):
            continue
        if b.is_game_over():
            break
        mv = _legal_move(b, tok) or _legal_move(b, tok, uci=True)
        if mv is None:
            break
        b.push(mv)
        played += 1
    if played == 0:
        return "unknown", None, 0, 0
    if b.is_game_over():
        return "settled_terminal", b, played, 0
    ext = 0
    while ext < quiesce_max:
        mv = _best_gaining_capture(b)
        if mv is None:
            return "settled_quiet", b, played, ext
        b.push(mv)
        ext += 1
        if b.is_game_over():
            return "settled_terminal", b, played, ext
    if _best_gaining_capture(b) is None:
        return "settled_quiet", b, played, ext
    return "unknown", None, played, ext


def _piece_label(
    caps: list[chess.PieceType | None],
    capturer: list[str | None],
    me_char: str,
    j: int,
    window: int = _PIECE_LABEL_WINDOW,
) -> str:
    """The largest player piece captured in [j-window, j+1] (1-based capture-log indices);
    a rook for a minor is 'exchange'."""
    lost: list[chess.PieceType] = []
    won: list[chess.PieceType] = []
    lo = max(1, j - window)
    hi = min(len(caps) - 1, j + 1)
    for k in range(lo, hi + 1):
        captured = caps[k]
        if captured is None:
            continue
        if capturer[k] != me_char:
            lost.append(captured)
        else:
            won.append(captured)
    if not lost:
        return "pawns"
    top = max(lost, key=lambda p: _VAL[p])
    if top == chess.QUEEN:
        return "queen"
    if top == chess.ROOK:
        return "exchange" if any(p in (chess.KNIGHT, chess.BISHOP) for p in won) else "rook"
    if top in (chess.KNIGHT, chess.BISHOP):
        return "minor"
    return "pawns"


# --- shape (classification only) -----------------------------------------------------------


def _drawdown(curve: list[float]) -> float:
    peak, dd = -1e9, 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak - v > dd:
            dd = peak - v
    return dd


def _shape_of(dd: float, m: float) -> tuple[str, float]:
    """clean | cliff | papercut | mixed, with the M/DD concentration."""
    conc = (m / dd) if dd > 0 else 0.0
    if dd < _CLEAN_DD:
        return "clean", conc
    if conc >= _CLIFF_MDD and m >= _CLIFF_M:
        return "cliff", conc
    if conc <= _PAPERCUT_MDD and dd >= _PAPERCUT_DD:
        return "papercut", conc
    return "mixed", conc


# --- expected score ------------------------------------------------------------------------


def _entry(ply_analysis: list[Any], i: int) -> dict[str, Any] | None:
    e: Any = ply_analysis[i]
    return cast(dict[str, Any], e) if isinstance(e, dict) else None


def _fill(values: list[float | None]) -> list[float]:
    """Forward-fill gaps; a leading gap takes the first known value; nothing known is 50."""
    out: list[float | None] = []
    last: float | None = None
    for v in values:
        if v is None:
            v = last
        out.append(v)
        if v is not None:
            last = v
    first = next((v for v in out if v is not None), 50.0)
    return [v if v is not None else first for v in out]


def _es_series(
    boards: list[chess.Board], ply_analysis: list[Any], player_is_white: bool
) -> tuple[list[float], list[str]]:
    """Per position: (expected score, authority), player's point of view. A forced mate
    (`mate_in_moves` present and non-zero; 0 is sign-ambiguous and treated as unknown) is
    100 for the player or 0 against, authority `mate`; else the sigmoid over `eval`,
    authority `sigmoid`, gaps forward-filled. `boards` is unused here; it is what a rung
    over the board would read, and the caller passes it so the series stays a function of
    the position list."""
    n = len(boards)
    es: list[float | None] = [None] * n
    auth = ["sigmoid"] * n
    for i in range(n):
        entry = _entry(ply_analysis, i)
        mim = entry.get("mate_in_moves") if entry else None
        if mim:
            player_mate = mim if player_is_white else -mim
            es[i] = 100.0 if player_mate > 0 else 0.0
            auth[i] = "mate"
            continue
        es[i] = _win_pct(entry.get("eval") if entry else None, player_is_white)
    return _fill(es), auth


def _sigmoid_series(ply_analysis: list[Any], n: int, player_is_white: bool) -> list[float]:
    """The pure win-probability curve, for shape classification only."""
    wp: list[float | None] = []
    for i in range(n):
        entry = _entry(ply_analysis, i)
        wp.append(_win_pct(entry.get("eval") if entry else None, player_is_white))
    return _fill(wp)


# --- pool identity, early, book relation ---------------------------------------------------


def _pool_key(game_ctx: dict[str, Any]) -> str | None:
    """Game-level, the same on every event: line > canonical pair > ECO > None."""
    rep = game_ctx.get("repertoire")
    if rep and all(rep.get(k) is not None for k in ("book_id", "chapter_id", "line_id")):
        return f"line:{rep['book_id']}:{rep['chapter_id']}:{rep['line_id']}"
    family = game_ctx.get("canonical_family")
    variation = game_ctx.get("canonical_variation")
    if family is not None and variation is not None:
        return f"canon:{family}::{variation}"
    eco = game_ctx.get("opening_eco")
    if eco:
        return f"eco:{eco}"
    return None


def _repertoire_exit_ply(rep: dict[str, Any] | None) -> int | None:
    """The deviation ply when the game left book, else the matched line's end; None when
    neither is known."""
    if not rep:
        return None
    dev = rep.get("deviated_at_ply")
    if isinstance(dev, int):
        return dev
    matched = rep.get("matched_ply")
    if isinstance(matched, int):
        return matched
    return None


def _book_relation(anchor_ply: int, rep: dict[str, Any] | None) -> str:
    if not rep:
        return "no_repertoire"
    dev = rep.get("deviated_at_ply")
    dev_by = rep.get("deviation_by")
    if dev is not None and dev <= anchor_ply:
        if dev_by in ("me", "player"):
            return "deviation_before"
        if dev_by in ("opponent", "opp"):
            return "opponent_left"
    matched = rep.get("matched_ply")
    if matched is not None and anchor_ply <= matched:
        return "inside_line"
    return "post_book"


# --- entry point ---------------------------------------------------------------------------


def tag_review_events(game_ctx: dict[str, Any], knobs: dict[str, int]) -> tuple[list[Event], int] | None:
    """Tag one game's review events.

    `game_ctx`: chess_game_id, moves (SAN list, or its JSON text), fen_sequence (len(moves)+1),
    ply_analysis (one dict per position — {ply, eval, best_move, best_line, mate_in_moves},
    eval and mate_in_moves White's point of view — or None), player_color ('white'|'black'),
    result ('win'|'loss'|'draw'|None), termination, variant, starting_fen, opening_eco,
    canonical_family, canonical_variation, analysis_depth (int|None), repertoire (None |
    {book_id, chapter_id, line_id, deviated_at_ply, deviation_by, expected_move, matched_ply,
    line_len}), motif_missed (list of {ply, metric_type ('motif'|'mate'), theme, mate_in_moves}).
    `knobs`: the eight `review_*` detection settings.

    Returns (events, unknown_candidate_count) — events sorted by anchor ply, each {anchor_ply,
    base_route, opening_candidate, pool_key, evidence, cost, phase, piece_label, book_relation,
    anchor_fen}; the count is the material candidates left UNKNOWN. `([], 0)` is an
    authoritative zero. None means the game could not be tagged (see the module page)."""
    if game_ctx.get("termination") in CLOCK_DECIDED_TERMINATIONS:
        return [], 0

    moves: Any = game_ctx.get("moves")
    if isinstance(moves, str):
        try:
            moves = json.loads(moves)
        except Exception:
            return None
    fens: Any = game_ctx.get("fen_sequence")
    ply_analysis: Any = game_ctx.get("ply_analysis")
    if not moves or not fens or not ply_analysis:
        return None
    n_pos = len(moves) + 1
    if len(fens) != n_pos or len(ply_analysis) != n_pos:
        return None

    player_is_white = game_ctx.get("player_color") == "white"
    me = chess.WHITE if player_is_white else chess.BLACK
    me_char = "w" if player_is_white else "b"
    result = game_ctx.get("result")
    rep: dict[str, Any] | None = game_ctx.get("repertoire")

    try:
        board = starting_board(game_ctx.get("starting_fen"), game_ctx.get("variant") or "standard")
    except Exception:
        return None
    replayed = _replay_moves(moves, board)
    if replayed is None:
        return None
    boards, caps, capturer, mover = replayed

    es, auth = _es_series(boards, ply_analysis, player_is_white)
    bal = [_material_balance(b, me) for b in boards]

    # Player-move expected-score drops: pricing and anchoring.
    drops: dict[int, float] = {}
    for i in range(n_pos - 1):
        if mover[i] == me_char:
            drops[i] = max(0.0, es[i] - es[i + 1])
    game_priced_loss = round(sum(d for d in drops.values() if d >= _PRICE_FLOOR_ES), 2)

    # Phase boundaries.
    exit_ref = _repertoire_exit_ply(rep)
    oe = exit_ref if exit_ref is not None else _heuristic_opening_exit(fens)
    eg = _endgame_start_ply(fens)
    if eg is not None and eg <= oe:
        eg = oe  # an endgame before the opening exit clamps to the exit ply

    def phase_of(i: int) -> str:
        if i < oe:
            return "opening"
        if eg is not None and i >= eg:
            return "endgame"
        return "middlegame"

    def pv_at(i: int) -> Any:
        entry = _entry(ply_analysis, i)
        return entry.get("best_line") if entry else None

    def anchor_for(k: int) -> tuple[int | None, float]:
        """The player ply with the largest single-move drop in [k-8, k]; the later ply wins
        a tie (the last moment avoidable). (None, 0.0) with no player drop in the window."""
        lo = max(0, k - _ANCHOR_WINDOW_PLIES)
        cand = [(drops[i], i) for i in range(lo, k + 1) if i in drops]
        if not cand:
            return None, 0.0
        d, i = max(cand)
        return i, d

    depth = game_ctx.get("analysis_depth")
    conf = knobs["review_conf_es_drop_depth12"] if depth is not None and depth <= 12 else knobs["review_conf_es_drop"]
    mat_drop_knob = knobs["review_material_candidate_drop"]
    quiesce_max = knobs["review_quiesce_max_plies"]

    evidence_items: list[Evidence] = []
    unknown_candidates = 0
    candidate_plies: list[int] = []  # every material candidate position, confirmed or not

    # Material candidates.
    last_cand_at = -(_CANDIDATE_GAP_PLIES + 99)
    for j in range(1, n_pos):
        ref = max(bal[max(0, j - _CANDIDATE_REF_LOOKBACK) : j])
        drop = ref - bal[j]
        if drop < mat_drop_knob or (j - last_cand_at) <= _CANDIDATE_GAP_PLIES:
            continue
        last_cand_at = j
        candidate_plies.append(j)
        state, end_board, pv_plies, ext_plies = _replay_pv_settled(boards[j], pv_at(j), quiesce_max)
        if state == "unknown" or end_board is None:
            unknown_candidates += 1
            continue
        deficit = ref - _material_balance(end_board, me)
        if deficit < mat_drop_knob:
            continue  # settled-compensated: the PV proves the material returns
        a, a_drop = anchor_for(j - 1)
        if a is None or a_drop < conf:
            continue  # the engine already prices the compensation
        evidence_items.append(
            {
                "kind": "material",
                "tag": "capture",
                "anchor_ply": a,
                "event_ply": j,
                "proof_state": state,
                "mat_drop_candidate": drop,
                "settled_deficit": deficit,
                "pv_plies": pv_plies,
                "extension_plies": ext_plies,
                "piece": _piece_label(caps, capturer, me_char, j),
            }
        )

    # The trapped/forced clause, under the same proof contract. "No candidate nearby" is
    # checked against every candidate position, confirmed, pruned or UNKNOWN: a cliff that
    # belongs to an UNKNOWN candidate is already counted and is not probed twice.
    for i in sorted(drops):
        d = drops[i]
        if d < _CLIFF_ES_DROP:
            continue
        if any(abs(p - i) <= _CANDIDATE_GAP_PLIES for p in candidate_plies):
            continue
        if i + 1 >= n_pos:
            continue
        state, end_board, pv_plies, ext_plies = _replay_pv_settled(boards[i + 1], pv_at(i + 1), quiesce_max)
        if state == "unknown" or end_board is None:
            unknown_candidates += 1
            continue
        forced_loss = bal[i] - _material_balance(end_board, me)
        if forced_loss < mat_drop_knob:
            continue
        evidence_items.append(
            {
                "kind": "material",
                "tag": "forced",
                "anchor_ply": i,
                "event_ply": i,
                "proof_state": state,
                "settled_deficit": forced_loss,
                "pv_plies": pv_plies,
                "extension_plies": ext_plies,
                "piece": "forced-loss",
            }
        )
        candidate_plies.append(i)

    # Missed wins: the tagger's rows, then the replay clause for untagged geometry.
    tagger_plies: set[int] = set()
    rows: list[dict[str, Any]] = list(game_ctx.get("motif_missed") or [])
    for row in rows:
        p = row.get("ply")
        if not isinstance(p, int) or not (0 <= p < n_pos - 1):
            continue
        if mover[p] != me_char:
            continue  # only the player's decisions are evidence
        metric = row.get("metric_type")
        if metric == "mate":
            tagger_plies.add(p)
            evidence_items.append(
                {
                    "kind": "missed_mate",
                    "source": "tagger",
                    "anchor_ply": p,
                    "event_ply": p,
                    "proof_state": "settled_terminal",  # a forced mate is terminal ground truth
                    "theme": "mate",
                    "mate_in_moves": row.get("mate_in_moves"),
                }
            )
        elif metric == "motif":
            tagger_plies.add(p)
            evidence_items.append(
                {
                    "kind": "missed_material",
                    "source": "motif",
                    "anchor_ply": p,
                    "event_ply": p,
                    "proof_state": "settled_quiet",  # the tagger's static material proof
                    "theme": row.get("theme"),
                }
            )
    shed = knobs["review_missed_win_shed"]
    for i in sorted(drops):
        if drops[i] < shed or i in tagger_plies:
            continue
        state, end_board, pv_plies, ext_plies = _replay_pv_settled(boards[i], pv_at(i), quiesce_max)
        if state == "unknown" or end_board is None:
            continue  # not a material candidate, so not counted either
        gained = _material_balance(end_board, me) - bal[i]
        if gained < _MISSED_GAIN_MINOR:
            continue
        evidence_items.append(
            {
                "kind": "missed_material",
                "source": "replay",
                "anchor_ply": i,
                "event_ply": i,
                "proof_state": state,
                "pv_gain": gained,
                "pv_plies": pv_plies,
                "extension_plies": ext_plies,
            }
        )

    # Faded advantage: game-level, shape from the pure sigmoid curve.
    wp = _sigmoid_series(ply_analysis, n_pos, player_is_white)
    sig_drops = [max(0.0, wp[i] - wp[i + 1]) for i in range(n_pos - 1) if mover[i] == me_char]
    m_metric = max(sig_drops) if sig_drops else 0.0
    dd_metric = _drawdown(wp)
    shape, conc = _shape_of(dd_metric, m_metric)
    peak_es = max(es)
    has_sharp = any(e["kind"] == "material" for e in evidence_items)
    if (
        shape == "papercut"
        and peak_es >= knobs["review_faded_peak_es"]
        and not has_sharp
        and result in ("loss", "draw")
        and drops
    ):
        _, fa = max((d, i) for i, d in drops.items())
        evidence_items.append(
            {
                "kind": "faded",
                "anchor_ply": fa,
                "event_ply": fa,
                "proof_state": None,  # a gradual bucket: no replay proof applies
                "peak_es": round(peak_es, 2),
                "shape": shape,
                "dd": round(dd_metric, 2),
                "m": round(m_metric, 2),
                "m_dd": round(conc, 2),
            }
        )

    # Merge at the anchor and assemble.
    pool_key = _pool_key(game_ctx)
    early_cap = exit_ref + knobs["review_early_k_plies"] if exit_ref is not None else knobs["review_early_ply_cap"]

    by_anchor: dict[int, list[Evidence]] = {}
    for item in evidence_items:
        by_anchor.setdefault(item["anchor_ply"], []).append(item)

    events: list[Event] = []
    for a in sorted(by_anchor):
        items = sorted(
            by_anchor[a],
            key=lambda e: (_PRECEDENCE[e["kind"]], e["event_ply"], e.get("source") or "", e.get("tag") or ""),
        )
        primary = items[0]
        kinds: list[str] = []
        for it in items:
            if it["kind"] not in kinds:
                kinds.append(it["kind"])
        secondary = [k for k in kinds if k != primary["kind"]]

        cost = round(min(100.0, max(0.0, es[a] - es[a + 1])), 2)
        phase = phase_of(a)
        kind = primary["kind"]
        piece_label: str | None
        if kind == "material":
            board_a = boards[a]
            men = chess.popcount(board_a.occupied)
            pawn_ending = all(pc.piece_type in (chess.KING, chess.PAWN) for pc in board_a.piece_map().values())
            drillable = men <= _DRILLABLE_MAX_MEN or pawn_ending
            base_route = "endgame_technique" if phase == "endgame" and drillable else "lapse_defense"
            piece_label = primary.get("piece")
        elif kind in ("missed_mate", "missed_material"):
            base_route = "lapse_offense"
            piece_label = "mate" if kind == "missed_mate" else primary.get("theme") or "material"
        else:
            base_route = "faded"
            piece_label = "faded"

        opening_candidate = bool(kind == "material" and pool_key is not None and a <= early_cap)

        evidence: Evidence = {
            "kinds": kinds,
            # The read side's seam: the primary class, its theme, the missed-mate marker and
            # the game's peak expected score (the same value on every event of the game).
            "class": primary["kind"],
            "theme": primary.get("theme"),
            "mate_in_moves": primary.get("mate_in_moves") if primary["kind"] == "missed_mate" else None,
            "game_peak_es": round(peak_es, 2),
            "proof_state": primary["proof_state"],
            "es_authority_before": auth[a],
            "es_authority_after": auth[a + 1],
            "es_before": round(es[a], 2),
            "es_after": round(es[a + 1], 2),
            "secondary": secondary,
            "game_priced_loss": game_priced_loss,
            "detectors": items,
        }
        events.append(
            {
                "anchor_ply": a,
                "base_route": base_route,
                "opening_candidate": opening_candidate,
                "pool_key": pool_key,
                "evidence": evidence,
                "cost": cost,
                "phase": phase,
                "piece_label": piece_label,
                "book_relation": _book_relation(a, rep),
                "anchor_fen": fens[a],
            }
        )

    return events, unknown_candidates
