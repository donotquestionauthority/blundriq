import { configure, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Practice from "./Practice";
import { _resetProbesForTests } from "../utils/attemptQueue";
import { _resetRemovalsForTests } from "../utils/puzzleRemoval";
import { _resetUnsavedAttemptForTests } from "../utils/unsavedAttempt";
import { readQueue, resetQueueForTests } from "../utils/drillQueue";
import { _resetPracticeTodayForTests } from "../practiceToday";
import type { DrillResponse, Puzzle, PuzzlesResponse, RepertoireScopesResponse } from "../practice";

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

const srsAt = (next_show_at: string, level: Puzzle["srs"] extends infer S ? (S extends { level: infer L } ? L : never) : never = "pawn") => ({ level, correct_at_level: 0, last_3_attempts: [], last_correct_date: null, next_show_at, updated_at: "" });

const puzzle = (id: number, over: Partial<Puzzle> = {}): Puzzle => ({
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
  play_batch_id: null,
  ...over,
});
/** A repertoire puzzle of three plies (Ra7, Kf8, Rb7), presented to ply 2, as the list and a drill serve it. */
const line = (id: number, lineId: number, over: Partial<Puzzle> = {}): Puzzle =>
  puzzle(id, { solution_line: ["Ra7", "Kf8", "Rb7"], source_types: ["deviation"], themes: [], title: `Book › Ch › L${lineId}`, occurrence_count: 0, source_breakdown: {}, is_repertoire: true, presentation_ply: 2, repertoire_line_id: lineId, ...over });

const list = (puzzles: Puzzle[]): PuzzlesResponse => ({ puzzles, total: puzzles.length, mastered_count: 0, advance_threshold: 1, batch_id: null, scope: null, mint_ahead_threshold: null, served_themes: [] });
const scopes = (drill_max_lines = 50): RepertoireScopesResponse => ({ books: [{ id: 1, title: "Book", color: "white", count: 3, chapters: [{ id: 7, title: "Ch", count: 3, lines: [] }] }], drill_max_lines });
const drillBody = (puzzles: Puzzle[]): DrillResponse => ({ batch_id: 1, puzzles, skipped: [], advance_threshold: 1, max_lines: 50 });
const OK = { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };

type Reply = { status: number; body: unknown };
type Handler = (method: string, query: URLSearchParams) => Reply | Promise<Reply>;
function stubFetch(routes: Record<string, Handler>) {
  const calls: Array<{ path: string; method: string; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const method = init?.method ?? "GET";
      const query = new URLSearchParams(url.split("?")[1] ?? "");
      calls.push({ path, method, query });
      const h = routes[path];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(method, query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

function Location() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}
/** The browser's Back and Forward buttons. */
function History() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>
        browser back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        browser forward
      </button>
    </>
  );
}
function renderPage(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/practice" element={<Practice />} />
        <Route path="/repertoire/report" element={<p>report page</p>} />
      </Routes>
      <Location />
      <History />
    </MemoryRouter>,
  );
}

