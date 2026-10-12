/**
 * The "Asked here before" list against a fake history read: what it shows, what Ask again does,
 * and which response wins when boards change or an answer lands while a read is still out.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistoryItem } from "../ask";
import { AskHistory } from "./AskHistory";

type Reply = { items: HistoryItem[] };
const getAskHistory = vi.fn();
vi.mock("../ask", () => ({ getAskHistory: (...a: unknown[]) => getAskHistory(...a) }));

const A = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const B = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
const item = (over: Partial<HistoryItem> = {}): HistoryItem => ({ question: "Why not Nf3?", prompt_label: "Ask about this position", model: "claude-opus-5-5", explanation: "**Because** it hangs.", created_at: "2026-10-01T12:00:00+00:00", ...over });

/** Every read waits until the test settles it, in any order; `fens` records what was asked for. */
function pendingReads() {
  const waiting: ((r: Reply) => void)[] = [];
  const fens: string[] = [];
  getAskHistory.mockImplementation((fen: string) => {
    fens.push(fen);
    return new Promise<Reply>((resolve) => waiting.push(resolve));
  });
  const settle = async (i: number, r: Reply) => {
    await act(async () => {
      waiting[i](r);
    });
  };
  return { fens, settle };
}

const summary = () => screen.queryByText(/Asked here before/);

beforeEach(() => {
  getAskHistory.mockReset();
});
afterEach(cleanup);

describe("AskHistory", () => {
  it("renders nothing while there is nothing, and nothing until the first read of a board answers", async () => {
    const r = pendingReads();
    const { container } = render(<AskHistory fen={A} answerRevision={0} />);
    expect(container).toBeEmptyDOMElement();
    expect(r.fens).toEqual([A]);
    await r.settle(0, { items: [] });
    expect(container).toBeEmptyDOMElement();
  });

  it("a failed read shows nothing", async () => {
    getAskHistory.mockRejectedValue(new Error("no"));
    const { container } = render(<AskHistory fen={A} answerRevision={0} />);
    await act(async () => {});
    expect(container).toBeEmptyDOMElement();
  });

  it("counts the rows, shows the question or the label, and expands to the answer", async () => {
    getAskHistory.mockResolvedValue({ items: [item(), item({ question: null, prompt_label: "Explain", explanation: "Nf6 hangs.", model: "m2" })] });
    render(<AskHistory fen={A} answerRevision={0} />);
    expect(await screen.findByText("Asked here before (2)")).toBeInTheDocument();
    expect(screen.getByText("Why not Nf3?")).toBeInTheDocument();
    expect(screen.getByText(/Ask about this position ·/)).toBeInTheDocument(); // the label beside a question
    expect(screen.getByText("Explain")).toBeInTheDocument(); // the label in place of a missing question
    expect(screen.getByText("Because")).toBeInTheDocument(); // the answer, through MinimalMarkdown
    expect(screen.getByText("it hangs.")).toBeInTheDocument();
    expect(screen.getByText("Nf6 hangs.")).toBeInTheDocument();
    expect(screen.getByText("m2")).toBeInTheDocument();
  });

  it("Ask again hands the question to the host, only when there is a host box and a question", async () => {
    getAskHistory.mockResolvedValue({ items: [item(), item({ question: null, prompt_label: "Explain" })] });
    const onReuse = vi.fn();
    const { rerender } = render(<AskHistory fen={A} answerRevision={0} onReuse={onReuse} />);
    await screen.findByText("Asked here before (2)");
    const buttons = screen.getAllByRole("button", { name: "Ask again" });
    expect(buttons).toHaveLength(1); // the row with no question has none
    fireEvent.click(buttons[0]);
    expect(onReuse).toHaveBeenCalledWith("Why not Nf3?");
    rerender(<AskHistory fen={A} answerRevision={0} />);
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });

  it("a board change clears the rows at once, and a late reply for the old board is dropped", async () => {
    const r = pendingReads();
    const { rerender } = render(<AskHistory fen={A} answerRevision={0} />);
    await r.settle(0, { items: [item({ question: "about A" })] });
    expect(screen.getByText("about A")).toBeInTheDocument();
    rerender(<AskHistory fen={B} answerRevision={0} />);
    expect(summary()).toBeNull(); // nothing of A while B's read is out
    expect(r.fens).toEqual([A, B]);
    await r.settle(1, { items: [item({ question: "about B" })] });
    expect(screen.getByText("about B")).toBeInTheDocument();
    // A reply for A arriving now belongs to no board on show.
    rerender(<AskHistory fen={A} answerRevision={0} />);
    expect(summary()).toBeNull();
    await r.settle(2, { items: [item({ question: "A again, fresh" })] });
    expect(screen.getByText("A again, fresh")).toBeInTheDocument();
  });

  it("A → B → A is three reads and the last one wins, whatever order they answer in", async () => {
    const r = pendingReads();
    const { rerender } = render(<AskHistory fen={A} answerRevision={0} />);
    rerender(<AskHistory fen={B} answerRevision={0} />);
    rerender(<AskHistory fen={A} answerRevision={0} />);
    expect(r.fens).toEqual([A, B, A]);
    await r.settle(2, { items: [item({ question: "A, third read" })] });
    expect(screen.getByText("A, third read")).toBeInTheDocument();
    await r.settle(0, { items: [item({ question: "A, first read" })] }); // stale: same board, older generation
    await r.settle(1, { items: [item({ question: "B" })] });
    expect(screen.getByText("A, third read")).toBeInTheDocument();
    expect(screen.queryByText("A, first read")).toBeNull();
    expect(screen.queryByText("B")).toBeNull();
  });

  it("an answer revision re-reads the same board; a read still out from before it is dropped", async () => {
    const r = pendingReads();
    const { rerender } = render(<AskHistory fen={A} answerRevision={0} />);
    rerender(<AskHistory fen={A} answerRevision={1} />);
    expect(r.fens).toEqual([A, A]);
    await r.settle(1, { items: [item({ question: "refreshed" })] });
    expect(screen.getByText("refreshed")).toBeInTheDocument();
    await r.settle(0, { items: [item({ question: "before the answer" })] });
    expect(screen.getByText("refreshed")).toBeInTheDocument();
    expect(screen.queryByText("before the answer")).toBeNull();
    // The list on show stays while the next read is out: no flash between answers.
    rerender(<AskHistory fen={A} answerRevision={2} />);
    expect(screen.getByText("refreshed")).toBeInTheDocument();
    await r.settle(2, { items: [item({ question: "second answer" }), item({ question: "refreshed" })] });
    expect(screen.getByText("Asked here before (2)")).toBeInTheDocument();
    expect(r.fens).toEqual([A, A, A]);
  });

  it("asks the server for exactly the board, URL-encoded", async () => {
    getAskHistory.mockResolvedValue({ items: [] });
    render(<AskHistory fen={A} answerRevision={0} />);
    await waitFor(() => expect(getAskHistory).toHaveBeenCalledWith(A));
  });
});
