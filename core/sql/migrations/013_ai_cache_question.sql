-- The player's question as sent, trimmed, so a cached answer can be shown at its board with what
-- was asked. NULL means no question recorded: a blunder Explain (the prompt has no question), or a
-- row written before the column existed; such a row is still listed, by its label, without a
-- question line. The index serves "every answer at this board, newest first".
ALTER TABLE public.ai_explanation_cache ADD COLUMN question text;
CREATE INDEX ix_ai_explanation_cache_board_time ON public.ai_explanation_cache USING btree (canonical_fen, created_at DESC);
