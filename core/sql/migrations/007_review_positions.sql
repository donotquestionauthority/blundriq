-- Review positions: an opening prefix kept for every game, and one engine evaluation per board.

-- The prefix is the first 30 moves (SAN) and the position keys of the first 31 positions. It
-- outlives the analysis window: housekeeping nulls moves / fen_sequence beyond the window and
-- names its columns, so these two stay. NULL means "not recorded yet" (a game imported before
-- this migration and outside the window, until `pipeline backfill-openings` reaches it), never
-- "no opening". Only analysable variants get one; the variant list below is pinned to
-- core.constants.ANALYSABLE_VARIANTS by tests/test_review_positions.py.
CREATE OR REPLACE FUNCTION public.bq_opening_keys(fens jsonb)
 RETURNS bigint[]
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
  SELECT public.bq_position_keys(jsonb_path_query_array(fens, '$[0 to 30]'))
$function$
;

ALTER TABLE public.chess_games
    ADD COLUMN opening_moves jsonb,
    ADD COLUMN opening_keys bigint[];

UPDATE public.chess_games
   SET opening_moves = jsonb_path_query_array(moves, '$[0 to 29]'),
       opening_keys = public.bq_opening_keys(fen_sequence)
 WHERE moves IS NOT NULL AND fen_sequence IS NOT NULL AND variant IN ('standard');

CREATE INDEX ix_chess_games_opening_keys ON public.chess_games USING gin (opening_keys);

-- One engine evaluation per board (board_key = bq_position_key of the six-field fen), from
-- White's point of view like ply_analysis: eval_cp, or mate_in in moves. Written by
-- `pipeline position-evals`; read by the Review page to say whether a position was already
-- worse when Rob got there.
CREATE TABLE public.position_evals (
    board_key bigint NOT NULL,
    fen text NOT NULL,
    eval_cp integer,
    mate_in integer,
    depth integer NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT position_evals_pkey PRIMARY KEY (board_key),
    CONSTRAINT position_evals_one_score CHECK (((eval_cp IS NULL) <> (mate_in IS NULL)))
);
