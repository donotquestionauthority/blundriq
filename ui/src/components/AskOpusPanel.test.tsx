/**
 * The Ask Opus panel against a fake engine and fake hosts: what it sends, what it keeps across
 * boards, what it drops, and the named-move alternative — searched, dismissed, or sent with its
 * outcome when the move ends the game.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Chess } from "chess.js";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { EngineEval } from "../engine/useStockfish";
import { AskOpusPanel } from "./AskOpusPanel";

// The engine is a fake: `enabled` is recorded on every render (the real hook creates a worker
// when it turns on and terminates it when it turns off), `push` publishes ready / evalState.
const analyze = vi.fn();
const enabledSeen: boolean[] = [];
let push: ((s: { ready: boolean; evalState: EngineEval | null }) => void) | null = null;
vi.mock("../engine/useStockfish", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../engine/useStockfish")>();
  return {
    ...actual,
    useStockfish: ({ enabled }: { enabled: boolean }) => {
      enabledSeen.push(enabled);
      const [s, setS] = useState<{ ready: boolean; evalState: EngineEval | null }>({ ready: false, evalState: null });
      push = setS;
      return { ...s, analyze, reset: vi.fn(), stop: vi.fn() };
    },
  };
});

const settingsGet = vi.fn();
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => settingsGet(...a) } };
});

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const STALEMATE_TRAP = "7k/8/5KQ1/8/8/8/8/8 w - - 0 1";
const AFTER_NF3 = (() => {
  const g = new Chess(START);
  g.move("Nf3");
  return g.fen();
})();
const ev = (fen: string, over: Partial<EngineEval> = {}): EngineEval => ({ fen, evalCp: -10, bestMoveUci: "d7d5", pvUci: ["d7d5", "d2d4"], depth: 16, thinking: false, ...over });

const ask = vi.fn();
const dryRun = vi.fn();
const writeText = vi.fn();

function Host({ fen = START, best = "e4", reason = null }: { fen?: string; best?: string | null; reason?: string | null }) {
  return <AskOpusPanel fen={fen} bestMoveSan={best} disabledReason={reason} ask={ask} dryRun={dryRun} />;
}
const box = () => screen.getByLabelText("Your question") as HTMLTextAreaElement;
const askButton = () => screen.getByRole("button", { name: /Ask Opus|Analysing/ });
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const flush = () => act(async () => {});

beforeEach(() => {
  enabledSeen.length = 0;
  push = null;
  ask.mockReset();
  dryRun.mockReset();
  analyze.mockReset();
  settingsGet.mockReset().mockResolvedValue({ explore_engine_depth: 20 });
  writeText.mockReset().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("asking", () => {
  it("sends the question with no alternative and shows the answer with its model", async () => {
    ask.mockResolvedValue({ explanation: "**d4 works because** it takes the centre.", cached: true, model: "claude-opus-5-5" });
    render(<Host />);
    type("I don't get this part");
    fireEvent.click(askButton());
    expect(ask).toHaveBeenCalledWith("I don't get this part", null);
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    await flush();
    expect(screen.getByText("d4 works because")).toBeInTheDocument();
    expect(screen.getByText("claude-opus-5-5 · cached")).toBeInTheDocument();
    expect(enabledSeen.every((e) => !e)).toBe(true); // no move named, no engine
  });

  it("a refusal is shown as its text; the cap's refusal by its message", async () => {
    ask.mockRejectedValueOnce(new ApiError(422, "the question is longer than 500 characters"));
    render(<Host />);
    fireEvent.click(askButton());
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent("the question is longer than 500 characters");
    ask.mockRejectedValueOnce(new ApiError(429, JSON.stringify({ window: "daily", message: "Limit reached: 50 AI calls per day." })));
    fireEvent.click(askButton());
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent("Limit reached: 50 AI calls per day.");
  });

  it("a board change clears the answer and keeps the question; a late reply for the old board is dropped", async () => {
    let resolve: (a: unknown) => void = () => {};
    ask.mockImplementationOnce(() => new Promise((r) => (resolve = r)));
    const { rerender } = render(<Host />);
    type("why?");
    fireEvent.click(askButton());
    expect(askButton()).toBeDisabled();
    rerender(<Host fen={AFTER_NF3} best="d5" />);
    expect(box().value).toBe("why?");
    expect(askButton()).not.toBeDisabled(); // the old request no longer owns the button
    await act(async () => resolve({ explanation: "Late.", cached: false, model: "m" }));
    expect(screen.queryByText("Late.")).toBeNull();
    ask.mockResolvedValueOnce({ explanation: "Now.", cached: false, model: "m" });
    fireEvent.click(askButton());
    await flush();
    expect(screen.getByText("Now.")).toBeInTheDocument();
    rerender(<Host fen={START} />);
    expect(screen.queryByText("Now.")).toBeNull();
  });

  it("a late reply for an earlier board never replaces the answer on show", async () => {
    let resolveSlow: (a: unknown) => void = () => {};
    ask.mockImplementationOnce(() => new Promise((r) => (resolveSlow = r)));
    const { rerender } = render(<Host />);
    fireEvent.click(askButton());
    rerender(<Host fen={AFTER_NF3} best="d5" />);
    ask.mockResolvedValueOnce({ explanation: "B answer.", cached: true, model: "m" });
    fireEvent.click(askButton());
    await flush();
    expect(screen.getByText("B answer.")).toBeInTheDocument();
    await act(async () => resolveSlow({ explanation: "A answer.", cached: false, model: "m" }));
    expect(screen.getByText("B answer.")).toBeInTheDocument();
    expect(screen.queryByText("A answer.")).toBeNull();
  });

  it("coming back to a board is a new visit: the earlier visit's reply never replaces the newer answer, nor holds the button", async () => {
    let resolveOld: (a: unknown) => void = () => {};
    ask.mockImplementationOnce(() => new Promise((r) => (resolveOld = r)));
    const { rerender } = render(<Host />);
    type("first");
    fireEvent.click(askButton());
    rerender(<Host fen={AFTER_NF3} best="d5" />);
    rerender(<Host fen={START} />);
    expect(askButton()).not.toBeDisabled(); // the old request belongs to a visit that ended
    ask.mockResolvedValueOnce({ explanation: "Newest.", cached: false, model: "m" });
    type("second");
    fireEvent.click(askButton());
    await flush();
    expect(screen.getByText("Newest.")).toBeInTheDocument();
    await act(async () => resolveOld({ explanation: "Obsolete answer.", cached: false, model: "m" }));
    expect(screen.getByText("Newest.")).toBeInTheDocument();
    expect(screen.queryByText("Obsolete answer.")).toBeNull();
    expect(askButton()).not.toBeDisabled();
  });

  it("within one visit the newer request wins, whatever order the replies arrive in", async () => {
    let resolveFirst: (a: unknown) => void = () => {};
    ask.mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)));
    const { rerender } = render(<Host />);
    fireEvent.click(askButton());
    // The button is held by the first request; a second ask is only possible after a board change
    // and return, which is the previous case — so the second request here comes from Copy prompt.
    rerender(<Host fen={AFTER_NF3} best="d5" />);
    rerender(<Host fen={START} />);
    dryRun.mockResolvedValueOnce({ model: "m", provider: "anthropic", temperature: null, thinking: null, max_tokens: 1, prefill: "", system_prompt: "", rendered_prompt: "p" });
    fireEvent.click(screen.getByText("Copy prompt"));
    await flush();
    expect(screen.getByText("✓ copied")).toBeInTheDocument();
    await act(async () => resolveFirst({ explanation: "Obsolete answer.", cached: false, model: "m" }));
    expect(screen.queryByText("Obsolete answer.")).toBeNull();
  });

  it("Copy prompt waits for its dry run and drops a failure for an earlier board", async () => {
    let resolveDry: (a: unknown) => void = () => {};
    dryRun.mockImplementationOnce(() => new Promise((r) => (resolveDry = r)));
    const { rerender } = render(<Host />);
    fireEvent.click(screen.getByText("Copy prompt"));
    expect(screen.getByText("Copy prompt")).toBeDisabled();
    expect(askButton()).toBeDisabled();
    rerender(<Host fen={AFTER_NF3} best="d5" />);
    expect(screen.getByText("Copy prompt")).not.toBeDisabled();
    await act(async () => resolveDry(Promise.reject(new ApiError(500, "boom"))));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("counts from 400 characters and stops at 500", () => {
    render(<Host />);
    type("x".repeat(399));
    expect(screen.queryByText(/\/500/)).toBeNull();
    type("x".repeat(401));
    expect(screen.getByText("401/500")).toBeInTheDocument();
    type("x".repeat(600));
    expect(box().value).toHaveLength(500);
  });

  it("Copy prompt puts the dry run on the clipboard", async () => {
    dryRun.mockResolvedValue({ model: "claude-opus-5-5", provider: "anthropic", temperature: null, thinking: { type: "adaptive" }, max_tokens: 16000, prefill: "", system_prompt: "coach", rendered_prompt: "Position (FEN): x\n\nMy question: q" });
    render(<Host />);
    type("q");
    fireEvent.click(screen.getByText("Copy prompt"));
    await flush();
    expect(dryRun).toHaveBeenCalledWith("q", null);
    expect(writeText.mock.calls[0][0]).toContain("=== USER PROMPT ===\nPosition (FEN): x\n\nMy question: q");
    expect(screen.getByText("✓ copied")).toBeInTheDocument();
  });

  it("a disabled reason is the one line, with no box and no button", () => {
    render(<Host reason="No engine analysis for this position." />);
    expect(screen.getByText("No engine analysis for this position.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Your question")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("a move named in the question", () => {
  it("is searched on the board after it, at the saved depth, and sent as the alternative", async () => {
    ask.mockResolvedValue({ explanation: "Because Nf3 lets ...d5.", cached: false, model: "m" });
    render(<Host />);
    type("Why can't I play Nf3 here?");
    expect(screen.getByTestId("ask-chip")).toHaveTextContent("Analysing Nf3…");
    expect(askButton()).toBeDisabled();
    expect(askButton()).toHaveTextContent("Analysing Nf3…");
    expect(enabledSeen.at(-1)).toBe(true);
    await flush(); // the settings answer
    act(() => push?.({ ready: true, evalState: null }));
    await waitFor(() => expect(analyze).toHaveBeenCalledWith(AFTER_NF3, 20));
    act(() => push?.({ ready: true, evalState: ev(AFTER_NF3, { thinking: true, bestMoveUci: null }) }));
    expect(askButton()).toBeDisabled();
    act(() => push?.({ ready: true, evalState: ev(AFTER_NF3, { pvUci: Array.from({ length: 15 }, () => "d7d5") }) }));
    expect(screen.getByTestId("ask-chip")).toHaveTextContent("Analysing Nf3 as your alternative");
    expect(askButton()).not.toBeDisabled();
    fireEvent.click(askButton());
    expect(ask).toHaveBeenCalledWith("Why can't I play Nf3 here?", { move: "Nf3", engine: { depth: 16, eval_cp: -10, best_move: "d7d5", pv: Array.from({ length: 12 }, () => "d7d5") } });
    await flush();
    expect(screen.getByText("Because Nf3 lets ...d5.")).toBeInTheDocument();
  });

  it("the engine's own move and an illegal move get no chip and no engine", () => {
    render(<Host />);
    type("Why is e4 the engine's choice?");
    expect(screen.queryByTestId("ask-chip")).toBeNull();
    type("What about Nf9 or Bxe5?");
    expect(screen.queryByTestId("ask-chip")).toBeNull();
    expect(enabledSeen.every((e) => !e)).toBe(true);
    expect(askButton()).not.toBeDisabled();
  });

  it("✕ dismisses the move and turns the engine off; retyping the same text does not bring it back; the next move named does; a new board does", () => {
    const { rerender } = render(<Host />);
    type("Nf3?");
    expect(enabledSeen.at(-1)).toBe(true);
    fireEvent.click(screen.getByLabelText("Not asking about Nf3"));
    expect(screen.queryByTestId("ask-chip")).toBeNull();
    expect(enabledSeen.at(-1)).toBe(false);
    expect(askButton()).not.toBeDisabled();
    type("");
    type("Nf3?");
    expect(screen.queryByTestId("ask-chip")).toBeNull();
    type("Nf3 or Nc3?");
    expect(screen.getByTestId("ask-chip")).toHaveTextContent("Analysing Nc3…");
    fireEvent.click(screen.getByLabelText("Not asking about Nc3"));
    expect(screen.queryByTestId("ask-chip")).toBeNull(); // both stay dismissed; Nf3 does not come back
    expect(enabledSeen.at(-1)).toBe(false);
    expect(askButton()).not.toBeDisabled();
    ask.mockResolvedValueOnce({ explanation: "x", cached: false, model: "m" });
    fireEvent.click(askButton());
    expect(ask).toHaveBeenLastCalledWith("Nf3 or Nc3?", null);
    type("Nf3?");
    rerender(<Host fen={AFTER_NF3} best="d5" />);
    type("Nc6?");
    expect(screen.getByTestId("ask-chip")).toHaveTextContent("Analysing Nc6…");
    rerender(<Host fen={START} />);
    expect(screen.queryByTestId("ask-chip")).toBeNull(); // Nc6 is not a move here
    expect(enabledSeen.at(-1)).toBe(false);
  });

  it("a move that ends the game is sent with no engine and starts no search; Copy prompt carries it too", async () => {
    ask.mockResolvedValue({ explanation: "Stalemate.", cached: false, model: "m" });
    dryRun.mockResolvedValue({ model: "m", provider: "anthropic", temperature: null, thinking: null, max_tokens: 1, prefill: "", system_prompt: "", rendered_prompt: "It ends the game: stalemate." });
    render(<Host fen={STALEMATE_TRAP} best="Qh6+" />);
    type("Why can't I play Qf7?");
    expect(screen.getByTestId("ask-chip")).toHaveTextContent("Qf7 ends the game — stalemate");
    expect(enabledSeen.every((e) => !e)).toBe(true);
    expect(settingsGet).not.toHaveBeenCalled();
    expect(askButton()).not.toBeDisabled();
    fireEvent.click(askButton());
    expect(ask).toHaveBeenCalledWith("Why can't I play Qf7?", { move: "Qf7" });
    await flush();
    fireEvent.click(screen.getByText("Copy prompt"));
    await flush();
    expect(dryRun).toHaveBeenCalledWith("Why can't I play Qf7?", { move: "Qf7" });
    expect(writeText.mock.calls[0][0]).toContain("It ends the game: stalemate.");
    type("Qg7!");
    expect(screen.getByTestId("ask-chip")).toHaveTextContent("Qg7# ends the game — checkmate");
    fireEvent.click(askButton());
    expect(ask).toHaveBeenLastCalledWith("Qg7!", { move: "Qg7#" });
  });
});
