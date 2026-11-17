-- Conversations about one repertoire chapter (core/repertoire/chapter_idea.py). Two new tables, no
-- change to an existing one, so there is no existing row whose NULLs need meaning.
--
-- chapter_idea_conversations: what the first call sent and every follow-up resends: the rendered
-- chapter (the cached block), the line map the answer's tags were resolved against, and the
-- effective parameters frozen at the first call. full_reason NULL means no follow-up has been
-- refused by the provider's count; the turn cap is computed from the turns. asking_until is set
-- while a follow-up is being asked and cleared when it is answered or fails; NULL or past means none
-- is in flight. A conversation goes only with its chapter.
CREATE TABLE public.chapter_idea_conversations (
    id bigint NOT NULL,
    chapter_id integer NOT NULL,
    params jsonb NOT NULL,
    prompt_hash text NOT NULL,
    context_hash text NOT NULL,
    rendered_prompt text NOT NULL,
    lines jsonb NOT NULL,
    full_reason text,
    asking_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chapter_idea_conversations_pkey PRIMARY KEY (id),
    CONSTRAINT chapter_idea_conversations_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.chapters(id) ON DELETE CASCADE
);
CREATE SEQUENCE public.chapter_idea_conversations_id_seq AS bigint START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.chapter_idea_conversations_id_seq OWNED BY public.chapter_idea_conversations.id;
ALTER TABLE ONLY public.chapter_idea_conversations ALTER COLUMN id SET DEFAULT nextval('public.chapter_idea_conversations_id_seq'::regclass);
CREATE INDEX ix_chapter_idea_conversations_chapter ON public.chapter_idea_conversations USING btree (chapter_id, created_at DESC);

-- One question and its answer: the answer as text and as the resolved segments the dialog draws,
-- and the provider's token counts (NULL where the reply carried none).
CREATE TABLE public.chapter_idea_turns (
    conversation_id bigint NOT NULL,
    seq integer NOT NULL,
    question text NOT NULL,
    answer text NOT NULL,
    segments jsonb NOT NULL,
    input_tokens integer,
    cache_read_tokens integer,
    cache_write_tokens integer,
    output_tokens integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chapter_idea_turns_pkey PRIMARY KEY (conversation_id, seq),
    CONSTRAINT chapter_idea_turns_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chapter_idea_conversations(id) ON DELETE CASCADE
);
