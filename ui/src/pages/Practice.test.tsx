import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Practice from "./Practice";
import { _resetProbesForTests } from "../utils/attemptQueue";
import type { Puzzle, PuzzlesResponse } from "../practice";

// The board is not under test: the mock exposes a button that drops the move the test set up.
type DropArgs = { piece: unknown; sourceSquare: string; targetSquare: string | null };
let nextDrop: { from: string; to: string } = { from: "a1", to: "a8" };
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { onPieceDrop?: (a: DropArgs) => boolean } }) => (
    <button type="button" onClick={() => options.onPieceDrop?.({ piece: null, sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
      drop
    </button>
  ),
}));

const puzzle = (id: number, batch: number, over: Partial<Puzzle> = {}): Puzzle => ({
  id,
  fen: "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
  solution_line: ["Ra8#"],
  acceptance_map: null,
  source_types: ["blunder"],
  themes: ["backRankMate"],
  color: "w",
  title: null,
  description: null,
  created_at: "2026-09-01T00:00:00Z",
  occurrence_count: 2,
  source_breakdown: { blunder: 2 },
  game_links: [],
  correct_game_links: [],
  attempt_count: 0,
  attempt_summary: { total: 0, solved: 0, last_attempt_at: null, streak: 0 },
  is_repertoire: false,
  presentation_ply: null,
  presentation_fen: null,
  repertoire_line_id: null,
  srs: null,
  play_batch_id: batch,
  ...over,
});

const serve = (puzzles: Puzzle[], batchId: number, threshold = 1): PuzzlesResponse => ({
  puzzles,
  total: puzzles.length,
  mastered_count: 0,
  advance_threshold: 1,
  batch_id: batchId,
  scope: "all",
  mint_ahead_threshold: threshold,
  served_themes: ["fork", "pin"],
});

type Handler = (method: string, body: unknown) => { status: number; body: unknown };

/** A fetch stub routed by path; returns the list of calls for assertions. */
function stubFetch(routes: Record<string, Handler>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      calls.push({ path, method, body });
      const h = routes[path];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = h(method, body);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function renderPage(entry = "/practice") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Practice />
    </MemoryRouter>,
  );
}

