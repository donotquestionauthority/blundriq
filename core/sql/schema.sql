-- BlundrIQ Personal — database schema (single source of truth for a FRESH install; lives in core/sql/ so it ships in the package)
--
-- Rules:
--   * A fresh database loads this file and records the current baseline in schema_version.
--   * An existing database applies only migrations/NNN_*.sql above its recorded version;
--     migrations are never replayed over this file. When a migration lands, this file is
--     regenerated to match and CI diffs fresh-install vs upgrade.
--   * One user: player_id is always 1. No RLS, one database role.
--   * Every table carries a one-line "why it exists" comment. Keep them true.
--
-- Provenance: harvested from the previous multi-tenant schema on 2026-09-19 with
-- tenancy, billing, trial, creator, email, orchestration and endgame-drill objects removed.

SET client_min_messages = warning;

-- ---------------------------------------------------------------------------
-- Functions. bq_canonical_fen strips move counters so the same position hashes
-- the same regardless of move number; the *_key(s) functions feed generated
-- columns and GIN indexes used by repertoire matching and Scout.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.bq_canonical_fen(fen text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT CASE WHEN cardinality(parts) >= 4
              THEN array_to_string(parts[1:4], ' ')
              ELSE NULL END
  FROM (SELECT regexp_split_to_array(regexp_replace(fen, '^\s+|\s+$', '', 'g'), '\s+') AS parts) s
$function$
;

CREATE OR REPLACE FUNCTION public.bq_position_key(fen text)
 RETURNS bigint
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT hashtextextended(public.bq_canonical_fen(fen), 0)
$function$
;

CREATE OR REPLACE FUNCTION public.bq_position_keys(fens jsonb)
 RETURNS bigint[]
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT COALESCE(
           (SELECT array_agg(public.bq_position_key(e.elem) ORDER BY e.ord)
            FROM jsonb_array_elements_text(fens) WITH ORDINALITY AS e(elem, ord)),
           '{}'::bigint[])
$function$
;

CREATE OR REPLACE FUNCTION public.bq_material_sig(fen text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT CASE
           WHEN cardinality(parts) >= 4 AND parts[2] IN ('w','b') THEN
             parts[2] || '|' || (
               SELECT string_agg((length(parts[1]) - length(replace(parts[1], t.pc, '')))::text, ',' ORDER BY t.ord)
               FROM unnest(ARRAY['P','N','B','R','Q','K','p','n','b','r','q','k']) WITH ORDINALITY AS t(pc, ord)
             )
           ELSE NULL
         END
  FROM (SELECT regexp_split_to_array(regexp_replace(fen, '^\s+|\s+$', '', 'g'), '\s+') AS parts) s
$function$
;

CREATE OR REPLACE FUNCTION public.bq_material_key(fen text)
 RETURNS bigint
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT hashtextextended(public.bq_material_sig(fen), 0)
$function$
;

CREATE OR REPLACE FUNCTION public.bq_material_keys(fens jsonb)
 RETURNS bigint[]
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT COALESCE(
           (SELECT array_agg(public.bq_material_key(e.elem) ORDER BY e.ord)
            FROM jsonb_array_elements_text(fens) WITH ORDINALITY AS e(elem, ord)),
           '{}'::bigint[])
$function$
;


-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

-- Cached AI explanations keyed by position + prompt, so a repeat click costs nothing.
CREATE TABLE public.ai_explanation_cache (
    fen text NOT NULL,
    prompt_hash text NOT NULL,
    model text NOT NULL,
    prompt_label text DEFAULT ''::text NOT NULL,
    rendered_prompt text,
    explanation text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    context_hash text NOT NULL,
    canonical_fen text GENERATED ALWAYS AS ((public.bq_canonical_fen(fen) || ' 0 1'::text)) STORED
);

-- One row per (player, game, ply) the engine flagged; the input to the Blunders page and blunder puzzles.
CREATE TABLE public.blunders (
    id bigint NOT NULL,
    player_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    ply integer NOT NULL,
    fen text NOT NULL,
    move_played text,
    best_move text,
    best_line text,
    post_blunder_line text,
    centipawn_loss integer,
    classification text,
    phase text,
    opening_eco text,
    engine_version text,
    analysis_depth integer,
    themes text[],
    canonical_fen text GENERATED ALWAYS AS ((public.bq_canonical_fen(fen) || ' 0 1'::text)) STORED
);

CREATE SEQUENCE public.blunders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.blunders_id_seq OWNED BY public.blunders.id;

-- A repertoire book (one color). source_book_id is the opaque id from the course it came from.
CREATE TABLE public.books (
    id integer NOT NULL,
    source_book_id integer,
    title text NOT NULL,
    color text NOT NULL,
    active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    player_id integer NOT NULL,
    source_url text,
    source_author text,
    source_title text,
    CONSTRAINT books_color_check CHECK ((color = ANY (ARRAY['white'::text, 'black'::text])))
);

CREATE SEQUENCE public.books_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.books_id_seq OWNED BY public.books.id;

-- Chapters of a book. root_fen is the tabiya the chapter starts from (NULL = initial position).
CREATE TABLE public.chapters (
    id integer NOT NULL,
    book_id integer,
    source_chapter_id integer,
    title text NOT NULL,
    active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    root_fen text
);

CREATE SEQUENCE public.chapters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.chapters_id_seq OWNED BY public.chapters.id;

-- Every imported game (own and opponents'), deduplicated by platform id. fen_sequence has len(moves)+1 entries; position_keys is a generated hash array used for repertoire/scout matching via the GIN index.
CREATE TABLE public.chess_games (
    id bigint NOT NULL,
    platform text NOT NULL,
    platform_game_id text NOT NULL,
    url text,
    played_at timestamp with time zone,
    time_control text,
    opening_name text,
    opening_eco text,
    moves jsonb,
    fen_sequence jsonb,
    termination text,
    analysis_status text DEFAULT 'unanalyzed'::text NOT NULL,
    analysis_engine text,
    analysis_depth integer,
    peak_advantage integer,
    final_eval integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    variant text DEFAULT 'standard'::text NOT NULL,
    starting_fen text,
    ply_analysis jsonb,
    ply_analysis_depth integer,
    time_class text,
    canonical_family text,
    canonical_variation text,
    position_keys bigint[] GENERATED ALWAYS AS (public.bq_position_keys(fen_sequence)) STORED,
    clocks jsonb,
    CONSTRAINT chess_games_analysis_status_check CHECK ((analysis_status = ANY (ARRAY['unanalyzed'::text, 'pending'::text, 'completed'::text, 'failed_retryable'::text, 'failed_permanent'::text]))),
    CONSTRAINT chess_games_canonical_pair_coherent CHECK (((canonical_family IS NULL) = (canonical_variation IS NULL))),
    CONSTRAINT chess_games_chess960_starting_fen_matches_seq CHECK (((variant = 'standard'::text) OR (fen_sequence IS NULL) OR ((fen_sequence ->> 0) = starting_fen))),
    CONSTRAINT chess_games_platform_check CHECK ((platform = ANY (ARRAY['chesscom'::text, 'lichess'::text]))),
    CONSTRAINT chess_games_variant_check CHECK ((variant = ANY (ARRAY['standard'::text, 'chess960'::text]))),
    CONSTRAINT chess_games_variant_starting_fen_coherent CHECK ((((variant = 'standard'::text) AND (starting_fen IS NULL)) OR ((variant = 'chess960'::text) AND (starting_fen IS NOT NULL))))
);

CREATE SEQUENCE public.chess_games_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.chess_games_id_seq OWNED BY public.chess_games.id;

-- Positions the player asked Blunders to stop showing. Unique per canonical position.
CREATE TABLE public.dismissed_blunder_fens (
    id integer NOT NULL,
    player_id integer NOT NULL,
    fen text NOT NULL,
    dismissed_at timestamp with time zone DEFAULT now() NOT NULL,
    canonical_fen text GENERATED ALWAYS AS ((public.bq_canonical_fen(fen) || ' 0 1'::text)) STORED
);

CREATE SEQUENCE public.dismissed_blunder_fens_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.dismissed_blunder_fens_id_seq OWNED BY public.dismissed_blunder_fens.id;

-- Boards the Blunders list has shown. A board absent here is NEW on the list and counted on Home; the page adds the boards it rendered (the first look adds everything then listed).
CREATE TABLE public.seen_blunder_boards (
    player_id integer NOT NULL,
    canonical_fen text NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT seen_blunder_boards_pkey PRIMARY KEY (player_id, canonical_fen)
);

-- Per (game, player): where the game left the repertoire and who deviated; the Deviations page reads this.
CREATE TABLE public.game_repertoire_results (
    id bigint NOT NULL,
    player_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    book_id integer NOT NULL,
    chapter_id integer NOT NULL,
    deviated_at_ply integer,
    deviation_by text,
    expected_move text,
    played_move text,
    deviation_fen text,
    canonical_fen text GENERATED ALWAYS AS ((public.bq_canonical_fen(deviation_fen) || ' 0 1'::text)) STORED
);

CREATE SEQUENCE public.game_repertoire_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.game_repertoire_results_id_seq OWNED BY public.game_repertoire_results.id;

-- Which repertoire lines a game matched and how far.
CREATE TABLE public.game_result_lines (
    id bigint NOT NULL,
    game_repertoire_result_id bigint NOT NULL,
    line_id integer NOT NULL,
    matched_ply integer NOT NULL
);

CREATE SEQUENCE public.game_result_lines_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.game_result_lines_id_seq OWNED BY public.game_result_lines.id;

-- Moves the player committed in Review learn mode, for scoring.
CREATE TABLE public.learn_commits (
    id bigint NOT NULL,
    player_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    ply integer NOT NULL,
    attempt_id uuid NOT NULL,
    committed_move text NOT NULL,
    submitted_move text NOT NULL,
    game_move text,
    engine_move text,
    book_move text,
    in_check boolean NOT NULL,
    ply_classified boolean NOT NULL,
    elapsed_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT learn_commits_elapsed_ms_check CHECK (((elapsed_ms IS NULL) OR (elapsed_ms >= 0)))
);

CREATE SEQUENCE public.learn_commits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.learn_commits_id_seq OWNED BY public.learn_commits.id;

-- Capped sample of the Lichess CC0 puzzle corpus, served by theme and rating bucket in motif practice.
CREATE TABLE public.lichess_puzzles (
    puzzle_id text NOT NULL,
    fen text NOT NULL,
    solution_line jsonb NOT NULL,
    color character(1) NOT NULL,
    rating integer NOT NULL,
    rating_bucket integer NOT NULL,
    popularity smallint NOT NULL,
    nb_plays integer NOT NULL,
    themes text[] NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    canonical_fen text GENERATED ALWAYS AS ((public.bq_canonical_fen(fen) || ' 0 1'::text)) STORED,
    CONSTRAINT lichess_puzzles_color_check CHECK ((color = ANY (ARRAY['w'::bpchar, 'b'::bpchar])))
);

-- Named opponents tracked by Scout.
CREATE TABLE public.opponent_profiles (
    id integer NOT NULL,
    player_id integer NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    active boolean DEFAULT true NOT NULL,
    is_initialized boolean DEFAULT false NOT NULL
);

CREATE SEQUENCE public.opponent_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.opponent_profiles_id_seq OWNED BY public.opponent_profiles.id;

-- Platform accounts that make up an opponent profile.
CREATE TABLE public.opponent_sources (
    id integer NOT NULL,
    opponent_profile_id integer NOT NULL,
    source_type text NOT NULL,
    username text,
    last_fetched timestamp with time zone,
    active boolean DEFAULT true NOT NULL,
    CONSTRAINT opponent_sources_source_type_check CHECK ((source_type = ANY (ARRAY['chesscom'::text, 'lichess'::text, 'manual'::text])))
);

CREATE SEQUENCE public.opponent_sources_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.opponent_sources_id_seq OWNED BY public.opponent_sources.id;

-- Which games in chess_games belong to which opponent, and the opponent's color/result.
CREATE TABLE public.opponent_views (
    opponent_profile_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    source_type text NOT NULL,
    played_as text NOT NULL,
    result text,
    CONSTRAINT opponent_views_played_as_check CHECK ((played_as = ANY (ARRAY['white'::text, 'black'::text]))),
    CONSTRAINT opponent_views_result_check CHECK ((result = ANY (ARRAY['win'::text, 'loss'::text, 'draw'::text]))),
    CONSTRAINT opponent_views_source_type_check CHECK ((source_type = ANY (ARRAY['chesscom'::text, 'lichess'::text])))
);

-- The player's side of a chess_games row: color, ratings, result, analysis progress.
CREATE TABLE public.player_games (
    player_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    player_color text NOT NULL,
    opponent_username text,
    opponent_rating integer,
    player_rating integer,
    source text NOT NULL,
    result text,
    no_repertoire_match boolean DEFAULT false NOT NULL,
    analyzed_at_depth integer,
    reviewed_at timestamp with time zone,
    CONSTRAINT player_games_player_color_check CHECK ((player_color = ANY (ARRAY['white'::text, 'black'::text]))),
    CONSTRAINT player_games_result_check CHECK ((result = ANY (ARRAY['win'::text, 'loss'::text, 'draw'::text]))),
    CONSTRAINT player_games_source_check CHECK ((source = ANY (ARRAY['chesscom'::text, 'lichess'::text])))
);

-- Per-ply tactical findings (motif found/missed, mate found/missed, positional) that drive puzzle selection and weakness detection.
CREATE TABLE public.player_motif_events (
    id bigint NOT NULL,
    player_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    ply integer NOT NULL,
    metric_type text NOT NULL,
    theme text NOT NULL,
    found boolean,
    mate_in_moves integer,
    cp_loss integer,
    player_color text NOT NULL,
    engine_version text,
    analysis_depth integer,
    slip_ply integer,
    CONSTRAINT player_motif_events_metric_type_check CHECK ((metric_type = ANY (ARRAY['motif'::text, 'mate'::text, 'endgame'::text, 'positional'::text]))),
    CONSTRAINT player_motif_events_player_color_check CHECK ((player_color = ANY (ARRAY['white'::text, 'black'::text])))
);

CREATE SEQUENCE public.player_motif_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.player_motif_events_id_seq OWNED BY public.player_motif_events.id;

-- Which puzzles were served in which batch, so a batch is not re-served.
CREATE TABLE public.player_puzzle_exposure (
    id bigint NOT NULL,
    player_id integer NOT NULL,
    puzzle_id integer NOT NULL,
    bucket text NOT NULL,
    batch_id bigint NOT NULL,
    scope text DEFAULT 'all'::text NOT NULL,
    served_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT player_puzzle_exposure_bucket_chk CHECK ((bucket = ANY (ARRAY['your_puzzles'::text, 'motifs_first_class'::text, 'motifs_remaining'::text, 'own_missed_mate'::text, 'cc0_mate_endgame'::text])))
);

CREATE SEQUENCE public.player_puzzle_exposure_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.player_puzzle_exposure_id_seq OWNED BY public.player_puzzle_exposure.id;

-- Puzzles/lines the player skipped within a batch scope.
CREATE TABLE public.player_puzzle_skip (
    player_id integer NOT NULL,
    scope text NOT NULL,
    batch_id bigint NOT NULL,
    puzzle_id integer NOT NULL,
    skipped_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint NOT NULL
);

CREATE SEQUENCE public.player_puzzle_skip_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.player_puzzle_skip_id_seq OWNED BY public.player_puzzle_skip.id;

-- SRS state per puzzle: level (pawn..king), streak, next due time. Irreplaceable.
CREATE TABLE public.player_puzzle_state (
    player_id integer NOT NULL,
    puzzle_id integer NOT NULL,
    level text DEFAULT 'pawn'::text NOT NULL,
    correct_at_level integer DEFAULT 0 NOT NULL,
    last_3_attempts boolean[] DEFAULT '{}'::boolean[] NOT NULL,
    last_correct_date date,
    next_show_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT player_puzzle_state_level_check CHECK ((level = ANY (ARRAY['pawn'::text, 'knight'::text, 'bishop'::text, 'rook'::text, 'queen'::text, 'king'::text])))
);

-- Exactly one row (id = 1). Platform usernames and last-checked timestamps live here; blunders_seen_at is when the Blunders list was last looked at (Home shows it; seen_blunder_boards holds what was shown).
CREATE TABLE public.players (
    id integer NOT NULL,
    blunders_seen_at timestamp with time zone,
    chesscom_username text,
    chesscom_id text,
    lichess_username text,
    lichess_id text,
    created_at timestamp with time zone DEFAULT now(),
    chesscom_last_checked timestamp with time zone,
    lichess_last_checked timestamp with time zone
);

CREATE SEQUENCE public.players_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.players_id_seq OWNED BY public.players.id;

-- Every attempt, with an idempotency key so a retried request scores once.
CREATE TABLE public.puzzle_attempts (
    id integer NOT NULL,
    puzzle_id integer NOT NULL,
    player_id integer NOT NULL,
    solved boolean NOT NULL,
    moves_played text,
    attempt_at timestamp with time zone DEFAULT now(),
    attempt_id uuid,
    session_id uuid
)
WITH (autovacuum_vacuum_scale_factor='0.02', autovacuum_vacuum_threshold='50', autovacuum_analyze_scale_factor='0.01');

CREATE SEQUENCE public.puzzle_attempts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.puzzle_attempts_id_seq OWNED BY public.puzzle_attempts.id;

-- Materialised puzzles from the player's games, the repertoire, the corpus, or custom creation.
CREATE TABLE public.puzzles (
    id integer NOT NULL,
    fen text NOT NULL,
    solution_line jsonb NOT NULL,
    source_types text[] NOT NULL,
    color character(1) NOT NULL,
    title text,
    description text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    solution_fen_sequence jsonb,
    is_repertoire boolean DEFAULT false NOT NULL,
    repertoire_line_id integer,
    player_id integer NOT NULL,
    themes text[],
    acceptance_map jsonb,
    canonical_fen text GENERATED ALWAYS AS ((public.bq_canonical_fen(fen) || ' 0 1'::text)) STORED,
    CONSTRAINT puzzles_repertoire_consistency_check CHECK ((((is_repertoire IS TRUE) AND (player_id IS NOT NULL) AND (repertoire_line_id IS NOT NULL)) OR ((is_repertoire IS FALSE) AND (repertoire_line_id IS NULL))))
)
WITH (autovacuum_vacuum_scale_factor='0.02', autovacuum_vacuum_threshold='50', autovacuum_analyze_scale_factor='0.01');

CREATE SEQUENCE public.puzzles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.puzzles_id_seq OWNED BY public.puzzles.id;

-- Author notes attached to a repertoire line at a position (fen_norm). source is 'course' or 'manual'.
CREATE TABLE public.repertoire_annotations (
    player_id integer NOT NULL,
    fen_norm text NOT NULL,
    text text NOT NULL,
    source text NOT NULL,
    source_ref text,
    author text,
    book_title text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint NOT NULL,
    line_id integer,
    book_id integer,
    chapter_id integer,
    CONSTRAINT repertoire_annotations_source_check CHECK ((source = ANY (ARRAY['course'::text, 'manual'::text])))
);

ALTER TABLE public.repertoire_annotations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.repertoire_annotations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

-- One line per row: moves, fen_sequence, and generated position/material key arrays for matching.
CREATE TABLE public.repertoire_lines (
    id integer NOT NULL,
    chapter_id integer,
    source_line_id text,
    line_name text NOT NULL,
    moves jsonb NOT NULL,
    is_alternative boolean DEFAULT false,
    active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    fen_sequence jsonb,
    position_keys bigint[] GENERATED ALWAYS AS (public.bq_position_keys(fen_sequence)) STORED,
    material_keys bigint[] GENERATED ALWAYS AS (public.bq_material_keys(fen_sequence)) STORED
);

CREATE SEQUENCE public.repertoire_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.repertoire_lines_id_seq OWNED BY public.repertoire_lines.id;

-- Per config version: how many candidates the Review detector could not settle (honesty counter).
CREATE TABLE public.review_detection_state (
    player_id integer NOT NULL,
    config_version integer NOT NULL,
    unknown_candidates integer DEFAULT 0 NOT NULL,
    window_games integer DEFAULT 0 NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    review_input_sig text DEFAULT ''::text NOT NULL,
    review_knobs_digest text DEFAULT ''::text NOT NULL
);

-- Review buckets: one event per (player, game, anchor ply) with evidence and cost. Regenerated by the pipeline, never migrated.
CREATE TABLE public.review_events (
    id bigint NOT NULL,
    player_id integer NOT NULL,
    chess_game_id bigint NOT NULL,
    anchor_ply integer NOT NULL,
    config_version integer NOT NULL,
    base_route text NOT NULL,
    opening_candidate boolean DEFAULT false NOT NULL,
    pool_key text,
    evidence jsonb,
    cost numeric,
    phase text,
    piece_label text,
    book_relation text,
    board_key bigint,
    first_detected_at timestamp with time zone NOT NULL,
    meaning_changed_at timestamp with time zone,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT review_events_base_route_check CHECK ((base_route = ANY (ARRAY['endgame_technique'::text, 'lapse_defense'::text, 'lapse_offense'::text, 'faded'::text])))
);

CREATE SEQUENCE public.review_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.review_events_id_seq OWNED BY public.review_events.id;

-- When each Review pool was last shown, for rotation.
CREATE TABLE public.review_pool_state (
    player_id integer NOT NULL,
    pool_id text NOT NULL,
    last_shown_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.blunders ALTER COLUMN id SET DEFAULT nextval('public.blunders_id_seq'::regclass);

ALTER TABLE ONLY public.books ALTER COLUMN id SET DEFAULT nextval('public.books_id_seq'::regclass);

ALTER TABLE ONLY public.chapters ALTER COLUMN id SET DEFAULT nextval('public.chapters_id_seq'::regclass);

ALTER TABLE ONLY public.chess_games ALTER COLUMN id SET DEFAULT nextval('public.chess_games_id_seq'::regclass);

ALTER TABLE ONLY public.dismissed_blunder_fens ALTER COLUMN id SET DEFAULT nextval('public.dismissed_blunder_fens_id_seq'::regclass);

ALTER TABLE ONLY public.game_repertoire_results ALTER COLUMN id SET DEFAULT nextval('public.game_repertoire_results_id_seq'::regclass);

ALTER TABLE ONLY public.game_result_lines ALTER COLUMN id SET DEFAULT nextval('public.game_result_lines_id_seq'::regclass);

ALTER TABLE ONLY public.learn_commits ALTER COLUMN id SET DEFAULT nextval('public.learn_commits_id_seq'::regclass);

ALTER TABLE ONLY public.opponent_profiles ALTER COLUMN id SET DEFAULT nextval('public.opponent_profiles_id_seq'::regclass);

ALTER TABLE ONLY public.opponent_sources ALTER COLUMN id SET DEFAULT nextval('public.opponent_sources_id_seq'::regclass);

ALTER TABLE ONLY public.player_motif_events ALTER COLUMN id SET DEFAULT nextval('public.player_motif_events_id_seq'::regclass);

ALTER TABLE ONLY public.player_puzzle_exposure ALTER COLUMN id SET DEFAULT nextval('public.player_puzzle_exposure_id_seq'::regclass);

ALTER TABLE ONLY public.player_puzzle_skip ALTER COLUMN id SET DEFAULT nextval('public.player_puzzle_skip_id_seq'::regclass);

ALTER TABLE ONLY public.players ALTER COLUMN id SET DEFAULT nextval('public.players_id_seq'::regclass);

ALTER TABLE ONLY public.puzzle_attempts ALTER COLUMN id SET DEFAULT nextval('public.puzzle_attempts_id_seq'::regclass);

ALTER TABLE ONLY public.puzzles ALTER COLUMN id SET DEFAULT nextval('public.puzzles_id_seq'::regclass);

ALTER TABLE ONLY public.repertoire_lines ALTER COLUMN id SET DEFAULT nextval('public.repertoire_lines_id_seq'::regclass);

ALTER TABLE ONLY public.review_events ALTER COLUMN id SET DEFAULT nextval('public.review_events_id_seq'::regclass);

ALTER TABLE ONLY public.ai_explanation_cache
    ADD CONSTRAINT ai_explanation_cache_pkey PRIMARY KEY (fen, prompt_hash, context_hash);

ALTER TABLE ONLY public.blunders
    ADD CONSTRAINT blunders_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.blunders
    ADD CONSTRAINT blunders_player_id_chess_game_id_ply_key UNIQUE (player_id, chess_game_id, ply);

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_source_book_id_player_id_key UNIQUE (source_book_id, player_id);

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.chapters
    ADD CONSTRAINT chapters_book_id_source_chapter_id_key UNIQUE (book_id, source_chapter_id);

ALTER TABLE ONLY public.chapters
    ADD CONSTRAINT chapters_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.chess_games
    ADD CONSTRAINT chess_games_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.chess_games
    ADD CONSTRAINT chess_games_platform_platform_game_id_key UNIQUE (platform, platform_game_id);

ALTER TABLE ONLY public.dismissed_blunder_fens
    ADD CONSTRAINT dismissed_blunder_fens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.dismissed_blunder_fens
    ADD CONSTRAINT dismissed_blunder_fens_player_id_fen_key UNIQUE (player_id, fen);

ALTER TABLE ONLY public.game_repertoire_results
    ADD CONSTRAINT game_repertoire_results_chess_game_id_player_id_key UNIQUE (chess_game_id, player_id);

ALTER TABLE ONLY public.game_repertoire_results
    ADD CONSTRAINT game_repertoire_results_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.game_result_lines
    ADD CONSTRAINT game_result_lines_game_repertoire_result_id_line_id_key UNIQUE (game_repertoire_result_id, line_id);

ALTER TABLE ONLY public.game_result_lines
    ADD CONSTRAINT game_result_lines_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.learn_commits
    ADD CONSTRAINT learn_commits_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.learn_commits
    ADD CONSTRAINT learn_commits_player_id_attempt_id_key UNIQUE (player_id, attempt_id);

ALTER TABLE ONLY public.lichess_puzzles
    ADD CONSTRAINT lichess_puzzles_pkey PRIMARY KEY (puzzle_id);

ALTER TABLE ONLY public.opponent_profiles
    ADD CONSTRAINT opponent_profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.opponent_profiles
    ADD CONSTRAINT opponent_profiles_player_id_name_key UNIQUE (player_id, name);

ALTER TABLE ONLY public.opponent_sources
    ADD CONSTRAINT opponent_sources_opponent_profile_id_source_type_username_key UNIQUE (opponent_profile_id, source_type, username);

ALTER TABLE ONLY public.opponent_sources
    ADD CONSTRAINT opponent_sources_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.opponent_views
    ADD CONSTRAINT opponent_views_pkey PRIMARY KEY (opponent_profile_id, chess_game_id);

ALTER TABLE ONLY public.player_games
    ADD CONSTRAINT player_games_chess_game_id_player_id_key UNIQUE (chess_game_id, player_id);

ALTER TABLE ONLY public.player_games
    ADD CONSTRAINT player_games_pkey PRIMARY KEY (player_id, chess_game_id);

ALTER TABLE ONLY public.player_motif_events
    ADD CONSTRAINT player_motif_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.player_puzzle_exposure
    ADD CONSTRAINT player_puzzle_exposure_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.player_puzzle_skip
    ADD CONSTRAINT player_puzzle_skip_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.player_puzzle_state
    ADD CONSTRAINT player_puzzle_state_pkey PRIMARY KEY (player_id, puzzle_id);

ALTER TABLE ONLY public.players
    ADD CONSTRAINT players_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.puzzle_attempts
    ADD CONSTRAINT puzzle_attempts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.puzzles
    ADD CONSTRAINT puzzles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.repertoire_annotations
    ADD CONSTRAINT repertoire_annotations_line_fen_uniq UNIQUE (player_id, line_id, fen_norm);

ALTER TABLE ONLY public.repertoire_annotations
    ADD CONSTRAINT repertoire_annotations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.repertoire_lines
    ADD CONSTRAINT repertoire_lines_chapter_id_moves_key UNIQUE (chapter_id, moves);

ALTER TABLE ONLY public.repertoire_lines
    ADD CONSTRAINT repertoire_lines_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.review_detection_state
    ADD CONSTRAINT review_detection_state_pkey PRIMARY KEY (player_id);

ALTER TABLE ONLY public.review_events
    ADD CONSTRAINT review_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.review_events
    ADD CONSTRAINT review_events_player_id_chess_game_id_anchor_ply_key UNIQUE (player_id, chess_game_id, anchor_ply);

ALTER TABLE ONLY public.review_pool_state
    ADD CONSTRAINT review_pool_state_pkey PRIMARY KEY (player_id, pool_id);

CREATE INDEX idx_chess_games_variant_played_at ON public.chess_games USING btree (variant, played_at DESC) WHERE (variant <> 'standard'::text);

CREATE INDEX idx_opponent_profiles_player_id ON public.opponent_profiles USING btree (player_id);

CREATE INDEX idx_opponent_sources_profile_id ON public.opponent_sources USING btree (opponent_profile_id);

CREATE INDEX idx_puzzle_attempts_player ON public.puzzle_attempts USING btree (player_id, puzzle_id);

CREATE UNIQUE INDEX idx_puzzles_repertoire_line ON public.puzzles USING btree (repertoire_line_id) WHERE (repertoire_line_id IS NOT NULL);

CREATE INDEX idx_repertoire_lines_fen_sequence ON public.repertoire_lines USING gin (fen_sequence);

CREATE UNIQUE INDEX ix_ai_explanation_cache_canonical ON public.ai_explanation_cache USING btree (canonical_fen, prompt_hash, context_hash);

CREATE INDEX ix_blunders_canonical_fen ON public.blunders USING btree (canonical_fen);

CREATE INDEX ix_blunders_classification ON public.blunders USING btree (classification);

CREATE INDEX ix_blunders_fen ON public.blunders USING btree (fen);

CREATE INDEX ix_blunders_player_game ON public.blunders USING btree (player_id, chess_game_id);

CREATE INDEX ix_books_player_id ON public.books USING btree (player_id);

CREATE INDEX ix_chapters_book_id ON public.chapters USING btree (book_id);

CREATE UNIQUE INDEX ix_chapters_identity ON public.chapters USING btree (book_id, title, root_fen) NULLS NOT DISTINCT;

CREATE INDEX ix_chess_games_pending ON public.chess_games USING btree (analysis_status) WHERE (analysis_status = ANY (ARRAY['unanalyzed'::text, 'failed_retryable'::text, 'pending'::text]));

CREATE INDEX ix_chess_games_played_at ON public.chess_games USING btree (played_at DESC);

CREATE INDEX ix_chess_games_position_keys ON public.chess_games USING gin (position_keys);

CREATE UNIQUE INDEX ix_dismissed_blunder_fens_player_canonical ON public.dismissed_blunder_fens USING btree (player_id, canonical_fen);

CREATE INDEX ix_grl_grr ON public.game_result_lines USING btree (game_repertoire_result_id);

CREATE INDEX ix_grl_line_id ON public.game_result_lines USING btree (line_id);

CREATE INDEX ix_grr_canonical_fen ON public.game_repertoire_results USING btree (canonical_fen);

CREATE INDEX ix_grr_chess_game ON public.game_repertoire_results USING btree (chess_game_id);

CREATE INDEX ix_grr_deviation_fen ON public.game_repertoire_results USING btree (deviation_fen);

CREATE INDEX ix_grr_player ON public.game_repertoire_results USING btree (player_id);

CREATE INDEX ix_learn_commits_player_created ON public.learn_commits USING btree (player_id, created_at DESC);

CREATE INDEX ix_lichess_puzzles_rating ON public.lichess_puzzles USING btree (rating);

CREATE INDEX ix_lichess_puzzles_themes ON public.lichess_puzzles USING gin (themes);

CREATE INDEX ix_lichess_puzzles_topk ON public.lichess_puzzles USING btree (popularity DESC, nb_plays DESC, puzzle_id) INCLUDE (fen, rating, themes, color, solution_line);

CREATE INDEX ix_opponent_views_chess_game ON public.opponent_views USING btree (chess_game_id);

CREATE INDEX ix_player_games_player ON public.player_games USING btree (player_id);

CREATE INDEX ix_player_games_unanalyzed ON public.player_games USING btree (player_id) WHERE (analyzed_at_depth IS NULL);

CREATE UNIQUE INDEX ix_player_motif_events_perply ON public.player_motif_events USING btree (player_id, chess_game_id, ply, metric_type, theme) WHERE (metric_type <> 'endgame'::text);

CREATE INDEX ix_player_motif_events_player_theme ON public.player_motif_events USING btree (player_id, metric_type, theme);

CREATE INDEX ix_ppe_player_puzzle ON public.player_puzzle_exposure USING btree (player_id, puzzle_id);

CREATE INDEX ix_ppe_player_scope_id ON public.player_puzzle_exposure USING btree (player_id, scope, id DESC);

CREATE INDEX ix_pps_due ON public.player_puzzle_state USING btree (player_id, next_show_at) WHERE (level <> 'king'::text);

CREATE INDEX ix_pps_retired ON public.player_puzzle_state USING btree (player_id) WHERE (level = 'king'::text);

CREATE UNIQUE INDEX ix_puzzle_attempts_idempotency ON public.puzzle_attempts USING btree (player_id, attempt_id) WHERE (attempt_id IS NOT NULL);

CREATE UNIQUE INDEX ix_puzzles_one_per_player_line ON public.puzzles USING btree (player_id, repertoire_line_id) WHERE (is_repertoire = true);

CREATE INDEX ix_puzzles_player_active_repertoire ON public.puzzles USING btree (player_id, active) WHERE (is_repertoire = true);

CREATE UNIQUE INDEX ix_puzzles_player_standard_fen ON public.puzzles USING btree (player_id, canonical_fen) WHERE ((is_repertoire = false) AND (active = true));

CREATE INDEX ix_repertoire_annotations_source ON public.repertoire_annotations USING btree (player_id, source, source_ref);

CREATE INDEX ix_repertoire_lines_chapter_id ON public.repertoire_lines USING btree (chapter_id);

CREATE INDEX ix_repertoire_lines_material_keys ON public.repertoire_lines USING gin (material_keys);

CREATE INDEX ix_repertoire_lines_position_keys ON public.repertoire_lines USING gin (position_keys);

CREATE INDEX ix_review_events_player_route ON public.review_events USING btree (player_id, base_route);

CREATE UNIQUE INDEX repertoire_annotations_unattached_uniq ON public.repertoire_annotations USING btree (player_id, fen_norm) WHERE (line_id IS NULL);

CREATE UNIQUE INDEX ux_pps_puzzle ON public.player_puzzle_skip USING btree (player_id, scope, batch_id, puzzle_id);

ALTER TABLE ONLY public.blunders
    ADD CONSTRAINT blunders_chess_game_id_player_id_fkey FOREIGN KEY (chess_game_id, player_id) REFERENCES public.player_games(chess_game_id, player_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.books
    ADD CONSTRAINT books_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.chapters
    ADD CONSTRAINT chapters_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.dismissed_blunder_fens
    ADD CONSTRAINT dismissed_blunder_fens_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.seen_blunder_boards
    ADD CONSTRAINT seen_blunder_boards_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.game_repertoire_results
    ADD CONSTRAINT game_repertoire_results_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.game_repertoire_results
    ADD CONSTRAINT game_repertoire_results_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.chapters(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.game_repertoire_results
    ADD CONSTRAINT game_repertoire_results_chess_game_id_player_id_fkey FOREIGN KEY (chess_game_id, player_id) REFERENCES public.player_games(chess_game_id, player_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.game_result_lines
    ADD CONSTRAINT game_result_lines_game_repertoire_result_id_fkey FOREIGN KEY (game_repertoire_result_id) REFERENCES public.game_repertoire_results(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.game_result_lines
    ADD CONSTRAINT game_result_lines_line_id_fkey FOREIGN KEY (line_id) REFERENCES public.repertoire_lines(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.learn_commits
    ADD CONSTRAINT learn_commits_chess_game_id_player_id_fkey FOREIGN KEY (chess_game_id, player_id) REFERENCES public.player_games(chess_game_id, player_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.opponent_profiles
    ADD CONSTRAINT opponent_profiles_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.opponent_sources
    ADD CONSTRAINT opponent_sources_opponent_profile_id_fkey FOREIGN KEY (opponent_profile_id) REFERENCES public.opponent_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.opponent_views
    ADD CONSTRAINT opponent_views_chess_game_id_fkey FOREIGN KEY (chess_game_id) REFERENCES public.chess_games(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.opponent_views
    ADD CONSTRAINT opponent_views_opponent_profile_id_fkey FOREIGN KEY (opponent_profile_id) REFERENCES public.opponent_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_games
    ADD CONSTRAINT player_games_chess_game_id_fkey FOREIGN KEY (chess_game_id) REFERENCES public.chess_games(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_games
    ADD CONSTRAINT player_games_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_motif_events
    ADD CONSTRAINT player_motif_events_player_game_fkey FOREIGN KEY (chess_game_id, player_id) REFERENCES public.player_games(chess_game_id, player_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_puzzle_exposure
    ADD CONSTRAINT player_puzzle_exposure_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_puzzle_exposure
    ADD CONSTRAINT player_puzzle_exposure_puzzle_id_fkey FOREIGN KEY (puzzle_id) REFERENCES public.puzzles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_puzzle_skip
    ADD CONSTRAINT player_puzzle_skip_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_puzzle_skip
    ADD CONSTRAINT player_puzzle_skip_puzzle_id_fkey FOREIGN KEY (puzzle_id) REFERENCES public.puzzles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_puzzle_state
    ADD CONSTRAINT player_puzzle_state_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.player_puzzle_state
    ADD CONSTRAINT player_puzzle_state_puzzle_id_fkey FOREIGN KEY (puzzle_id) REFERENCES public.puzzles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.puzzle_attempts
    ADD CONSTRAINT puzzle_attempts_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.puzzle_attempts
    ADD CONSTRAINT puzzle_attempts_puzzle_id_fkey FOREIGN KEY (puzzle_id) REFERENCES public.puzzles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.puzzles
    ADD CONSTRAINT puzzles_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.puzzles
    ADD CONSTRAINT puzzles_repertoire_line_id_fkey FOREIGN KEY (repertoire_line_id) REFERENCES public.repertoire_lines(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.repertoire_annotations
    ADD CONSTRAINT repertoire_annotations_book_id_fkey FOREIGN KEY (book_id) REFERENCES public.books(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.repertoire_annotations
    ADD CONSTRAINT repertoire_annotations_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.chapters(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.repertoire_annotations
    ADD CONSTRAINT repertoire_annotations_line_id_fkey FOREIGN KEY (line_id) REFERENCES public.repertoire_lines(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.repertoire_annotations
    ADD CONSTRAINT repertoire_annotations_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.repertoire_lines
    ADD CONSTRAINT repertoire_lines_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.chapters(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.review_detection_state
    ADD CONSTRAINT review_detection_state_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.review_events
    ADD CONSTRAINT review_events_chess_game_id_player_id_fkey FOREIGN KEY (chess_game_id, player_id) REFERENCES public.player_games(chess_game_id, player_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.review_pool_state
    ADD CONSTRAINT review_pool_state_player_id_fkey FOREIGN KEY (player_id) REFERENCES public.players(id) ON DELETE CASCADE;

-- ---------------------------------------------------------------------------
-- Tables new in this schema
-- ---------------------------------------------------------------------------

-- Exactly one row (id = 1): every user-tunable setting as JSON, validated by core/settings.py.
CREATE TABLE public.settings (
    id integer NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT settings_pkey PRIMARY KEY (id),
    CONSTRAINT settings_single_row CHECK ((id = 1))
);

-- One row per pipeline step run: what ran, when, outcome, a small JSON summary. Drives the Home page's pipeline line and the ops alert.
CREATE TABLE public.pipeline_runs (
    id bigint NOT NULL,
    step text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    status text NOT NULL,
    summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    error text,
    CONSTRAINT pipeline_runs_pkey PRIMARY KEY (id),
    CONSTRAINT pipeline_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'ok'::text, 'failed'::text])))
);
CREATE SEQUENCE public.pipeline_runs_id_seq AS bigint START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.pipeline_runs_id_seq OWNED BY public.pipeline_runs.id;
ALTER TABLE ONLY public.pipeline_runs ALTER COLUMN id SET DEFAULT nextval('public.pipeline_runs_id_seq'::regclass);
CREATE INDEX ix_pipeline_runs_started_at ON public.pipeline_runs USING btree (started_at DESC);

-- One row per AI call that reached a provider: what the hourly and daily caps count.
CREATE TABLE public.ai_calls (
    id bigint NOT NULL,
    called_at timestamp with time zone DEFAULT now() NOT NULL,
    prompt_key text NOT NULL,
    model text NOT NULL,
    input_tokens integer,
    output_tokens integer,
    CONSTRAINT ai_calls_pkey PRIMARY KEY (id)
);
CREATE SEQUENCE public.ai_calls_id_seq AS bigint START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.ai_calls_id_seq OWNED BY public.ai_calls.id;
ALTER TABLE ONLY public.ai_calls ALTER COLUMN id SET DEFAULT nextval('public.ai_calls_id_seq'::regclass);
CREATE INDEX ix_ai_calls_called_at ON public.ai_calls USING btree (called_at DESC);

-- Which schema version this database is at. Written by `pipeline db init` (fresh) and `pipeline db upgrade`.
CREATE TABLE public.schema_version (
    version integer NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schema_version_pkey PRIMARY KEY (version)
);
