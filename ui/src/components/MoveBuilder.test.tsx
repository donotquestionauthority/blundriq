import { useEffect, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EngineEval, StockfishApi } from "../engine/useStockfish";
import { MoveBuilder } from "./MoveBuilder";
import type { MoveBuilderState } from "./MoveBuilder";

// A board whose "play" button drops the next queued move.
let nextDrop = { from: "e2", to: "e4" };
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position?: string; onPieceDrop?: (a: { sourceSquare: string; targetSquare: string }) => boolean } }) => (
    <div data-testid="board" data-position={options.position}>
      <button type="button" onClick={() => options.onPieceDrop?.({ sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
        play
      </button>
    </div>
  ),
}));

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
const AFTER_E4_C5 = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";

/** A fake engine the test drives: `finish` publishes a search's states into the builder. */
function fakeEngine() {
  const analyze = vi.fn();
  const stop = vi.fn();
  const reset = vi.fn();
  let setState: ((s: EngineEval | null) => void) | null = null;
  const api = (evalState: EngineEval | null): StockfishApi => ({ ready: true, evalState, analyze, stop, reset });
  function Host({ onChange, initialMoves, line, disabled }: { onChange: (s: MoveBuilderState) => void; initialMoves?: string[]; line: string[]; disabled?: boolean }) {
    const [ev, setEv] = useState<EngineEval | null>(null);
    useEffect(() => {
      setState = setEv;
    }, []);
    return <MoveBuilder fen={START} color="w" onChange={onChange} initialMoves={initialMoves} extend={{ line, engine: api(ev), depth: 12 }} disabled={disabled} />;
  }
  const push = (fen: string, pvUci: string[], thinking: boolean) => act(() => setState?.({ fen, evalCp: 10, bestMoveUci: pvUci[0] ?? null, pvUci, depth: 12, thinking }));
  const search = (fen: string, pvUci: string[]) => {
    push(fen, [], true); // the fresh dispatch
    push(fen, pvUci, false); // the finished search
  };
  return { Host, analyze, stop, reset, push, search, clear: () => act(() => setState?.(null)) };
}

function lineOf(): { last: () => string[]; onChange: (s: MoveBuilderState) => void } {
  let last: string[] = [];
  return { last: () => last, onChange: (s) => void (last = s.solutionLine) };
}

const shownPly = () => Number(screen.getByTestId("solution-line").getAttribute("data-shown"));
const board = () => screen.getByTestId("board").getAttribute("data-position");

beforeEach(() => {
  nextDrop = { from: "e2", to: "e4" };
});

describe("the seed and the cursor", () => {
  it("opens a seeded line at its starting board, lists the whole line and reports it", () => {
    const l = lineOf();
    render(<MoveBuilder fen={START} color="w" onChange={l.onChange} initialMoves={["e4", "e5", "Nf3"]} />);
    expect(l.last()).toEqual(["e4", "e5", "Nf3"]);
    expect(shownPly()).toBe(0);
    expect(board()).toBe(START);
    expect(screen.getByTestId("solution-line").textContent).toBe("1. e4 e5 2. Nf3");
  });

  it("steps through the seed with ▶ ◀ and the arrow keys without changing the line", () => {
    const l = lineOf();
    render(<MoveBuilder fen={START} color="w" onChange={l.onChange} initialMoves={["e4", "e5", "Nf3"]} />);
    fireEvent.click(screen.getByLabelText("Next move"));
    fireEvent.click(screen.getByLabelText("Next move"));
    expect(shownPly()).toBe(2);
    expect(board()).toBe(AFTER_E4_E5);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(shownPly()).toBe(3);
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    fireEvent.click(screen.getByLabelText("Previous move"));
    expect(shownPly()).toBe(1);
    expect(l.last()).toEqual(["e4", "e5", "Nf3"]);
  });

  it("a move played at an earlier position replaces the suffix", () => {
    const l = lineOf();
    render(<MoveBuilder fen={START} color="w" onChange={l.onChange} initialMoves={["e4", "e5", "Nf3", "Nc6"]} />);
    fireEvent.click(screen.getByLabelText("Next move"));
    nextDrop = { from: "c7", to: "c5" };
    fireEvent.click(screen.getByText("play"));
    expect(l.last()).toEqual(["e4", "c5"]);
    expect(shownPly()).toBe(2);
    expect(board()).toBe(AFTER_E4_C5);
  });

  it("Undo and Clear act on the line and clamp the cursor; an illegal seed move stops the seed", () => {
    const l = lineOf();
    render(<MoveBuilder fen={START} color="w" onChange={l.onChange} initialMoves={["e4", "e5", "Qh5", "Nf3", "Nc6"]} />);
    expect(l.last()).toEqual(["e4", "e5", "Qh5"]); // Black has no Nf3 after Qh5
    fireEvent.click(screen.getByLabelText("Next move"));
    fireEvent.click(screen.getByLabelText("Next move"));
    fireEvent.click(screen.getByLabelText("Next move"));
    expect(shownPly()).toBe(3);
    fireEvent.click(screen.getByText("↶ Undo"));
    expect(l.last()).toEqual(["e4", "e5"]);
    expect(shownPly()).toBe(2);
    fireEvent.click(screen.getByText("✕ Clear"));
    expect(l.last()).toEqual([]);
    expect(shownPly()).toBe(0);
  });
});

