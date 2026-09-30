/**
 * Promotion in the solver: a pawn move to the last rank is held for the chooser rather than
 * played as a queen, the chosen piece is what gets graded, both entry paths reach the chooser,
 * and the chooser does not survive a reset or a puzzle change.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PuzzleEngine } from "./PuzzleEngine";

let nextDrop = { from: "b7", to: "b8" };
let lastPosition = "";
vi.mock("react-chessboard", () => ({
  Chessboard: ({
    options,
  }: {
    options: {
      position: string;
      onPieceDrop?: (a: { piece: unknown; sourceSquare: string; targetSquare: string | null }) => boolean;
      onSquareClick?: (a: { piece: unknown; square: string }) => void;
    };
  }) => {
    lastPosition = options.position;
    return (
      <div>
        <button type="button" onClick={() => options.onPieceDrop?.({ piece: null, sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
          drop
        </button>
        <button type="button" onClick={() => options.onSquareClick?.({ piece: null, square: nextDrop.from })}>
          click from
        </button>
        <button type="button" onClick={() => options.onSquareClick?.({ piece: null, square: nextDrop.to })}>
          click to
        </button>
      </div>
    );
  },
}));

// White pawn on b7, kings far apart: b8=Q checks but does not mate, so only the stored piece solves.
const PROMO = "7k/1P6/8/8/8/8/8/K7 w - - 0 1";
const after = (fen: string, ...sans: string[]) => {
  const g = new Chess(fen);
  for (const s of sans) g.move(s);
  return g.fen();
};
const chooser = () => screen.queryByText("Promote to:");

beforeEach(() => {
  vi.useFakeTimers();
  nextDrop = { from: "b7", to: "b8" };
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) })));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("promotion in the solver", () => {
  it("a drop onto the last rank opens the chooser and plays nothing until a piece is chosen", () => {
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    expect(chooser()).toBeNull();
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).not.toBeNull();
    expect(lastPosition).toBe(PROMO); // snapped back
    expect(onComplete).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Your turn");
  });

  it.each([
    ["rook", "b8=R+", true, "Puzzle complete!"],
    ["queen", "b8=Q+", false, "Not quite"],
    ["knight", "b8=N", false, "Not quite"],
  ])("choosing %s is graded as %s", (piece, san, solved, message) => {
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("drop"));
    fireEvent.click(screen.getByLabelText(`Promote to ${piece}`));
    expect(chooser()).toBeNull();
    // A wrong move is marked, not played: the board keeps the position it was played from.
    expect(lastPosition).toBe(solved ? after(PROMO, san) : PROMO);
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith(solved, [san]);
    expect(screen.getByRole("status")).toHaveTextContent(message);
  });

  it("the click path reaches the same chooser", () => {
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("click from"));
    fireEvent.click(screen.getByText("click to"));
    expect(chooser()).not.toBeNull();
    expect(lastPosition).toBe(PROMO);
    fireEvent.click(screen.getByLabelText("Promote to rook"));
    expect(onComplete).toHaveBeenCalledWith(true, ["b8=R+"]);
  });

  it("a black pawn capturing onto the first rank is held for the chooser too, and the knight is what gets played", () => {
    // Black to move: ...bxa1=N. The chooser carries no colour of its own; the piece letters serve both sides.
    const fen = "k7/8/8/8/8/8/1p6/R6K b - - 0 1";
    nextDrop = { from: "b2", to: "a1" };
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={fen} solutionLine={["bxa1=N"]} color="b" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).not.toBeNull();
    expect(lastPosition).toBe(fen);
    fireEvent.click(screen.getByLabelText("Promote to knight"));
    expect(lastPosition).toBe(after(fen, "bxa1=N"));
    expect(onComplete).toHaveBeenCalledWith(true, ["bxa1=N"]);
  });

  it("a miss while the chooser is open (an illegal drop) keeps the chooser; a real move supersedes it", () => {
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("drop")); // b7-b8 held
    nextDrop = { from: "a1", to: "h8" }; // not a king move
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).not.toBeNull();
    expect(onComplete).not.toHaveBeenCalled();
    nextDrop = { from: "a1", to: "a2" };
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).toBeNull();
    expect(onComplete).toHaveBeenCalledWith(false, ["Ka2"]);
  });

  it("a move that is not a promotion never opens the chooser", () => {
    nextDrop = { from: "a1", to: "a2" };
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).toBeNull();
    expect(onComplete).toHaveBeenCalledWith(false, ["Ka2"]);
  });

  it("Try Again after a wrong promotion starts clean, and the next promotion is graded afresh", () => {
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("drop"));
    fireEvent.click(screen.getByLabelText("Promote to queen"));
    expect(onComplete).toHaveBeenCalledWith(false, ["b8=Q+"]);
    fireEvent.click(screen.getByText("Try Again"));
    expect(chooser()).toBeNull();
    expect(lastPosition).toBe(PROMO);
    fireEvent.click(screen.getByText("drop"));
    fireEvent.click(screen.getByLabelText("Promote to rook"));
    expect(lastPosition).toBe(after(PROMO, "b8=R"));
    expect(screen.getByRole("status")).toHaveTextContent("Puzzle complete!");
    expect(onComplete).toHaveBeenLastCalledWith(true, ["b8=R+"]); // the wrong move was sliced off
  });

  it("a pending chooser does not survive a puzzle change", () => {
    const { rerender } = render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" />);
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).not.toBeNull();
    const start = new Chess().fen();
    rerender(<PuzzleEngine fen={start} solutionLine={["e4"]} color="w" />);
    expect(chooser()).toBeNull();
    expect(lastPosition).toBe(start);
  });

  it("a pending chooser is withheld while the parent still owes an attempt, and choosing then plays nothing", () => {
    const onComplete = vi.fn();
    const { rerender } = render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    fireEvent.click(screen.getByText("drop"));
    expect(chooser()).not.toBeNull();
    rerender(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} submissionLocked />);
    expect(chooser()).toBeNull();
    rerender(<PuzzleEngine fen={PROMO} solutionLine={["b8=R"]} color="w" onComplete={onComplete} />);
    expect(chooser()).not.toBeNull(); // the held squares are still valid on the unchanged board
    fireEvent.click(screen.getByLabelText("Promote to rook"));
    expect(onComplete).toHaveBeenCalledWith(true, ["b8=R+"]);
  });

  it("in a longer line the opponent's reply follows a promotion like any other move", () => {
    // 1.b8=R Kg7 2.Rb7+ : the reply auto-plays after the chosen piece.
    render(<PuzzleEngine fen={PROMO} solutionLine={["b8=R", "Kg7", "Rb7+"]} color="w" />);
    fireEvent.click(screen.getByText("drop"));
    fireEvent.click(screen.getByLabelText("Promote to rook"));
    expect(screen.getByRole("status")).toHaveTextContent("Correct!");
    act(() => void vi.advanceTimersByTime(500));
    expect(lastPosition).toBe(after(PROMO, "b8=R", "Kg7"));
    expect(chooser()).toBeNull();
  });
});
