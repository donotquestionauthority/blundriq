"""Engineering constants: things that need a code change anyway.

Tunables the user may want to change from the Preferences page belong in
core/settings.py, not here.
"""

# The single player. There is no multi-user branch anywhere; this is a
# constant so the SQL stays portable and the value is never guessed.
PLAYER_ID = 1

# Stockfish depth for the scheduled analyzer (Rob's ruling: 18, as before).
STOCKFISH_DEPTH = 18
STOCKFISH_VERSION = "18"

# Variants. Chess960 games are imported and counted but never analysed,
# matched, turned into puzzles, or reviewed. See core/chess/eligibility.py.
VARIANT_STANDARD = "standard"
VARIANT_CHESS960 = "chess960"
ANALYSABLE_VARIANTS = (VARIANT_STANDARD,)

# The opening prefix every analysable game keeps after housekeeping nulls its bulk payload:
# the first OPENING_PREFIX_PLIES moves and the keys of the positions before and after each
# (one more than the moves). The SQL side is bq_opening_keys (migration 007); a test pins
# the two together. Review's position statistics read only this prefix.
OPENING_PREFIX_PLIES = 30

# Position evaluations (`pipeline position-evals`): how many boards an hourly run evaluates.
POSITION_EVALS_PER_RUN = 40

# Move classifications, most severe first, and what one game's worst instance at a board
# adds to that board's score on the Blunders page.
BLUNDER_CLASSES = ("miss", "blunder", "mistake", "inaccuracy")
BLUNDER_SCORE_WEIGHTS = {"miss": 8, "blunder": 4, "mistake": 2, "inaccuracy": 1}

# Scout: a position shared with an opponent counts only from this 0-based ply on (the first
# few moves of every game are shared with everyone).
SCOUT_MIN_MATCH_PLY = 6

# Puzzle sources that exist in this system. 'endgame_drill' from the old
# system does not, and its rows are dropped in migration.
PUZZLE_SOURCES = ("blunder", "deviation", "own_mate", "lichess_cc0", "scout", "custom")

# Lichess CC0 corpus themes served in motif practice (was app_settings.cc0_serve_themes).
# The bare 'mate' tag is deliberately absent: the player's own missed mates are served from
# his own games (BUCKET_OWN_MISSED_MATE) and corpus mates arrive by the mateInN and
# named-mate tags. The dump's metadata tags (length, phase, eval, source, castling,
# collinearMove) are not motifs and are never served.
CC0_SERVE_THEMES = (
    "fork",
    "pin",
    "skewer",
    "hangingPiece",
    "discoveredAttack",
    "sacrifice",
    "advancedPawn",
    "defensiveMove",
    "deflection",
    "quietMove",
    "attraction",
    "promotion",
    "discoveredCheck",
    "clearance",
    "intermezzo",
    "trappedPiece",
    "zugzwang",
    "capturingDefender",
    "doubleCheck",
    "interference",
    "xRayAttack",
    "enPassant",
    "underPromotion",
    "kingsideAttack",
    "queensideAttack",
    "exposedKing",
    "attackingF2F7",
    "mateIn1",
    "mateIn2",
    "mateIn3",
    "mateIn4",
    "mateIn5",
    "backRankMate",
    "smotheredMate",
    "anastasiaMate",
    "arabianMate",
    "bodenMate",
    "dovetailMate",
    "hookMate",
    "doubleBishopMate",
    "killBoxMate",
    "vukovicMate",
    "rookEndgame",
    "pawnEndgame",
    "bishopEndgame",
    "queenEndgame",
    "knightEndgame",
    "queenRookEndgame",
)

# --- Practice: the five buckets a play batch is drawn from -----------------------------
# Two are bounded by spaced repetition (the player's own puzzles and his own missed mates);
# three rotate through the corpus by theme class. The order is the fill order when minting.
BUCKET_YOUR_PUZZLES = "your_puzzles"
BUCKET_MOTIFS_FIRST_CLASS = "motifs_first_class"
BUCKET_MOTIFS_REMAINING = "motifs_remaining"
BUCKET_OWN_MISSED_MATE = "own_missed_mate"
BUCKET_CC0_MATE_ENDGAME = "cc0_mate_endgame"
PUZZLE_MIX_BUCKETS = (
    BUCKET_YOUR_PUZZLES,
    BUCKET_MOTIFS_FIRST_CLASS,
    BUCKET_MOTIFS_REMAINING,
    BUCKET_OWN_MISSED_MATE,
    BUCKET_CC0_MATE_ENDGAME,
)
SRS_BUCKETS = frozenset({BUCKET_YOUR_PUZZLES, BUCKET_OWN_MISSED_MATE})
ROTATION_BUCKETS = frozenset({BUCKET_MOTIFS_FIRST_CLASS, BUCKET_MOTIFS_REMAINING, BUCKET_CC0_MATE_ENDGAME})

