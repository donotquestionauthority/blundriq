/**
 * The engine wrapper is a state machine over asynchronous UCI lines and a debounce timer; this
 * suite drives it with a scriptable fake `Worker` and fake timers. The supersession cases are the
 * regression suite for the stop-and-drain argument: UCI cannot echo an app token, so ordering is
 * the whole of the correctness claim.
 */
import { act, renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clampDepth, toWhitePovCp, useStockfish } from "./useStockfish";

class MockWorker {
  static instances: MockWorker[] = [];
  posted: string[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  terminated = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWorker.instances.push(this);
  }
  postMessage(cmd: string) {
    this.posted.push(cmd);
  }
  terminate() {
    this.terminated = true;
  }
  emit(line: string) {
    this.onmessage?.({ data: line });
  }
}

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const BLACK = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
const FEN_B = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2";
const MATED = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3";

const worker = () => {
  const w = MockWorker.instances.at(-1);
  if (!w) throw new Error("no worker");
  return w;
};
const live = () => MockWorker.instances.filter((w) => !w.terminated);
const gos = (w: MockWorker) => w.posted.filter((c) => c.startsWith("go "));
const positions = (w: MockWorker) => w.posted.filter((c) => c.startsWith("position ")).map((c) => c.slice("position fen ".length));
const handshake = () => {
  act(() => worker().emit("uciok"));
  act(() => worker().emit("readyok"));
};
const tick = () => act(() => void vi.advanceTimersByTime(100));

beforeEach(() => {
  MockWorker.instances = [];
  vi.stubGlobal("Worker", MockWorker as unknown as typeof Worker);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("handshake and seed", () => {
  it("sends uci, then the three init commands on uciok, and is ready on readyok", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    expect(worker().url).toBe("/engine/stockfish-18-lite-single.js");
    expect(worker().posted).toEqual(["uci"]);
    act(() => worker().emit("uciok"));
    expect(worker().posted).toEqual(["uci", "ucinewgame", "setoption name UCI_Chess960 value false", "isready"]);
    expect(result.current.ready).toBe(false);
    act(() => worker().emit("readyok"));
    expect(result.current.ready).toBe(true);
  });

  it("a seed analysed before readyok is dispatched exactly once, after it", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    act(() => result.current.analyze(START));
    tick();
    expect(gos(worker())).toHaveLength(0);
    handshake();
    expect(positions(worker())).toEqual([START]);
    expect(gos(worker())).toEqual(["go depth 16"]);
    tick();
    expect(gos(worker())).toHaveLength(1);
  });

  it("the seed survives its debounce timer being cancelled before the handshake ends", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    act(() => result.current.analyze(START));
    vi.clearAllTimers();
    handshake();
    expect(positions(worker())).toEqual([START]);
  });

  it("does nothing while disabled", () => {
    renderHook(() => useStockfish({ enabled: false }));
    expect(MockWorker.instances).toHaveLength(0);
  });

  it("unmount terminates the worker", () => {
    const { unmount } = renderHook(() => useStockfish({ enabled: true }));
    unmount();
    expect(worker().terminated).toBe(true);
  });

  it("under StrictMode the double-mount ends with one live worker and the seed analysed", () => {
    const { result } = renderHook(
      () => useStockfish({ enabled: true }),
      { wrapper: StrictMode },
    );
    act(() => result.current.analyze(START));
    expect(MockWorker.instances).toHaveLength(2);
    expect(live()).toHaveLength(1);
    handshake();
    expect(positions(live()[0])).toEqual([START]);
  });
});

describe("scores", () => {
  it("converts info lines to White-POV, both sides, cp and mate", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("info depth 12 score cp 35 pv e2e4 e7e5"));
    expect(result.current.evalState).toMatchObject({ fen: START, evalCp: 35, bestMoveUci: "e2e4", pvUci: ["e2e4", "e7e5"], depth: 12, thinking: true });
    act(() => worker().emit("info depth 14 score mate 3 pv d1h5"));
    expect(result.current.evalState?.evalCp).toBe(9997);
    act(() => worker().emit("info depth 14 score mate -2 pv e1e2"));
    expect(result.current.evalState?.evalCp).toBe(-9998);

    act(() => result.current.analyze(BLACK));
    tick();
    act(() => worker().emit("bestmove e2e4"));
    act(() => worker().emit("info depth 10 score cp 20 pv e7e5"));
    expect(result.current.evalState).toMatchObject({ fen: BLACK, evalCp: -20 });
    act(() => worker().emit("info depth 10 score mate 2 pv d8h4"));
    expect(result.current.evalState?.evalCp).toBe(-9998);
  });

  it("scores mate 0 as a loss for the side to move", () => {
    expect(toWhitePovCp("mate", 0, "w")).toBe(-10000);
    expect(toWhitePovCp("mate", 0, "b")).toBe(10000);
  });

  it("bestmove finalises with its token; (none) leaves the last PV head", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("info depth 5 score cp 10 pv e2e4"));
    act(() => worker().emit("bestmove d2d4 ponder d7d5"));
    expect(result.current.evalState).toMatchObject({ bestMoveUci: "d2d4", thinking: false });

    act(() => result.current.analyze(MATED));
    tick();
    act(() => worker().emit("bestmove (none)"));
    expect(result.current.evalState).toMatchObject({ fen: MATED, bestMoveUci: null, thinking: false });
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("info depth 5 score cp 10 pv e2e4"));
    act(() => worker().emit("bestmove (none)"));
    expect(result.current.evalState?.bestMoveUci).toBe("e2e4");
  });
});

