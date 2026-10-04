-- The Review page's position sections, computed once an hour for every time class and opening
-- the page offers, so a request reads one row instead of aggregating every game's opening
-- prefix. `fingerprint` names what the body was computed from (the settings it depends on, the
-- newest game, the evaluations); a request whose fingerprint differs computes the sections
-- itself. Additive: applied before the code that writes it is deployed.
CREATE TABLE public.review_snapshots (
    time_class text NOT NULL,
    opening text NOT NULL,
    fingerprint text NOT NULL,
    body jsonb NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT review_snapshots_pkey PRIMARY KEY (time_class, opening)
);
