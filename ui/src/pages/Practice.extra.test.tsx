import { configure, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Practice from "./Practice";
import { _resetProbesForTests, QUEUE_STORE } from "../utils/attemptQueue";
import { _resetRemovalsForTests } from "../utils/puzzleRemoval";
import { _resetUnsavedAttemptForTests } from "../utils/unsavedAttempt";
import type { Puzzle, PuzzlesResponse } from "../practice";
import { _resetPracticeTodayForTests } from "../practiceToday";

// Practice past a cleared queue: Keep practising, its pages, and attempts that say whether they counted.

configure({ asyncUtilTimeout: 3000 });

type DropArgs = { piece: unknown; sourceSquare: string; targetSquare: string | null };
vi.mock("react-chessboard", async () => {
  const { useEffect, useRef } = await import("react");
  return {
    Chessboard: ({ options }: { options: { onPieceDrop?: (a: DropArgs) => boolean } }) => {
      const latest = useRef(options);
      latest.current = options;
      const mounted = useRef(false);
      useEffect(() => {
        mounted.current = true;
      }, []);
      const drop = () => {
        if (!mounted.current) return void setTimeout(drop, 0);
        latest.current.onPieceDrop?.({ piece: null, sourceSquare: "a1", targetSquare: "a8" });
      };
      return (
        <button type="button" data-testid="board" onClick={drop}>
          drop
        </button>
      );
    },
  };
});

const puzzle = (id: number, batch: number): Puzzle => ({
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
  attempt_count: 1,
  attempt_summary: { total: 1, solved: 1, last_attempt_at: null, streak: 1 },
  is_repertoire: false,
  presentation_ply: null,
  presentation_fen: null,
  repertoire_line_id: null,
  srs: { level: "knight", correct_at_level: 0, last_3_attempts: [true], last_correct_date: null, next_show_at: "2099-01-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" },
  play_batch_id: batch,
});

const page = (puzzles: Puzzle[], batchId: number | null, run: number | null = batchId): PuzzlesResponse => ({
  puzzles,
  total: puzzles.length,
  mastered_count: 0,
  advance_threshold: 1,
  batch_id: batchId,
  scope: null,
  mint_ahead_threshold: null,
  run,
  served_themes: [],
});

const answer = (counted: boolean) => ({ status: 200, body: { detail: "attempt recorded", solved: true, counted, attempt_summary: { total: 2, solved: 2, streak: 2 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } });

type Reply = { status: number; body: unknown };
type Handler = (method: string, body: unknown, query: URLSearchParams) => Reply | Promise<Reply>;

function stubFetch(routes: Record<string, Handler>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      const query = new URLSearchParams(url.split("?")[1] ?? "");
      calls.push({ path, method, body, query });
      const h = routes[path];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(method, body, query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

/** The queue is empty; extra practice answers its pages in turn, then an empty one. */
function queueThenPages(pages: PuzzlesResponse[]) {
  let n = 0;
  return stubFetch({
    "/practice/puzzles": (_m, _b, q) => {
      if (q.get("srs") !== "extra") return { status: 200, body: { ...page([], 7), scope: "blunder", run: null } };
      const body = n < pages.length ? pages[n] : page([], null, pages.length ? pages[0].run : null);
      n += 1;
      return { status: 200, body };
    },
    "/practice/puzzles/21/attempt": () => answer(false),
    "/practice/puzzles/22/attempt": () => answer(false),
    "/practice/puzzles/23/attempt": () => answer(false),
    "/practice/skip": () => ({ status: 200, body: { status: "DEFERRED" } }),
    "/practice/today": () => ({ status: 200, body: { date: "2026-11-12", solved: 3, target: 10, next_day_at: "2026-11-13T05:00:00Z", now: "2026-11-12T13:00:00Z" } }),
  });
}

const reads = (calls: ReturnType<typeof stubFetch>, srs: string) => calls.filter((c) => c.path === "/practice/puzzles" && (c.query.get("srs") ?? "due") === srs);

function renderPage(entry = "/practice?type=blunder") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Practice />
    </MemoryRouter>,
  );
}

/** a precedes b in the document. */
const before = (a: Element, b: Element) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

describe("Practice past a cleared queue", () => {
  beforeEach(() => {
    window.localStorage.clear();
    _resetProbesForTests();
    _resetUnsavedAttemptForTests();
    _resetRemovalsForTests();
    _resetPracticeTodayForTests();
  });
  afterEach(() => {
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
    _resetProbesForTests();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("the empty queue offers Keep practising, which reads the same filter's extra practice", async () => {
    const calls = queueThenPages([page([puzzle(21, 1), puzzle(22, 1)], 1)]);
    renderPage();
    expect(await screen.findByText("You're all caught up")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep practising" }));
    expect(await screen.findByText("#21")).toBeInTheDocument();
    const first = reads(calls, "extra")[0];
    expect(first.query.get("ptype")).toBe("blunder");
    expect(first.query.has("run")).toBe(false); // a new run
    // The line under the board, never above it: the board does not move.
    const line = screen.getByText("Extra practice: counts only for puzzles that come due.");
    expect(before(screen.getByTestId("board"), line)).toBe(true);
  });

  it("an extra attempt is sent as extra practice, a skip only moves on, and the run is asked for by its id until it ends", async () => {
    const calls = queueThenPages([page([puzzle(21, 1), puzzle(22, 1)], 1), page([puzzle(23, 2)], 2, 1)]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep practising" }));
    expect(await screen.findByText("#21")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles/21/attempt")).toHaveLength(1));
    expect(calls.find((c) => c.path === "/practice/puzzles/21/attempt")!.body).toMatchObject({ mode: "extra" });
    // In extra practice the not-due line is the standing line above; it is not repeated.
    expect(screen.queryByText(/Not due yet/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Next Puzzle →"));
    expect(await screen.findByText("#22")).toBeInTheDocument();
    await vi.waitFor(() => expect(reads(calls, "extra").length).toBeGreaterThanOrEqual(2));
    expect(reads(calls, "extra")[1].query.get("run")).toBe("1");
    fireEvent.click(screen.getByText("Skip →"));
    expect(await screen.findByText("#23")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/practice/skip")).toBe(false);
    fireEvent.click(screen.getByText("Skip →"));
    expect(await screen.findByText("That's every puzzle in this filter.")).toBeInTheDocument();
    expect(reads(calls, "extra").every((c, i) => i === 0 || c.query.get("run") === "1")).toBe(true);
    // Back to the queue reads the queue again.
    const dueReads = reads(calls, "due").length;
    fireEvent.click(screen.getByRole("button", { name: "Back to the queue" }));
    expect(await screen.findByText("You're all caught up")).toBeInTheDocument();
    await vi.waitFor(() => expect(reads(calls, "due").length).toBe(dueReads + 1));
  });

  it("nothing not yet due in the filter says so, with the way back", async () => {
    queueThenPages([]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep practising" }));
    expect(await screen.findByText("Nothing else in this filter yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to the queue" })).toBeInTheDocument();
  });

  it("a queue attempt the server did not count says so under the board; one it counted says nothing", async () => {
    let counted = false;
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { ...page([puzzle(31, 1), puzzle(32, 1)], 1), scope: "all", run: null, mint_ahead_threshold: 1 } }),
      "/practice/puzzles/31/attempt": () => answer(counted),
      "/practice/puzzles/32/attempt": () => answer(counted),
    });
    renderPage("/practice");
    expect(await screen.findByText("#31")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    const line = await screen.findByText("Not due yet: this one doesn't count toward today or your schedule.");
    expect(before(screen.getByTestId("board"), line)).toBe(true);
    counted = true;
    fireEvent.click(screen.getByText("Next Puzzle →"));
    expect(await screen.findByText("#32")).toBeInTheDocument();
    expect(screen.queryByText(/Not due yet/)).not.toBeInTheDocument(); // a new load starts clean
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/Not due yet/)).not.toBeInTheDocument();
  });

  it("a queued attempt keeps its mode through a replay; one from before the field sends none", async () => {
    Object.defineProperty(navigator, "locks", { value: { request: (_n: string, cb: () => Promise<unknown>) => cb() }, configurable: true });
    _resetProbesForTests();
    const extra = { attempt_id: "q-1", puzzle_id: 21, solved: true, moves_played: "Ra8#", enqueued_at: Date.now(), last_attempted_at: null, attempt_count: 0, session_id: "s-1", presentation_ply: null, mode: "extra" };
    const older = { ...extra, attempt_id: "q-0", puzzle_id: 22, mode: undefined };
    window.localStorage.setItem(QUEUE_STORE, JSON.stringify([extra, older]));
    const calls = queueThenPages([]);
    renderPage();
    await vi.waitFor(() => expect(calls.filter((c) => c.path.endsWith("/attempt"))).toHaveLength(2));
    expect(calls.find((c) => c.path === "/practice/puzzles/21/attempt")!.body).toMatchObject({ attempt_id: "q-1", mode: "extra" });
    expect(calls.find((c) => c.path === "/practice/puzzles/22/attempt")!.body).not.toHaveProperty("mode");
  });

  it("another filter is the queue again", async () => {
    const calls = queueThenPages([page([puzzle(21, 1)], 1)]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep practising" }));
    expect(await screen.findByText("#21")).toBeInTheDocument();
    const dueReads = reads(calls, "due").length;
    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    await vi.waitFor(() => expect(reads(calls, "due").length).toBe(dueReads + 1));
    expect(reads(calls, "due").at(-1)!.query.has("ptype")).toBe(false);
    expect(screen.queryByText("Extra practice: counts only for puzzles that come due.")).not.toBeInTheDocument();
  });
});
