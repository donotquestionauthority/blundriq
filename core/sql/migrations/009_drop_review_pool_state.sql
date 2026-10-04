-- Review no longer rotates a representative game per worklist node: the page ranks positions
-- and habits at read time and stores nothing. Applied only after the API that stopped reading
-- and writing this table is live, so the old API never runs without it.
DROP TABLE public.review_pool_state;