describe("+2 plies", () => {
  it("consumes the stored line first, then asks the engine once the stored line is used up", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={["e4", "e5", "Nf3", "Nc6", "Bb5"]} />);
    fireEvent.click(screen.getByText("+2 plies"));
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6"]);
    expect(e.analyze).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("+2 plies"));
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"]); // one ply left in the store
    expect(e.analyze).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("+2 plies"));
    expect(e.reset).toHaveBeenCalledTimes(1);
    expect(e.analyze).toHaveBeenCalledTimes(1);
    const end = e.analyze.mock.calls[0][0] as string;
    expect(screen.getByText("searching…")).toBeDisabled();
    e.search(end, ["a7a6", "b5a4", "g8f6"]);
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]);
    expect(shownPly()).toBe(7);
    expect(screen.getByText("+2 plies")).toBeEnabled();
  });

  it("Undo back onto the stored line reuses it; a replaced suffix goes to the engine", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5", "Nf3"]} line={["e4", "e5", "Nf3", "Nc6"]} />);
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("+2 plies"));
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6"]);
    expect(e.analyze).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("↶ Undo"));
    nextDrop = { from: "c7", to: "c5" };
    fireEvent.click(screen.getByText("play"));
    expect(l.last()).toEqual(["e4", "c5"]);
    fireEvent.click(screen.getByText("+2 plies"));
    expect(e.analyze).toHaveBeenCalledWith(AFTER_E4_C5, 12);
  });

  it("a stored line whose next token does not play hands over to the engine instead of going inert", () => {
    const e = fakeEngine();
    const l = lineOf();
    // The seed stopped at Qh5 (Black has no Nf3); the stored line still "continues" past it.
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5", "Qh5", "Nf3"]} line={["e4", "e5", "Qh5", "Nf3", "Nc6"]} />);
    expect(l.last()).toEqual(["e4", "e5", "Qh5"]);
    fireEvent.click(screen.getByText("+2 plies"));
    expect(e.analyze).toHaveBeenCalledTimes(1);
    expect(screen.getByText("searching…")).toBeDisabled();
  });

  it("takes one ply from a one-move answer and stops at the cap and at a terminal board", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("+2 plies"));
    e.search(AFTER_E4_E5, ["d1h5"]);
    expect(l.last()).toEqual(["e4", "e5", "Qh5"]);
    // Scholar's mate: a terminal end disables the control.
    nextDrop = { from: "b8", to: "c6" };
    fireEvent.click(screen.getByText("play"));
    nextDrop = { from: "f1", to: "c4" };
    fireEvent.click(screen.getByText("play"));
    nextDrop = { from: "g8", to: "f6" };
    fireEvent.click(screen.getByText("play"));
    nextDrop = { from: "h5", to: "f7" };
    fireEvent.click(screen.getByText("play"));
    expect(l.last()).toEqual(["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]);
    expect(screen.getByText("+2 plies")).toBeDisabled();
  });

  it("caps the line at thirty plies", () => {
    const e = fakeEngine();
    const l = lineOf();
    const long = ["Nf3", "Nf6", "Ng1", "Ng8"];
    const seed = [...long, ...long, ...long, ...long, ...long, ...long, ...long, "e4"]; // 29 plies
    render(<e.Host onChange={l.onChange} initialMoves={seed} line={[]} />);
    expect(l.last()).toHaveLength(29);
    fireEvent.click(screen.getByText("+2 plies"));
    const end = e.analyze.mock.calls[0][0] as string;
    e.search(end, ["e7e5", "g1f3"]);
    expect(l.last()).toHaveLength(30);
    expect(screen.getByText("+2 plies")).toBeDisabled();
  });
});

