/**
 * Review latest game, at `/review/latest?source=chesscom|lichess`: the player's most recent finished
 * game on one site, fetched and parsed by the server (GET /review/latest) and never stored, stepped
 * through from the start position. At every board the in-browser Stockfish 19 searches as the
 * player lands on it and draws the best move; the repertoire's move, its book › chapter › line and
 * Read the whole line are the per-game review's own panel and walk-through (`reviewWalk.ts`).
 * Ask Opus asks over the board on show and the engine's finished snapshot for it, with no stored
 * game (`game: null`); Explore from here opens the Explore layer on the board.
 *
 * The engine: one worker per depth. The body is keyed by the depth in use, so every result the
 * worker gives is a search at that depth, and a depth change starts a fresh worker with an empty
 * cache. A finished search is kept per board for the visit: stepping back to a board already
 * searched draws it at once and asks for nothing. Only a result for the board on show is drawn.
 * A board where the game is over is not searched.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Chessboard } from "react-chessboard";
import { api, ApiError } from "../api";
import { askExplore, askExploreDryRun } from "../ask";
import type { ExploreAskRequest } from "../ask";
import { AskOpusPanel } from "../components/AskOpusPanel";
import { EvalBar } from "../components/EvalBar";
import { ExploreLayer } from "../components/ExploreLayer";
import { ArrowLegend, RepertoirePanel, ReviewLineReader } from "../components/ReviewBits";
import { engineSnapshot, fmtCp, uciPvToSan } from "../engine/eval";
import { DEFAULT_DEPTH, clampDepth, useStockfish } from "../engine/useStockfish";
import type { EngineEval } from "../engine/useStockfish";
import { getLatestReview } from "../games";
import { SITE_LABEL, SLOW_FETCH_MS, boardIsOver, sourceOf } from "../latestReview";
import type { LatestReviewResponse, RepertoireEntry, RepertoireProjection } from "../games";
import { ARROWS, HIGHLIGHT, SQUARES } from "../utils/board";
import { appendBookArrow, buildPgn, parsesAsFen, spineLastMove } from "../utils/chess";
import type { BoardArrow } from "../utils/chess";
import { walkSurvives } from "./reviewWalk";
import type { ReviewWalk } from "./reviewWalk";

const DEPTH_STEPS = [12, 16, 18, 20, 24];

const btn = "rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900 disabled:opacity-40 disabled:pointer-events-none";
const panel = "rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800";
const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm tabular-nums dark:border-zinc-700 dark:bg-zinc-900";

type Refusal = { code: string; message: string };
type Load = { state: "loading" } | { state: "ok"; data: LatestReviewResponse } | { state: "refused"; refusal: Refusal } | { state: "failed"; message: string | null };

function refusalOf(err: unknown): Refusal | null {
  if (!(err instanceof ApiError)) return null;
  const d = (err.body as { detail?: unknown } | undefined)?.detail as { code?: unknown; message?: unknown } | undefined;
  return d && typeof d.code === "string" && typeof d.message === "string" ? { code: d.code, message: d.message } : null;
}

export default function LatestReview() {
  const [params] = useSearchParams();
  const source = sourceOf(params.get("source"));
  const navigate = useNavigate();
  const exit = useCallback(() => navigate("/"), [navigate]);

  const [attempt, setAttempt] = useState(0);
  // The answer and the slow note belong to the request they were made for (site + attempt): a
  // newer request shows as loading without resetting anything inside the effect.
  const key = `${source}:${attempt}`;
  const [answer, setAnswer] = useState<{ key: string; load: Load } | null>(null);
  const [slowFor, setSlowFor] = useState<string | null>(null);
  useEffect(() => {
    if (!source) return;
    let alive = true;
    const timer = setTimeout(() => alive && setSlowFor(key), SLOW_FETCH_MS);
    getLatestReview(source)
      .then((data) => alive && setAnswer({ key, load: { state: "ok", data } }))
      .catch((err: unknown) => {
        if (!alive) return;
        // A refusal is about the game and has nothing to retry; the site not answering
        // (`fetch_failed`) or any other error is a failure, offered again.
        const refusal = refusalOf(err);
        setAnswer({ key, load: refusal && refusal.code !== "fetch_failed" ? { state: "refused", refusal } : { state: "failed", message: refusal?.message ?? null } });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [source, key]);
  const load: Load = answer?.key === key ? answer.load : { state: "loading" };
  const slow = slowFor === key;

  // The saved Explore depth, read once; the body mounts only once it has settled, so the first
  // worker is already at the depth in use. A pick from the selector is never overridden.
  const [depth, setDepth] = useState<number | null>(null);
  const picked = useRef(false);
  useEffect(() => {
    let alive = true;
    api
      .get<{ explore_engine_depth?: unknown }>("/settings")
      .then((s) => {
        const v = s.explore_engine_depth;
        if (alive && !picked.current) setDepth(typeof v === "number" && Number.isFinite(v) ? clampDepth(v) : DEFAULT_DEPTH);
      })
      .catch(() => {
        if (alive && !picked.current) setDepth(DEFAULT_DEPTH);
      });
    return () => {
      alive = false;
    };
  }, []);

  const data = load.state === "ok" ? load.data : null;
  const fenSeq = data?.game.fen_sequence ?? null;
  const maxPly = fenSeq ? Math.max(0, fenSeq.length - 1) : 0;
  const [ply, setPly] = useState(0);
  const curPly = Math.min(Math.max(ply, 0), maxPly);
  const step = useCallback((dir: 1 | -1) => setPly((p) => Math.max(0, Math.min(maxPly, p + dir))), [maxPly]);

  // The open line belongs to the line, not the ply (029): page state, decided during render.
  const repertoire: RepertoireProjection | null = data?.repertoire ?? null;
  const rawEntry: RepertoireEntry | null = repertoire ? (repertoire.by_ply[String(curPly)] ?? null) : null;
  const [walk, setWalk] = useState<ReviewWalk | null>(null);
  if (walk && !walkSurvives(rawEntry, walk.lineId)) setWalk(null);
  const walkOnShow = walk && walkSurvives(rawEntry, walk.lineId) ? walk : null;

  // Explore is open for the ply it was opened at.
  const [exploreAt, setExploreAt] = useState<number | null>(null);
  const exploring = exploreAt === curPly;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (exploring || e.metaKey || e.altKey || e.ctrlKey) return;
      const t = e.target;
      if (t instanceof HTMLSelectElement || t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") exit();
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exploring, exit, step]);

  const site = source ? SITE_LABEL[source] : null;
  const g = data?.game ?? null;
  const header = g
    ? [
        `${g.player_color === "white" ? "♔" : "♚"} vs ${g.opponent_username ?? "opponent"}${g.opponent_rating ? ` (${g.opponent_rating})` : ""}`,
        g.result,
        g.time_class,
        g.played_at ? new Date(g.played_at).toLocaleString() : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";
  const steps = depth == null || DEPTH_STEPS.includes(depth) ? DEPTH_STEPS : [...DEPTH_STEPS, depth].sort((a, b) => a - b);

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto overscroll-contain bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100" data-testid="latest-review">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex min-w-0 flex-1 items-baseline gap-3">
          <span className="shrink-0 text-sm font-semibold">Latest game{site ? ` · ${site}` : ""}</span>
          {g && <span className="min-w-0 flex-1 truncate text-sm text-zinc-500">{header}</span>}
        </div>
        <button type="button" onClick={exit} aria-label="Close review" title="Close (Esc)" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ✕ Close
        </button>
      </div>

      <div className="mx-auto max-w-5xl px-4 py-6">
        {!source && <p className="text-center text-sm text-zinc-500">Unknown site.</p>}
        {source && load.state === "loading" && (
          <div className="space-y-1 text-center text-sm text-zinc-500" role="status">
            <p>Fetching your latest {site} game…</p>
            {slow && <p data-testid="slow-note">The server may take up to a minute to wake.</p>}
          </div>
        )}
        {source && load.state === "refused" && (
          <div className="space-y-3 text-center" data-testid="latest-refused">
            <p className="text-sm text-zinc-600 dark:text-zinc-400">{load.refusal.message}</p>
            <button type="button" className={btn} onClick={exit}>
              ← Back
            </button>
          </div>
        )}
        {source && load.state === "failed" && (
          <div className="space-y-3 text-center">
            <p className="text-sm text-rose-600">{load.message ?? "Could not fetch the game."}</p>
            <button type="button" className={btn} onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        )}
        {g && fenSeq && depth == null && <p className="text-center text-sm text-zinc-500">Loading engine…</p>}
        {g && fenSeq && depth != null && (
          <>
            <div className="mb-4 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-xs text-zinc-600 dark:text-zinc-400">
              <label className="flex items-center gap-2">
                <span className="text-zinc-500">Engine depth</span>
                <select
                  className={select}
                  value={depth}
                  aria-label="Engine depth"
                  onChange={(e) => {
                    picked.current = true;
                    setDepth(Number(e.target.value));
                  }}
                >
                  {steps.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              {g.url && (
                <a href={g.url} target="_blank" rel="noreferrer" className="underline">
                  View on {SITE_LABEL[g.source]} ↗
                </a>
              )}
            </div>
            <LatestBody
              key={depth}
              depth={depth}
              fenSeq={fenSeq}
              moves={g.moves}
              orientation={g.player_color}
              curPly={curPly}
              maxPly={maxPly}
              setPly={setPly}
              step={step}
              rawEntry={rawEntry}
              walk={walkOnShow}
              setWalk={setWalk}
              exploring={exploring}
              setExploring={(v) => setExploreAt(v ? curPly : null)}
            />
          </>
        )}
      </div>
    </div>
  );
}

function LatestBody(p: {
  depth: number;
  fenSeq: string[];
  moves: string[];
  orientation: "white" | "black";
  curPly: number;
  maxPly: number;
  setPly: (n: number) => void;
  step: (dir: 1 | -1) => void;
  rawEntry: RepertoireEntry | null;
  walk: ReviewWalk | null;
  setWalk: (w: ReviewWalk | null) => void;
  exploring: boolean;
  setExploring: (v: boolean) => void;
}) {
  const { depth, fenSeq, moves, orientation, curPly, maxPly, setPly, step, rawEntry, walk, setWalk, exploring, setExploring } = p;
  const boardId = "latest" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const engine = useStockfish({ enabled: true, depth });
  const { analyze, stop, evalState } = engine;
  const position = fenSeq[curPly] ?? fenSeq[0];
  const over = boardIsOver(position);

  // Finished searches, per board, for this worker's depth: recorded during render (never painted
  // stale) the first time a finished state for a board is seen.
  const [cache, setCache] = useState<ReadonlyMap<string, EngineEval>>(() => new Map());
  if (evalState && !evalState.thinking && !cache.has(evalState.fen)) setCache(new Map(cache).set(evalState.fen, evalState));
  const cached = cache.has(position);
  useEffect(() => {
    if (over || cached) stop();
    else analyze(position, depth);
  }, [position, over, cached, analyze, stop, depth]);

  const ev = over ? null : (cache.get(position) ?? (evalState && evalState.fen === position ? evalState : null));
  const bm = ev?.bestMoveUci ?? null;
  let arrows: BoardArrow[] = bm ? [{ startSquare: bm.slice(0, 2), endSquare: bm.slice(2, 4), color: ARROWS.engine }] : [];
  const repEntry = rawEntry && rawEntry.status !== "not_your_turn" ? rawEntry : null;
  const withBook = appendBookArrow(arrows, position, repEntry?.book_move ?? null);
  arrows = withBook.arrows;
  const bestSan = bm ? uciPvToSan(position, [bm]) || null : null;
  const pvSan = ev && ev.pvUci.length ? uciPvToSan(position, ev.pvUci) : "";

  const squareStyles: Record<string, CSSProperties> = {};
  const last = spineLastMove(fenSeq, moves, curPly);
  if (last) {
    squareStyles[last[0]] = { backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[last[1]] = { backgroundColor: HIGHLIGHT.lastMove };
  }

  const snapshot = ev ? engineSnapshot(ev) : null;
  const request = (): ExploreAskRequest | null => (snapshot ? { seed_fen: position, orientation, moves: [], engine: snapshot, game: null } : null);
  const askReason = over ? "Finished board." : null;
  const askWaiting = askReason === null && (!engine.ready || !snapshot) ? "Waiting for the engine…" : null;
  const status = over ? "The game is over on this board." : !engine.ready ? "Loading engine…" : !ev || ev.thinking ? `Stockfish 19 · depth ${ev?.depth ?? 0}/${depth}` : `Stockfish 19 · depth ${ev.depth}`;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0">
        <div className="flex w-full items-stretch justify-center gap-2">
          <EvalBar evalCp={ev?.evalCp ?? null} flipped={orientation === "black"} />
          <div className="min-w-0 max-w-[560px] flex-1 self-start">
            <Chessboard options={{ id: boardId, position, boardOrientation: orientation, squareStyles, arrows, boardStyle: { borderRadius: "4px" }, ...SQUARES, allowDragging: false, animationDurationInMs: 150 }} />
          </div>
        </div>
        <div className="mt-2 min-h-[20px]">
          <ArrowLegend arrows={arrows} ply="ordinary" />
        </div>
        <div className="mt-2 flex min-h-[44px] flex-wrap items-center justify-center gap-2 sm:gap-3" data-testid="stepper">
          <button type="button" className={`${btn} min-h-11`} onClick={() => setPly(0)} disabled={curPly === 0} aria-label="First move">
            ⏮
          </button>
          <button type="button" className={`${btn} min-h-11 px-5`} onClick={() => step(-1)} disabled={curPly === 0}>
            ← Back
          </button>
          <span className="text-xs tabular-nums text-zinc-500">
            move {curPly} / {maxPly}
          </span>
          <button type="button" className={`${btn} min-h-11 px-5`} onClick={() => step(1)} disabled={curPly === maxPly}>
            Next →
          </button>
          <button type="button" className={`${btn} min-h-11`} onClick={() => setPly(maxPly)} disabled={curPly === maxPly} aria-label="Last move">
            ⏭
          </button>
        </div>
        <div className="mt-2 flex min-h-[32px] items-center justify-center">
          {parsesAsFen(position) && (
            <button type="button" className={btn} onClick={() => setExploring(true)}>
              🔍 Explore from here
            </button>
          )}
        </div>
        <p className="mt-3 text-center text-xs text-zinc-500">
          In-browser analysis by Stockfish 19 (GPL-3.0) ·{" "}
          <a href="/engine/Copying.txt" target="_blank" rel="noreferrer" className="underline">
            licence
          </a>{" "}
          ·{" "}
          <a href="/engine/NNUE-NOTICE.txt" target="_blank" rel="noreferrer" className="underline">
            notices
          </a>{" "}
          ·{" "}
          <a href="/engine/stockfish-source.tar.gz" target="_blank" rel="noreferrer" className="underline">
            source
          </a>
        </p>
      </div>

      <div className="min-w-0 space-y-4">
        <div className={`${panel} space-y-1`} data-testid="engine-panel">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">Best move</span>
            <span className="font-mono text-xs text-zinc-500">{fmtCp(ev?.evalCp ?? null)}</span>
          </div>
          {bestSan && (
            <p>
              <span className="text-zinc-500">Best</span> <span className="font-mono text-emerald-600 dark:text-emerald-400">{bestSan}</span>
            </p>
          )}
          {pvSan && <p className="break-words font-mono text-xs leading-relaxed text-zinc-500">{pvSan}</p>}
          <p className="text-xs text-zinc-500" data-testid="engine-status">
            {status}
          </p>
        </div>

        {repEntry && <RepertoirePanel entry={repEntry} bookArrowDrawn={withBook.drew} walk={walk} onOpen={(lineId, openAt) => setWalk({ lineId, openAt })} onClose={() => setWalk(null)} />}
        {walk && <ReviewLineReader walk={walk} onClose={() => setWalk(null)} />}

        <AskOpusPanel
          fen={position}
          bestMoveSan={bestSan}
          disabledReason={askReason}
          waiting={askWaiting}
          ask={(question, alternative) => {
            const req = request();
            return req ? askExplore(req, question, alternative) : Promise.reject(new Error("Waiting for the engine…"));
          }}
          dryRun={(question, alternative) => {
            const req = request();
            return req ? askExploreDryRun(req, question, alternative) : Promise.reject(new Error("Waiting for the engine…"));
          }}
        />

        <div className={`${panel} bg-zinc-100/60 dark:bg-zinc-900/40`}>
          <span className="text-xs font-medium uppercase tracking-widest text-zinc-500">Moves so far</span>
          <p className="mt-1 line-clamp-4 break-words font-mono text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{buildPgn(moves, curPly) || "—"}</p>
        </div>
      </div>

      {exploring && <ExploreLayer fen={position} orientation={orientation} onClose={() => setExploring(false)} />}
    </div>
  );
}
