/**
 * The review phase: a pass that reaches its last ply with missed boards goes back to them, in
 * line order and cycling, until each is right three times in a row; a wrong answer resets that
 * board alone. The pass's attempts are sent exactly as before and the review sends nothing.
 * `onNavState` says, synchronously, whether a host may offer a way past the puzzle.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PuzzleEngine, REVIEW_STREAK, type NavState } from "./PuzzleEngine";
import type { AcceptanceMap } from "../practice";
import { mapKey } from "../utils/chess";

let lastPosition = "";
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position: string; onPieceDrop?: (a: { piece: unknown; sourceSquare: string; targetSquare: string | null }) => boolean } }) => {
    lastPosition = options.position;
    return (
      <div>
        {["a1a8", "a1a7", "a1b1", "a1a2", "a2a1", "a7a8", "a7b7", "b7c7", "b7b8", "f6f7", "f6g6", "b8b7", "h2h3", "b1b7", "b1b8"].map((m) => (
          <button key={m} type="button" onClick={() => options.onPieceDrop?.({ piece: null, sourceSquare: m.slice(0, 2), targetSquare: m.slice(2, 4) })}>
            {m}
          </button>
        ))}
      </div>
    );
  },
}));

// A rook ending, three player moves: Ra7 Kf8 Rb7 Ke8 Rc7 Kd8 (the line ends on the opponent's reply).
// The player's boards are plies 0, 2 and 4. Ra8 is wrong at ply 0 and ply 2, Rb8 at ply 4.
const ROOK = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1";
const ROOK_LINE = ["Ra7", "Kf8", "Rb7", "Ke8", "Rc7", "Kd8"];
const after = (fen: string, ...sans: string[]) => {
  const g = new Chess(fen);
  for (const s of sans) g.move(s);
  return g.fen();
};
const BOARD0 = ROOK;
const BOARD2 = after(ROOK, "Ra7", "Kf8");

const play = (move: string) => {
  fireEvent.click(screen.getByText(move));
  act(() => {
    vi.advanceTimersByTime(400); // the opponent's reply
  });
};
const tryAgain = () => fireEvent.click(screen.getByText("Try Again"));
const status = () => screen.getByRole("status").textContent;
const progress = () => screen.queryByTestId("review-progress")?.textContent ?? null;
/** A right review answer, then the pause before the next board. */
const reviewRight = (move: string) => {
  fireEvent.click(screen.getByText(move));
  expect(status()).toMatch(/^Right — \d in a row$/);
  act(() => {
    vi.advanceTimersByTime(600);
  });
};

function setup(props: Partial<Parameters<typeof PuzzleEngine>[0]> = {}) {
  const nav: NavState[] = [];
  const onComplete = vi.fn();
  const onNavState = vi.fn((s: NavState) => nav.push(s));
  const view = render(<PuzzleEngine fen={ROOK} solutionLine={ROOK_LINE} color="w" onComplete={onComplete} onNavState={onNavState} {...props} />);
  return { nav, onComplete, view };
}

