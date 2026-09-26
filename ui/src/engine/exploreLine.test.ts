/**
 * The explored line against an engine mock: every transition ends in exactly one engine call —
 * `analyze` for an ongoing board, `reset` for a finished one — and the terminal status the panel
 * reads is the one the engine was gated on.
 */
import { act, renderHook } from "@testing-library/react";
import { Chess } from "chess.js";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useExploreLine } from "./exploreLine";

const START = new Chess().fen();
// 1.f3 e5 2.g4: Black mates with Qh4#.
const FOOLS = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2";
const MATED = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3";
// White to move, a7 pawn about to promote.
const PROMO = "8/P6k/8/8/8/8/8/K7 w - - 0 1";

const engine = { analyze: vi.fn(), reset: vi.fn() };
beforeEach(() => {
  engine.analyze.mockClear();
  engine.reset.mockClear();
});

const seeded = (seed: string) => {
  const hook = renderHook(() => useExploreLine({ engine, seed }));
  act(() => hook.result.current.reset());
  return hook;
};

describe("seed and moves", () => {
  it("reset seeds the board and analyses it", () => {
    const { result } = seeded(START);
    expect(result.current.fen).toBe(START);
    expect(result.current.san).toEqual([]);
    expect(result.current.terminal).toBeNull();
    expect(engine.analyze).toHaveBeenCalledTimes(1);
    expect(engine.analyze).toHaveBeenCalledWith(START, undefined);
    expect(engine.reset).not.toHaveBeenCalled();
  });

  it("a legal move appends its SAN and analyses; an illegal one returns false and analyses nothing", () => {
    const { result } = seeded(START);
    let ok = false;
    act(() => {
      ok = result.current.move("e2", "e4");
    });
    expect(ok).toBe(true);
    expect(result.current.san).toEqual(["e4"]);
    expect(result.current.last).toEqual(["e2", "e4"]);
    expect(engine.analyze).toHaveBeenCalledTimes(2);
    expect(engine.analyze).toHaveBeenLastCalledWith(result.current.fen, undefined);
    act(() => {
      ok = result.current.move("e4", "e6");
    });
    expect(ok).toBe(false);
    expect(result.current.san).toEqual(["e4"]);
    expect(engine.analyze).toHaveBeenCalledTimes(2);
  });

  it("auto-queens a promotion", () => {
    const { result } = seeded(PROMO);
    act(() => void result.current.move("a7", "a8"));
    expect(result.current.san).toEqual(["a8=Q"]);
  });

  it("click-to-move selects an own piece, moves on the second click, re-selects or clears on a failed one", () => {
    const { result } = seeded(START);
    act(() => result.current.squareClick("e7")); // not White's piece
    expect(result.current.selected).toBeNull();
    act(() => result.current.squareClick("e2"));
    expect(result.current.selected).toBe("e2");
    expect(result.current.targets.map((t) => t.to).sort()).toEqual(["e3", "e4"]);
    act(() => result.current.squareClick("d2")); // another own piece: re-select
    expect(result.current.selected).toBe("d2");
    act(() => result.current.squareClick("h5")); // nothing there: clear
    expect(result.current.selected).toBeNull();
    act(() => result.current.squareClick("g1"));
    act(() => result.current.squareClick("f3"));
    expect(result.current.san).toEqual(["Nf3"]);
    expect(result.current.selected).toBeNull();
    expect(result.current.targets).toEqual([]);
  });
});

describe("undo and redo", () => {
  it("back/forward round trip preserves SAN and FEN; a fresh move empties redo", () => {
    const { result } = seeded(START);
    act(() => void result.current.move("e2", "e4"));
    act(() => void result.current.move("e7", "e5"));
    const afterTwo = result.current.fen;
    expect(result.current.canBack).toBe(true);
    expect(result.current.canForward).toBe(false);
    act(() => result.current.back());
    expect(result.current.san).toEqual(["e4"]);
    expect(result.current.canForward).toBe(true);
    expect(engine.analyze).toHaveBeenLastCalledWith(result.current.fen, undefined);
    act(() => result.current.forward());
    expect(result.current.san).toEqual(["e4", "e5"]);
    expect(result.current.fen).toBe(afterTwo);
    act(() => result.current.back());
    act(() => void result.current.move("d7", "d5"));
    expect(result.current.san).toEqual(["e4", "d5"]);
    expect(result.current.canForward).toBe(false);
    act(() => result.current.back());
    act(() => result.current.back());
    expect(result.current.canBack).toBe(false);
    act(() => result.current.back()); // no-op at the root
    expect(result.current.fen).toBe(START);
    expect(engine.analyze).toHaveBeenCalledTimes(9);
  });
});

