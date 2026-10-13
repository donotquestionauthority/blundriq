import { configure, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Practice from "./Practice";
import { _resetProbesForTests } from "../utils/attemptQueue";
import { _resetRemovalsForTests } from "../utils/puzzleRemoval";
import { _resetUnsavedAttemptForTests } from "../utils/unsavedAttempt";
import type { DrillResponse, Puzzle } from "../practice";
import { _resetPracticeTodayForTests } from "../practiceToday";

configure({ asyncUtilTimeout: 3000 });

type DropArgs = { piece: unknown; sourceSquare: string; targetSquare: string | null };
let nextDrop: { from: string; to: string } = { from: "a1", to: "a8" };
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
      const drop = (move: { from: string; to: string }) => {
        if (!mounted.current) return void setTimeout(() => drop(move), 0);
        latest.current.onPieceDrop?.({ piece: null, sourceSquare: move.from, targetSquare: move.to });
      };
      return (
        <button type="button" onClick={() => drop(nextDrop)}>
          drop
        </button>
      );
    },
  };
});

/** A whole repertoire line of three plies from a rook ending: Ra7, Kf8, Rb7 (presented to ply 2). */
const line = (id: number, lineId: number): Puzzle => ({
  id,
  fen: "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
  solution_line: ["Ra7", "Kf8", "Rb7"],
  acceptance_map: null,
  source_types: ["deviation"],
  themes: [],
  color: "w",
  title: `Book › Ch › L${lineId}`,
  description: null,
  created_at: "2026-09-01T00:00:00Z",
  occurrence_count: 0,
  source_breakdown: {},
  game_links: [],
  correct_game_links: [],
  attempt_count: 0,
  attempt_summary: { total: 0, solved: 0, last_attempt_at: null, streak: 0 },
  is_repertoire: true,
  presentation_ply: 2,
  presentation_fen: null,
  repertoire_line_id: lineId,
  srs: null,
  play_batch_id: 1,
});
const drill = (puzzles: Puzzle[], skipped: number[] = []): DrillResponse => ({ batch_id: 1, puzzles, skipped, advance_threshold: 1, max_lines: 50 });
const OK = { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };

type Reply = { status: number; body: unknown };
type Handler = (method: string, body: unknown) => Reply | Promise<Reply>;
function stubFetch(routes: Record<string, Handler>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const method = init?.method ?? "GET";
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      calls.push({ path, method, body, query: new URLSearchParams(url.split("?")[1] ?? "") });
      const h = routes[path];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(method, body);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

function Location() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}
function renderPage(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/practice" element={<Practice />} />
        <Route path="/repertoire/report" element={<p>report page</p>} />
      </Routes>
      <Location />
    </MemoryRouter>,
  );
}

const solveLine = async () => {
  nextDrop = { from: "a1", to: "a7" };
  fireEvent.click(screen.getByText("drop"));
  expect(await screen.findByText("Your turn", { exact: true })).toBeInTheDocument();
  nextDrop = { from: "a7", to: "b7" };
  fireEvent.click(screen.getByText("drop"));
};
const failLine = () => {
  nextDrop = { from: "a1", to: "a8" }; // Ra8 is not the line's move
  fireEvent.click(screen.getByText("drop"));
};

