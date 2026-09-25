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
"""

from __future__ import annotations

import json
from typing import Any, Literal

from jinja2 import TemplateSyntaxError
from psycopg import Connection
from psycopg.rows import tuple_row
from pydantic import BaseModel, Field, field_validator

from core.prompts import DEFAULT_PROMPTS, compile_template

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
    max_tokens: int = Field(default=512, ge=64, le=8192, description="Longest reply, in tokens.")

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
    puzzle_mix_batch_size: int = Field(default=12, ge=1, le=50, description="Puzzles per practice batch.")
    puzzle_mix_window: int = Field(default=50, ge=5, le=500, description="Recent-game window puzzles are drawn from.")
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
        default=20, ge=1, le=200, description="How many weak-motif puzzles to keep available."
    )
    weak_motif_theme_cap_pct: int = Field(
        default=40, ge=1, le=100, description="Cap on one theme's share of weak-motif puzzles."
    )
    coverage_practice_min_attempts: int = Field(
        default=3, ge=1, le=50, description="Attempts before a puzzle counts toward coverage."
    )
    coverage_mastered_success_pct: int = Field(
        default=80, ge=1, le=100, description="Success % that counts as mastered."
    )
    coverage_recent_games_window: int = Field(
        default=1000, ge=10, le=5000, description="Games considered for coverage."
    )
    coverage_weakness_min_occurrences: int = Field(
        default=5, ge=1, le=100, description="Occurrences before a theme is a weakness."
    )
    coverage_weakness_miss_rate_pct: int = Field(
        default=50, ge=1, le=100, description="Miss rate % that marks a weakness."
    )
    coverage_strength_found_rate_pct: int = Field(
        default=80, ge=1, le=100, description="Found rate % that marks a strength."
    )

    # --- Corpus (Lichess CC0) ---------------------------------------------
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
    review_pool_floor_line: int = Field(default=5, ge=1, le=50, description="Minimum events for a line-scoped pool.")
    review_pool_floor_eco: int = Field(default=8, ge=1, le=50, description="Minimum events for an ECO-scoped pool.")
    review_recency_half_life_games: int = Field(
        default=200, ge=10, description="Half-life (games) for recency weighting."
    )

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


# ---------------------------------------------------------------------------


def schema() -> dict[str, Any]:
    """JSON schema of the settings model; the Preferences page renders from this."""
    return Settings.model_json_schema()


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
