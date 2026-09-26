/**
 * The solver's Explore launcher: it seeds the layer with the board the solver is showing at the
 * click, keeps that seed while a reply lands underneath, and closes on a puzzle change. The layer
 * itself is tested in ExploreLayer.test.tsx.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PuzzleEngine } from "./PuzzleEngine";

let nextDrop = { from: "e2", to: "e4" };
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { onPieceDrop?: (a: { piece: unknown; sourceSquare: string; targetSquare: string | null }) => boolean } }) => (
    <button type="button" onClick={() => options.onPieceDrop?.({ piece: null, sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
      drop
    </button>
  ),
}));
vi.mock("./ExploreLayer", () => ({
  ExploreLayer: ({ fen, orientation, onClose }: { fen: string; orientation: string; onClose: () => void }) => (
    <div data-testid="explore-layer" data-fen={fen} data-orientation={orientation}>
      <button type="button" onClick={onClose}>
        close explore
      </button>
    </div>
  ),
}));

const START = new Chess().fen();
const after = (...sans: string[]) => {
  const g = new Chess();
  for (const s of sans) g.move(s);
  return g.fen();
};

beforeEach(() => {
  vi.useFakeTimers();
  nextDrop = { from: "e2", to: "e4" };
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) })));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("PuzzleEngine's Explore launcher", () => {
  it("opens the layer on the live board, oriented as the solver, and a reply landing underneath does not re-seed it", () => {
    render(<PuzzleEngine fen={START} solutionLine={["e4", "e5", "Nf3"]} color="w" />);
    const launch = screen.getByTestId("explore-launch");
    expect(launch).toBeEnabled();
    fireEvent.click(launch);
    let layer = screen.getByTestId("explore-layer");
    expect(layer.dataset.fen).toBe(START);
    expect(layer.dataset.orientation).toBe("white");
    fireEvent.click(screen.getByText("close explore"));
    expect(screen.queryByTestId("explore-layer")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("drop")); // 1.e4, correct; the reply is due in 400 ms
    fireEvent.click(screen.getByTestId("explore-launch"));
    layer = screen.getByTestId("explore-layer");
    expect(layer.dataset.fen).toBe(after("e4"));
    act(() => void vi.advanceTimersByTime(500)); // 1...e5 lands
    expect(screen.getByTestId("explore-layer").dataset.fen).toBe(after("e4")); // the captured seed
    fireEvent.click(screen.getByText("close explore"));
    fireEvent.click(screen.getByTestId("explore-launch"));
    expect(screen.getByTestId("explore-layer").dataset.fen).toBe(after("e4", "e5")); // the board now
  });

  it("closes on a puzzle change, and takes the black orientation", () => {
    const { rerender } = render(<PuzzleEngine fen={START} solutionLine={["e4"]} color="w" />);
    fireEvent.click(screen.getByTestId("explore-launch"));
    expect(screen.getByTestId("explore-layer")).toBeInTheDocument();
    const next = after("e4");
    rerender(<PuzzleEngine fen={next} solutionLine={["e5"]} color="b" />);
    expect(screen.queryByTestId("explore-layer")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("explore-launch"));
    const layer = screen.getByTestId("explore-layer");
    expect(layer.dataset.fen).toBe(next);
    expect(layer.dataset.orientation).toBe("black");
  });
});