describe("Practice: a drill", () => {
  beforeEach(() => {
    window.localStorage.clear();
    _resetProbesForTests();
    _resetUnsavedAttemptForTests();
    _resetRemovalsForTests();
    _resetPracticeTodayForTests();
  });
  afterEach(() => {
    _resetProbesForTests();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("serves the lines in order, each a load of its own with numbered attempts, counts the first try, and ends on Done", async () => {
    const calls = stubFetch({
      "/practice/drill": () => ({ status: 200, body: drill([line(11, 901), line(12, 902), line(13, 903)], [777]) }),
      "/practice/puzzles/11/attempt": () => OK,
      "/practice/puzzles/12/attempt": (_m, body) => ({ ...OK, body: { ...OK.body, solved: (body as { solved: boolean }).solved } }),
      "/practice/puzzles/13/attempt": () => OK,
    });
    renderPage("/practice?drill=901,902,903,777");
    const header = await screen.findByTestId("drill-header");
    expect(header.textContent).toContain("Drill · 1 of 3 · 0 first-try");
    expect(header.textContent).toContain("1 skipped");
    expect(calls.find((c) => c.path === "/practice/drill")?.query.get("lines")).toBe("901,902,903,777");
    expect(calls.some((c) => c.path === "/practice/puzzles")).toBe(false); // not the queue
    expect(screen.queryByRole("tablist", { name: "Practice type" })).toBeNull();
    expect(screen.queryByText("Skip →")).toBeNull();
    expect(screen.getByText("#11")).toBeInTheDocument();
    expect(screen.getByTestId("location").textContent).toBe("/practice?drill=901,902,903,777"); // kept: a reload serves it again
    expect(screen.queryByText("← Previous")).toBeNull(); // no stepper row before a verdict: no Skip, no Previous
    // Line 1: wrong, Try Again, right. One load, two attempts numbered 1 and 2, first wrong.
    failLine();
    expect(await screen.findByText("Try Again")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Try Again"));
    await solveLine();
    expect(await screen.findByText("Next line →")).toBeInTheDocument();
    expect(screen.getByText("← Previous")).toBeDisabled(); // a drill only goes forward
    const attempts11 = () => calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    await vi.waitFor(() => expect(attempts11()).toHaveLength(2));
    expect(attempts11().map((c) => [c.body!.attempt_seq, c.body!.solved, c.body!.presentation_ply])).toEqual([
      [1, false, 2],
      [2, true, 2],
    ]);
    expect(attempts11()[0].body!.session_id).toBe(attempts11()[1].body!.session_id);
    expect(screen.getByTestId("drill-header").textContent).toContain("1 of 3 · 0 first-try");
    fireEvent.click(screen.getByText("Next line →"));
    // Line 2: right first time.
    expect(await screen.findByText("#12")).toBeInTheDocument();
    await solveLine();
    expect(await screen.findByText("Next line →")).toBeInTheDocument();
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles/12/attempt")).toHaveLength(1));
    expect(screen.getByTestId("drill-header").textContent).toContain("2 of 3 · 1 first-try");
    fireEvent.click(screen.getByText("Next line →"));
    // Line 3: a new load, the sequence from 1.
    expect(await screen.findByText("#13")).toBeInTheDocument();
    await solveLine();
    expect(await screen.findByText("Finish →")).toBeInTheDocument();
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles/13/attempt")).toHaveLength(1));
    expect(calls.find((c) => c.path === "/practice/puzzles/13/attempt")!.body!.attempt_seq).toBe(1);
    fireEvent.click(screen.getByText("Finish →"));
    const done = await screen.findByTestId("drill-done");
    expect(done.textContent).toContain("3 lines drilled · first try right 2 · wrong 1");
    expect(screen.getByRole("link", { name: "Back to the report" })).toHaveAttribute("href", "/repertoire/report");
    // Drill again: the same list, new loads, the tally from zero.
    fireEvent.click(screen.getByText("Drill again"));
    expect(await screen.findByText("#11")).toBeInTheDocument();
    expect(screen.getByTestId("drill-header").textContent).toContain("1 of 3 · 0 first-try");
    await solveLine();
    await vi.waitFor(() => expect(attempts11()).toHaveLength(3));
    expect(attempts11()[2].body!.attempt_seq).toBe(1);
    expect(new Set(attempts11().map((c) => c.body!.session_id)).size).toBe(2); // round 1's load, round 2's load
  });

  it("Stop waits for a posting verdict, then returns to the report; an empty drill says so", async () => {
    let release: (() => void) | null = null;
    const slow = new Promise<void>((r) => {
      release = r;
    });
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true }); // blocking mode: navigation waits for the save
    stubFetch({
      "/practice/drill": () => ({ status: 200, body: drill([line(11, 901)]) }),
      "/practice/puzzles/11/attempt": async () => {
        await slow;
        return OK;
      },
    });
    renderPage("/practice?drill=901");
    await screen.findByTestId("drill-header");
    await solveLine();
    const stop = await screen.findByRole("link", { name: "Stop" });
    expect(stop).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(stop);
    expect(screen.getByTestId("location").textContent).toBe("/practice?drill=901");
    release!();
    await vi.waitFor(() => expect(screen.getByRole("link", { name: "Stop" })).toHaveAttribute("aria-disabled", "false"));
    fireEvent.click(screen.getByRole("link", { name: "Stop" }));
    expect(await screen.findByText("report page")).toBeInTheDocument();
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
  });

  it("an empty drill and a malformed parameter", async () => {
    stubFetch({
      "/practice/drill": () => ({ status: 200, body: drill([], [5]) }),
      "/practice/puzzles": () => ({ status: 200, body: { puzzles: [], total: 0, mastered_count: 0, advance_threshold: 1, batch_id: null, scope: "all", served_themes: [] } }),
    });
    renderPage("/practice?drill=5");
    const empty = await screen.findByTestId("drill-empty");
    expect(within(empty).getByText("None of the chosen lines is active.")).toBeInTheDocument();
    expect(within(empty).getByRole("link", { name: "Back to the report" })).toHaveAttribute("href", "/repertoire/report");
  });

  it("a drill parameter that is not a list of ids is the ordinary queue", async () => {
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { puzzles: [], total: 0, mastered_count: 0, advance_threshold: 1, batch_id: null, scope: "all", served_themes: [] } }),
    });
    renderPage("/practice?drill=abc");
    expect(await screen.findByText("You're all caught up")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/practice/drill")).toBe(false);
    expect(screen.getByRole("tablist", { name: "Practice type" })).toBeInTheDocument();
  });
});
