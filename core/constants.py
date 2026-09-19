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
