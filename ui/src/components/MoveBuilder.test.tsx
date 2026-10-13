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
  function Host({ onChange, initialMoves, line, disabled, color = "w" }: { onChange: (s: MoveBuilderState) => void; initialMoves?: string[]; line: string[]; disabled?: boolean; color?: "w" | "b" }) {
    const [ev, setEv] = useState<EngineEval | null>(null);
    useEffect(() => {
      setState = setEv;
    }, []);
    return <MoveBuilder fen={START} color={color} onChange={onChange} initialMoves={initialMoves} extend={{ line, engine: api(ev), depth: 12 }} disabled={disabled} />;
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

describe("Extend", () => {
  it("adds up to and including the player's next move: one ply when it is the player's turn, two when not", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]} />);
    fireEvent.click(screen.getByText("Extend +1"));
    expect(l.last()).toEqual(["e4", "e5", "Nf3"]);
    expect(e.analyze).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Extend +2"));
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"]); // the store has one ply left, the opponent's: not enough
    fireEvent.click(screen.getByText("Extend +2"));
    expect(e.reset).toHaveBeenCalledTimes(1);
    expect(e.analyze).toHaveBeenCalledTimes(1);
    const end = e.analyze.mock.calls[0][0] as string;
    expect(screen.getByText("searching…")).toBeDisabled();
    e.search(end, ["a7a6", "b5a4", "g8f6"]);
    expect(l.last()).toEqual(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]);
    expect(shownPly()).toBe(7);
    expect(screen.getByText("Extend +2")).toBeEnabled();
  });

  it("establishes a player-ending line from an empty seed, for both colours", () => {
    const e = fakeEngine();
    const l = lineOf();
    const { unmount } = render(<e.Host onChange={l.onChange} line={[]} />);
    expect(l.last()).toEqual([]);
    fireEvent.click(screen.getByText("Extend +1"));
    e.search(START, ["e2e4", "e7e5"]);
    expect(l.last()).toEqual(["e4"]);
    unmount();
    const black = fakeEngine();
    const lb = lineOf();
    render(<black.Host onChange={lb.onChange} color="b" initialMoves={["d4"]} line={[]} />);
    fireEvent.click(screen.getByText("Extend +1"));
    const end = black.analyze.mock.calls[0][0] as string;
    black.search(end, ["d7d5", "c2c4"]);
    expect(lb.last()).toEqual(["d4", "d5"]);
    fireEvent.click(screen.getByText("Extend +2"));
    black.search(black.analyze.mock.calls[1][0] as string, ["c2c4", "e7e6"]);
    expect(lb.last()).toEqual(["d4", "d5", "c4", "e6"]);
  });

  it("Undo back onto the stored line reuses it; a replaced suffix goes to the engine", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5", "Nf3"]} line={["e4", "e5", "Nf3", "Nc6", "Bb5"]} />);
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("Extend +1"));
    expect(l.last()).toEqual(["e4", "e5", "Nf3"]);
    expect(e.analyze).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("↶ Undo"));
    nextDrop = { from: "c7", to: "c5" };
    fireEvent.click(screen.getByText("play"));
    expect(l.last()).toEqual(["e4", "c5"]);
    fireEvent.click(screen.getByText("Extend +1"));
    expect(e.analyze).toHaveBeenCalledWith(AFTER_E4_C5, 12);
  });

  it("a stored line whose next token does not play hands over to the engine instead of going inert", () => {
    const e = fakeEngine();
    const l = lineOf();
    // The seed stopped at Qh5 (Black has no Nf3); the stored line still "continues" past it.
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5", "Qh5", "Nf3"]} line={["e4", "e5", "Qh5", "Nf3", "Nc6"]} />);
    expect(l.last()).toEqual(["e4", "e5", "Qh5"]);
    fireEvent.click(screen.getByText("Extend +2"));
    expect(e.analyze).toHaveBeenCalledTimes(1);
    expect(screen.getByText("searching…")).toBeDisabled();
  });

  it("a one-ply answer is taken when it is the player's move and refused with a note when it is the opponent's; a terminal board disables the control", () => {
    const e = fakeEngine();
    const l = lineOf();
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("Extend +1"));
    e.search(AFTER_E4_E5, ["d1h5"]);
    expect(l.last()).toEqual(["e4", "e5", "Qh5"]);
    fireEvent.click(screen.getByText("Extend +2"));
    const end = e.analyze.mock.calls[1][0] as string;
    e.search(end, ["b8c6"]); // the opponent's reply alone cannot end on the player's move
    expect(l.last()).toEqual(["e4", "e5", "Qh5"]);
    expect(screen.getByRole("status")).toHaveTextContent("The engine has no further move here.");
    expect(screen.getByText("Extend +2")).toBeEnabled();
    // Scholar's mate: a terminal end disables the control, and an edit clears the note.
    nextDrop = { from: "b8", to: "c6" };
    fireEvent.click(screen.getByText("play"));
    expect(screen.queryByRole("status")).toBeNull();
    nextDrop = { from: "f1", to: "c4" };
    fireEvent.click(screen.getByText("play"));
    nextDrop = { from: "g8", to: "f6" };
    fireEvent.click(screen.getByText("play"));
    nextDrop = { from: "h5", to: "f7" };
    fireEvent.click(screen.getByText("play"));
    expect(l.last()).toEqual(["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]);
    expect(screen.getByText("Extend +2")).toBeDisabled();
    expect(screen.getByText("Extend +2")).toHaveAttribute("title", "The game is over at the end of the line");
  });

  it("caps the line at the parent's limit, thirty by default", () => {
    const e = fakeEngine();
    const l = lineOf();
    const seed = "a3 a6 b3 b6 c3 c6 d3 d6 e3 e6 f3 f6 g3 g6 h3 h6 a4 a5 b4 b5 c4 c5 d4 d5 e4 e5 f4 f5".split(" "); // 28 plies, White to move
    render(<e.Host onChange={l.onChange} initialMoves={seed} line={[]} />);
    expect(l.last()).toHaveLength(28);
    fireEvent.click(screen.getByText("Extend +1"));
    const end = e.analyze.mock.calls[0][0] as string;
    e.search(end, ["g1f3", "g8f6"]); // e4 is already played in the seed
    expect(l.last()).toHaveLength(29);
    expect(screen.getByText("Extend +2")).toBeDisabled(); // two more would pass thirty
    expect(screen.getByText("Extend +2")).toHaveAttribute("title", "A line holds at most 30 plies");
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
    fireEvent.click(screen.getByText("Extend +1"));
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
    fireEvent.click(screen.getByText("Extend +1"));
    e.search(AFTER_E4_E5, ["g1f3", "b8c6"]);
    expect(l.last()).toEqual(["e4", "e5", "Nf3"]);
    e.push(AFTER_E4_E5, ["g1f3", "b8c6"], false); // the hook reports the same finished search again
    expect(l.last()).toEqual(["e4", "e5", "Nf3"]);
    // Back to e4 e5: the hook still holds the old finished evaluation for that very board.
    fireEvent.click(screen.getByText("↶ Undo"));
    fireEvent.click(screen.getByText("Extend +1"));
    expect(e.analyze).toHaveBeenCalledTimes(2);
    expect(l.last()).toEqual(["e4", "e5"]); // not applied: no fresh dispatch seen yet
    expect(screen.getByText("searching…")).toBeDisabled();
    e.search(AFTER_E4_E5, ["f1c4", "g8f6"]);
    expect(l.last()).toEqual(["e4", "e5", "Bc4"]);
  });

  it("saving (disabled) drops the pending search and keeps the line it had at the click", () => {
    const e = fakeEngine();
    const l = lineOf();
    const { rerender } = render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("Extend +1"));
    rerender(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} disabled />);
    expect(e.stop).toHaveBeenCalledTimes(1);
    e.search(AFTER_E4_E5, ["g1f3", "b8c6"]);
    expect(l.last()).toEqual(["e4", "e5"]);
    expect(screen.getByText("Extend +1")).toBeDisabled();
    rerender(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    expect(screen.getByText("Extend +1")).toBeEnabled();
  });

  it("unmounting during a search stops it; a new mount starts clean", () => {
    const e = fakeEngine();
    const l = lineOf();
    const { unmount } = render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    fireEvent.click(screen.getByText("Extend +1"));
    unmount();
    expect(e.stop).toHaveBeenCalledTimes(1);
    render(<e.Host onChange={l.onChange} initialMoves={["e4", "e5"]} line={[]} />);
    expect(screen.getByText("Extend +1")).toBeEnabled();
    expect(l.last()).toEqual(["e4", "e5"]);
  });
});
