/**
 * The solver resets when its puzzle changes, never on mount: its state already starts as a reset
 * leaves it, and a move played before the mount effects had run (a fast first drop) must stand.
 */
import { render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PuzzleEngine } from "./PuzzleEngine";

type Drop = (a: { piece: unknown; sourceSquare: string; targetSquare: string | null }) => boolean;
let dropOnMount: { from: string; to: string } | null = null;
let lastPosition = "";
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position: string; onPieceDrop?: Drop } }) => {
    lastPosition = options.position;
    // A layout effect runs before the solver's passive effects: the drop lands where a user's
    // could on a loaded device, after the first paint and before the mount effects.
    useLayoutEffect(() => {
      if (dropOnMount) options.onPieceDrop?.({ piece: null, sourceSquare: dropOnMount.from, targetSquare: dropOnMount.to });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return <div />;
  },
}));

// Back-rank mate in one: Ra8#.
const MATE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1";
const AFTER = "R5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 1 1";
const OTHER = "6k1/5ppp/8/8/8/8/5PPP/1R4K1 w - - 0 1";

beforeEach(() => {
  dropOnMount = null;
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) })));
});
afterEach(() => vi.unstubAllGlobals());

describe("the solver's reset", () => {
  it("keeps a move played before the mount effects ran", () => {
    dropOnMount = { from: "a1", to: "a8" };
    const onComplete = vi.fn();
    render(<PuzzleEngine fen={MATE} solutionLine={["Ra8#"]} color="w" onComplete={onComplete} />);
    expect(lastPosition).toBe(AFTER);
    expect(screen.getByRole("status")).toHaveTextContent("Puzzle complete!");
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("still starts afresh when the puzzle changes", () => {
    dropOnMount = { from: "a1", to: "a8" };
    const { rerender } = render(<PuzzleEngine fen={MATE} solutionLine={["Ra8#"]} color="w" onComplete={vi.fn()} />);
    expect(lastPosition).toBe(AFTER);
    rerender(<PuzzleEngine fen={OTHER} solutionLine={["Rb8#"]} color="w" onComplete={vi.fn()} />);
    expect(lastPosition).toBe(OTHER);
    expect(screen.getByRole("status")).toHaveTextContent("Your turn");
  });
});
