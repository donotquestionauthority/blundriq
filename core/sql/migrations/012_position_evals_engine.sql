-- Which engine wrote a position evaluation. Rows written before this column exist were all
-- written by the previous engine, so NULL reads as "another engine" and the row is pending
-- again (core/review/mistakes.py eval_candidates); a terminal row (checkmate, a draw by the board
-- alone) depends on no engine and is never redone, whatever its stamp. Every write sets it.
ALTER TABLE public.position_evals ADD COLUMN engine text;
