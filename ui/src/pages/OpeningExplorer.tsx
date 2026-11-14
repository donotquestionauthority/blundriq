/**
 * The Opening Explorer, at `/explorer?colour=white|black&moves=e4,c6,d4&time_class=focus|all`: an
 * opening stepped through from the start as one colour, over the player's own repertoire and games.
 *
 * The URL carries the board and everything the page shows about it: the colour, the path from the
 * start in SAN and the time class. Each path is one GET /explorer/position, whose answer says what
 * the repertoire prescribes or prepares there, the player's games from the board, and whether the
 * path's last move left the book (`arrival`) — so a reload or a deep link shows what stepping
 * there showed. An answer is kept per request key for the visit and painted only while its key is
 * the URL's; while the path's own answer is pending or failed nothing from another answer is shown.
 *
 * Visit-only, not in the URL: the Forward branch (moves stepped back over, cleared by a different
 * move, a colour change or a URL the page did not write), the engine depth picked, the lines
 * picker, and Ask Opus's answers on show. A move or a step replaces the history entry, so browser
 * Back leaves the page; an entry from a card or from Review pushes.
 *
 * The engine: one worker per depth, the body keyed by the depth in use, a finished search kept per
 * board for the visit; a finished board is not searched (as on Review latest game).
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { Chess } from "chess.js";
import type { Square } from "chess.js";
import { Chessboard } from "react-chessboard";
import { api } from "../api";
import { askExplore, askExploreDryRun } from "../ask";
import type { ExploreAskRequest } from "../ask";
import { AskOpusPanel } from "../components/AskOpusPanel";
import { EvalBar } from "../components/EvalBar";
import { PromotionChooser } from "../components/PromotionChooser";
import type { PromotionPiece } from "../components/PromotionChooser";
import { ArrowLegend, ReviewLineReader } from "../components/ReviewBits";
import { engineSnapshot, fmtCp, uciPvToSan } from "../engine/eval";
import { DEFAULT_DEPTH, clampDepth, useStockfish } from "../engine/useStockfish";
import type { EngineEval } from "../engine/useStockfish";
import { EXPLORER_LINES_PAGE, getExplorerLines, getExplorerPosition, prescribedMoves } from "../explorer";
import type { ExplorerLines, ExplorerNumbers, ExplorerPosition, RepertoireMove } from "../explorer";
import { EXPLORER_MAX_PLIES, explorerSearch, readExplorerUrl, requestKey } from "../utils/explorer";
import type { Colour, ExplorerTimeClass, ExplorerUrl } from "../utils/explorer";
import { boardIsOver } from "../latestReview";
import { pct } from "../review";
import { ARROWS, HIGHLIGHT, SQUARES } from "../utils/board";
import { legalMove, sanToSquares } from "../utils/chess";
import type { BoardArrow } from "../utils/chess";
import type { ReviewWalk } from "./reviewWalk";

const DEPTH_STEPS = [12, 16, 18, 20, 24];
const btn = "rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900 disabled:opacity-40 disabled:pointer-events-none";
const panel = "rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800";
const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm tabular-nums dark:border-zinc-700 dark:bg-zinc-900";
const link = "whitespace-nowrap text-xs text-sky-700 hover:underline dark:text-sky-400";

type Answer = { state: "ok"; data: ExplorerPosition } | { state: "failed" };

/** The board a legal path reaches, and the squares of its last move. */
function replayed(moves: string[]): { fen: string; last: [string, string] | null } {
  const g = new Chess();
  let last: [string, string] | null = null;
  for (const m of moves) {
    const mv = legalMove(g, m);
    if (!mv) break;
    last = [mv.from, mv.to];
  }
  return { fen: g.fen(), last };
}