describe("a pending search belongs to the line it was asked for", () => {
  it.each([
    ["Undo", () => fireEvent.click(screen.getByText("↶ Undo"))],
    ["Clear", () => fireEvent.click(screen.getByText("✕ Clear"))],
    [
      "a replaced suffix",
      () => {
        fireEvent.click(screen.getByLabelText("Next move")); // the seed opens at its start; to the board after e4
        nextDrop = { from: "c7", to: "c5" };
        fireEvent.click(screen.getByText("play"));
      },
    ],
  ])("%s during a search drops its result even when the moves are still legal", (_name, edit) => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("+2 plies"));
    expect(e.analyze).toHaveBeenCalledTimes(1);
    edit();
    expect(e.stop).toHaveBeenCalledTimes(1);
    const after = l.last();
    expect(screen.queryByText("searching…")).toBeNull();
    // Nf3 Nc6 is legal after e4 c5 too, and after Undo (e4): ownership, not legality, drops it.
    e.search(AFTER_E4_E5, ["g1f3", "b8c6"]);
    expect(l.last()).toEqual(after);
  });

  it("applies a result once, ignores an old finished evaluation on the next click, and the second click runs from the new end", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("+2 plies"));
    e.search(AFTER_E4_E5, ["g1f3", "b8c6"]);
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6"]);
    e.push(AFTER_E4_E5, ["g1f3", "b8c6"], false); // the hook reports the same finished search again
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6"]);
    // Back to e4 e5: the hook still holds the old finished evaluation for that very board.
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("+2 plies"));
    expect(e.analyze).toHaveBeenCalledTimes(2);
    expect(l.last()).toEqual(["e4", "e5"]); // not applied: no fresh dispatch seen yet
    expect(screen.getByText("searching…")).toBeDisabled();
    e.search(AFTER_E4_E5, ["f1c4", "g8f6"]);
    expect(l.last()).toEqual(["e4", "e5", "Bc4", "Nf6"]);
  });

  it("saving (disabled) drops the pending search and keeps the line it had at the click", () => {
    const e = fakeEngine();
    const l = lineOf();
    const { rerender } = render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("+2 plies"));
    rerender(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} disabled />);
    expect(e.stop).toHaveBeenCalledTimes(1);
    e.search(AFTER_E4_E5, ["g1f3", "b8c6"]);
    expect(l.last()).toEqual(["e4", "e5"]);
    expect(screen.getByText("+2 plies")).toBeDisabled();
    rerender(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    expect(screen.getByText("+2 plies")).toBeEnabled();
  });

  it("unmounting during a search stops it; a new mount starts clean", () => {
    const e = fakeEngine();
    const l = lineOf();
    const { unmount } = render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("+2 plies"));
    unmount();
    expect(e.stop).toHaveBeenCalledTimes(1);
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    expect(screen.getByText("+2 plies")).toBeEnabled();
    expect(l.last()).toEqual(["e4", "e5"]);
  });
});
