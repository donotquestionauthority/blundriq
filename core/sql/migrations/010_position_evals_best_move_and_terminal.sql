-- Review's opening mistakes read, for every board Rob moved from often enough and every board his
-- moves led to, the engine's best move as well as its score, and whether the board is over.
--
-- A board is terminal only through a fact of the board itself: checkmate, or a draw by stalemate
-- or insufficient material. Such a row has no score and no best move; the old one-score check
-- would refuse it, so it is replaced by one that admits exactly the two kinds of row. A stored
-- `mate 0` is the engine's word for a checkmated side to move: it is converted before the new
-- check goes on. A stalemate the engine stored as a score of 0 cannot be told apart here;
-- `pipeline position-evals` re-evaluates every row without a best move and converts it then.
ALTER TABLE public.position_evals
    ADD COLUMN best_move text,
    ADD COLUMN terminal text,
    ADD CONSTRAINT position_evals_terminal_kind CHECK ((terminal = ANY (ARRAY['checkmate'::text, 'draw'::text])));

ALTER TABLE public.position_evals DROP CONSTRAINT position_evals_one_score;

UPDATE public.position_evals SET terminal = 'checkmate', eval_cp = NULL, mate_in = NULL WHERE mate_in = 0;

ALTER TABLE public.position_evals ADD CONSTRAINT position_evals_state CHECK ((
    ((terminal IS NULL) AND ((eval_cp IS NULL) <> (mate_in IS NULL)))
    OR ((terminal IS NOT NULL) AND (eval_cp IS NULL) AND (mate_in IS NULL) AND (best_move IS NULL))
));
