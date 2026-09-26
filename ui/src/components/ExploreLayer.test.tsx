import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Chess } from "chess.js";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EngineEval } from "../engine/useStockfish";
import { ExploreLayer } from "./ExploreLayer";

// The board is not under test: the mock shows its arrows and plays the move the test set up.
let nextDrop = { from: "e2", to: "e4" };
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string; arrows?: unknown[]; onPieceDrop?: (a: { sourceSquare: string; targetSquare: string }) => boolean } }) => (
    <div data-testid="board" data-id={options.id} data-position={options.position} data-arrows={JSON.stringify(options.arrows ?? [])}>
      <button type="button" onClick={() => options.onPieceDrop?.({ sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
        play
      </button>
    </div>
  ),
}));

// The engine is a fake the test drives: `push` publishes ready / evalState into the mounted hook.
const analyze = vi.fn();
const reset = vi.fn();
const stop = vi.fn();
let push: ((s: { ready: boolean; evalState: EngineEval | null }) => void) | null = null;
vi.mock("../engine/useStockfish", () => ({
  useStockfish: () => {
    const [s, setS] = useState<{ ready: boolean; evalState: EngineEval | null }>({ ready: false, evalState: null });
    push = setS;
    return { ...s, analyze, reset, stop };
  },
}));

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4 = (() => {
  const g = new Chess(START);
  g.move("e4");
  return g.fen();
})();
const MATED = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3";