/** The ids of the rows in table order. */
const rowIds = () =>
  within(screen.getByRole("table"))
    .getAllByRole("row")
    .slice(1)
    .map((r) => Number(/#(\d+)/.exec(r.textContent ?? "")?.[1]));
const rowOf = (id: number) => within(screen.getByRole("table")).getByText(`#${id}`).closest("tr")!;
const header = (name: string) => screen.getByRole("columnheader", { name: new RegExp(`^${name}`) });

/** Solve the open overlay's puzzle (Ra8# for a standard puzzle) and wait for its save. */
async function solveInOverlay(id: number) {
  nextDrop = { from: "a1", to: "a8" };
  fireEvent.click(within(await screen.findByRole("dialog", { name: `Puzzle ${id}` })).getByText("drop"));
  await screen.findByText(/Checkmate!|Puzzle complete!/);
}
const solveLine = async () => {
  nextDrop = { from: "a1", to: "a7" };
  fireEvent.click(screen.getByText("drop"));
  expect(await screen.findByText("Your turn", { exact: true })).toBeInTheDocument();
  nextDrop = { from: "a7", to: "b7" };
  fireEvent.click(screen.getByText("drop"));
};

describe("Practice: the All list is one visit", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetQueueForTests([901, 902]);
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

  it("keeps the mount's order through an attempt's refetch, updating the row in place; appends and drops; a new query starts over", async () => {
    let reads = 0;
    const calls = stubFetch({
      "/practice/puzzles": (_m, q) => {
        reads += 1;
        if (q.get("ptype") === "motif") return { status: 200, body: list([puzzle(13, { source_types: ["lichess_cc0"], themes: ["fork"] }), puzzle(11, { source_types: ["lichess_cc0"], themes: ["fork"] })]) };
        // The first read: A, B, C. After the attempt the server re-sorts: C, B, A (A now scheduled)
        // and D has become visible; B is gone.
        if (reads === 1) return { status: 200, body: list([puzzle(11), puzzle(12), puzzle(13)]) };
        return { status: 200, body: list([puzzle(13), puzzle(14), puzzle(11, { attempt_summary: { total: 1, solved: 1, last_attempt_at: null, streak: 1 }, srs: srsAt("2027-01-01T00:00:00Z", "knight") })]) };
      },
      "/practice/puzzles/11/attempt": () => OK,
    });
    renderPage("/practice?srs=all");
    expect(await screen.findByText("#11")).toBeInTheDocument();
    expect(rowIds()).toEqual([11, 12, 13]);
    expect(screen.getByTestId("list-caption").textContent).toBe("Default order: due first, then your blunders before deviations, lower level first, most seen first. Frozen for this visit — a row you solve stays where it is.");
    expect(rowOf(11).dataset.attempted).toBeUndefined();
    fireEvent.click(screen.getByText("#11"));
    await solveInOverlay(11);
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles")).toHaveLength(2));
    fireEvent.click(screen.getByLabelText("Close"));
    // Still first, with its new data and the tint; B dropped, D appended.
    await vi.waitFor(() => expect(rowIds()).toEqual([11, 13, 14]));
    expect(within(rowOf(11)).getByText("1/1")).toBeInTheDocument();
    expect(within(rowOf(11)).getByText("+1")).toBeInTheDocument();
    expect(rowOf(11).dataset.attempted).toBe("true");
    expect(rowOf(13).dataset.attempted).toBeUndefined();
    // A new query: the new response's order, no tint.
    fireEvent.click(screen.getByRole("tab", { name: "Motif" }));
    await vi.waitFor(() => expect(rowIds()).toEqual([13, 11]));
    expect(rowOf(11).dataset.attempted).toBeUndefined();
    expect(screen.getByTestId("location").textContent).toBe("/practice"); // the entry params were stripped
  });

  it("sorts by a header: first the actionable direction, then reversed, then Default; nulls last; the tint survives; a refetch moves nothing", async () => {
    let reads = 0;
    const rows = [
      puzzle(11, { srs: srsAt("2026-12-01T00:00:00Z", "rook"), occurrence_count: 5, attempt_summary: { total: 3, solved: 2, last_attempt_at: null, streak: -1 } }),
      puzzle(12), // never attempted: no due date, no level
      puzzle(13, { srs: srsAt("2026-11-01T00:00:00Z", "pawn"), occurrence_count: 9, attempt_summary: { total: 1, solved: 1, last_attempt_at: null, streak: 1 } }),
    ];
    const calls = stubFetch({
      "/practice/puzzles": () => {
        reads += 1;
        return { status: 200, body: list(reads === 1 ? rows : [rows[2], rows[0], { ...rows[1], attempt_summary: { total: 1, solved: 1, last_attempt_at: null, streak: 1 }, srs: srsAt("2027-01-01T00:00:00Z", "knight") }]) };
      },
      "/practice/puzzles/12/attempt": () => OK,
    });
    renderPage("/practice?srs=all");
    expect(await screen.findByText("#11")).toBeInTheDocument();
    expect(rowIds()).toEqual([11, 12, 13]);
    expect(header("Next due")).toHaveAttribute("aria-sort", "none");
    expect(screen.getByRole("columnheader", { name: "Source" })).not.toHaveAttribute("aria-sort");

    fireEvent.click(within(header("Next due")).getByRole("button"));
    expect(rowIds()).toEqual([13, 11, 12]); // soonest first, the undated row last
    expect(header("Next due")).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByTestId("list-caption").textContent).toBe("Sorted by Next due, ascending.");
    fireEvent.click(within(header("Next due")).getByRole("button"));
    expect(rowIds()).toEqual([11, 13, 12]); // latest first, the undated row still last
    expect(header("Next due")).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(header("Next due")).getByRole("button"));
    expect(rowIds()).toEqual([11, 12, 13]); // the mount's order
    expect(header("Next due")).toHaveAttribute("aria-sort", "none");
    expect(screen.getByTestId("list-caption").textContent).toMatch(/^Default order: due first/);

    // The other columns, first click each (a new column starts in its own first direction).
    fireEvent.click(within(header("Seen")).getByRole("button"));
    expect(rowIds()).toEqual([13, 11, 12]); // most seen first
    fireEvent.click(within(header("Level")).getByRole("button"));
    expect(rowIds()).toEqual([12, 13, 11]); // no level below pawn, then pawn, rook
    fireEvent.click(within(header("Attempts")).getByRole("button"));
    expect(rowIds()).toEqual([12, 13, 11]); // fewest attempts first (0, 1, 3)
    fireEvent.click(within(header("Streak")).getByRole("button"));
    expect(rowIds()).toEqual([11, 12, 13]); // lowest streak first (-1, 0, 1)
    fireEvent.click(within(header("Puzzle")).getByRole("button"));
    fireEvent.click(within(header("Puzzle")).getByRole("button"));
    expect(rowIds()).toEqual([13, 12, 11]); // id descending

    // An attempt under a sort: the refetch updates the row where it is, tinted; the order holds.
    fireEvent.click(screen.getByText("#12"));
    await solveInOverlay(12);
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles")).toHaveLength(2));
    fireEvent.click(screen.getByLabelText("Close"));
    await vi.waitFor(() => expect(within(rowOf(12)).getByText("1/1")).toBeInTheDocument());
    expect(rowIds()).toEqual([13, 12, 11]);
    expect(rowOf(12).dataset.attempted).toBe("true");
    fireEvent.click(within(header("Seen")).getByRole("button")); // a sort after the attempt keeps the tint
    expect(rowIds()).toEqual([13, 11, 12]);
    expect(rowOf(12).dataset.attempted).toBe("true");
  });

  it("tints the row at the verdict: a save that fails and lands later from the queue still marks this visit's attempt", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let attempts = 0;
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list([puzzle(11), puzzle(12)]) }),
      "/practice/puzzles/11/attempt": () => ((attempts += 1), attempts === 1 ? { status: 500, body: { detail: "boom" } } : OK),
    });
    renderPage("/practice?srs=all");
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("#11"));
    await solveInOverlay(11);
    await vi.waitFor(() => expect(attempts).toBe(1));
    expect(rowOf(11).dataset.attempted).toBe("true"); // the verdict is in, whatever the save did
    fireEvent.click(screen.getByLabelText("Close"));
    expect(rowIds()).toEqual([11, 12]);
    expect(rowOf(11).dataset.attempted).toBe("true");
  });

  it("the queue after a failed re-read: the rows stay, and 'Try again' asks once more", async () => {
    let reads = 0;
    stubFetch({
      "/practice/puzzles": () => {
        reads += 1;
        if (reads === 2) return { status: 500, body: { detail: "down" } };
        return { status: 200, body: { ...list([puzzle(11, { play_batch_id: reads })]), batch_id: reads, mastered_count: 4, mint_ahead_threshold: 1 } };
      },
      "/practice/puzzles/11/attempt": () => OK,
    });
    renderPage("/practice");
    expect(await screen.findByText("#11")).toBeInTheDocument();
    expect(screen.getByText("4 mastered")).toBeInTheDocument();
    nextDrop = { from: "a1", to: "a8" };
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    // The save's re-read failed: the solved puzzle stays showing, and where "Loading more
    // puzzles…" would be there is the way to ask again.
    expect(await screen.findByText(/More puzzles could not be loaded/)).toBeInTheDocument();
    expect(screen.queryByText(/Loading more puzzles/)).not.toBeInTheDocument();
    expect(screen.getByText(/Could not refresh/)).toBeInTheDocument();
    expect(screen.getByText("4 mastered")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await vi.waitFor(() => expect(reads).toBe(3));
    await vi.waitFor(() => expect(screen.queryByText(/More puzzles could not be loaded/)).not.toBeInTheDocument());
    expect(screen.queryByText(/Could not refresh/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Next Puzzle →"));
    expect(await screen.findByText("Your turn — find the best move")).toBeInTheDocument(); // the next batch
  });

  it("Mastered has its own Default caption", async () => {
    stubFetch({ "/practice/puzzles": () => ({ status: 200, body: list([puzzle(21, { srs: srsAt("2026-10-01T00:00:00Z", "king") })]) }) });
    renderPage("/practice?srs=retired");
    expect(await screen.findByText("#21")).toBeInTheDocument();
    expect(screen.getByTestId("list-caption").textContent).toBe("Default order: most recently updated first.");
    expect(screen.getByText("1 mastered puzzles")).toBeInTheDocument();
  });

  it("a failed refetch keeps the visit and says so; a failed read for a new query shows the error alone", async () => {
    let reads = 0;
    stubFetch({
      "/practice/puzzles": (_m, q) => {
        reads += 1;
        if (q.get("ptype") === "motif") return { status: 500, body: { detail: "down" } };
        return reads === 2 ? { status: 500, body: { detail: "down" } } : { status: 200, body: list([puzzle(11), puzzle(12)]) };
      },
      "/practice/puzzles/11/attempt": () => OK,
    });
    renderPage("/practice?srs=all");
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(within(header("Puzzle")).getByRole("button"));
    fireEvent.click(within(header("Puzzle")).getByRole("button"));
    expect(rowIds()).toEqual([12, 11]);
    fireEvent.click(screen.getByText("#11"));
    await solveInOverlay(11);
    expect(await screen.findByText(/Could not refresh/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Close"));
    expect(rowIds()).toEqual([12, 11]); // the sort and the rows survived the failed read
    expect(rowOf(11).dataset.attempted).toBe("true");
    // A new query that fails: no rows, the error.
    fireEvent.click(screen.getByRole("tab", { name: "Motif" }));
    await vi.waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
    expect(await screen.findByText("down")).toBeInTheDocument();
    expect(screen.queryByText(/Could not refresh/)).not.toBeInTheDocument();
    expect(screen.queryByText("…")).not.toBeInTheDocument();
    expect(screen.queryByText(/mastered$/)).not.toBeInTheDocument();
  });
});

describe("Practice: Drill these", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetQueueForTests([901, 902]);
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

  const threeRows = [line(31, 901), line(32, 902), line(33, 901)]; // two rows of line 901: one line

  it("counts the listed rows' distinct lines in the list's order and links the drill with the list's query; the cap and an empty list disable it", async () => {
    let cap = 50;
    let rows = threeRows;
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list(rows) }),
      "/practice/repertoire-scopes": () => ({ status: 200, body: scopes(cap) }),
    });
    const view = renderPage("/practice?type=repertoire&subtype=chapter:7&srs=all&last_n_games=100");
    const button = await screen.findByRole("link", { name: "Drill these (2 lines)" });
    expect(button).toHaveAttribute("href", "/practice?drill=901,902&from=list&type=repertoire&srs=all&last_n_games=100&subtype=chapter%3A7");
    // Under a sort the drill follows the rows as shown.
    fireEvent.click(within(header("Puzzle")).getByRole("button"));
    fireEvent.click(within(header("Puzzle")).getByRole("button"));
    expect(rowIds()).toEqual([33, 32, 31]);
    expect(screen.getByRole("link", { name: "Drill these (2 lines)" })).toHaveAttribute("href", expect.stringContaining("drill=901,902&"));
    view.unmount();

    cap = 1;
    renderPage("/practice?type=repertoire&srs=all");
    const over = await screen.findByRole("button", { name: "Drill these (2 lines)" });
    expect(over).toBeDisabled();
    expect(over).toHaveAttribute("title", "1 at most — narrow the filter");
    expect(screen.getByText("1 at most — narrow the filter")).toBeInTheDocument();
    view.unmount();

    rows = [];
    renderPage("/practice?type=repertoire&srs=all");
    expect(await screen.findByText("No puzzles match.")).toBeInTheDocument();
    const none = screen.getByRole("button", { name: "Drill these (0 lines)" });
    expect(none).toBeDisabled();
    expect(none).toHaveAttribute("title", "Nothing listed to drill");
  });

  it("is disabled while the cap is unknown — the scopes read pending or failed", async () => {
    let answer: ((r: Reply) => void) | null = null;
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list(threeRows) }),
      "/practice/repertoire-scopes": () => new Promise<Reply>((resolve) => (answer = resolve)),
    });
    renderPage("/practice?type=repertoire&srs=all");
    const pending = await screen.findByRole("button", { name: "Drill these (2 lines)" });
    expect(pending).toBeDisabled();
    expect(pending).toHaveAttribute("title", "The drill limit is not known yet");
    answer!({ status: 500, body: { detail: "down" } });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Drill these (2 lines)" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Drill these (2 lines)" })).toHaveAttribute("title", "The drill limit is not known yet");
    expect(screen.queryByRole("link", { name: /Drill these/ })).not.toBeInTheDocument();
  });

  it("is only on the repertoire All list", async () => {
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list(threeRows) }),
      "/practice/repertoire-scopes": () => ({ status: 200, body: scopes() }),
    });
    const view = renderPage("/practice?type=repertoire&srs=retired");
    expect(await screen.findByText("#31")).toBeInTheDocument();
    expect(screen.queryByText(/Drill these/)).not.toBeInTheDocument();
    view.unmount();
    renderPage("/practice?type=blunder&srs=all");
    expect(await screen.findByText("#31")).toBeInTheDocument();
    expect(screen.queryByText(/Drill these/)).not.toBeInTheDocument();
  });

  it("a drill from the list comes back to the list — after a reload, on Stop — with its filters, a fresh order and no tint", async () => {
    let reads = 0;
    stubFetch({
      "/practice/puzzles": () => {
        reads += 1;
        return { status: 200, body: list(reads === 1 ? [line(31, 901), line(32, 902)] : [line(32, 902), line(31, 901, { attempt_summary: { total: 1, solved: 1, last_attempt_at: null, streak: 1 }, srs: srsAt("2027-01-01T00:00:00Z", "knight") })]) };
      },
      "/practice/repertoire-scopes": () => ({ status: 200, body: scopes() }),
      "/practice/drill": () => ({ status: 200, body: drillBody([line(31, 901), line(32, 902)]) }),
      "/practice/puzzles/31/attempt": () => OK,
    });
    const view = renderPage("/practice?type=repertoire&subtype=chapter:7&srs=all&last_n_games=100");
    const button = await screen.findByRole("link", { name: "Drill these (2 lines)" });
    expect(screen.getByTestId("location").textContent).toBe("/practice");
    fireEvent.click(button);
    expect(await screen.findByTestId("drill-header")).toBeInTheDocument();
    // The URL keeps the drill and the list's query while drilling: a reload serves the same drill.
    const drillUrl = screen.getByTestId("location").textContent!;
    expect(drillUrl).toBe("/practice?drill=901,902&from=list&type=repertoire&srs=all&last_n_games=100&subtype=chapter%3A7");
    expect(screen.queryByRole("tablist", { name: "Practice type" })).toBeNull();
    view.unmount();

    // The reload.
    renderPage(drillUrl);
    expect(await screen.findByTestId("drill-header")).toBeInTheDocument();
    expect(screen.getByTestId("location").textContent).toBe(drillUrl);
    await solveLine();
    expect(await screen.findByText("Next line →")).toBeInTheDocument();
    const stop = screen.getByRole("link", { name: "Stop" });
    expect(stop).toHaveAttribute("href", "/practice?type=repertoire&srs=all&last_n_games=100&subtype=chapter%3A7");
    await vi.waitFor(() => expect(stop).toHaveAttribute("aria-disabled", "false"));
    fireEvent.click(stop);
    // Back on the list: the same query (the read carries it), the entry params stripped, a fresh order.
    expect(await screen.findByText("#32")).toBeInTheDocument();
    await vi.waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/practice"));
    expect(rowIds()).toEqual([32, 31]);
    expect(rowOf(31).dataset.attempted).toBeUndefined();
    expect(within(rowOf(31)).getByText("1/1")).toBeInTheDocument();
    const read = (vi.mocked(fetch).mock.calls as unknown as Array<[string]>).map((c) => c[0]).filter((u) => u.includes("/practice/puzzles?")).at(-1)!;
    expect(new URLSearchParams(read.split("?")[1])).toEqual(new URLSearchParams({ srs: "all", ptype: "repertoire", subtype: "chapter:7", last_n_games: "100" }));
    expect(screen.getByRole("tab", { name: "Repertoire" })).toHaveAttribute("aria-selected", "true");
  });

  it("a drill from the list that reaches Done offers the list, not the report, and keeps the report's queue", async () => {
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list([line(31, 901)]) }),
      "/practice/repertoire-scopes": () => ({ status: 200, body: scopes() }),
      "/practice/drill": () => ({ status: 200, body: drillBody([line(31, 901)]) }),
      "/practice/puzzles/31/attempt": () => OK,
    });
    renderPage("/practice?type=repertoire&srs=all");
    fireEvent.click(await screen.findByRole("link", { name: "Drill these (1 line)" }));
    expect(await screen.findByTestId("drill-header")).toBeInTheDocument();
    await solveLine();
    fireEvent.click(await screen.findByText("Finish →"));
    expect(await screen.findByTestId("drill-done")).toBeInTheDocument();
    expect(readQueue()).toEqual([901, 902]);
    const back = screen.getByRole("link", { name: "Back to the list" });
    expect(back).toHaveAttribute("href", "/practice?type=repertoire&srs=all&last_n_games=0");
    fireEvent.click(back);
    expect(await screen.findByText("#31")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Repertoire" })).toHaveAttribute("aria-selected", "true");
  });

  it("browser Back into a list drill after the type was changed: the origin and the return come from the drill URL, and the report's queue survives Done", async () => {
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list([line(31, 901)]) }),
      "/practice/repertoire-scopes": () => ({ status: 200, body: scopes() }),
      "/practice/drill": () => ({ status: 200, body: drillBody([line(31, 901)]) }),
      "/practice/puzzles/31/attempt": () => OK,
    });
    renderPage("/practice?type=repertoire&subtype=chapter:7&srs=all&last_n_games=100");
    fireEvent.click(await screen.findByRole("link", { name: "Drill these (1 line)" }));
    await screen.findByTestId("drill-header");
    fireEvent.click(screen.getByRole("link", { name: "Stop" }));
    await screen.findByRole("link", { name: "Drill these (1 line)" });
    fireEvent.click(screen.getByRole("tab", { name: "Blunder" }));
    await vi.waitFor(() => expect(screen.getByRole("tab", { name: "Blunder" })).toHaveAttribute("aria-selected", "true"));
    // Back to the drill URL (the Stop landing was stripped in place). The page's filters still say Blunder.
    fireEvent.click(screen.getByText("browser back"));
    await screen.findByTestId("drill-header");
    expect(screen.getByTestId("location").textContent).toBe("/practice?drill=901&from=list&type=repertoire&srs=all&last_n_games=100&subtype=chapter%3A7");
    expect(screen.getByRole("link", { name: "Stop" })).toHaveAttribute("href", "/practice?type=repertoire&srs=all&last_n_games=100&subtype=chapter%3A7");
    await solveLine();
    const finish = await screen.findByText("Finish →");
    await vi.waitFor(() => expect(screen.getByRole("link", { name: "Stop" })).toHaveAttribute("aria-disabled", "false"));
    fireEvent.click(finish);
    await screen.findByTestId("drill-done");
    expect(readQueue()).toEqual([901, 902]); // not this drill's queue
    const back = screen.getByRole("link", { name: "Back to the list" });
    expect(back).toHaveAttribute("href", "/practice?type=repertoire&srs=all&last_n_games=100&subtype=chapter%3A7");
    fireEvent.click(back);
    await screen.findByRole("link", { name: "Drill these (1 line)" });
    expect(screen.getByRole("tab", { name: "Repertoire" })).toHaveAttribute("aria-selected", "true"); // the drill's list, not Blunder
    await vi.waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/practice"));
  });

  it("browser Back across a scope change: a drill of the whole repertoire returns to the whole repertoire, not to the chapter chosen later", async () => {
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: list([line(31, 901)]) }),
      "/practice/repertoire-scopes": () => ({ status: 200, body: scopes() }),
      "/practice/drill": () => ({ status: 200, body: drillBody([line(31, 901)]) }),
    });
    renderPage("/practice?type=repertoire&srs=all"); // the whole repertoire: no subtype
    fireEvent.click(await screen.findByRole("link", { name: "Drill these (1 line)" }));
    await screen.findByTestId("drill-header");
    expect(screen.getByRole("link", { name: "Stop" })).toHaveAttribute("href", "/practice?type=repertoire&srs=all&last_n_games=0");
    fireEvent.click(screen.getByRole("link", { name: "Stop" }));
    await screen.findByRole("link", { name: "Drill these (1 line)" });
    // Narrow to chapter 7 in the filters, then Back into the drill of the whole book.
    fireEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    fireEvent.change(screen.getByLabelText("Book"), { target: { value: "1" } });
    fireEvent.change(await screen.findByLabelText("Chapter"), { target: { value: "7" } });
    await vi.waitFor(() => expect(screen.getByRole("link", { name: "Drill these (1 line)" })).toHaveAttribute("href", expect.stringContaining("subtype=chapter%3A7")));
    fireEvent.click(screen.getByText("browser back"));
    await screen.findByTestId("drill-header");
    expect(screen.getByRole("link", { name: "Stop" })).toHaveAttribute("href", "/practice?type=repertoire&srs=all&last_n_games=0"); // an omitted subtype is no subtype
    // Stop lands on the whole repertoire: the chapter chosen later is gone from the read and the page.
    const readsBefore = calls.filter((c) => c.path === "/practice/puzzles").length;
    fireEvent.click(screen.getByRole("link", { name: "Stop" }));
    await screen.findByRole("link", { name: "Drill these (1 line)" });
    await vi.waitFor(() => expect(calls.filter((c) => c.path === "/practice/puzzles").length).toBeGreaterThan(readsBefore));
    const landed = calls.filter((c) => c.path === "/practice/puzzles").at(-1)!.query;
    expect(landed.get("ptype")).toBe("repertoire");
    expect(landed.has("subtype")).toBe(false);
    expect(screen.getByRole("link", { name: "Drill these (1 line)" })).toHaveAttribute("href", "/practice?drill=901&from=list&type=repertoire&srs=all&last_n_games=0");
    await vi.waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/practice"));
    fireEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    expect(screen.getByLabelText("Chapter")).toHaveValue("");
    expect(readQueue()).toEqual([901, 902]);
  });

  it("a drill with from=list but no repertoire filter returns to the report, as a report drill does", async () => {
    stubFetch({
      "/practice/drill": () => ({ status: 200, body: drillBody([line(31, 901)]) }),
    });
    renderPage("/practice?drill=901&from=list");
    expect(await screen.findByTestId("drill-header")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Stop" })).toHaveAttribute("href", "/repertoire/report");
  });
});