describe("terminal positions", () => {
  it("a checkmated seed resets the engine and analyses nothing", () => {
    const { result } = seeded(MATED);
    expect(result.current.terminal).toBe("checkmate");
    expect(engine.analyze).not.toHaveBeenCalled();
    expect(engine.reset).toHaveBeenCalledTimes(1);
  });

  it("a mating move resets the engine; back out of it analyses again; forward into it resets again", () => {
    const { result } = seeded(FOOLS);
    expect(engine.analyze).toHaveBeenCalledTimes(1);
    act(() => void result.current.move("d8", "h4"));
    expect(result.current.terminal).toBe("checkmate");
    expect(result.current.san).toEqual(["Qh4#"]);
    expect(engine.analyze).toHaveBeenCalledTimes(1);
    expect(engine.reset).toHaveBeenCalledTimes(1);
    act(() => result.current.back());
    expect(result.current.terminal).toBeNull();
    expect(engine.analyze).toHaveBeenCalledTimes(2);
    expect(engine.analyze).toHaveBeenLastCalledWith(FOOLS, undefined);
    act(() => result.current.forward());
    expect(result.current.terminal).toBe("checkmate");
    expect(engine.reset).toHaveBeenCalledTimes(2);
    expect(engine.analyze).toHaveBeenCalledTimes(2);
  });

  it("a repetition made inside Explore is a draw on the instance, and one back() reopens it", () => {
    const { result } = seeded(START);
    const line: [string, string][] = [
      ["g1", "f3"],
      ["g8", "f6"],
      ["f3", "g1"],
      ["f6", "g8"],
      ["g1", "f3"],
      ["g8", "f6"],
      ["f3", "g1"],
      ["f6", "g8"],
    ];
    for (const [from, to] of line) act(() => void result.current.move(from as "a1", to as "a1"));
    expect(result.current.terminal).toBe("draw");
    expect(engine.reset).toHaveBeenCalledTimes(1);
    expect(engine.analyze).toHaveBeenCalledTimes(1 + 7);
    // The same board from its FEN alone is an ordinary position: only the live instance sees it.
    expect(new Chess(result.current.fen).isDraw()).toBe(false);
    act(() => result.current.back());
    expect(result.current.terminal).toBeNull();
    expect(engine.analyze).toHaveBeenCalledTimes(9);
  });

  it("stalemate is terminal too", () => {
    const { result } = seeded("k7/8/2K5/1Q6/8/8/8/8 w - - 0 1");
    act(() => void result.current.move("b5", "b6"));
    expect(result.current.terminal).toBe("stalemate");
    expect(engine.reset).toHaveBeenCalledTimes(1);
  });

  it("reanalyse on a terminal board resets and never analyses; on an ongoing board analyses at the depth", () => {
    const mated = seeded(MATED);
    act(() => mated.result.current.reanalyse(20));
    expect(engine.analyze).not.toHaveBeenCalled();
    expect(engine.reset).toHaveBeenCalledTimes(2);
    const open = seeded(START);
    act(() => open.result.current.squareClick("e2"));
    act(() => open.result.current.reanalyse(20));
    expect(engine.analyze).toHaveBeenLastCalledWith(START, 20);
    expect(open.result.current.fen).toBe(START);
    expect(open.result.current.selected).toBe("e2"); // a re-search of the same board keeps a click-to-move in progress
  });
});

describe("identity", () => {
  it("reset changes only with the seed", () => {
    const { result, rerender } = renderHook(({ seed }) => useExploreLine({ engine, seed }), { initialProps: { seed: START } });
    const r1 = result.current.reset;
    rerender({ seed: START });
    expect(result.current.reset).toBe(r1);
    rerender({ seed: FOOLS });
    expect(result.current.reset).not.toBe(r1);
  });
});
