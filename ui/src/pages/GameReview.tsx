/**
 * The per-game review at `/review/:gameId?ply=N`: a full-screen layer with its own top bar,
 * the board with the eval bar from the stored per-position analysis, a move stepper (← / →,
 * "step only through inaccuracies+", the ply in the URL), the blunder card with the AI
 * explanation, the repertoire panel, and Learn mode — at each prompt ply the eval bar, arrows,
 * card and repertoire panel are blanked by the one `leakBlocked` flag until the player commits a
 * move (or asks to be shown), and the reveal draws the committed / engine / book / game /
 * opponent arrows with a legend. "Explore from here" mounts the Explore layer over the current
 * board. Landing on a game stamps it reviewed once. Escape or Close returns to where the review
 * was opened from (`location.state.from`), else to the worklist.
 *
 * A game whose moves are no longer stored shows a card naming the window setting. A standard
 * game without stored analysis shows the board and the moves with no eval bar, no arrows and no
 * card; Chess960 never reaches this page (the server answers 422).
 */
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, RefObject } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import type { Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { EvalBar } from "../components/EvalBar";
import { ExploreLayer } from "../components/ExploreLayer";
import { AiExplanationPanel } from "../components/PositionCard/AiExplanationPanel";
import { PromotionChooser } from "../components/PromotionChooser";
import { getGameReview, getReviewPrefs, markGameReviewed, setReviewPref } from "../games";
import type { PlyAnalysisEntry, RepertoireEntry, RepertoireProjection, ReviewBlunder, ReviewMode } from "../games";
import { useApi } from "../hooks/useApi";
import { ARROWS, HIGHLIGHT, SQUARES } from "../utils/board";
import { appendBookArrow, buildArrows, buildLearnRevealArrows, buildPgn, parsesAsFen, reviewArrowsForPly, reviewArrowsFromPlyAnalysis, spineLastMove } from "../utils/chess";
import type { BoardArrow, RevealArrows } from "../utils/chess";
import { LEARN_COMMIT_PROMPT, LEARN_COPY, formatElapsed, learnPromptPlies, learnStepPly, useLearnMode } from "./learnMode";
import type { LearnRep } from "./learnMode";

const CLASS_LABEL: Record<string, string> = { miss: "Miss", blunder: "Blunder", mistake: "Mistake", inaccuracy: "Inaccuracy" };
/** Tailwind's `lg`, in pixels: the page's layout uses the `lg:` class and the prompt-surface rule
 *  below has to be readable in JS, so the two must agree. */
const LG_BREAKPOINT_PX = 1024;

const COMMIT_FAILURE_COPY = { network: LEARN_COPY.commitNetwork, conflict: LEARN_COPY.commitConflict, gone: LEARN_COPY.commitGone, invalid: LEARN_COPY.commitInvalid } as const;

const btn = "rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900 disabled:opacity-40 disabled:pointer-events-none";
const panel = "rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800";
const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";

// --- the repertoire panel ---------------------------------------------------------------------------

/** What the repertoire says at the ply, by status. `none` is a statement, not an empty state. The
 *  swatch appears only beside a `book` arrow that is actually on the board — the panel is that
 *  arrow's only legend outside Learn mode. */
function RepertoirePanel({ entry, bookArrowDrawn }: { entry: RepertoireEntry; bookArrowDrawn: boolean }) {
  const provenance = entry.book && entry.chapter && entry.line_name ? `${entry.book} · ${entry.chapter} · ${entry.line_name}` : null;
  return (
    <div className={`${panel} space-y-2`} data-testid="repertoire-panel">
      {provenance && <p className="break-words text-xs text-zinc-500">{provenance}</p>}
      {(entry.status === "match" || entry.status === "agree") && entry.book_move && (
        <p className="break-words">
          {bookArrowDrawn && <span aria-hidden className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ backgroundColor: ARROWS.book }} />}
          <span className="text-zinc-500">{LEARN_COPY.prepPlays}</span> <span className="font-mono">{entry.book_move}</span>.
        </p>
      )}
      {entry.status === "agree" && entry.more_lines > 0 && <p className="text-xs text-zinc-500">+{entry.more_lines} more lines</p>}
      {entry.status === "end_of_line" && <p>{LEARN_COPY.lineEndsHere}</p>}
      {entry.status === "unreadable" && <p>{LEARN_COPY.prepUnreadable}</p>}
      {entry.status === "none" && <p>{LEARN_COPY.notInRepertoire}</p>}
      {entry.status === "conflict" && (
        <>
          <p>{LEARN_COPY.conflictHeadline}</p>
          {/* Every distinct move, in the order received: the server owns the grouping. */}
          <ul className="space-y-1">
            {(entry.conflict ?? []).map((g, i) => (
              <li key={`${g.move}-${i}`} className="break-words">
                <span className="font-mono">{g.move}</span>
                <span className="text-zinc-500">
                  {" "}
                  — {g.book} / {g.chapter} / {g.line_name}
                  {g.more_lines > 0 ? `, +${g.more_lines} more` : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {entry.transposed === true && <p className="text-xs text-zinc-500">{LEARN_COPY.transposedNote}</p>}
    </div>
  );
}

// --- the Learn panel ------------------------------------------------------------------------------

/** The rep and its reveal. Below `lg` the fixed sheet is the sole prompt surface, so the prompt
 *  phase renders nothing here (`return null`, not a hidden class: exactly one copy of each control
 *  exists in the DOM). The reveal renders in flow at every width. */
function LearnPanel({ learn, reveal, engineAvailable, promptInFlow }: { learn: LearnRep; reveal: RevealArrows | null; engineAvailable: boolean; promptInFlow: boolean }) {
  if (!learn.revealed && !promptInFlow) return null;
  const prompt = learn.atCommitStep ? LEARN_COMMIT_PROMPT : learn.prompts[learn.promptIndex];
  return (
    <div className={`${panel} space-y-3`} data-testid="learn-panel">
      {!learn.revealed ? (
        <>
          <div className="flex items-start justify-between gap-3">
            <p className="min-h-[48px] leading-relaxed">{prompt}</p>
            {learn.timed && (
              <span className="shrink-0 font-mono text-xs tabular-nums text-zinc-500" aria-label={LEARN_COPY.a11yElapsed}>
                {formatElapsed(learn.elapsedMs)}
              </span>
            )}
          </div>
          <div className="flex min-h-[36px] flex-wrap items-center gap-2">
            <button type="button" className={btn} onClick={learn.advance} disabled={learn.atCommitStep}>
              {LEARN_COPY.nextQuestion}
            </button>
            <button type="button" className={btn} onClick={learn.skip}>
              {LEARN_COPY.justShowMe}
            </button>
          </div>
          {learn.promotionPending && <PromotionChooser onChoose={learn.choosePromotion} />}
        </>
      ) : (
        <>
          <p>{LEARN_COPY.routineComplete}</p>
          {reveal && reveal.rows.length > 0 && (
            <ul className="space-y-1">
              {reveal.rows.map((r) => (
                <li key={`${r.from}-${r.to}-${r.move}`} className="flex items-baseline gap-2 break-words">
                  {/* A row with no arrow of its own (a promotion to another piece on the same squares) has no swatch. */}
                  <span aria-hidden className="h-2.5 w-2.5 shrink-0 translate-y-0.5 rounded-sm" style={r.drawn ? { backgroundColor: r.color } : undefined} />
                  <span className="text-zinc-500">{r.label}</span>
                  <span className="font-mono" aria-label={r.move}>
                    {r.move}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {!engineAvailable && <p className="text-xs text-zinc-500">{LEARN_COPY.noEngineData}</p>}
          <div className="min-h-[20px] text-xs">
            {learn.commitState === "saving" && <span className="text-zinc-500">{LEARN_COPY.saving}</span>}
            {learn.commitState === "failed" && learn.commitFailure && (
              <span className="flex flex-wrap items-center gap-2 text-zinc-600 dark:text-zinc-400">
                {COMMIT_FAILURE_COPY[learn.commitFailure]}
                {learn.commitFailure === "network" && (
                  <button type="button" onClick={learn.retryCommit} className="underline hover:text-zinc-900 dark:hover:text-zinc-100">
                    {LEARN_COPY.tryAgain}
                  </button>
                )}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// --- the page -------------------------------------------------------------------------------------

type From = { pathname: string; search?: string };

export default function GameReview() {
  const { gameId } = useParams<{ gameId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const numericId = Number(gameId);
  const idValid = Number.isInteger(numericId) && numericId > 0;

  const [ply, setPly] = useState(() => {
    const p = Math.trunc(Number(searchParams.get("ply")));
    return Number.isFinite(p) && p >= 0 ? p : 0;
  });
  const [inaccOnly, setInaccOnly] = useState(false);

  // The mode. Learn — the non-spoiling state — renders until the preference has settled, and a
  // preference the player has already moved the selector away from is not applied over them.
  const [mode, setMode] = useState<ReviewMode>("learn");
  const [storedMode, setStoredMode] = useState<ReviewMode>("learn");
  const [showTimer, setShowTimer] = useState(true);
  const [prefError, setPrefError] = useState(false);
  const [modeSettled, setModeSettled] = useState(false);
  // A field the player has already changed is never overwritten by the initial read, which can
  // land after the game did; and a save's failure reverts only the selection it was for — a
  // newer one has its own generation.
  const modeTouched = useRef(false);
  const storedTouched = useRef(0);
  const timerTouched = useRef(0);
  useEffect(() => {
    let alive = true;
    getReviewPrefs()
      .then((p) => {
        if (!alive) return;
        if (!storedTouched.current) setStoredMode(p.review_default_mode);
        if (!modeTouched.current) setMode(p.review_default_mode);
        if (!timerTouched.current) setShowTimer(p.review_show_timer);
        setModeSettled(true);
      })
      .catch(() => {
        if (alive) setModeSettled(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Entering Learn checks "inaccuracies+ only"; unchecking it inside Learn sticks for the session.
  // The edge detector starts at null so the mount counts as an entry, once the mode has settled.
  const prevMode = useRef<ReviewMode | null>(null);
  useEffect(() => {
    if (!modeSettled) return;
    if (prevMode.current !== "learn" && mode === "learn") setInaccOnly(true);
    prevMode.current = mode;
  }, [mode, modeSettled]);

  const chooseMode = useCallback((next: ReviewMode) => {
    modeTouched.current = true;
    setMode(next);
  }, []);
  const makeDefault = useCallback(() => {
    const target = mode;
    const previous = storedMode;
    const gen = ++storedTouched.current;
    setStoredMode(target);
    setReviewPref({ review_default_mode: target })
      .then(() => setPrefError(false))
      .catch(() => {
        if (gen === storedTouched.current) setStoredMode(previous);
        setPrefError(true);
      });
  }, [mode, storedMode]);
  /** Off means untimed: nothing is measured or sent, and NULL is what lands in the column. */
  const toggleTimer = useCallback(
    (next: boolean) => {
      const previous = showTimer;
      const gen = ++timerTouched.current;
      setShowTimer(next);
      setReviewPref({ review_show_timer: next })
        .then(() => setPrefError(false))
        .catch(() => {
          if (gen === timerTouched.current) setShowTimer(previous);
          setPrefError(true);
        });
    },
    [showTimer],
  );

  const { data, isLoading, error, refetch } = useApi(() => (idValid ? getGameReview(numericId) : Promise.reject(new Error("Invalid game id"))), [numericId]);
  const notAnalysable = error !== null && /not_analysable/.test(error);

  const wrapRef = useRef<HTMLDivElement>(null);
  const [boardSize, setBoardSize] = useState(400);
  useLayoutEffect(() => {
    const measure = () => setBoardSize(Math.max(240, Math.min(wrapRef.current?.clientWidth ?? 400, 560)));
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [data]);

  const game = data?.game ?? null;
  const blunders = useMemo(() => data?.blunders ?? [], [data]);
  const fenSeq = game?.fen_sequence ?? null;
  const maxPly = fenSeq ? Math.max(0, fenSeq.length - 1) : 0;
  const curPly = Math.min(Math.max(ply, 0), maxPly);
  const repertoire: RepertoireProjection | null = data?.repertoire ?? null;

  // A different game under the same route resets the view during render, so the new game never
  // paints at the old ply.
  const [trackedGame, setTrackedGame] = useState(numericId);
  if (trackedGame !== numericId) {
    setTrackedGame(numericId);
    setPly(0);
  }

  // Landing on a game stamps it reviewed, once per game landed on; the server keeps the first.
  useEffect(() => {
    if (!idValid) return;
    markGameReviewed(numericId).catch(() => {
      /* best effort */
    });
  }, [numericId, idValid]);

  const blundersByPly = useMemo(() => {
    const m: Record<number, ReviewBlunder> = {};
    for (const b of blunders) m[b.ply] = b;
    return m;
  }, [blunders]);
  const blunderPlies = useMemo(() => Object.keys(blundersByPly).map(Number).sort((a, b) => a - b), [blundersByPly]);

  const learnAvailable = !!game && game.variant === "standard" && !!fenSeq && !!game.moves;
  const promptPlies = useMemo(
    () => (learnAvailable && game ? learnPromptPlies({ fenSequence: fenSeq, moves: game.moves, playerColor: game.player_color, inaccOnly, blunderPlies }) : []),
    [learnAvailable, game, fenSeq, inaccOnly, blunderPlies],
  );
  const learnActive = mode === "learn" && learnAvailable;

  // One stepper for the buttons and the keys. In Learn it lands only on prompt plies — an explicit
  // branch, never the inaccuracies+ fall-through below, whose guard ignores the filter when a game
  // has no classified plies.
  const stepPly = useCallback(
    (dir: 1 | -1) => {
      setPly((p) => {
        if (learnActive) return learnStepPly(p, dir, promptPlies);
        if (inaccOnly && blunderPlies.length > 0) {
          if (dir === 1) {
            const next = blunderPlies.find((x) => x > p);
            return next != null ? Math.min(next, maxPly) : p;
          }
          let prev: number | null = null;
          for (const x of blunderPlies) {
            if (x < p) prev = x;
            else break;
          }
          return prev != null ? prev : p;
        }
        return dir === 1 ? Math.min(maxPly, p + 1) : Math.max(0, p - 1);
      });
    },
    [learnActive, promptPlies, inaccOnly, blunderPlies, maxPly],
  );

  // The URL carries the ply (replace, so Back is not polluted); the router state rides along, or
  // the exit would lose where the review was opened from.
  useEffect(() => {
    if (!fenSeq) return;
    if (searchParams.get("ply") !== String(curPly)) {
      const next = new URLSearchParams(searchParams);
      next.set("ply", String(curPly));
      setSearchParams(next, { replace: true, state: location.state });
    }
  }, [curPly, fenSeq, searchParams, setSearchParams, location.state]);

  const exit = useCallback(() => {
    const state = location.state as { from?: From } | null;
    const from = state?.from;
    if (from) navigate(`${from.pathname}${from.search ?? ""}`, { state });
    else navigate("/review");
  }, [location.state, navigate]);

  // Explore is open for the ply it was opened at: a ply change closes it, since the layer was
  // seeded from a board that is no longer showing.
  const [exploreAt, setExploreAt] = useState<number | null>(null);
  const exploring = exploreAt === curPly;
  const setExploring = useCallback((v: boolean) => setExploreAt(v ? curPly : null), [curPly]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (exploring || e.metaKey || e.altKey || e.ctrlKey) return;
      const t = e.target;
      if (t instanceof HTMLSelectElement || t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") exit();
      else if (e.key === "ArrowLeft") stepPly(-1);
      else if (e.key === "ArrowRight") stepPly(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exploring, exit, stepPly]);

  const header = game ? `${game.player_color === "white" ? "♔" : "♚"} vs ${game.opponent_username ?? "opponent"}${game.opponent_rating ? ` (${game.opponent_rating})` : ""}${game.result ? ` · ${game.result}` : ""}${game.opening_name ? ` · ${game.opening_name}` : ""}` : "";

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto overscroll-contain bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100" data-testid="game-review">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex min-w-0 flex-1 items-baseline gap-3">
          <span className="shrink-0 text-sm font-semibold">Game review</span>
          {game && <span className="min-w-0 flex-1 truncate text-sm text-zinc-500">{header}</span>}
        </div>
        <button type="button" onClick={exit} aria-label="Close review" title="Close (Esc)" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
          ✕ Close
        </button>
      </div>

      <div className="mx-auto max-w-5xl px-4 py-6">
        {game && fenSeq && (
          <div className="mb-4 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-xs text-zinc-600 dark:text-zinc-400">
            <label className="flex items-center gap-2">
              <span className="text-zinc-500">{LEARN_COPY.modeLabel}</span>
              <select value={mode} onChange={(e) => chooseMode(e.target.value as ReviewMode)} className={select}>
                <option value="learn">{LEARN_COPY.modeLearn}</option>
                <option value="review">{LEARN_COPY.modeReview}</option>
              </select>
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" checked={mode === storedMode} disabled={mode === storedMode} onChange={makeDefault} />
              <span title={mode === storedMode ? LEARN_COPY.alreadyDefault : undefined}>{LEARN_COPY.alwaysStartHere}</span>
            </label>
            {/* Kept in the row invisible outside Learn so switching mode never moves the board. */}
            <label className={`flex items-center gap-2 ${learnActive ? "cursor-pointer" : "invisible pointer-events-none"}`} aria-hidden={!learnActive}>
              <input type="checkbox" checked={showTimer} disabled={!learnActive} onChange={(e) => toggleTimer(e.target.checked)} />
              {LEARN_COPY.timeMyReps}
            </label>
            {prefError && (
              <span role="alert" className="text-zinc-600 dark:text-zinc-400">
                {LEARN_COPY.commitNetwork}
              </span>
            )}
          </div>
        )}

        {isLoading && <p className="text-center text-sm text-zinc-500">{LEARN_COPY.loading}</p>}
        {error && !notAnalysable && (
          <div className="space-y-3 text-center">
            <p className="text-sm text-rose-600">{LEARN_COPY.gameFailed}</p>
            <button type="button" className={btn} onClick={refetch}>
              {LEARN_COPY.tryAgain}
            </button>
          </div>
        )}
        {notAnalysable && <p className="text-center text-sm text-zinc-500">Chess960 games are kept as history and are not reviewed.</p>}

        {game && !fenSeq && (
          <div className={`${panel} space-y-3 p-8 text-center`}>
            <h2 className="text-lg font-semibold">Outside your analysis window</h2>
            <p className="mx-auto max-w-md text-sm text-zinc-500">This game is older than your analysis window (the analysis_game_limit setting), so its moves are no longer stored for in-app review.</p>
            {game.url && (
              <a href={game.url} target="_blank" rel="noreferrer" className="inline-block text-sm underline">
                View on {game.source === "lichess" ? "Lichess" : "Chess.com"} ↗
              </a>
            )}
          </div>
        )}

        {game && fenSeq && (
          <ReviewBody
            fenSeq={fenSeq}
            moves={game.moves}
            curPly={curPly}
            maxPly={maxPly}
            setPly={setPly}
            orientation={game.player_color}
            boardSize={boardSize}
            wrapRef={wrapRef}
            gameId={game.id}
            analysisDepth={game.analyzed ? game.ply_analysis_depth ?? game.analysis_depth : null}
            plyAnalysis={game.analyzed ? game.ply_analysis : null}
            blunderHere={blundersByPly[curPly] ?? null}
            blunderPrev={blundersByPly[curPly - 1] ?? null}
            stepPly={stepPly}
            inaccOnly={inaccOnly}
            setInaccOnly={setInaccOnly}
            blunderPlies={blunderPlies}
            learnActive={learnActive}
            promptPlies={promptPlies}
            repertoire={repertoire}
            showTimer={showTimer}
            exploring={exploring}
            setExploring={setExploring}
          />
        )}
      </div>
    </div>
  );
}

// --- the body -------------------------------------------------------------------------------------

function ReviewBody(p: {
  fenSeq: string[];
  moves: string[] | null;
  curPly: number;
  maxPly: number;
  setPly: (fn: number | ((p: number) => number)) => void;
  orientation: "white" | "black";
  boardSize: number;
  wrapRef: RefObject<HTMLDivElement | null>;
  gameId: number;
  analysisDepth: number | null;
  plyAnalysis: PlyAnalysisEntry[] | null;
  blunderHere: ReviewBlunder | null;
  blunderPrev: ReviewBlunder | null;
  stepPly: (dir: 1 | -1) => void;
  inaccOnly: boolean;
  setInaccOnly: (v: boolean) => void;
  blunderPlies: number[];
  learnActive: boolean;
  promptPlies: number[];
  repertoire: RepertoireProjection | null;
  showTimer: boolean;
  exploring: boolean;
  setExploring: (v: boolean) => void;
}) {
  const { fenSeq, moves, curPly, maxPly, setPly, orientation, boardSize, wrapRef, gameId, analysisDepth, plyAnalysis, blunderHere, blunderPrev, stepPly, inaccOnly, setInaccOnly, blunderPlies, learnActive, promptPlies, repertoire, showTimer, exploring, setExploring } = p;
  const boardId = "review" + useId().replace(/[^a-zA-Z0-9-]/g, "");

  const hasBlunderPlies = blunderPlies.length > 0;
  // The buttons and keys are enabled exactly when `stepPly` would move: in Learn, only a prompt
  // ply in that direction; with the skip on, a blunder ply; else the bounds.
  const stops = learnActive ? promptPlies : inaccOnly && hasBlunderPlies ? blunderPlies : null;
  const canStepBack = stops ? stops.some((x) => x < curPly) : curPly > 0;
  const canStepNext = stops ? stops.some((x) => x > curPly) : curPly < maxPly;
  const position = fenSeq[curPly] ?? fenSeq[0];

  // A rep runs at a prompt ply and nowhere else: a position reached by ⏭ or a deep link that is
  // not one gets the ordinary review surface, never a stuck rep.
  const learnHere = learnActive && promptPlies.includes(curPly);
  const learn = useLearnMode({ enabled: learnHere, gameId, ply: curPly, fen: position, showTimer });
  /** The leak gate: every surface blanked at a prompted ply keys off this one flag. */
  const leakBlocked = learnHere && !learn.revealed;
  const learnCanMove = learnHere && learn.atCommitStep && !learn.revealed;

  // The breakpoint read in JS: one predicate decides which surface is the prompt, so the fixed
  // sheet (below `lg`) and the in-flow panel never both show it.
  const [winWidth, setWinWidth] = useState(() => (typeof window !== "undefined" ? window.innerWidth : LG_BREAKPOINT_PX));
  useEffect(() => {
    const onResize = () => setWinWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const learnSheetOnScreen = learnHere && !learn.revealed && winWidth < LG_BREAKPOINT_PX;

  // Coverage is read from `status`, never from a key's presence; an opponent-turn ply is flattened
  // to null here, once, so nothing downstream renders for it.
  const rawEntry: RepertoireEntry | null = repertoire ? (repertoire.by_ply[String(curPly)] ?? null) : null;
  const repEntry = rawEntry && rawEntry.status !== "not_your_turn" ? rawEntry : null;

  // Which blunder the position is reviewing. Stepping one ply at a time reviews the move after it
  // was played (the flagged move is the previous ply's, now on the board); the inaccuracies+ skip
  // and Learn sit on the decision ply and preview it.
  const onDecision = learnActive || inaccOnly;
  const cardBlunder = onDecision ? blunderHere : blunderPrev;
  const cardPly = onDecision ? curPly : curPly - 1;
  const cardFen = fenSeq[cardPly] ?? fenSeq[0];

  const here = plyAnalysis ? (plyAnalysis[curPly] ?? null) : null;
  const showBar = !!plyAnalysis;
  const effBoard = showBar ? Math.max(240, boardSize - 28) : boardSize;
  // Blanked by feeding the bar null, not by dropping it: hiding the bar would resize the board on
  // reveal, which would itself be a tell.
  const barEvalCp = leakBlocked ? null : here ? here.eval : null;

  const revealEngineSan = here?.best_move ?? null;
  const reveal =
    learnHere && learn.revealed
      ? buildLearnRevealArrows({
          fen: position,
          prevFen: curPly > 0 ? fenSeq[curPly - 1] : null,
          committedMove: learn.committedMove,
          engineMove: revealEngineSan,
          bookMove: repEntry?.book_move ?? null,
          gameMove: moves?.[curPly] ?? null,
          opponentMove: curPly > 0 ? (moves?.[curPly - 1] ?? null) : null,
        })
      : null;

  // The arrows. Before a commit the only arrow is the opponent's last move — it is already
  // visible on the board; every other arrow answers the question being asked.
  let arrows: BoardArrow[];
  let reviewBestMissed = false;
  if (learnHere) {
    if (!learn.revealed) {
      arrows = [];
      const opp = spineLastMove(fenSeq, moves, curPly);
      if (opp) arrows.push({ startSquare: opp[0], endSquare: opp[1], color: ARROWS.opponent });
    } else {
      arrows = reveal ? reveal.arrows : [];
    }
  } else if (cardBlunder) {
    arrows = buildArrows({ fen: cardFen, moves, ply: cardPly, movePlayed: cardBlunder.move_played, bestMove: cardBlunder.best_move });
  } else if (plyAnalysis) {
    const built = reviewArrowsFromPlyAnalysis({ fenSequence: fenSeq, ply: curPly, plyAnalysis, moves });
    arrows = built.arrows;
    reviewBestMissed = built.bestMissed;
  } else {
    const built = reviewArrowsForPly({ fenSequence: fenSeq, ply: curPly, blunderAtPly: blunderHere, blunderAtPrevPly: blunderPrev });
    arrows = built.arrows;
    reviewBestMissed = built.bestMissed;
  }
  // The prep arrow on an ordinary ply: before a commit it is an answer, after one the reveal owns
  // it. Parsed against the board on show, the FEN the entry is keyed to.
  let bookArrowDrawn = false;
  if (!learnHere) {
    const withBook = appendBookArrow(arrows, position, repEntry?.book_move ?? null);
    arrows = withBook.arrows;
    bookArrowDrawn = withBook.drew;
  }

  const squareStyles: Record<string, CSSProperties> = {};
  if (learnHere) {
    if (learn.selected) {
      squareStyles[learn.selected] = { backgroundColor: HIGHLIGHT.selected };
      for (const t of learn.legalTargets) squareStyles[t.to] = { ...squareStyles[t.to], background: t.capture ? HIGHLIGHT.legalRing : HIGHLIGHT.legalDot };
    }
  } else {
    // On a blunder ply the arrows already mark the last move; elsewhere the squares do, framed
    // rather than filled when a yellow bestMissed arrow would share the colour.
    const last = cardBlunder ? null : spineLastMove(fenSeq, moves, curPly);
    if (last) {
      const style: CSSProperties = reviewBestMissed ? { boxShadow: HIGHLIGHT.lastMoveFrame } : { backgroundColor: HIGHLIGHT.lastMove };
      squareStyles[last[0]] = { ...style };
      squareStyles[last[1]] = { ...style };
    }
  }

  const onDrop = useCallback(({ sourceSquare, targetSquare }: { sourceSquare: string; targetSquare: string | null }) => (targetSquare ? learn.move(sourceSquare as Square, targetSquare as Square) : false), [learn]);
  const canExplore = parsesAsFen(position);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0">
        <div ref={wrapRef} className="flex w-full items-stretch justify-center gap-2">
          {showBar && <EvalBar evalCp={barEvalCp} flipped={orientation === "black"} />}
          <div style={{ width: effBoard }}>
            <Chessboard
              options={{
                id: boardId,
                position,
                boardOrientation: orientation,
                squareStyles,
                arrows,
                boardStyle: { borderRadius: "4px" },
                ...SQUARES,
                onPieceDrop: learnCanMove ? onDrop : undefined,
                onSquareClick: learnCanMove ? ({ square }) => learn.squareClick(square as Square) : undefined,
                allowDragging: learnCanMove,
                animationDurationInMs: 150,
              }}
            />
          </div>
        </div>

        <div className="mt-4 flex min-h-[36px] flex-wrap items-center justify-center gap-2 sm:gap-3">
          <button type="button" className={btn} onClick={() => setPly(0)} disabled={curPly === 0} aria-label="First move">
            ⏮
          </button>
          <button type="button" className={btn} onClick={() => stepPly(-1)} disabled={!canStepBack}>
            ← Back
          </button>
          <span className="text-xs tabular-nums text-zinc-500">
            move {curPly} / {maxPly}
          </span>
          <button type="button" className={btn} onClick={() => stepPly(1)} disabled={!canStepNext}>
            Next →
          </button>
          <button type="button" className={btn} onClick={() => setPly(maxPly)} disabled={curPly === maxPly} aria-label="Last move">
            ⏭
          </button>
        </div>

        {hasBlunderPlies && (
          <div className="mt-2 flex items-center justify-center">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
              <input type="checkbox" checked={inaccOnly} onChange={(e) => setInaccOnly(e.target.checked)} />
              Step only through inaccuracies+
            </label>
          </div>
        )}

        {/* Not while a rep is open: the layer's engine would answer the question being asked. */}
        <div className="mt-2 flex min-h-[32px] items-center justify-center">
          {canExplore && !leakBlocked && (
            <button type="button" className={btn} onClick={() => setExploring(true)}>
              🔍 Explore from here
            </button>
          )}
        </div>
        {analysisDepth != null && <p className="mt-2 text-center text-xs text-zinc-500">Analysed at depth {analysisDepth}</p>}
      </div>

      <div className="min-w-0 space-y-4">
        {learnActive && promptPlies.length === 0 && <p className={`${panel} text-zinc-500`}>{LEARN_COPY.nothingToWorkThrough}</p>}

        {learnHere && <LearnPanel learn={learn} reveal={reveal} engineAvailable={revealEngineSan != null} promptInFlow={!learnSheetOnScreen} />}

        {leakBlocked ? null : cardBlunder ? (
          // Never at a prompted ply before the commit: the classification, the cp figure, the best
          // move and the line are all answers, and the card renders every one.
          <div className={`${panel} space-y-3`} data-testid="blunder-card">
            <div className="flex items-center justify-between">
              <span className="font-semibold">{cardBlunder.classification ? (CLASS_LABEL[cardBlunder.classification] ?? cardBlunder.classification) : "Issue"}</span>
              {cardBlunder.cp_loss != null && <span className="font-mono text-xs text-rose-600 dark:text-rose-400">−{cardBlunder.cp_loss}cp</span>}
            </div>
            <div className="space-y-1">
              {cardBlunder.move_played && (
                <p>
                  <span className="text-zinc-500">Played</span> <span className="font-mono text-rose-600 dark:text-rose-400">{cardBlunder.move_played}</span>
                </p>
              )}
              {cardBlunder.best_move && (
                <p>
                  <span className="text-zinc-500">Best</span> <span className="font-mono text-emerald-600 dark:text-emerald-400">{cardBlunder.best_move}</span>
                </p>
              )}
              {cardBlunder.best_line && <p className="break-words font-mono text-xs leading-relaxed text-zinc-500">{cardBlunder.best_line}</p>}
            </div>
            {cardBlunder.fen_occurrence_count > 1 && (
              <p className="text-xs text-zinc-500">
                You've reached this position <span className="font-medium text-zinc-900 dark:text-zinc-100">{cardBlunder.fen_occurrence_count}×</span> across your games —{" "}
                <Link to="/blunders" className="underline">
                  see it on Blunders
                </Link>
                .
              </p>
            )}
            <AiExplanationPanel key={`${gameId}:${cardBlunder.ply}`} chessGameId={gameId} ply={cardBlunder.ply} />
          </div>
        ) : learnHere ? null : here?.best_move ? (
          <div className={`${panel} space-y-2`}>
            <span className="font-semibold">Best move</span>
            <p>
              <span className="text-zinc-500">Best</span> <span className="font-mono text-emerald-600 dark:text-emerald-400">{here.best_move}</span>
            </p>
            {here.best_line && <p className="break-words font-mono text-xs leading-relaxed text-zinc-500">{here.best_line}</p>}
            <p className="text-xs text-zinc-500">No mistake flagged here. Step through with Back / Next (or ← / → keys).</p>
          </div>
        ) : (
          <div className={`${panel} text-zinc-500`}>{plyAnalysis ? "No flagged move here. Step through with Back / Next (or ← / → keys); flagged positions show the engine's best move and an explanation." : "This game has not been analysed yet: the board and the moves only, until the next hourly run reaches it."}</div>
        )}

        {repEntry && !leakBlocked && <RepertoirePanel entry={repEntry} bookArrowDrawn={bookArrowDrawn} />}

        {!!moves && (
          <div className={`${panel} bg-zinc-100/60 dark:bg-zinc-900/40`}>
            <span className="text-xs font-medium uppercase tracking-widest text-zinc-500">Moves so far</span>
            <p className="mt-1 line-clamp-4 break-words font-mono text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">{buildPgn(moves, curPly) || "—"}</p>
          </div>
        )}
      </div>

      {/* Below `lg` the prompt sits over the board, where the player is looking: a fixed sheet
          that clears a phone's bottom toolbar and does not reflow the measured board. */}
      {learnSheetOnScreen && (
        <div className="fixed bottom-0 left-0 right-0 z-[55] border-t border-zinc-200 bg-zinc-50 px-4 pt-3 dark:border-zinc-800 dark:bg-zinc-950" style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 0.75rem)" }} data-testid="learn-sheet">
          <div className="mx-auto flex max-w-5xl items-start gap-3">
            <p className="min-h-[40px] flex-1 text-sm leading-relaxed">{learn.atCommitStep ? LEARN_COMMIT_PROMPT : learn.prompts[learn.promptIndex]}</p>
            {learn.timed && (
              <span className="shrink-0 font-mono text-xs tabular-nums text-zinc-500" aria-label={LEARN_COPY.a11yElapsed}>
                {formatElapsed(learn.elapsedMs)}
              </span>
            )}
          </div>
          <div className="mx-auto mt-2 flex max-w-5xl flex-wrap items-center gap-2">
            <button type="button" className={btn} onClick={learn.advance} disabled={learn.atCommitStep}>
              {LEARN_COPY.nextQuestion}
            </button>
            <button type="button" className={btn} onClick={learn.skip}>
              {LEARN_COPY.justShowMe}
            </button>
          </div>
          {learn.promotionPending && <PromotionChooser onChoose={learn.choosePromotion} />}
        </div>
      )}

      {exploring && <ExploreLayer fen={position} orientation={orientation} onClose={() => setExploring(false)} />}
    </div>
  );
}
