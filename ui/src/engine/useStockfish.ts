/**
 * In-browser Stockfish: UCI over `postMessage` to a dedicated Web Worker, one per hook instance.
 *
 * The worker URL is a BARE STRING, never `new URL(..., import.meta.url)`: the bare string keeps the
 * engine out of Vite's module graph, so no hashed copy of the glue or the WASM lands in
 * `dist/assets/`. The engine is a separate work (GPL-3.0, see `public/engine/`) that the app talks
 * UCI to at arm's length; `useStockfish.test.ts` asserts the literal and that nothing under `src/`
 * imports from `/engine/`. The worker is created when the hook is enabled and terminated when it
 * unmounts; the handshake (`uci` → `uciok` → `ucinewgame`, `UCI_Chess960 false`, `isready` →
 * `readyok`) runs once. There is no game switching: a host that needs a new engine remounts the hook.
 *
 * Queue: stop-and-drain, at most one live `go`. UCI cannot echo an app token, so supersession is
 * by protocol sequencing and wrapper state (`idle` / `searching` / `draining`), never a token in
 * the response: `analyze` writes the single pending slot synchronously and schedules `pump` after
 * a short debounce; a request arriving while a search runs sends `stop` and waits for the stale
 * `bestmove`, which is discarded, before the pending slot is dispatched. `info` lines belong to
 * the current search only and are ignored while draining, so a superseded search's PV never
 * reaches the consumer.
 *
 * Scores: UCI `score cp|mate N` is relative to the side to move; `evalCp` is White-POV centipawns
 * with mate as ±(10000 − distance), read off FEN field 2. `mate 0` is the side to move being
 * mated. A `bestmove` token is kept only when it looks like a move: `(none)` on a finished board
 * leaves the last PV head (or null).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { MATE_SCORE } from "./eval";

const DEFAULT_DEPTH = 16;
/** The settings field's bounds: a bad row cannot send a pathological `go depth`. */
const MIN_DEPTH = 6;
const MAX_DEPTH = 30;
const DEBOUNCE_MS = 90;
const MOVE_TOKEN = /^[a-h][1-8][a-h][1-8][nbrq]?$/;

export interface EngineEval {
  fen: string;
  /** White-POV centipawns (mate ±10000); null until the first `info` line. */
  evalCp: number | null;
  /** e.g. "e2e4" / "e7e8q"; null until the first PV or a well-formed `bestmove`. */
  bestMoveUci: string | null;
  /** The principal variation, UCI tokens. */
  pvUci: string[];
  depth: number;
  thinking: boolean;
}

export interface StockfishApi {
  /** The handshake is complete; searches dispatch at once. */
  ready: boolean;
  /** The latest *dispatched* search's state, or null before the first request and after `reset()`.
   *  Consumers check `evalState.fen` against the board they show before drawing anything. */
  evalState: EngineEval | null;
  /** Analyse a position; supersedes any running search (latest wins). Stable identity. */
  analyze: (fen: string, depth?: number) => void;
  /** Cancel the pending request and stop a running search; nothing is dispatched after. Stable. */
  stop: () => void;
  /** `stop()` and clear `evalState`. Stable. */
  reset: () => void;
}

type Phase = "idle" | "searching" | "draining";

function sideToMove(fen: string): "w" | "b" {
  return fen.split(" ")[1] === "b" ? "b" : "w";
}

/** A side-to-move-relative UCI score as White-POV centipawns. */
export function toWhitePovCp(kind: "cp" | "mate", value: number, side: "w" | "b"): number {
  let rel: number;
  if (kind === "mate") {
    if (value === 0) rel = -MATE_SCORE; // the side to move is mated
    else rel = (value > 0 ? 1 : -1) * (MATE_SCORE - Math.min(Math.abs(value), MATE_SCORE - 1));
  } else {
    rel = value;
  }
  return side === "w" ? rel : -rel;
}

export function clampDepth(raw: number): number {
  return Math.max(MIN_DEPTH, Math.min(MAX_DEPTH, Math.round(raw)));
}