let settings: Record<string, unknown> | null = { explore_engine_depth: 16 };
let resolveSettings: (() => void) | null = null;
beforeEach(() => {
  analyze.mockClear();
  reset.mockClear();
  stop.mockClear();
  nextDrop = { from: "e2", to: "e4" };
  settings = { explore_engine_depth: 16 };
  resolveSettings = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (!url.endsWith("/settings")) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({}) };
      // Held until the test releases it, when a `resolveSettings` is wanted; immediate otherwise.
      if (resolveSettings === undefined) await new Promise<void>((r) => (resolveSettings = r));
      if (settings === null) return { ok: false, status: 500, statusText: "boom", json: async () => ({ detail: "boom" }) };
      return { ok: true, status: 200, statusText: "OK", json: async () => settings };
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const ev = (fen: string, over: Partial<EngineEval> = {}): EngineEval => ({ fen, evalCp: 35, bestMoveUci: "e2e4", pvUci: ["e2e4", "e7e5"], depth: 12, thinking: false, ...over });
const arrows = () => JSON.parse(screen.getByTestId("board").getAttribute("data-arrows") ?? "[]") as { startSquare: string; endSquare: string }[];
const settled = () => waitFor(() => expect(fetch).toHaveBeenCalled());

describe("ExploreLayer", () => {
  it("shows Loading engine… until ready, then the evaluation and the best line", async () => {
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    expect(screen.getByText("Loading engine…")).toBeInTheDocument();
    act(() => push?.({ ready: true, evalState: ev(START) }));
    expect(screen.queryByText("Loading engine…")).not.toBeInTheDocument();
    expect(screen.getAllByText("+0.3")).toHaveLength(2); // the bar and the panel
    expect(screen.getByText("e4")).toBeInTheDocument(); // Best
    expect(screen.getByText("e4 e5")).toBeInTheDocument(); // the line
    await settled();
  });

  it("draws the best-move arrow only when the evaluation is for the board on show", async () => {
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    act(() => push?.({ ready: true, evalState: ev(START) }));
    expect(arrows()).toEqual([{ startSquare: "e2", endSquare: "e4", color: "#009E73" }]);
    fireEvent.click(screen.getByText("play")); // the board moves on; the evaluation is stale
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(AFTER_E4);
    expect(arrows()).toEqual([]);
    expect(screen.getAllByText("–")).toHaveLength(2);
    act(() => push?.({ ready: true, evalState: ev(AFTER_E4, { bestMoveUci: "e7e5", pvUci: ["e7e5"], evalCp: -20 }) }));
    expect(arrows()).toEqual([{ startSquare: "e7", endSquare: "e5", color: "#009E73" }]);
    expect(screen.getAllByText("-0.2")).toHaveLength(2);
    await settled();
  });

  it("asks for exactly one search at mount when the saved depth equals the one in use", async () => {
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    await settled();
    await act(async () => {});
    expect(analyze).toHaveBeenCalledTimes(1);
    expect(analyze).toHaveBeenCalledWith(START, undefined);
    expect(reset).not.toHaveBeenCalled();
  });

  it("re-analyses at the saved depth when it differs, and the selector lists it when it is not a step", async () => {
    settings = { explore_engine_depth: 14 };
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    await waitFor(() => expect(analyze).toHaveBeenCalledTimes(2));
    expect(analyze).toHaveBeenLastCalledWith(START, 14);
    const sel = screen.getByLabelText("Engine depth") as HTMLSelectElement;
    expect(sel.value).toBe("14");
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(["12", "14", "16", "18", "20", "24"]);
  });

  it("a failed settings call leaves the default and one search", async () => {
    settings = null;
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    await settled();
    await act(async () => {});
    expect(analyze).toHaveBeenCalledTimes(1);
    expect((screen.getByLabelText("Engine depth") as HTMLSelectElement).value).toBe("16");
  });

  it("the selector re-analyses the current board at the new depth", async () => {
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    await settled();
    fireEvent.click(screen.getByText("play"));
    fireEvent.change(screen.getByLabelText("Engine depth"), { target: { value: "20" } });
    expect(analyze).toHaveBeenLastCalledWith(AFTER_E4, 20);
    expect(analyze).toHaveBeenCalledTimes(3);
  });

  it("a finished board shows Checkmate and asks for nothing — not at mount, not on the settings response, not on a depth change", async () => {
    settings = { explore_engine_depth: 20 };
    render(<ExploreLayer fen={MATED} orientation="white" onClose={() => {}} />);
    act(() => push?.({ ready: true, evalState: null }));
    expect(screen.getAllByText("Checkmate").length).toBeGreaterThan(0);
    await waitFor(() => expect((screen.getByLabelText("Engine depth") as HTMLSelectElement).value).toBe("20"));
    fireEvent.change(screen.getByLabelText("Engine depth"), { target: { value: "24" } });
    expect(analyze).not.toHaveBeenCalled();
    expect(reset).toHaveBeenCalledTimes(3); // seed, settings, selector
    expect(arrows()).toEqual([]);
    // Undo out of it is impossible (no moves); a mating move made here is caught by the line's tests.
    expect(screen.getByText("← Undo")).toBeDisabled();
  });

  it("Escape closes; ← / → undo and redo; a modifier leaves the key to the browser", async () => {
    const onClose = vi.fn();
    render(<ExploreLayer fen={START} orientation="white" onClose={onClose} />);
    await settled();
    fireEvent.click(screen.getByText("play"));
    expect(screen.getByText("1 move in")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft", metaKey: true });
    expect(screen.getByText("1 move in")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("Exploring — make a move")).toBeInTheDocument();
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(START);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("1 move in")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Undo, Redo and Reset buttons walk the line", async () => {
    render(<ExploreLayer fen={START} orientation="white" onClose={() => {}} />);
    await settled();
    fireEvent.click(screen.getByText("play"));
    nextDrop = { from: "e7", to: "e5" };
    fireEvent.click(screen.getByText("play"));
    expect(screen.getByText("2 moves in")).toBeInTheDocument();
    fireEvent.click(screen.getByText("← Undo"));
    expect(screen.getByText("1 move in")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Redo →"));
    expect(screen.getByText("2 moves in")).toBeInTheDocument();
    fireEvent.click(screen.getByText("↺ Reset"));
    expect(screen.getByText("Exploring — make a move")).toBeInTheDocument();
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(START);
    expect(screen.getByText("Redo →")).toBeDisabled();
  });

  it("the footer links the licence, the notices and the source under /engine/", async () => {
    render(<ExploreLayer fen={START} orientation="black" onClose={() => {}} />);
    expect(screen.getByText("licence")).toHaveAttribute("href", "/engine/Copying.txt");
    expect(screen.getByText("notices")).toHaveAttribute("href", "/engine/NNUE-NOTICE.txt");
    expect(screen.getByText("source")).toHaveAttribute("href", "/engine/stockfish-source.tar.gz");
    expect(screen.getByRole("dialog", { name: "Explore" })).toHaveClass("z-[60]");
    await settled();
  });
});