# Corpus theme classes. A corpus puzzle is routed to the first class it overlaps, in
# ROTATION_ROUTING_ORDER: a fork that is also a mate is a fork lesson. The first class is
# the five tactical themes the motif tagger detects in the player's own games (its sixth,
# mate, has its own bucket), so they are the only themes a weakness can be measured for and
# weighted by; the other served themes
# rotate by rating alone, and one is promoted only together with tagger support for it.
ROTATION_FIRST_CLASS_THEMES = frozenset({"fork", "pin", "skewer", "hangingPiece", "discoveredAttack"})
ROTATION_MATE_THEMES = frozenset(
    {
        "mateIn1",
        "mateIn2",
        "mateIn3",
        "mateIn4",
        "mateIn5",
        "backRankMate",
        "smotheredMate",
        "anastasiaMate",
        "arabianMate",
        "bodenMate",
        "dovetailMate",
        "hookMate",
        "doubleBishopMate",
        "killBoxMate",
        "vukovicMate",
    }
)
ROTATION_ENDGAME_THEMES = frozenset(
    {"rookEndgame", "pawnEndgame", "bishopEndgame", "queenEndgame", "knightEndgame", "queenRookEndgame"}
)
ROTATION_REMAINING_THEMES = (
    frozenset(CC0_SERVE_THEMES) - ROTATION_FIRST_CLASS_THEMES - ROTATION_MATE_THEMES - ROTATION_ENDGAME_THEMES
)
ROTATION_BUCKET_THEMES: dict[str, frozenset[str]] = {
    BUCKET_MOTIFS_FIRST_CLASS: ROTATION_FIRST_CLASS_THEMES,
    BUCKET_MOTIFS_REMAINING: ROTATION_REMAINING_THEMES,
    BUCKET_CC0_MATE_ENDGAME: ROTATION_MATE_THEMES | ROTATION_ENDGAME_THEMES,
}
ROTATION_ROUTING_ORDER = (BUCKET_MOTIFS_FIRST_CLASS, BUCKET_CC0_MATE_ENDGAME, BUCKET_MOTIFS_REMAINING)

# The motif vocabulary a first-class corpus puzzle keeps on its row (the tagger's five plus
# 'mate'); the rotation buckets keep the whole served vocabulary.
MOTIF_THEME_VOCAB = frozenset({"fork", "pin", "skewer", "hangingPiece", "discoveredAttack", "mate"})

# Advisory-lock keyspaces (the first int of pg_advisory_xact_lock). One player, so the
# second int is a constant for the queue and the puzzle id for an attempt.
LOCK_PUZZLE_QUEUE = 3001
LOCK_SRS_ATTEMPT = 3002
LOCK_AI_BUDGET = 3003
LOCK_REPERTOIRE = 3004  # matching and every repertoire change, from reading lines to publishing results
LOCK_REVIEW = 3005  # the review run, from reading the window to publishing events (core/review/run.py)
LOCK_SEEN_BLUNDERS = 3006  # a visit's first list read and every acknowledgement of the Blunders list
LOCK_SEEN_DEVIATIONS = 3007  # the same for the Deviations list

# Game terminations decided off the board. Such a game carries no review signal: the
# detector returns an authoritative zero for it. The one membership authority; the
# vocabulary is the platforms' (core/chess/platform.py).
CLOCK_DECIDED_TERMINATIONS = ("timeout", "abandonment")

# AI explanations (core/ai.py). Anthropic's floor for a thinking budget; the room kept after
# the budget for the answer itself. Two separate model properties follow. Adaptive models take
# only `{"type": "adaptive"}` when thinking is on (they reject a budget) and think unless told
# not to, so "thinking off" is sent explicitly or the reply comes back with no text. Always-on
# models are adaptive models that reject "thinking off" outright: that combination is refused
# when settings are saved and again before any call (core/settings.py `save_errors`).
AI_THINKING_MIN_BUDGET_TOKENS = 1024
AI_THINKING_HEADROOM_TOKENS = 256
AI_ADAPTIVE_THINKING_MODELS = frozenset({"claude-sonnet-5", "claude-opus-5-5"})
AI_THINKING_ALWAYS_ON_MODELS = frozenset({"claude-opus-5-5"})

# Spaced-repetition ladder. 'king' is mastery (core/puzzles/srs.py). Six levels because
# there are six pieces; the intervals are settings, and their provenance is in
# core/settings.py.
SRS_LEVELS = ("pawn", "knight", "bishop", "rook", "queen", "king")

# Play queue: mint the next batch when this many items or fewer are still pending
# (clamped below the batch size at run time), and never hold more than two pending batches.
MINT_AHEAD_THRESHOLD = 4
PENDING_BATCH_DEPTH_CAP = 2

# A repertoire puzzle is served once the player has deviated from its line in this many
# distinct games, all time. This is the old system's hard-coded threshold; the generator's
# gate is the `deviation_puzzle_min_occurrences` setting, and this constant is the serve
# side's floor.
REPERTOIRE_PUZZLE_MIN_EVENTS = 3
