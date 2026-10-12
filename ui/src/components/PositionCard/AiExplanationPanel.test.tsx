/**
 * The blunder Explain panel's history: what was asked at the blunder's board, by any panel,
 * shown whether or not an Explain prompt is usable, read-only, and read again after an Explain.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiExplanationPanel } from "./AiExplanationPanel";

const FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3";
const PROMPTS = { prompts: [{ key: "a", label: "Explain", model: "claude-x" }] };
const row = (question: string | null, label: string) => ({ question, prompt_label: label, model: "m", explanation: "An answer.", created_at: "2026-10-01T12:00:00+00:00" });

function stubFetch(prompts: unknown, history: () => unknown[]) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [path, query] = url.replace(/^.*\/api/, "").split("?");
      calls.push(path + (query ? `?${query}` : ""));
      const body = path === "/blunders/prompts" ? prompts : path === "/ai/history" ? { items: history() } : path === "/blunders/explain" && init?.method === "POST" ? { explanation: "Paid.", cached: false, model: "claude-x", prompt_label: "Explain" } : null;
      return body ? { ok: true, status: 200, statusText: "OK", json: async () => body } : { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AiExplanationPanel", () => {
  it("shows the board's history with no usable Explain prompt, read-only", async () => {
    const calls = stubFetch({ prompts: [] }, () => [row("asked in a review", "Ask about this position"), row(null, "Explain")]);
    render(<AiExplanationPanel chessGameId={7} ply={6} fen={FEN} />);
    expect(await screen.findByText("Asked here before (2)")).toBeInTheDocument();
    expect(screen.getByText("asked in a review")).toBeInTheDocument();
    expect(screen.getByText("Explain")).toBeInTheDocument(); // the label in place of no question
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Explain" })).toBeNull(); // no prompt, no button
    expect(calls).toContain(`/ai/history?fen=${encodeURIComponent(FEN)}`);
  });

  it("reads the history again after an Explain lands, and Ask again is never offered here", async () => {
    let rows: unknown[] = [];
    const calls = stubFetch(PROMPTS, () => rows);
    render(<AiExplanationPanel chessGameId={7} ply={6} fen={FEN} />);
    const explain = await screen.findByRole("button", { name: "Explain" });
    expect(screen.queryByText(/Asked here before/)).toBeNull();
    rows = [row(null, "Explain")];
    fireEvent.click(explain);
    await act(async () => {});
    expect(await screen.findByText("Paid.")).toBeInTheDocument();
    expect(await screen.findByText("Asked here before (1)")).toBeInTheDocument();
    expect(calls.filter((c) => c.startsWith("/ai/history"))).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Ask again" })).toBeNull();
  });
});
