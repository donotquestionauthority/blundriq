"""Every user-tunable setting, declared once.

The `Settings` model is the single definition of what can be tuned: its
fields, types, bounds, defaults and one-line descriptions. The current values
live in one row of the `settings` table as JSON; the API and the pipeline read
that row (`load`), the Preferences page renders the form from `schema()`, and
`save` validates before writing. A setting exists only if it is a field here.

Engineering constants that need a code change anyway (engine depth, corpus
theme mapping, the Chess960 rule) are in core/constants.py, not here. Secrets
are in core/secrets.py, never here.

Defaults below are the values in use on 2026-09-19 (see the phase-0 export),
so a fresh database behaves like the old one until Rob changes something.
Where a value has a recorded reason it is in the section comment above its
field; docs/decisions/008-tuning-values-provenance.md says which values were
argued for, which were labelled provisional, and which were never explained.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from jinja2 import TemplateSyntaxError
from psycopg import Connection
from psycopg.rows import tuple_row
from pydantic import BaseModel, Field, field_validator

from core.constants import AI_THINKING_ALWAYS_ON_MODELS
from core.prompts import DEFAULT_PROMPTS, LINE_PROMPT, compile_template

TimeClass = Literal["bullet", "blitz", "rapid", "classical"]
DifficultyTier = Literal["easier", "normal", "hard", "very_hard"]
FilterMode = Literal["days", "games"]


class AiPrompt(BaseModel):
    """One explanation prompt. `text` and `system_prompt` are Jinja-style templates over the
    blunder context (fen, color, move_played, cp_loss, post_blunder_line, ...)."""

    label: str = Field(default="", description="Name shown on the explain button.")
    model: str = Field(default="", description="Model id sent to the provider (Anthropic or OpenAI).")
    system_prompt: str = Field(default="", description="System prompt.")
    text: str = Field(default="", description="User prompt template.")
    temperature: float | None = Field(
        default=None, ge=0, le=2, description="Sampling temperature; null = provider default."
    )
    thinking_enabled: bool = Field(default=False, description="Extended thinking (Anthropic models).")
    thinking_budget_tokens: int = Field(default=2048, ge=0, le=32000, description="Thinking budget when enabled.")
    prefill: str = Field(default="", description="Assistant prefill, if any.")
    max_tokens: int = Field(
        default=512, ge=64, le=32000, description="Longest reply, in tokens (thinking included on adaptive models)."
    )

    @field_validator("text")
    @classmethod
    def _text_is_a_template(cls, value: str) -> str:
        try:
            compile_template(value)
        except TemplateSyntaxError as exc:
            raise ValueError(f"template syntax error on line {exc.lineno}: {exc.message}") from exc
        return value


class Settings(BaseModel):
    # --- General -----------------------------------------------------------
    timezone: str = Field(
        default="America/New_York",
        description="IANA timezone for 'today' in SRS scheduling, daily targets and streaks.",
    )
    time_class_focus: Literal["rapid_plus", "all"] = Field(
        default="rapid_plus",
        description="Which time controls feed analysis and puzzle generation. rapid_plus = rapid and slower.",
    )

    # --- Home page ---------------------------------------------------------
    daily_puzzle_target: int = Field(default=10, ge=1, le=200, description="Puzzles per day that count as 'done'.")
    daily_game_target: int = Field(
        default=1, ge=0, le=50, description="Games per day (standard or 960) that count as 'done'."
    )

    # --- Analysis ---------------------------------------------------------
    # The 50/100/200/300 centipawn ladder was seeded and never argued for. Only the
    # contested gate has a written reason: a large loss from a position that was already
    # decided is noise, not a recurring pattern, so the move is dropped rather than
    # downgraded (core/analysis/game.py). missed_mate_max_moves was tunable 1-5 in the old
    # system with no case made for 3; the field admits up to 10 here and the generator
    # clamps at 5. motif_min_material_gain = 1 is the old "SEE > 0" rule
    # made a knob (precision comes from the winnability gate; raising it suppresses
    # pawn-only motifs). motif_found_material_tolerance = 3 is one full piece: six real
    # positions won the material with a slightly worse capturer and read as missed under a
    # pure eval test, so a bishop taken over a rook counts as found and a pawn over a rook
    # does not; forks, pins, skewers and discovered attacks keep the eval test, because
    # their gain is a sequence with no single capture to compare.
    analysis_game_limit: int = Field(
        default=1000, ge=50, le=20000, description="Most recent N games kept under analysis."
    )
    blunder_threshold: int = Field(
        default=200, ge=50, le=1000, description="Centipawn loss at or above which a move is a blunder."
    )
    mistake_threshold: int = Field(
        default=100, ge=20, le=500, description="Centipawn loss at or above which a move is a mistake."
    )
    inaccuracy_threshold: int = Field(
        default=50, ge=10, le=300, description="Centipawn loss at or above which a move is an inaccuracy."
    )
    miss_threshold: int = Field(
        default=300,
        ge=50,
        le=1000,
        description="Advantage thrown away that counts as a 'miss' (winning move not played).",
    )
    miss_contested_gate: int = Field(
        default=300, ge=0, le=1000, description="Minimum eval swing for a miss in a contested position."
    )
    max_cp_display: int = Field(default=500, ge=100, le=2000, description="Clamp for eval display in the UI.")
    missed_mate_max_moves: int = Field(
        default=3, ge=1, le=10, description="Longest mate-in-N that counts as a missed mate."
    )
    motif_min_material_gain: int = Field(
        default=1, ge=0, le=9, description="Minimum material gain for a tactic to count as a motif."
    )
    motif_found_material_tolerance: int = Field(
        default=3, ge=0, le=9, description="Material tolerance when deciding a motif was 'found'."
    )

    # --- Blunders / Deviations / Scout page defaults -----------------------
    # blunder_puzzle_min_occurrences (3) and blunders_default_min_occurrences (2) are
    # separate on purpose. The old repertoire generator hard-coded 3 with no window, the
    # blunder generator read the page's filter defaults from birth, and a "one setting
    # everywhere" directive then bound every recurrence gate to those defaults; by the
    # export one key covered an admin global the generators read (3) and Rob's page filter
    # (2), and they are two fields here so the page can be widened without minting
    # puzzles.
    # Blunder, deviation and repertoire recurrences are counted in DISTINCT games: a
    # position reached again and again in one game counts once (an endgame was once flagged
    # as a recurring blunder); the weak-motif gate counts miss events. The page defaults on
    # Deviations and Scout and `weak_motif_min_occurrences` mirror the Blunders page default
    # (2), `deviation_puzzle_min_occurrences` the generator gate (3): never diagnose a
    # weakness off a handful of games. Whether 3 becomes 2 is Rob's call.
    blunders_default_last_n_games: int = Field(
        default=500, ge=10, le=5000, description="Default game window on the Blunders page."
    )
    blunders_default_filter_mode: FilterMode = Field(
        default="days", description="Whether the Blunders page opens on a day window or a game-count window."
    )
    blunders_default_window_days: int = Field(default=20, ge=1, le=3650, description="Default day window on Blunders.")
    blunders_default_min_occurrences: int = Field(
        default=2, ge=1, le=50, description="Default minimum occurrences on the Blunders page."
    )
    blunder_puzzle_min_occurrences: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Distinct games a position must be blundered in before it becomes a puzzle.",
    )
    blunders_default_classifications: list[str] = Field(
        default=["blunder", "miss", "mistake"], description="Classifications shown by default."
    )
    deviations_default_filter_mode: FilterMode = Field(
        default="days", description="Whether Deviations opens on a day window or a game-count window."
    )
    deviations_default_window_days: int = Field(
        default=20, ge=1, le=3650, description="Default day window on Deviations."
    )
    deviations_default_last_n_games: int = Field(
        default=500, ge=10, le=5000, description="Default game window on Deviations."
    )
    deviations_default_min_occurrences: int = Field(
        default=2, ge=1, le=50, description="Default minimum occurrences on Deviations."
    )
    deviation_puzzle_min_occurrences: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Distinct games a line must be deviated from before it becomes a puzzle.",
    )
    deviations_default_min_ply: int = Field(default=1, ge=1, le=80, description="Ignore deviations before this ply.")
    games_default_window_days: int = Field(
        default=60, ge=1, le=3650, description="Default day window on the Games page."
    )
    games_columns: list[str] = Field(
        default=["Date", "Color", "Opponent", "Result", "Repertoire", "Section", "Deviation", "Link"],
        description="Columns shown on the Games page, in order.",
    )
    scout_default_last_n_games: int = Field(
        default=500, ge=10, le=5000, description="Default game window on Scout, for both sides."
    )
    scout_default_min_occurrences: int = Field(
        default=2, ge=1, le=50, description="Default minimum occurrences on Scout."
    )
    scout_bayesian_prior_strength: int = Field(
        default=10, ge=0, le=100, description="Prior strength for Scout win-rate smoothing."
    )
    similar_max_distance: int = Field(
        default=6,
        ge=1,
        le=8,
        description="Similar positions: most squares a repertoire position may differ from the card's board by.",
    )
    similar_max_positions: int = Field(
        default=12, ge=1, le=50, description="Similar positions: most boards shown (each with all of its lines)."
    )
    branch_compare_max_boards: int = Field(
        default=12, ge=1, le=50, description="Compare similar positions: most alternative branches shown."
    )
    branch_min: int = Field(default=2, ge=1, le=20, description="Minimum games for a branch to appear in branch views.")
    reply_min_freq: int = Field(
        default=1, ge=1, le=50, description="Minimum frequency for an opponent reply to be listed."
    )
    reply_cap: int = Field(default=4, ge=1, le=20, description="Maximum opponent replies listed per position.")

    # --- Puzzle mix & serving ---------------------------------------------
    # The five buckets are a hybrid (core/constants.py, core/puzzles/serve.py): the three corpus
    # rotation buckets fill their share whenever the corpus or owned rows can supply it; the two SRS
    # buckets serve only what is due; a bucket nothing can supply drops out of that batch, its share
    # renormalised over the rest, and a residual goes round-robin to whatever still supplies. Every
    # batch targets the configured percentages independently, with no memory of earlier batches: the
    # old system once measured the realised mix over a trailing window of puzzles shown and "repaid"
    # a bucket's shortfall in later batches, and the repayment was a flood, so catch-up was removed
    # and `puzzle_mix_window` survived there only as a housekeeping retention floor. Nothing reads
    # it here; it is kept so the settings row round-trips. The seeded split was 25/30/15/10/20; the
    # live values had first-class and corpus mates down and remaining up (25/20/35/10/10), the
    # direction the corpus-variety memo asked for after "the same types over and over" turned out to
    # be arithmetic: five first-class themes at 30 % of every 12-puzzle batch, against 22 themes in
    # the remaining bucket. 12 has no recorded reason. Weak motifs: the old system materialised a
    # pool of weak-motif puzzles weighted by raw miss count per theme (Rob's ruling over the
    # design's severity weighting: the app's theme is frequency), capped so one weakness could not
    # crowd the queue; that is where `weak_motif_target_count` and `weak_motif_theme_cap_pct` come
    # from. This serve does not weight: it orders the first-class themes most-missed first (misses
    # at or above `weak_motif_min_occurrences`, else every theme alphabetically) and round-robins
    # one candidate per theme, so a sole weak theme can take the whole first-class share. The two
    # old knobs have no consumer here and are kept for the round-trip only. The six `coverage_*`
    # fields drove the old Stats page's weakness / strength / mastered verdicts, which are not
    # ported (backlog: Stats is Rob's call); nothing reads them. They were set at build with only
    # their meaning written down.
    puzzle_mix_batch_size: int = Field(default=12, ge=1, le=50, description="Puzzles per practice batch.")
    puzzle_mix_window: int = Field(
        default=50, ge=5, le=500, description="Not used by this implementation (the old mix catch-up window)."
    )
    puzzle_mix_your_puzzles_pct: int = Field(
        default=25, ge=0, le=100, description="% of a batch from the player's own blunders/deviations."
    )
    puzzle_mix_motifs_first_class_pct: int = Field(
        default=20, ge=0, le=100, description="% from the weakest motif class."
    )
    puzzle_mix_motifs_remaining_pct: int = Field(
        default=35, ge=0, le=100, description="% from the remaining motif classes."
    )
    puzzle_mix_own_missed_mate_pct: int = Field(
        default=10, ge=0, le=100, description="% from the player's own missed mates."
    )
    puzzle_mix_cc0_mate_endgame_pct: int = Field(default=10, ge=0, le=100, description="% from corpus mate patterns.")
    repertoire_puzzle_lookahead_moves: int = Field(
        default=1, ge=0, le=6, description="Moves of the line shown after a deviation puzzle."
    )
    blunder_puzzle_max_player_plies: int = Field(
        default=3, ge=1, le=6, description="Longest solution, in your own moves, for a blunder puzzle."
    )
    weak_motif_min_occurrences: int = Field(
        default=2, ge=1, le=50, description="Occurrences before a motif counts as a weakness."
    )
    weak_motif_target_count: int = Field(
        default=20, ge=1, le=200, description="Not used by this implementation (the old weak-motif pool size)."
    )
    weak_motif_theme_cap_pct: int = Field(
        default=40, ge=1, le=100, description="Not used by this implementation (the old weak-motif theme cap)."
    )
    coverage_practice_min_attempts: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Not used by this implementation (the old Stats coverage: attempts before a puzzle counted).",
    )
    coverage_mastered_success_pct: int = Field(
        default=80,
        ge=1,
        le=100,
        description="Not used by this implementation (the old Stats coverage: success % that counted as mastered).",
    )
    coverage_recent_games_window: int = Field(
        default=1000,
        ge=10,
        le=5000,
        description="Not used by this implementation (the old Stats coverage: games considered).",
    )
    coverage_weakness_min_occurrences: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Not used by this implementation (the old Stats coverage: occurrences before a weakness).",
    )
    coverage_weakness_miss_rate_pct: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Not used by this implementation (the old Stats coverage: miss rate % marking a weakness).",
    )
    coverage_strength_found_rate_pct: int = Field(
        default=80,
        ge=1,
        le=100,
        description="Not used by this implementation (the old Stats coverage: found rate % marking a strength).",
    )

    # --- Corpus (Lichess CC0) ---------------------------------------------
    # Corpus ratings are on the Lichess scale and a Chess.com rating sits below it (about
    # 250-400 points in the mid-range, ~400 at 800, near 0 by 2200), so a Chess.com game's
    # rating is raised before the tier window is placed (core/puzzles/serve.py). `default`
    # = -325 is the puzzle-path calibration: the middle of that gap, anchored on Rob's paired
    # accounts (~1250 Chess.com <-> ~1600 Lichess; it was -200 before, which read as "too
    # easy"). The per-time-class values came from Scout's opponent comparison and the old
    # serve path never read them; this one applies them when the latest game has a time
    # class (docs/decisions/008). A flat offset is least accurate at the low end. Tiers:
    # `normal` reproduces the original symmetric +-150 band; the wider bands exist so a
    # solver stronger than his rating can ask for harder material, and their widths were
    # not argued for. Import range 1050-2700: the weakest player served (Chess.com 800 ~
    # Lichess 1200) never needs material below ~1050, and 2700 keeps very_hard (+600)
    # available up to ~2100. 500 per cell kept a ~50-theme stratified sample of a 6M-row
    # dump inside a 500 MB database; the serve path reads the loaded corpus's own rating
    # extent, never the import keys. Pool 40: the old serve took `popularity DESC LIMIT 1`,
    # so only 150 distinct corpus puzzles were ever served; a uniform pick over the top K
    # breaks that, and K = 1 is the old behaviour.
    cc0_difficulty_tier: DifficultyTier = Field(
        default="very_hard", description="Corpus puzzle difficulty relative to your rating."
    )
    cc0_candidate_pool_size: int = Field(
        default=40, ge=5, le=500, description="Candidates sampled before picking a corpus puzzle."
    )
    cc0_import_rating_min: int = Field(default=1050, ge=400, le=3000, description="Lowest corpus rating imported.")
    cc0_import_rating_max: int = Field(default=2700, ge=400, le=3500, description="Highest corpus rating imported.")
    cc0_import_cap_per_cell: int = Field(
        default=500, ge=10, le=5000, description="Puzzles kept per (theme, rating bucket) cell."
    )
    cc0_rating_bucket_size: int = Field(default=100, ge=25, le=500, description="Width of a rating bucket.")
    cc0_tier_easier: tuple[int, int] = Field(
        default=(-300, 150), description="Rating offset window (lo, hi) for 'easier'."
    )
    cc0_tier_normal: tuple[int, int] = Field(default=(-150, 150), description="Rating offset window for 'normal'.")
    cc0_tier_hard: tuple[int, int] = Field(default=(-150, 300), description="Rating offset window for 'hard'.")
    cc0_tier_very_hard: tuple[int, int] = Field(
        default=(-150, 600), description="Rating offset window for 'very_hard'."
    )
    lichess_rating_offsets: dict[str, int] = Field(
        default={
            "bullet": -150,
            "blitz": -200,
            "rapid": -250,
            "classical": -275,
            "correspondence": -200,
            "default": -325,
        },
        description="Offset from platform rating to corpus puzzle rating, per time class.",
    )

    # --- SRS -------------------------------------------------------------
    # The ladder's mechanics were written down (core/puzzles/srs.py); its numbers were not:
    # the doubling curve to 14 days, the 1 h retry, 2-of-3 demotion and 2 hits / 300 games
    # all shipped without a reason. Rob's recorded observation (May 2026): promotions feel
    # too slow and the volume just before clearing too high; the advance threshold, seeded
    # at 2, was 1 at the export, and nothing else moved. The demotion lookback
    # counts ANALYSED games only, so a burst of imports cannot switch demotion off while
    # analysis catches up.
    srs_pawn_interval_hours: int = Field(default=24, ge=1, description="Interval at level pawn.")
    srs_knight_interval_hours: int = Field(default=48, ge=1, description="Interval at level knight.")
    srs_bishop_interval_hours: int = Field(default=96, ge=1, description="Interval at level bishop.")
    srs_rook_interval_hours: int = Field(default=192, ge=1, description="Interval at level rook.")
    srs_queen_interval_hours: int = Field(default=336, ge=1, description="Interval at level queen.")
    srs_wrong_retry_hours: int = Field(default=1, ge=0, description="Hours before a wrong answer is shown again.")
    srs_advance_threshold: int = Field(
        default=1, ge=1, le=10, description="Correct answers at a level before advancing."
    )
    srs_drop_window: int = Field(default=3, ge=1, le=10, description="Attempts considered for demotion.")
    srs_drop_wrongs: int = Field(default=2, ge=1, le=10, description="Wrongs within the window that demote.")
    srs_king_demotion_lookback_games: int = Field(
        default=300, ge=10, description="Games checked for a retired (king) puzzle's pattern recurring."
    )
    srs_king_demotion_min_hits: int = Field(default=2, ge=1, description="Recurrences that un-retire a king puzzle.")

    # --- Review ----------------------------------------------------------
    # From the review design (July 2026): a candidate is a 2-pawn material fall measured at a
    # SETTLED endpoint (the game ends, or up to 6 capture-resolution plies reach a quiet position),
    # never at a fixed ply count, and it is a calibrated heuristic, not a proof; the expected-score
    # leg is the compensation judgment. CONF 15 was validated on ~1,000 of Rob's games (21 of 1,145
    # events fell in already-lost positions, so no contested guard); 20 at depth <= 12 is the
    # fast-pass noise floor. Every detection knob was labelled provisional, pending a second
    # rating band and a depth-12-vs-18 sensitivity that were never run; K = 8, the 30-ply cap,
    # quiesce 6, shed 15 and faded 62 have nothing else behind them. Faded advantage was nearly
    # empty at Rob's rating (his losses are sharp) and stays for the profile where it is not.
    # Pricing is mate distance, else the win-probability sigmoid; the old system's first rung, a
    # lookup over the board, is gone (docs/decisions/001). The page reads the stored events by
    # habit (core/review/habits.py); the two pool floors and the half-life in games belonged to
    # the old event-pool worklist and nothing reads them.
    review_conf_es_drop: int = Field(
        default=15, ge=1, le=100, description="Expected-score drop that confirms a material event."
    )
    review_conf_es_drop_depth12: int = Field(
        default=20, ge=1, le=100, description="Same, for games analysed at depth <= 12."
    )
    review_material_candidate_drop: int = Field(
        default=2, ge=1, le=9, description="Material units lost that make a candidate."
    )
    review_missed_win_shed: int = Field(
        default=15, ge=1, le=100, description="Expected-score shed that marks a missed win."
    )
    review_faded_peak_es: int = Field(
        default=62, ge=50, le=100, description="Peak expected score for a 'faded advantage' event."
    )
    review_quiesce_max_plies: int = Field(
        default=6, ge=0, le=20, description="Capture-resolution extension when settling a candidate."
    )
    review_early_k_plies: int = Field(
        default=8, ge=0, le=40, description="Plies after book exit still counted as 'early'."
    )
    review_early_ply_cap: int = Field(
        default=30, ge=1, le=80, description="'Early' cap when no repertoire match exists."
    )
    review_pool_floor_line: int = Field(
        default=5, ge=1, le=50, description="Not used by this implementation (the old opening pools' line floor)."
    )
    review_pool_floor_eco: int = Field(
        default=8, ge=1, le=50, description="Not used by this implementation (the old opening pools' ECO floor)."
    )
    review_recency_half_life_games: int = Field(
        default=200, ge=10, description="Not used by this implementation (the old worklist's half-life in games)."
    )
    # Review reads the opening prefix every analysable game keeps (core.constants.
    # OPENING_PREFIX_PLIES), over this many months back from the newest game. Rob's decisions
    # before this ply are the ones ranked, and `pipeline position-evals` evaluates the boards
    # before and after each of them (core/review/mistakes.py); the backfill reaches as far back
    # as the history.
    review_history_months: int = Field(
        default=12, ge=1, le=36, description="Months of games counted toward a position's numbers."
    )
    review_position_min_games: int = Field(
        default=5, ge=3, le=100, description="Games in which you moved from a position before it can be ranked."
    )
    # A position ranks under "Opening mistakes to work on" only when Rob's move there gave
    # something away in at least this many of those games: one slip in fifty is not a pattern.
    review_min_costly_games: int = Field(
        default=3, ge=1, le=50, description="Games with a costly move from a position before it can be ranked."
    )
    # A move is charged only what it gave away above this many points of expected score (0-100).
    # Depth 18 and depth 24 disagree by up to about 2.6 points on nine moves in ten, so a smaller
    # loss is mostly the engine's noise; subtracting the floor (rather than cutting at it) keeps a
    # move just over it from counting much.
    review_mistake_floor_es: int = Field(
        default=3, ge=0, le=15, description="Expected-score points a move may lose before it is charged."
    )
    review_position_max_ply: int = Field(
        default=24, ge=4, le=30, description="Deepest ply (half-move) counted as a position."
    )
    review_recency_half_life_days: int = Field(
        default=30, ge=7, le=180, description="A game's weight halves every this many days, before the newest game."
    )
    review_default_mode: Literal["learn", "review"] = Field(
        default="learn", description="Mode the game review opens in."
    )
    review_show_timer: bool = Field(default=True, description="Time Learn-mode reps.")

    # --- Explore / AI ----------------------------------------------------
    explore_engine_depth: int = Field(
        default=16, ge=6, le=30, description="In-browser engine depth on Explore and Review."
    )
    ai_explain_max_per_hour: int = Field(
        default=20, ge=0, le=1000, description="Most AI calls per hour (cached answers are free). 0 = no cap."
    )
    ai_explain_max_per_day: int = Field(
        default=50, ge=0, le=5000, description="Most AI calls per day (cached answers are free). 0 = no cap."
    )
    ai_prompts: dict[str, AiPrompt] = Field(
        default_factory=lambda: {k: AiPrompt.model_validate(v) for k, v in DEFAULT_PROMPTS.items()},
        description="Explanation prompts by key (a, b, c...). Each is a button on a blunder; edit text and model here.",
    )
    ai_default_prompt: str = Field(default="a", description="Prompt key used by the primary Explain button.")
    ai_line_prompt: AiPrompt = Field(
        default_factory=lambda: AiPrompt.model_validate(LINE_PROMPT),
        description="The walk-through's 'Ask why this move matters' prompt, over one repertoire line (edit as JSON).",
    )


# ---------------------------------------------------------------------------


def thinking_off_refusal(model: str) -> str:
    """The one wording for a prompt whose model cannot run with thinking off."""
    return f"{model} always thinks; turn thinking on"


def save_errors(values: Settings) -> list[str]:
    """What a save must refuse that loading must still accept. A prompt whose model always
    thinks cannot have thinking off; that is checked here rather than in `AiPrompt`, because
    `load` validates the stored row on every request and one bad prompt must cost one refused
    button, not the whole API. core/ai.py refuses the same combination before any call."""
    prompts = [(f"ai_prompts.{key}", p) for key, p in sorted(values.ai_prompts.items())]
    prompts.append(("ai_line_prompt", values.ai_line_prompt))
    return [
        f"{field}: {thinking_off_refusal(p.model)}"
        for field, p in prompts
        if p.model in AI_THINKING_ALWAYS_ON_MODELS and not p.thinking_enabled
    ]


def schema() -> dict[str, Any]:
    """JSON schema of the settings model; the Preferences page renders from this. The form
    dispatches on each property's `type`, and pydantic gives a field typed as a model only a
    `$ref`, so such a property is marked as the object it is (the `$ref` stays)."""
    out = Settings.model_json_schema()
    defs = out.get("$defs", {})
    for prop in out["properties"].values():
        ref = prop.get("$ref", "")
        if "type" not in prop and defs.get(ref.rpartition("/")[2], {}).get("type") == "object":
            prop["type"] = "object"
    return out


def load(conn: Connection[Any]) -> Settings:
    """Read the single settings row; a database with no row yet gets the defaults."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT data FROM settings WHERE id = 1")
        row = cur.fetchone()
    if row is None:
        return Settings()
    data = row[0]
    return Settings.model_validate(data if isinstance(data, dict) else json.loads(data))


def save(conn: Connection[Any], values: Settings) -> None:
    """Validate and upsert the single row."""
    payload = json.dumps(values.model_dump(mode="json"))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO settings (id, data) VALUES (1, %s::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (payload,),
        )
    conn.commit()
