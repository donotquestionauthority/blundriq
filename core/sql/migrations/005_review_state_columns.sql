-- Review: drop the two columns of the old writer's dirty-check.

-- They were a freshness signature and a knobs digest that decided whether a player's events
-- needed regenerating. `pipeline review` (core/review/run.py) retags the whole window every
-- run, so nothing reads or writes them.
ALTER TABLE public.review_detection_state DROP COLUMN review_input_sig, DROP COLUMN review_knobs_digest;