/** Miss ply 0 and ply 2 (each fixed by Try Again), then finish the line. */
function missBoardsZeroAndTwo() {
  play("a1a8");
  tryAgain();
  play("a1a7");
  play("a7a8");
  tryAgain();
  play("a7b7");
  play("b7c7");
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) })));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the review phase", () => {
  it("two missed boards each need three right in a row, in line order; the pass's attempts are unchanged and the review sends nothing", () => {
    const { nav, onComplete } = setup();
    missBoardsZeroAndTwo();
    // Exactly the attempts the solver always sent: two misses, then the completion.
    expect(onComplete.mock.calls).toEqual([
      [false, ["Ra8#"]],
      [false, ["Ra7", "Ra8+"]],
      [true, ["Ra7", "Rb7", "Rc7"]],
    ]);
    expect(nav).toEqual(["owed"]);
    expect(progress()).toContain("Review: 0 of 2 boards fixed");
    expect(lastPosition).toBe(BOARD0);
    for (let round = 1; round <= REVIEW_STREAK; round++) {
      expect(lastPosition).toBe(BOARD0);
      reviewRight("a1a7");
      expect(lastPosition).toBe(BOARD2); // the next board in line order
      reviewRight("a7b7");
      if (round < REVIEW_STREAK) expect(nav).toEqual(["owed"]); // owed until the last board's third
    }
    expect(status()).toBe("Puzzle complete!");
    expect(progress()).toBeNull();
    expect(nav).toEqual(["owed", "free"]);
    expect(onComplete).toHaveBeenCalledTimes(3);
    expect(screen.getByText("Replay")).toBeInTheDocument();
    // The pass's end is shown again whole: its board and its place in the line, so the dots and the
    // launchers describe the board on show.
    expect(lastPosition).toBe(after(ROOK, ...ROOK_LINE));
    expect(screen.getByLabelText("3 of 3 moves")).toBeInTheDocument();
  });

  it("the server's downgrade keeps its banner over the review", () => {
    const { view } = setup();
    missBoardsZeroAndTwo();
    expect(status()).toBe("Find the move");
    view.rerender(<PuzzleEngine fen={ROOK} solutionLine={ROOK_LINE} color="w" serverDowngraded />);
    expect(status()).toBe("Server recorded this as a wrong attempt");
  });

  it("on a mating line's last player ply, review accepts any move that mates", () => {
    // Two rooks: the line mates with Rb8#; Ra8# mates as well.
    const rooks = "6k1/5ppp/8/8/8/8/5PPP/RR4K1 w - - 0 1";
    const { nav } = setup({ fen: rooks, solutionLine: ["h3", "Kh8", "Rb8#"] });
    play("h2h3");
    fireEvent.click(screen.getByText("b1b7")); // not mate
    tryAgain();
    fireEvent.click(screen.getByText("b1b8"));
    expect(progress()).toContain("Review: 0 of 1 board fixed");
    reviewRight("a1a8");
    reviewRight("b1b8");
    reviewRight("a1a8");
    expect(status()).toBe("Puzzle complete!");
    expect(nav).toEqual(["owed", "free"]);
  });

  it("a wrong review answer resets only that board's streak", () => {
    const { nav, onComplete } = setup();
    missBoardsZeroAndTwo();
    reviewRight("a1a7"); // board 0: 1
    reviewRight("a7b7"); // board 2: 1
    reviewRight("a1a7"); // board 0: 2
    reviewRight("a7b7"); // board 2: 2
    fireEvent.click(screen.getByText("a1a8")); // board 0 wrong
    expect(status()).toBe("Not quite — this board starts again");
    expect(screen.getByLabelText("0 of 3 in a row")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Continue"));
    expect(lastPosition).toBe(BOARD2);
    expect(screen.getByLabelText("2 of 3 in a row")).toBeInTheDocument(); // untouched
    reviewRight("a7b7"); // board 2: 3, fixed
    expect(progress()).toContain("Review: 1 of 2 boards fixed");
    // Only board 0 is left: it comes up alone, three times.
    for (let n = 1; n <= REVIEW_STREAK; n++) {
      expect(lastPosition).toBe(BOARD0);
      reviewRight("a1a7");
    }
    expect(status()).toBe("Puzzle complete!");
    expect(nav).toEqual(["owed", "free"]);
    expect(onComplete).toHaveBeenCalledTimes(3);
  });

  it("Show me in review reveals the move without counting it", () => {
    setup();
    missBoardsZeroAndTwo();
    fireEvent.click(screen.getByText("a1a8"));
    fireEvent.click(screen.getByText("Show me"));
    expect(lastPosition).toBe(after(BOARD0, "Ra7"));
    expect(screen.getByLabelText("0 of 3 in a row")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Continue"));
    expect(lastPosition).toBe(BOARD2);
  });

  it("a pass with no miss never enters review and is free at its last ply", () => {
    const { nav, onComplete } = setup();
    play("a1a7");
    play("a7b7");
    play("b7c7");
    expect(onComplete.mock.calls).toEqual([[true, ["Ra7", "Rb7", "Rc7"]]]);
    expect(status()).toBe("Puzzle complete!");
    expect(progress()).toBeNull();
    expect(nav).toEqual(["free"]);
  });

  it("a board missed and then solved by Try Again is reviewed, and the others are not", () => {
    const { nav } = setup();
    play("a1a7");
    play("a7b7");
    fireEvent.click(screen.getByText("b7b8")); // ply 4 wrong
    tryAgain();
    play("b7c7");
    expect(progress()).toContain("Review: 0 of 1 board fixed");
    expect(lastPosition).toBe(after(ROOK, "Ra7", "Kf8", "Rb7", "Ke8"));
    expect(nav).toEqual(["owed"]);
  });

  it("a board is judged by its first visit in the pass: right there once, a later miss on it is not reviewed", () => {
    // Ra2 Kh8 Ra1 Kg8 brings the start board back at ply 4, where the line plays Ra7.
    const { nav, onComplete } = setup({ solutionLine: ["Ra2", "Kh8", "Ra1", "Kg8", "Ra7"] });
    play("a1a2");
    play("a2a1");
    fireEvent.click(screen.getByText("a1a8")); // wrong on the start board's second visit
    expect(nav).toEqual(["owed"]);
    tryAgain();
    fireEvent.click(screen.getByText("a1a7"));
    expect(onComplete).toHaveBeenLastCalledWith(true, ["Ra2", "Ra1", "Ra7"]);
    expect(progress()).toBeNull();
    expect(status()).toBe("Puzzle complete!");
    expect(nav).toEqual(["owed", "free"]);
  });

  it("owed is reported in the same call that sends the miss, before anything is awaited", () => {
    const navAtMiss: NavState[][] = [];
    const nav: NavState[] = [];
    render(
      <PuzzleEngine
        fen={ROOK}
        solutionLine={ROOK_LINE}
        color="w"
        onNavState={(s) => nav.push(s)}
        onComplete={(solved) => {
          if (!solved) navAtMiss.push([...nav]);
        }}
      />,
    );
    fireEvent.click(screen.getByText("a1a8"));
    expect(navAtMiss).toEqual([["owed"]]);
  });

  it("a segment with one player move is not reviewed: owed until completed, then free", () => {
    const { nav, onComplete } = setup({ solutionLine: ["Ra8#"] });
    fireEvent.click(screen.getByText("a1b1"));
    expect(nav).toEqual(["owed"]);
    tryAgain();
    fireEvent.click(screen.getByText("a1a8"));
    expect(onComplete.mock.calls).toEqual([
      [false, ["Rb1"]],
      [true, ["Ra8#"]],
    ]);
    expect(progress()).toBeNull();
    expect(status()).toBe("Puzzle complete!");
    expect(nav).toEqual(["owed", "free"]);
  });

  it("a Replay pass reviews its own misses and none of the first pass's", () => {
    const { nav } = setup();
    // Pass 1 misses ply 0.
    play("a1a8");
    tryAgain();
    play("a1a7");
    play("a7b7");
    play("b7c7");
    for (let n = 0; n < REVIEW_STREAK; n++) reviewRight("a1a7");
    expect(nav).toEqual(["owed", "free"]);
    // Pass 2 misses ply 2 only.
    fireEvent.click(screen.getByText("Replay"));
    play("a1a7");
    play("a7a8");
    expect(nav).toEqual(["owed", "free", "owed"]); // owed again at once
    tryAgain();
    play("a7b7");
    play("b7c7");
    expect(progress()).toContain("Review: 0 of 1 board fixed");
    expect(lastPosition).toBe(BOARD2);
    for (let n = 0; n < REVIEW_STREAK; n++) reviewRight("a7b7");
    expect(nav).toEqual(["owed", "free", "owed", "free"]);
    // Pass 3 misses nothing: no review, nothing owed.
    fireEvent.click(screen.getByText("Replay"));
    play("a1a7");
    play("a7b7");
    play("b7c7");
    expect(progress()).toBeNull();
    expect(nav).toEqual(["owed", "free", "owed", "free"]);
  });

  it("Play On is never reviewed", () => {
    const { nav, onComplete } = setup({ isRepertoire: true, presentationPly: 2 });
    play("a1a7");
    play("a7b7");
    expect(status()).toBe("Puzzle complete!");
    play("Play On →"); // the remainder opens on the opponent's Ke8
    fireEvent.click(screen.getByText("b7b8")); // wrong in the remainder
    tryAgain();
    play("b7c7");
    expect(status()).toBe("Line complete!");
    expect(progress()).toBeNull();
    expect(nav).toEqual(["free"]);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("unmounting mid-review sends nothing and leaves no timer behind", () => {
    const { nav, onComplete, view } = setup();
    missBoardsZeroAndTwo();
    for (let n = 1; n < REVIEW_STREAK; n++) {
      reviewRight("a1a7");
      reviewRight("a7b7");
    }
    reviewRight("a1a7");
    fireEvent.click(screen.getByText("a7b7")); // the review's last right answer, its pause pending
    view.unmount();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onComplete).toHaveBeenCalledTimes(3);
    expect(nav).toEqual(["owed"]);
  });

  it("a missed-mate review accepts any move the acceptance map accepts", () => {
    // Mate in two two ways: Kf7 Kh7 Rh1#, or Kg6 Kg8 Ra8#.
    const start = "7k/8/5K2/8/8/8/8/R7 w - - 0 1";
    const key = (fen: string) => mapKey(new Chess(fen));
    const map: AcceptanceMap = {
      v: 1,
      n: 2,
      p: { [key(start)]: ["f6f7", "f6g6"], [key(after(start, "Kf7", "Kh7"))]: ["a1h1"], [key(after(start, "Kg6", "Kg8"))]: ["a1a8"] },
      d: { [key(after(start, "Kf7"))]: "h8h7", [key(after(start, "Kg6"))]: "h8g8" },
    };
    const { nav, onComplete } = setup({ fen: start, solutionLine: ["Kf7", "Kh7", "Rh1#"], acceptanceMap: map });
    fireEvent.click(screen.getByText("a1a2")); // not a mating move
    expect(status()).toBe("There’s a faster mate — find the quickest one");
    tryAgain();
    play("f6g6");
    fireEvent.click(screen.getByText("a1a8"));
    expect(onComplete).toHaveBeenLastCalledWith(true, ["Kg6", "Ra8#"]);
    expect(lastPosition).toBe(start);
    reviewRight("f6f7"); // the map's other first move
    reviewRight("f6g6");
    reviewRight("f6f7");
    expect(status()).toBe("Checkmate!"); // the pass's own end message
    expect(nav).toEqual(["owed", "free"]);
  });

  it("a review answer that promotes goes through the chooser and is graded on the piece", () => {
    const promo = "7k/1P6/8/8/8/8/8/K7 w - - 0 1";
    const { nav } = setup({ fen: promo, solutionLine: ["b8=R+", "Kh7", "Rb7+"] });
    const promote = (piece: string) => {
      fireEvent.click(screen.getByText("b7b8"));
      fireEvent.click(screen.getByLabelText(`Promote to ${piece}`));
    };
    promote("queen"); // wrong: the line promotes to a rook
    tryAgain();
    promote("rook");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    fireEvent.click(screen.getByText("b8b7"));
    expect(lastPosition).toBe(promo); // review: the promotion board
    promote("queen");
    expect(status()).toBe("Not quite — this board starts again");
    fireEvent.click(screen.getByText("Continue"));
    for (let n = 0; n < REVIEW_STREAK; n++) {
      promote("rook");
      expect(status()).toMatch(/^Right — \d in a row$/);
      act(() => {
        vi.advanceTimersByTime(600);
      });
    }
    expect(status()).toBe("Puzzle complete!");
    expect(nav).toEqual(["owed", "free"]);
  });
});