export function useStockfish({ enabled, depth: configuredDepth }: { enabled: boolean; depth?: number }): StockfishApi {
  const workerRef = useRef<Worker | null>(null);
  const phaseRef = useRef<Phase>("idle");
  const pendingRef = useRef<{ fen: string; depth: number } | null>(null);
  const curRef = useRef<{ fen: string; side: "w" | "b" } | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const readyRef = useRef(false);
  // The configured depth is read through a ref so a change never mints new callbacks.
  const depthRef = useRef(configuredDepth);
  useEffect(() => {
    depthRef.current = configuredDepth;
  }, [configuredDepth]);

  const [ready, setReady] = useState(false);
  const [evalState, setEvalState] = useState<EngineEval | null>(null);

  const send = useCallback((cmd: string) => {
    workerRef.current?.postMessage(cmd);
  }, []);

  const dispatch = useCallback(
    (req: { fen: string; depth: number }) => {
      curRef.current = { fen: req.fen, side: sideToMove(req.fen) };
      phaseRef.current = "searching";
      setEvalState({ fen: req.fen, evalCp: null, bestMoveUci: null, pvUci: [], depth: 0, thinking: true });
      send(`position fen ${req.fen}`);
      send(`go depth ${req.depth}`);
    },
    [send],
  );

  // Dispatch the pending slot when idle and ready; supersede a running search by stop-and-drain;
  // leave it for the drain or the `readyok` flush otherwise.
  const pump = useCallback(() => {
    const req = pendingRef.current;
    if (!req) return;
    if (!workerRef.current || !readyRef.current) return;
    if (phaseRef.current === "idle") {
      pendingRef.current = null;
      dispatch(req);
    } else if (phaseRef.current === "searching") {
      phaseRef.current = "draining";
      send("stop");
    }
  }, [dispatch, send]);

  const analyze = useCallback(
    (fen: string, depth?: number) => {
      // The synchronous capture is what survives a cancelled timer.
      pendingRef.current = { fen, depth: clampDepth(depth ?? depthRef.current ?? DEFAULT_DEPTH) };
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(pump, DEBOUNCE_MS);
    },
    [pump],
  );

  const stop = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = null;
    pendingRef.current = null;
    if (phaseRef.current === "searching") {
      phaseRef.current = "draining";
      send("stop");
    }
  }, [send]);

  const reset = useCallback(() => {
    stop();
    setEvalState(null);
  }, [stop]);

  useEffect(() => {
    if (!enabled) return;
    // Bare string literal, inlined at the call site: the only engine reference that survives
    // bundling is `new Worker("/engine/…")`. See the header.
    const w = new Worker("/engine/stockfish-18-lite-single.js");
    workerRef.current = w;

    w.onmessage = (e: MessageEvent) => {
      const line: string = typeof e.data === "string" ? e.data : String(e.data ?? "");
      if (!line) return;

      if (line === "uciok") {
        send("ucinewgame");
        send("setoption name UCI_Chess960 value false");
        send("isready");
        return;
      }
      if (line === "readyok") {
        readyRef.current = true;
        setReady(true);
        // A request that arrived before the handshake ended (the seed always does).
        if (phaseRef.current === "idle" && pendingRef.current) {
          const next = pendingRef.current;
          pendingRef.current = null;
          dispatch(next);
        }
        return;
      }
      if (line.startsWith("info ")) {
        if (phaseRef.current !== "searching") return;
        const cur = curRef.current;
        if (!cur) return;
        const score = line.match(/\bscore (cp|mate) (-?\d+)\b/);
        if (!score) return;
        const d = line.match(/\bdepth (\d+)\b/);
        const pv = line.match(/\bpv (.+)$/);
        const pvUci = pv ? pv[1].trim().split(/\s+/) : [];
        setEvalState({
          fen: cur.fen,
          evalCp: toWhitePovCp(score[1] as "cp" | "mate", parseInt(score[2], 10), cur.side),
          bestMoveUci: pvUci[0] ?? null,
          pvUci,
          depth: d ? parseInt(d[1], 10) : 0,
          thinking: true,
        });
        return;
      }
      if (line.startsWith("bestmove")) {
        const phase = phaseRef.current;
        phaseRef.current = "idle";
        // Only a live search finalises; a `bestmove` while draining (superseded or stopped) or
        // idle (stray) is discarded.
        if (phase === "searching") {
          const tok = line.split(/\s+/)[1] ?? "";
          const move = MOVE_TOKEN.test(tok) ? tok : null;
          setEvalState((prev) => (prev ? { ...prev, bestMoveUci: move ?? prev.bestMoveUci, thinking: false } : prev));
        }
        const next = pendingRef.current;
        pendingRef.current = null;
        if (next) dispatch(next);
      }
    };

    send("uci");

    return () => {
      try {
        w.terminate();
      } catch {
        /* already gone */
      }
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = null;
      workerRef.current = null;
      phaseRef.current = "idle";
      pendingRef.current = null;
      curRef.current = null;
      readyRef.current = false;
      setReady(false);
    };
  }, [enabled, dispatch, send]);

  return { ready, evalState, analyze, stop, reset };
}