describe("Practice page", () => {
  beforeEach(() => {
    window.localStorage.clear();
    _resetProbesForTests();
  });
  afterEach(() => {
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
    _resetProbesForTests();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the first queue item", async () => {
    stubFetch({ "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }) });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    expect(screen.getByText("Skip →")).toBeInTheDocument();
    expect(screen.getByText("← Previous")).toBeDisabled();
  });

  it("appends a new batch keyed by play_batch_id without resetting the cursor, prefetching at remainingAhead+1 <= threshold", async () => {
    let serveCount = 0;
    const calls = stubFetch({
      "/practice/puzzles": () => {
        serveCount += 1;
        // The second serve carries the (still pending) batch 1 rows again plus a minted batch 2.
        return { status: 200, body: serveCount === 1 ? serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) : serve([puzzle(12, 1), puzzle(13, 2)], 2, 1) };
      },
      "/practice/skip": () => ({ status: 200, body: { status: "DEFERRED" } }),
    });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    // remainingAhead = 1 at index 0: 1 + 1 > threshold 1, so no prefetch yet.
    expect(calls.filter((c) => c.path === "/practice/puzzles")).toHaveLength(1);

    fireEvent.click(screen.getByText("Skip →"));
    expect(await screen.findByText("#12")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/practice/skip")?.body).toEqual({ ptype: "all", subtype: null, batch_id: 1, puzzle_id: 11 });
    // remainingAhead = 0 at index 1: 0 + 1 <= 1 fires the prefetch.
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles")).toHaveLength(2));
    await flush();
    // The cursor stayed on #12; batch 1's re-served row was not appended twice.
    expect(screen.getByText("#12")).toBeInTheDocument();
    expect(screen.queryByText("#11")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Skip →"));
    expect(await screen.findByText("#13")).toBeInTheDocument();
    fireEvent.click(screen.getByText("← Previous"));
    expect(await screen.findByText("#12")).toBeInTheDocument();
  });

  it("advances past a STATE_MISS skip and refetches", async () => {
    let serveCount = 0;
    const calls = stubFetch({
      "/practice/puzzles": () => {
        serveCount += 1;
        return { status: 200, body: serve([puzzle(11, 1), puzzle(12, 1), puzzle(13, 1)], 1, 1) };
      },
      "/practice/skip": () => ({ status: 409, body: { detail: { status: "STATE_MISS" } } }),
    });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Skip →"));
    expect(await screen.findByText("#12")).toBeInTheDocument();
    // remainingAhead = 1 at index 1: no prefetch, so the second serve is the STATE_MISS refetch.
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles")).toHaveLength(2));
    await flush();
    expect(screen.queryByText("#11")).not.toBeInTheDocument();
    expect(screen.getByText("#12")).toBeInTheDocument();
  });

  it("falls back to blocking mode without navigator.locks: navigation disabled, explicit error, Retry", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let attemptCalls = 0;
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }),
      "/practice/puzzles/11/attempt": () => {
        attemptCalls += 1;
        return attemptCalls === 1
          ? { status: 500, body: { detail: "boom" } }
          : { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };
      },
    });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    nextDrop = { from: "a1", to: "a8" };
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    // Nothing reached the durable queue and the page did not move on.
    expect(window.localStorage.getItem("blundriq_pending_attempts_v1")).toBeNull();
    expect(screen.getByText("Skip →")).toBeDisabled();
    const first = calls.find((c) => c.path === "/practice/puzzles/11/attempt")!.body!;
    expect(first).toMatchObject({ solved: true, moves_played: "Ra8#" });
    expect(typeof first.attempt_id).toBe("string");
    expect(typeof first.session_id).toBe("string");

    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    expect(screen.getByText("Next Puzzle →")).not.toBeDisabled();
    const attempts = calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    expect(attempts).toHaveLength(2);
    // The retry replays the same idempotency key.
    expect(attempts[1].body!.attempt_id).toBe(first.attempt_id);
  });

  it("in queue mode commits to localStorage before advancing and clears it once the server confirms", async () => {
    Object.defineProperty(navigator, "locks", { value: { request: (_n: string, cb: () => Promise<unknown>) => cb() }, configurable: true });
    _resetProbesForTests();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = url.replace(/^.*\/api/, "").split("?")[0];
        if (path === "/practice/puzzles") return { ok: true, status: 200, json: async () => serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) };
        if (path === "/practice/puzzles/11/attempt") {
          await gate;
          const body = JSON.parse(String(init?.body));
          return { ok: true, status: 200, json: async () => ({ detail: "attempt recorded", solved: body.solved, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } }) };
        }
        return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      }),
    );
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    // Next is offered while the POST is still in flight, because the queue already holds it.
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    const queued = JSON.parse(window.localStorage.getItem("blundriq_pending_attempts_v1")!);
    expect(queued).toHaveLength(1);
    expect(queued[0]).toMatchObject({ puzzle_id: 11, solved: true, moves_played: "Ra8#" });
    release();
    await vi.waitFor(() => expect(JSON.parse(window.localStorage.getItem("blundriq_pending_attempts_v1")!)).toHaveLength(0));
  });

  it("shows the list for srs=all and opens a row in the overlay; ?puzzle= deep-links", async () => {
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { ...serve([puzzle(21, 1, { srs: { level: "rook", correct_at_level: 0, last_3_attempts: [true], last_correct_date: null, next_show_at: "2026-10-01T00:00:00Z", updated_at: "" } })], 1), batch_id: null } }),
      "/practice/puzzles/99": () => ({ status: 200, body: { id: 99, fen: puzzle(99, 1).fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["lichess_cc0"], themes: ["fork"], is_repertoire: false, presentation_ply: null } }),
    });
    renderPage("/practice?srs=all&puzzle=99");
    // The deep link opens first.
    expect(await screen.findByRole("dialog", { name: "Puzzle 99" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Close"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("#21")).toBeInTheDocument();
    expect(screen.getByText("rook")).toBeInTheDocument();
    fireEvent.click(screen.getByText("#21"));
    expect(await screen.findByRole("dialog", { name: "Puzzle 21" })).toBeInTheDocument();
  });
});
