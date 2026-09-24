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

# Move classifications, most severe first, and what one game's worst instance at a board
# adds to that board's score on the Blunders page.
BLUNDER_CLASSES = ("miss", "blunder", "mistake", "inaccuracy")
BLUNDER_SCORE_WEIGHTS = {"miss": 8, "blunder": 4, "mistake": 2, "inaccuracy": 1}

# Puzzle sources that exist in this system. 'endgame_drill' from the old
# system does not, and its rows are dropped in migration.
PUZZLE_SOURCES = ("blunder", "deviation", "own_mate", "lichess_cc0", "scout", "custom")

# Lichess CC0 corpus themes served in motif practice (was app_settings.cc0_serve_themes).
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

# Depth choices offered on Explore (was app_settings.explore_engine_depth_options).
EXPLORE_DEPTH_OPTIONS = (12, 16, 18, 20)

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
# ROTATION_ROUTING_ORDER: a fork that is also a mate is a fork lesson.
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

# AI explanations (core/ai.py). Anthropic's floor for a thinking budget; the room kept after
# the budget for the answer itself; and the models that think unless told not to, where
# "thinking off" has to be sent explicitly or the reply comes back with no text.
AI_THINKING_MIN_BUDGET_TOKENS = 1024
AI_THINKING_HEADROOM_TOKENS = 256
AI_ADAPTIVE_THINKING_MODELS = frozenset({"claude-sonnet-5"})

# Spaced-repetition ladder. 'king' is mastery (core/puzzles/srs.py).
SRS_LEVELS = ("pawn", "knight", "bishop", "rook", "queen", "king")

# Play queue: mint the next batch when this many items or fewer are still pending
# (clamped below the batch size at run time), and never hold more than two pending batches.
MINT_AHEAD_THRESHOLD = 4
PENDING_BATCH_DEPTH_CAP = 2

# A repertoire puzzle is served once the player has deviated from its line in this many
# distinct games, all time.
REPERTOIRE_PUZZLE_MIN_EVENTS = 3