describe("queue", () => {
  it("a second analyze during a search sends stop and dispatches the new FEN only after the stale bestmove, whose token is not applied", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("info depth 8 score cp 12 pv e2e4"));
    act(() => result.current.analyze(FEN_B));
    tick();
    expect(worker().posted.at(-1)).toBe("stop");
    expect(positions(worker())).toEqual([START]);
    // Info during draining is ignored.
    act(() => worker().emit("info depth 9 score cp 99 pv a2a3"));
    expect(result.current.evalState).toMatchObject({ fen: START, evalCp: 12, bestMoveUci: "e2e4" });
    act(() => worker().emit("bestmove a2a3"));
    expect(positions(worker())).toEqual([START, FEN_B]);
    expect(result.current.evalState).toMatchObject({ fen: FEN_B, evalCp: null, bestMoveUci: null, thinking: true });
  });

  it("three analyze calls within the debounce dispatch once, with the last FEN", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => {
      result.current.analyze(START);
      result.current.analyze(BLACK);
      result.current.analyze(FEN_B);
    });
    tick();
    expect(positions(worker())).toEqual([FEN_B]);
  });

  it("clamps the depth at both bounds and takes the per-call depth over the hook's", () => {
    expect(clampDepth(2)).toBe(6);
    expect(clampDepth(99)).toBe(30);
    expect(clampDepth(Number.NaN)).toBe(16);
    const { result } = renderHook(() => useStockfish({ enabled: true, depth: 22 }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("bestmove e2e4"));
    act(() => result.current.analyze(START, 1));
    tick();
    act(() => worker().emit("bestmove e2e4"));
    act(() => result.current.analyze(START, 100));
    tick();
    expect(gos(worker())).toEqual(["go depth 22", "go depth 6", "go depth 30"]);
  });

  it("keeps the callbacks' identity across a depth change", () => {
    const { result, rerender } = renderHook(({ depth }) => useStockfish({ enabled: true, depth }), { initialProps: { depth: 16 } });
    const before = result.current;
    rerender({ depth: 20 });
    expect(result.current.analyze).toBe(before.analyze);
    expect(result.current.stop).toBe(before.stop);
    expect(result.current.reset).toBe(before.reset);
    handshake();
    act(() => result.current.analyze(START));
    tick();
    expect(gos(worker())).toEqual(["go depth 20"]);
  });
});

describe("reset", () => {
  it("while a search runs: sends stop, discards the drained bestmove, dispatches nothing, evalState null", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("info depth 8 score cp 12 pv e2e4"));
    act(() => result.current.reset());
    expect(worker().posted.at(-1)).toBe("stop");
    expect(result.current.evalState).toBeNull();
    act(() => worker().emit("bestmove e2e4"));
    expect(result.current.evalState).toBeNull();
    expect(positions(worker())).toEqual([START]);
  });

  it("with a request waiting in the debounce: cancels it", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    act(() => result.current.reset());
    tick();
    expect(positions(worker())).toEqual([]);
    expect(worker().posted.filter((c) => c === "stop")).toHaveLength(0);
    expect(result.current.evalState).toBeNull();
  });

  it("clears a request waiting behind a running search: the drained bestmove dispatches nothing", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => result.current.analyze(FEN_B)); // queued behind the running search
    act(() => result.current.reset()); // a mating move within the debounce
    tick();
    act(() => worker().emit("bestmove e2e4"));
    expect(positions(worker())).toEqual([START]);
    expect(result.current.evalState).toBeNull();
  });

  it("stop() alone: the drained bestmove is not applied to the evaluation it interrupted", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => worker().emit("info depth 8 score cp 12 pv e2e4"));
    act(() => result.current.stop());
    act(() => worker().emit("bestmove a2a3"));
    expect(result.current.evalState).toMatchObject({ fen: START, bestMoveUci: "e2e4", thinking: true });
    expect(positions(worker())).toEqual([START]);
  });

  it("an analyze arriving while a reset's stop drains keeps its request until the stale bestmove", () => {
    const { result } = renderHook(() => useStockfish({ enabled: true }));
    handshake();
    act(() => result.current.analyze(START));
    tick();
    act(() => result.current.reset());
    act(() => result.current.analyze(FEN_B));
    tick();
    expect(positions(worker())).toEqual([START]);
    expect(worker().posted.filter((c) => c === "stop")).toHaveLength(1);
    act(() => worker().emit("bestmove e2e4"));
    expect(positions(worker())).toEqual([START, FEN_B]);
    expect(result.current.evalState).toMatchObject({ fen: FEN_B, thinking: true });
  });
});

describe("the engine stays out of the module graph", () => {
  const SRC = dirname(dirname(fileURLToPath(import.meta.url)));
  const walk = (dir: string): string[] => readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? walk(join(dir, n)) : [join(dir, n)]));

  it("creates the worker from the bare string literal", () => {
    const src = readFileSync(join(SRC, "engine", "useStockfish.ts"), "utf8");
    expect(src).toContain('new Worker("/engine/stockfish-18-lite-single.js")');
    expect(src).not.toMatch(/new Worker\(\s*new URL/);
  });

  it("nothing under src/ imports from /engine", () => {
    for (const f of walk(SRC).filter((f) => /\.(tsx?|css)$/.test(f))) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/from\s+["']\/engine/);
      expect(text, f).not.toMatch(/import\(\s*["']\/engine/);
    }
  });
});
