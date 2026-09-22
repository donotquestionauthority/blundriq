-- AI explanations: something for the hourly and daily caps to count, and one dead column.

-- One row per call that reached a provider. A cache hit writes nothing here, and a call
-- that failed has its row removed again, so the caps count what was actually paid for.
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

-- Explanations live in ai_explanation_cache; this column was never written.
ALTER TABLE public.blunders DROP COLUMN ai_explanation;