/** "1.e4 c6 2.d4" with each move a jump; the plies past `current` are the Forward branch. */
function MoveList({ all, current, onJump }: { all: string[]; current: number; onJump: (ply: number) => void }) {
  if (all.length === 0) return <p className="font-mono text-xs text-zinc-500">Starting position</p>;
  return (
    <p className="break-words font-mono text-xs leading-relaxed" data-testid="explorer-moves">
      {all.map((m, i) => (
        <span key={i}>
          {i % 2 === 0 && <span className="text-zinc-400">{i / 2 + 1}.</span>}
          <button type="button" onClick={() => onJump(i + 1)} data-forward={i >= current ? "true" : undefined} aria-current={i + 1 === current ? "step" : undefined} className={`mr-1 hover:underline ${i >= current ? "text-zinc-400" : i + 1 === current ? "font-semibold" : ""}`}>
            {m}
          </button>
        </span>
      ))}
    </p>
  );
}

function numbers(n: ExplorerNumbers): string {
  if (n.n === 0) return "no games";
  const parts = [`${n.n} game${n.n === 1 ? "" : "s"}`, `+${n.w} =${n.d} −${n.l}`, n.score != null ? pct(n.score) : null];
  return parts.filter(Boolean).join(" · ");
}

function vsExpectation(n: ExplorerNumbers): string {
  if (n.vs_expectation == null) return "—";
  const v = Math.round(n.vs_expectation * 100);
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v)}`;
}

export default function OpeningExplorer() {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const url = useMemo(() => readExplorerUrl(params), [params]);
  const { colour, moves, timeClass } = url;

  // The Forward branch is the visit's: a URL the page did not write itself (a reload, a deep
  // link, an entry, the nav) starts without one.
  const [forward, setForward] = useState<string[]>([]);
  const own = useRef<string | null>(null);
  const [seen, setSeen] = useState(location.search);
  if (seen !== location.search) {
    setSeen(location.search);
    if (own.current !== location.search) setForward([]);
  }
  const go = useCallback(
    (next: ExplorerUrl, fwd: string[]) => {
      const search = explorerSearch(next);
      own.current = search;
      setForward(fwd);
      navigate({ pathname: "/explorer", search }, { replace: true, state: location.state });
    },
    [navigate, location.state],
  );
  useEffect(() => {
    if (!url.rewrite) return;
    const search = explorerSearch(url);
    own.current = search;
    navigate({ pathname: "/explorer", search }, { replace: true, state: location.state });
  }, [url, navigate, location.state]);

  // One answer per request key for the visit; a late answer is kept, and painted only on its board.
  const key = requestKey(url);
  const [answers, setAnswers] = useState<ReadonlyMap<string, Answer>>(() => new Map());
  const answersRef = useRef(answers);
  answersRef.current = answers;
  const inflight = useRef(new Set<string>());
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (url.rewrite) return;
    if (answersRef.current.get(key)?.state === "ok" || inflight.current.has(key)) return;
    const asked: ExplorerUrl = { colour, moves, timeClass };
    inflight.current.add(key);
    if (answersRef.current.has(key))
      setAnswers((m) => {
        const next = new Map(m);
        next.delete(key);
        return next;
      });
    getExplorerPosition(asked)
      .then((data) => setAnswers((m) => new Map(m).set(key, { state: "ok", data })))
      .catch(() => setAnswers((m) => new Map(m).set(key, { state: "failed" })))
      .finally(() => inflight.current.delete(key));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt, url.rewrite]);
  const retry = () => {
    setAnswers((m) => {
      const next = new Map(m);
      next.delete(key);
      return next;
    });
    setAttempt((n) => n + 1);
  };
  const shown = answers.get(key);
  const answer = shown?.state === "ok" ? shown.data : null;

  // The saved Explore depth, read once; a pick from the selector is never overridden.
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

  const all = [...moves, ...forward];
  const play = (san: string) => {
    if (moves.length >= EXPLORER_MAX_PLIES) return;
    go({ colour, moves: [...moves, san], timeClass }, forward[0] === san ? forward.slice(1) : []);
  };
  const jump = (ply: number) => go({ colour, moves: all.slice(0, ply), timeClass }, all.slice(ply));
  const steps = depth == null || DEPTH_STEPS.includes(depth) ? DEPTH_STEPS : [...DEPTH_STEPS, depth].sort((a, b) => a - b);

  return (
    <div data-testid="opening-explorer">
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="text-lg font-semibold">Opening Explorer</h1>
        <div className="flex gap-1" role="group" aria-label="Colour">
          {(["white", "black"] as const).map((c) => (
            <button key={c} type="button" className={`${btn} ${c === colour ? "font-semibold" : "text-zinc-500"}`} aria-pressed={c === colour} onClick={() => c !== colour && go({ colour: c, moves, timeClass }, [])}>
              {c === "white" ? "White" : "Black"}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs">
          <span className="text-zinc-500">Games</span>
          <select className={select} value={timeClass} aria-label="Time class" onChange={(e) => go({ colour, moves, timeClass: e.target.value as ExplorerTimeClass }, forward)}>
            <option value="focus">Focus</option>
            <option value="all">All</option>
          </select>
        </label>
        {depth != null && (
          <label className="flex items-center gap-2 text-xs">
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
        )}
      </div>
      {depth == null ? (
        <p className="text-sm text-zinc-500">Loading engine…</p>
      ) : (
        <ExplorerBody
          key={depth}
          depth={depth}
          url={url}
          requestKey={key}
          answer={answer}
          failed={shown?.state === "failed"}
          onRetry={retry}
          all={all}
          onPlay={play}
          onJump={jump}
        />
      )}
    </div>
  );
}

function ExplorerBody(p: { depth: number; url: ExplorerUrl; requestKey: string; answer: ExplorerPosition | null; failed: boolean; onRetry: () => void; all: string[]; onPlay: (san: string) => void; onJump: (ply: number) => void }) {
  const { depth, url, answer, failed, onRetry, all, onPlay, onJump } = p;
  const { colour, moves } = url;
  const ply = moves.length;
  const boardId = "explorer" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const { fen: position, last } = useMemo(() => replayed(moves), [moves]);
  const over = boardIsOver(position);
  const atBound = ply >= EXPLORER_MAX_PLIES;
  const canMove = !over && !atBound;

  const engine = useStockfish({ enabled: true, depth });
  const { analyze, stop, evalState } = engine;
  const [cache, setCache] = useState<ReadonlyMap<string, EngineEval>>(() => new Map());
  if (evalState && !evalState.thinking && !cache.has(evalState.fen)) setCache(new Map(cache).set(evalState.fen, evalState));
  const cached = cache.has(position);
  useEffect(() => {
    if (over || cached) stop();
    else analyze(position, depth);
  }, [position, over, cached, analyze, stop, depth]);
  const ev = over ? null : (cache.get(position) ?? (evalState && evalState.fen === position ? evalState : null));
  const bm = ev?.bestMoveUci ?? null;
  const bestSan = bm ? uciPvToSan(position, [bm]) || null : null;
  const pvSan = ev && ev.pvUci.length ? uciPvToSan(position, ev.pvUci) : "";

  // Only this path's own answer says anything about the repertoire here.
  const book = answer ? prescribedMoves(answer) : [];
  const playerTurn = new Chess(position).turn() === colour[0];
  const arrows: BoardArrow[] = [];
  if (playerTurn)
    for (const m of book) {
      const sq = sanToSquares(position, m.san);
      if (sq) arrows.push({ startSquare: sq[0], endSquare: sq[1], color: ARROWS.book });
    }
  if (bm && !arrows.some((a) => a.startSquare === bm.slice(0, 2) && a.endSquare === bm.slice(2, 4))) arrows.push({ startSquare: bm.slice(0, 2), endSquare: bm.slice(2, 4), color: ARROWS.engine });

  // Move entry: drag or click; a pawn to the last rank asks for its piece.
  const [selected, setSelected] = useState<Square | null>(null);
  const [walk, setWalk] = useState<ReviewWalk | null>(null);
  const [promo, setPromo] = useState<{ from: Square; to: Square } | null>(null);
  const [selFor, setSelFor] = useState(`${colour}|${position}`);
  if (selFor !== `${colour}|${position}`) {
    setSelFor(`${colour}|${position}`);
    setSelected(null);
    setPromo(null);
    setWalk(null);
  }
  const apply = (from: Square, to: Square, promotion?: PromotionPiece): boolean => {
    const g = new Chess(position);
    let mv;
    try {
      mv = legalMove(g, { from, to, promotion });
    } catch {
      return false;
    }
    if (!mv) return false;
    onPlay(mv.san);
    return true;
  };
  const tryMove = (from: Square, to: Square): boolean => {
    if (!canMove) return false;
    const g = new Chess(position);
    if (g.moves({ square: from, verbose: true }).some((m) => m.to === to && m.promotion)) {
      setPromo({ from, to });
      setSelected(null);
      return false;
    }
    return apply(from, to);
  };
  const squareClick = (square: Square) => {
    if (!canMove) return;
    const g = new Chess(position);
    if (selected) {
      if (square === selected) return setSelected(null);
      if (g.moves({ square: selected, verbose: true }).some((m) => m.to === square)) {
        tryMove(selected, square);
        setSelected(null);
        return;
      }
    }
    const piece = g.get(square);
    setSelected(piece && piece.color === g.turn() && g.moves({ square, verbose: true }).length > 0 ? square : null);
  };
  const squareStyles: Record<string, CSSProperties> = {};
  if (last) {
    squareStyles[last[0]] = { backgroundColor: HIGHLIGHT.lastMove };
    squareStyles[last[1]] = { backgroundColor: HIGHLIGHT.lastMove };
  }
  if (selected) {
    squareStyles[selected] = { backgroundColor: HIGHLIGHT.selected };
    for (const m of new Chess(position).moves({ square: selected, verbose: true })) squareStyles[m.to] = { ...squareStyles[m.to], background: m.captured ? HIGHLIGHT.legalRing : HIGHLIGHT.legalDot };
  }

  const snapshot = ev ? engineSnapshot(ev) : null;
  const request = (): ExploreAskRequest | null => (snapshot ? { seed_fen: position, orientation: colour, moves: [], engine: snapshot, game: null } : null);
  const askReason = over ? "Finished board." : null;
  const askWaiting = askReason === null && (!engine.ready || !snapshot) ? "Waiting for the engine…" : null;
  const status = over ? "The game is over on this board." : !engine.ready ? "Loading engine…" : !ev || ev.thinking ? `Stockfish 19 · depth ${ev?.depth ?? 0}/${depth}` : `Stockfish 19 · depth ${ev.depth}`;
  const offBook = answer?.arrival?.off_book ? answer.arrival.book_moves : null;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0">
        <div className="flex w-full items-stretch justify-center gap-2">
          <EvalBar evalCp={ev?.evalCp ?? null} flipped={colour === "black"} />
          <div className="min-w-0 max-w-[560px] flex-1 self-start" data-testid="explorer-board">
            <Chessboard
              options={{
                id: boardId,
                position,
                boardOrientation: colour,
                squareStyles,
                arrows,
                boardStyle: { borderRadius: "4px" },
                ...SQUARES,
                allowDragging: canMove,
                onPieceDrop: ({ sourceSquare, targetSquare }) => (targetSquare ? tryMove(sourceSquare as Square, targetSquare as Square) : false),
                onSquareClick: ({ square }) => squareClick(square as Square),
                animationDurationInMs: 150,
              }}
            />
          </div>
        </div>
        {promo && (
          <div className="mt-2">
            <PromotionChooser
              onChoose={(piece) => {
                setPromo(null);
                apply(promo.from, promo.to, piece);
              }}
            />
          </div>
        )}
        <div className="mt-2 min-h-[20px]">
          <ArrowLegend arrows={arrows} ply="ordinary" />
        </div>
        <div className="mt-2 flex min-h-[44px] flex-wrap items-center justify-center gap-2 sm:gap-3" data-testid="stepper">
          <button type="button" className={`${btn} min-h-11`} onClick={() => onJump(0)} disabled={ply === 0} aria-label="Start">
            ⏮
          </button>
          <button type="button" className={`${btn} min-h-11 px-5`} onClick={() => onJump(ply - 1)} disabled={ply === 0}>
            ← Back
          </button>
          <button type="button" className={`${btn} min-h-11 px-5`} onClick={() => onJump(ply + 1)} disabled={all.length <= ply}>
            Forward →
          </button>
        </div>
        {atBound && (
          <p className="mt-2 text-center text-xs text-zinc-500" role="status">
            The Opening Explorer stops at move 30.
          </p>
        )}
        <div className="mt-3">
          <MoveList all={all} current={ply} onJump={onJump} />
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
        {offBook && (
          <p className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-200" role="status" data-testid="off-book">
            Off-book — your repertoire plays <span className="font-mono">{offBook.join(" or ")}</span> here
          </p>
        )}
        {!answer && !failed && (
          <p className="text-sm text-zinc-500" role="status">
            Loading…
          </p>
        )}
        {failed && (
          <p className="text-sm text-rose-600" role="alert">
            Couldn't load your games here ·{" "}
            <button type="button" className="underline" onClick={onRetry}>
              Try again
            </button>
          </p>
        )}
        {answer && <RepertoireSection answer={answer} playerTurn={playerTurn} onRead={(m) => setWalk({ lineId: m.line_id, openAt: m.line_ply })} />}
        {walk && <ReviewLineReader walk={walk} onClose={() => setWalk(null)} />}
        {answer && answer.lines_through > 0 && <LinesPicker key={p.requestKey} colour={colour} moves={moves} total={answer.lines_through} onOpen={(lineId, openAt) => setWalk({ lineId, openAt })} />}
        {answer && <GamesSection answer={answer} playerTurn={playerTurn} canMove={canMove} onPlay={onPlay} />}

        <div className={`${panel} space-y-1`} data-testid="engine-panel">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">{book.length && playerTurn ? "Engine" : "Engine hint"}</span>
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
      </div>
    </div>
  );
}

function provenance(m: RepertoireMove): string {
  return [m.book, m.chapter, m.line_name].filter(Boolean).join(" · ");
}

function RepertoireSection({ answer, playerTurn, onRead }: { answer: ExplorerPosition; playerTurn: boolean; onRead: (m: RepertoireMove) => void }) {
  const { status, moves } = answer.repertoire;
  return (
    <div className={`${panel} space-y-2`} data-testid="explorer-repertoire" data-status={status}>
      <span className="text-xs font-medium uppercase tracking-widest text-zinc-500">{playerTurn ? "Your repertoire" : "Replies your repertoire prepares"}</span>
      {status === "none" && <p className="text-zinc-500">Out of your repertoire. The green arrow is the engine's move.</p>}
      {status === "unreadable" && <p className="text-zinc-500">The repertoire's move here can't be read.</p>}
      {status === "end_of_line" && <p className="text-zinc-500">Your line ends here.</p>}
      {status === "conflict" && <p className="text-zinc-500">Your lines disagree here: every move they prescribe is drawn.</p>}
      {status === "replies" && moves.length === 0 && <p className="text-zinc-500">Your lines end here.</p>}
      {moves.length > 0 && (
        <ul className="space-y-1.5">
          {moves.map((m) => (
            <li key={`${m.san}:${m.line_id}`} className="flex flex-wrap items-baseline gap-x-2" data-testid="rep-move">
              {playerTurn && <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: ARROWS.book }} />}
              <span className="font-mono">{m.san}</span>
              <span className="min-w-0 break-words text-xs text-zinc-500">
                {provenance(m)}
                {m.lines > 1 ? ` · ${m.lines} lines` : ""}
              </span>
              <button type="button" className={link} onClick={() => onRead(m)}>
                Read the line
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function GamesSection({ answer, playerTurn, canMove, onPlay }: { answer: ExplorerPosition; playerTurn: boolean; canMove: boolean; onPlay: (san: string) => void }) {
  const scope = `${answer.time_class === "focus" ? "focus" : "all"} games, last ${answer.months} months`;
  return (
    <div className={`${panel} space-y-2`} data-testid="explorer-games">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <span className="text-xs font-medium uppercase tracking-widest text-zinc-500">{playerTurn ? "Your moves from here" : "Their replies"}</span>
        <span className="text-xs text-zinc-500">{scope}</span>
      </div>
      {answer.beyond_prefix ? (
        <p className="text-xs text-zinc-500">Your games' stored openings end at move 15.</p>
      ) : (
        answer.node && (
          <p className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400" data-testid="explorer-node">
            {answer.node.n === 0 ? "None of your games reach this board." : `Reached in ${numbers(answer.node)} · vs expectation ${vsExpectation(answer.node)}`}
          </p>
        )
      )}
      {answer.moves.length > 0 && (
        <table className="w-full text-left text-xs tabular-nums">
          <thead className="text-zinc-500">
            <tr>
              <th className="py-1 font-normal">Move</th>
              <th className="py-1 font-normal">Games</th>
              <th className="py-1 font-normal">+ = −</th>
              <th className="py-1 font-normal">Score</th>
              <th className="py-1 font-normal" title="Score against the rating expectation, points per 100 games">vs exp.</th>
            </tr>
          </thead>
          <tbody>
            {answer.moves.map((m) => (
              <tr key={m.san} data-testid="explorer-row" data-san={m.san} data-book={m.in_repertoire ? "true" : undefined} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="py-1">
                  <button type="button" className="font-mono underline disabled:no-underline" disabled={!canMove} onClick={() => onPlay(m.san)}>
                    {m.san}
                  </button>
                  {m.in_repertoire && <span aria-label="in your repertoire" title="in your repertoire" className="ml-1.5 inline-block h-2 w-2 rounded-sm align-middle" style={{ backgroundColor: ARROWS.book }} />}
                </td>
                <td className="py-1">{m.n}</td>
                <td className="py-1">{m.n ? `${m.w} ${m.d} ${m.l}` : "—"}</td>
                <td className="py-1">{m.score != null ? pct(m.score) : "—"}</td>
                <td className="py-1">{vsExpectation(m)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** The lines holding the board, on demand: one line opens straight away, more are listed fifty
 *  at a time. Keyed by the request: another board is a new picker. */
function LinesPicker({ colour, moves, total, onOpen }: { colour: Colour; moves: string[]; total: number; onOpen: (lineId: number, openAt: number) => void }) {
  const [page, setPage] = useState<{ offset: number; data: ExplorerLines } | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "failed">("idle");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  const load = (offset: number) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setState("loading");
    getExplorerLines(colour, moves, offset, ctl.signal)
      .then((data) => {
        if (ctl.signal.aborted) return;
        setState("idle");
        if (data.total === 1 && data.rows.length === 1) onOpen(data.rows[0].line_id, data.rows[0].line_ply);
        else setPage({ offset, data });
      })
      .catch(() => {
        if (!ctl.signal.aborted) setState("failed");
      });
  };
  return (
    <div className={`${panel} space-y-2`} data-testid="lines-picker">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs text-zinc-500">
          {total} line{total === 1 ? "" : "s"} hold this board
        </span>
        {page ? (
          <button type="button" className={link} onClick={() => setPage(null)}>
            Hide
          </button>
        ) : (
          <button type="button" className={link} onClick={() => load(0)} disabled={state === "loading"}>
            {total === 1 ? "Read the line" : "Pick a line"}
          </button>
        )}
      </div>
      {state === "loading" && <p className="text-xs text-zinc-500">Loading…</p>}
      {state === "failed" && <p className="text-xs text-rose-600">Couldn't load the lines.</p>}
      {page && (
        <>
          {page.data.total > EXPLORER_LINES_PAGE && <p className="text-xs text-zinc-500">Step further to narrow it.</p>}
          <ul className="space-y-1">
            {page.data.rows.map((r) => (
              <li key={r.line_id}>
                <button type="button" className="break-words text-left text-xs hover:underline" data-testid="line-row" onClick={() => onOpen(r.line_id, r.line_ply)}>
                  {[r.book, r.chapter, r.line_name].filter(Boolean).join(" · ")}
                </button>
              </li>
            ))}
          </ul>
          {page.data.total > EXPLORER_LINES_PAGE && (
            <div className="flex items-center justify-between text-xs">
              <button type="button" className={link} disabled={page.offset === 0} onClick={() => load(page.offset - EXPLORER_LINES_PAGE)}>
                ← Previous
              </button>
              <span className="text-zinc-500">
                {page.offset + 1}–{Math.min(page.offset + EXPLORER_LINES_PAGE, page.data.total)} of {page.data.total}
              </span>
              <button type="button" className={link} disabled={page.offset + EXPLORER_LINES_PAGE >= page.data.total} onClick={() => load(page.offset + EXPLORER_LINES_PAGE)}>
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
