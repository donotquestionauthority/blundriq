/**
 * The missed-board review in the three Practice hosts: the attempts a pass sends are unchanged and
 * the review sends none; from a pass's first miss until its review ends there is no way past the
 * puzzle (no Skip, no Next, no Next line, no Finish — absent, not disabled), a Replay's miss
 * included; and between a verdict and its storage nothing is offered either.
 */
import { configure, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Practice from "./Practice";
import { _resetProbesForTests } from "../utils/attemptQueue";
import { _resetRemovalsForTests } from "../utils/puzzleRemoval";
import { _resetUnsavedAttemptForTests } from "../utils/unsavedAttempt";
import { resetQueueForTests } from "../utils/drillQueue";
import type { DrillResponse, Puzzle, PuzzlesResponse } from "../practice";
import { _resetPracticeTodayForTests } from "../practiceToday";

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
      const drop = (from: string, to: string) => {
        if (!mounted.current) return void setTimeout(() => drop(from, to), 0);
        latest.current.onPieceDrop?.({ piece: null, sourceSquare: from, targetSquare: to });
      };
      return (
        <div>
          {["a1a8", "a1a7", "a7a8", "a7b7"].map((m) => (
            <button key={m} type="button" onClick={() => drop(m.slice(0, 2), m.slice(2))}>
              {m}
            </button>
          ))}
        </div>
      );
    },
  };
});

/** Two player moves from a rook ending: Ra7 Kf8 Rb7. Ra8 is wrong on both boards. */
const puzzle = (id: number, over: Partial<Puzzle> = {}): Puzzle => ({
  id,
  fen: "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
  solution_line: ["Ra7", "Kf8", "Rb7"],
  acceptance_map: null,
  source_types: ["deviation"],
  themes: [],
  color: "w",
  title: `Line ${id}`,
  description: null,
  created_at: "2026-09-01T00:00:00Z",
  occurrence_count: 0,
  source_breakdown: {},
  game_links: [],
  correct_game_links: [],
  attempt_count: 0,
  attempt_summary: { total: 0, solved: 0, last_attempt_at: null, streak: 0 },
  is_repertoire: false,
  presentation_ply: null,
  presentation_fen: null,
  repertoire_line_id: null,
  srs: null,
  play_batch_id: 1,
  ...over,
});
const serve = (puzzles: Puzzle[]): PuzzlesResponse => ({ puzzles, total: puzzles.length, mastered_count: 0, advance_threshold: 1, batch_id: 1, scope: "all", mint_ahead_threshold: 1, served_themes: [] });
const drill = (puzzles: Puzzle[]): DrillResponse => ({ batch_id: 1, puzzles, skipped: [], advance_threshold: 1, max_lines: 50 });
const recorded = (solved: boolean) => ({ status: 200, body: { detail: "attempt recorded", solved, counted: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } });

type Reply = { status: number; body: unknown };
type Call = { path: string; body: Record<string, unknown> | null };
function stubFetch(routes: Record<string, (body: Record<string, unknown> | null) => Reply | Promise<Reply>>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      calls.push({ path, body });
      const h = routes[path];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(body);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}
const attemptRoute = (body: Record<string, unknown> | null) => recorded(Boolean(body?.solved));
const posts = (calls: Call[], id: number) => calls.filter((c) => c.path === `/practice/puzzles/${id}/attempt`);

function renderPage(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/practice" element={<Practice />} />
        <Route path="/repertoire/report" element={<p>report page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}
const queueMode = (request: (n: string, cb: () => Promise<unknown>) => Promise<unknown> = (_n, cb) => cb()) => {
  Object.defineProperty(navigator, "locks", { value: { request }, configurable: true });
  _resetProbesForTests();
};
const drop = (move: string) => fireEvent.click(screen.getByText(move));
const flush = () => new Promise((r) => setTimeout(r, 0));
/** No way past the puzzle is offered as a control. */
const noWayPast = () => {
  for (const name of ["Skip →", "Next Puzzle →", "Next line →", "Finish →"]) expect(screen.queryByRole("button", { name })).toBeNull();
};
/** Miss both boards (each fixed by Try Again) and finish the line: the review begins. */
async function missBothAndFinish() {
  drop("a1a8");
  fireEvent.click(await screen.findByText("Try Again"));
  drop("a1a7");
  await screen.findByText("Your turn", { exact: true });
  drop("a7a8");
  fireEvent.click(await screen.findByText("Try Again"));
  drop("a7b7");
  await screen.findByText(/Review: 0 of 2 boards fixed/);
}
/** One right review answer on the board on show, and the pause before the next. */
async function reviewRight(move: string, n: number) {
  await screen.findByText("Find the move");
  drop(move);
  expect(await screen.findByText(`Right — ${n} in a row`)).toBeInTheDocument();
}
/** Both missed boards right three times each, in line order. */
async function reviewBoth() {
  for (let n = 1; n <= 3; n++) {
    await reviewRight("a1a7", n);
    await reviewRight("a7b7", n);
  }
  await screen.findByText("Puzzle complete!");
}

/** A durable queue whose lock can be held from the test: while `held`, each request waits for `release()`. */
function heldQueue() {
  let held = false;
  let gate: Promise<void> = Promise.resolve();
  let open: () => void = () => {};
  queueMode(async (_n, cb) => {
    if (held) await gate;
    return cb();
  });
  return {
    hold: () => {
      held = true;
      gate = new Promise<void>((r) => (open = r));
    },
    release: () => {
      held = false;
      open();
    },
  };
}
const pendingInStorage = () => JSON.parse(window.localStorage.getItem("blundriq_pending_attempts_v1") ?? "[]") as Array<{ solved: boolean; attempt_seq: number }>;

describe("Practice: the missed-board review", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetQueueForTests([]);
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

  it("queue: the pass's attempts are unchanged, the review sends none, and Next comes only after it", async () => {
    queueMode();
    const calls = stubFetch({ "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }), "/practice/puzzles/11/attempt": attemptRoute });
    renderPage("/practice");
    await screen.findByText("#11");
    expect(screen.getByRole("button", { name: "Skip →" })).toBeEnabled(); // before a verdict, as today
    drop("a1a8");
    await screen.findByText("Try Again");
    noWayPast();
    const slot = screen.getByText("Next Puzzle →");
    expect(slot).toHaveAttribute("aria-hidden", "true"); // the slot keeps its space, hidden
    expect(slot.className.split(" ")).toContain("invisible");
    fireEvent.click(screen.getByText("Try Again"));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7a8");
    fireEvent.click(await screen.findByText("Try Again"));
    drop("a7b7");
    await screen.findByText(/Review: 0 of 2 boards fixed/);
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(3));
    expect(posts(calls, 11).map((c) => [c.body!.attempt_seq, c.body!.solved, c.body!.moves_played])).toEqual([
      [1, false, "Ra8#"],
      [2, false, "Ra7,Ra8+"],
      [3, true, "Ra7,Rb7"],
    ]);
    noWayPast();
    await reviewBoth();
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
    expect(posts(calls, 11)).toHaveLength(3); // nothing from the review
    expect(new Set(posts(calls, 11).map((c) => c.body!.session_id)).size).toBe(1);
  }, 15_000);

  it("queue: Previous mid-review leaves without sending anything", async () => {
    queueMode();
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }),
      "/practice/puzzles/11/attempt": attemptRoute,
      "/practice/puzzles/12/attempt": attemptRoute,
    });
    renderPage("/practice");
    await screen.findByText("#11");
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    fireEvent.click(await screen.findByRole("button", { name: "Next Puzzle →" }));
    await screen.findByText("#12");
    await missBothAndFinish();
    await vi.waitFor(() => expect(posts(calls, 12)).toHaveLength(3));
    await vi.waitFor(() => expect(screen.getByText("← Previous")).toBeEnabled());
    fireEvent.click(screen.getByText("← Previous"));
    await screen.findByText("#11");
    await flush();
    expect(posts(calls, 12)).toHaveLength(3);
    expect(posts(calls, 11)).toHaveLength(1);
  });

  it("queue: a Replay's miss takes Next away at once until the replay's review is done", async () => {
    queueMode();
    const calls = stubFetch({ "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }), "/practice/puzzles/11/attempt": attemptRoute });
    renderPage("/practice");
    await screen.findByText("#11");
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
    fireEvent.click(screen.getByText("Replay"));
    expect(screen.getByRole("button", { name: "Next Puzzle →" })).toBeEnabled(); // nothing owed yet
    drop("a1a8");
    noWayPast(); // at once, in the miss's own handler
    fireEvent.click(await screen.findByText("Try Again"));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    await screen.findByText(/Review: 0 of 1 board fixed/);
    noWayPast();
    for (let n = 1; n <= 3; n++) await reviewRight("a1a7", n);
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(3));
    expect(posts(calls, 11).map((c) => [c.body!.attempt_seq, c.body!.solved])).toEqual([
      [1, true],
      [2, false],
      [3, true],
    ]);
  });

  it("queue: while the durable queue has not yet stored a verdict, neither Skip nor Next is offered", async () => {
    let open!: () => void;
    let gate = new Promise<void>((r) => (open = r));
    queueMode(async (_n, cb) => {
      await gate;
      return cb();
    });
    stubFetch({ "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }), "/practice/puzzles/11/attempt": attemptRoute, "/practice/puzzles/12/attempt": attemptRoute });
    renderPage("/practice");
    await screen.findByText("#11");
    // A wrong move: nothing to click from the verdict on, stored or not.
    drop("a1a8");
    noWayPast();
    await flush();
    noWayPast();
    open();
    await screen.findByText("Try Again");
    await flush();
    noWayPast(); // stored, and still owed
    // A right first completion on the next puzzle: nothing until the queue has it.
    fireEvent.click(screen.getByText("Try Again"));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    await screen.findByText(/Review: 0 of 1 board fixed/);
    for (let n = 1; n <= 3; n++) await reviewRight("a1a7", n);
    fireEvent.click(await screen.findByRole("button", { name: "Next Puzzle →" }));
    await screen.findByText("#12");
    gate = new Promise<void>((r) => (open = r));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    await screen.findByText("Puzzle complete!");
    noWayPast();
    await flush();
    noWayPast();
    open();
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
  });

  it("queue: returning to a puzzle (Previous, then Skip back) is a new mount and starts fresh", async () => {
    queueMode();
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }),
      "/practice/puzzles/11/attempt": attemptRoute,
      "/practice/puzzles/12/attempt": attemptRoute,
      "/practice/skip": () => ({ status: 200, body: { status: "DEFERRED" } }),
    });
    renderPage("/practice");
    await screen.findByText("#11");
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    fireEvent.click(await screen.findByRole("button", { name: "Next Puzzle →" }));
    await screen.findByText("#12");
    drop("a1a8");
    await screen.findByText("Try Again");
    noWayPast();
    await vi.waitFor(() => expect(screen.getByText("← Previous")).toBeEnabled());
    fireEvent.click(screen.getByText("← Previous"));
    await screen.findByText("#11");
    fireEvent.click(await screen.findByRole("button", { name: "Skip →" })); // #11 again: a new mount
    await screen.findByText("#12");
    expect(await screen.findByRole("button", { name: "Skip →" })).toBeEnabled(); // #12 again: no old report
  });

  it("queue, blocking mode: a wrong first move leaves nothing to click while its POST is out, nor after", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let open!: () => void;
    const gate = new Promise<void>((r) => (open = r));
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }),
      "/practice/puzzles/11/attempt": async (body) => {
        await gate;
        return attemptRoute(body);
      },
    });
    renderPage("/practice");
    await screen.findByText("#11");
    drop("a1a8");
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(1));
    noWayPast();
    open();
    await vi.waitFor(() => expect(screen.getByText("Try Again")).toBeEnabled());
    noWayPast(); // stored, and still owed
  });

  it("queue: an earlier stored miss does not open Next while the completing attempt still waits for the queue", async () => {
    const q = heldQueue();
    const calls = stubFetch({ "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }), "/practice/puzzles/11/attempt": attemptRoute });
    renderPage("/practice");
    await screen.findByText("#11");
    drop("a1a8"); // the miss: stored and sent
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(1));
    await vi.waitFor(() => expect(pendingInStorage()).toHaveLength(0));
    fireEvent.click(screen.getByText("Try Again"));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    q.hold();
    drop("a7b7"); // the completion: waits for the queue's lock
    for (let n = 1; n <= 3; n++) await reviewRight("a1a7", n);
    await screen.findByText("Puzzle complete!");
    await flush();
    noWayPast(); // the review is done, the miss is stored, the completion is not
    expect(posts(calls, 11)).toHaveLength(1);
    q.release();
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(2));
    expect(posts(calls, 11).map((c) => [c.body!.attempt_seq, c.body!.solved])).toEqual([
      [1, false],
      [2, true],
    ]);
  });

  it("queue: a store that lands after the solver was left counts for nothing on the next one", async () => {
    const q = heldQueue();
    const solvedBefore = { total: 1, solved: 1, last_attempt_at: null, streak: 1 };
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, { attempt_summary: solvedBefore }), puzzle(12)]) }),
      "/practice/puzzles/11/attempt": attemptRoute,
      "/practice/puzzles/12/attempt": attemptRoute,
    });
    renderPage("/practice");
    await screen.findByText("#11");
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    fireEvent.click(await screen.findByRole("button", { name: "Next Puzzle →" }));
    await screen.findByText("#12");
    q.hold();
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7"); // #12's completion waits for the queue
    await screen.findByText("Puzzle complete!");
    fireEvent.click(screen.getByText("← Previous")); // leave it waiting
    await screen.findByText("#11");
    expect(screen.getByTestId("puzzle-note").className.split(" ")).not.toContain("invisible"); // #11 is fresh
    q.release(); // #12's store lands now, for a play-through already left
    await vi.waitFor(() => expect(posts(calls, 12)).toHaveLength(1));
    await flush();
    expect(screen.getByTestId("puzzle-note").className.split(" ")).not.toContain("invisible"); // and stays so
    q.hold();
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7"); // #11's own completion, not yet stored
    await screen.findByText("Puzzle complete!");
    await flush();
    noWayPast();
    q.release();
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
  });

  it("drill: Finish waits for the completing attempt, and Stop before it lands loses nothing", async () => {
    const q = heldQueue();
    const calls = stubFetch({ "/practice/drill": () => ({ status: 200, body: drill([puzzle(11, { is_repertoire: true, presentation_ply: 2, repertoire_line_id: 901 })]) }), "/practice/puzzles/11/attempt": attemptRoute });
    renderPage("/practice?drill=901");
    await screen.findByText("#11");
    drop("a1a8");
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(1));
    await vi.waitFor(() => expect(pendingInStorage()).toHaveLength(0));
    fireEvent.click(screen.getByText("Try Again"));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    q.hold();
    drop("a7b7");
    for (let n = 1; n <= 3; n++) await reviewRight("a1a7", n);
    await screen.findByText("Puzzle complete!");
    await flush();
    noWayPast();
    fireEvent.click(screen.getByRole("link", { name: "Stop" })); // leave before the completion is stored
    await screen.findByText("report page");
    q.release();
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(2));
    expect(posts(calls, 11)[1].body).toMatchObject({ attempt_seq: 2, solved: true, mode: "drill" });
  });

  it("queue, blocking mode: while the POST is out, neither Skip nor Next is offered", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let open!: () => void;
    const gate = new Promise<void>((r) => (open = r));
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11), puzzle(12)]) }),
      "/practice/puzzles/11/attempt": async (body) => {
        await gate;
        return attemptRoute(body);
      },
    });
    renderPage("/practice"); // no navigator.locks: blocking mode
    await screen.findByText("#11");
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    await screen.findByText("Puzzle complete!");
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(1));
    noWayPast();
    open();
    expect(await screen.findByRole("button", { name: "Next Puzzle →" })).toBeEnabled();
    expect(posts(calls, 11)).toHaveLength(1);
  });

  it("drill: Next line comes only after the review, a Replay's miss takes it away, and the tally is the first try's", async () => {
    const calls = stubFetch({ "/practice/drill": () => ({ status: 200, body: drill([puzzle(11, { is_repertoire: true, presentation_ply: 2, repertoire_line_id: 901 }), puzzle(12, { is_repertoire: true, presentation_ply: 2, repertoire_line_id: 902 })]) }), "/practice/puzzles/11/attempt": attemptRoute, "/practice/puzzles/12/attempt": attemptRoute });
    renderPage("/practice?drill=901,902");
    await screen.findByText("#11");
    await missBothAndFinish();
    noWayPast();
    await reviewBoth();
    expect(await screen.findByRole("button", { name: "Next line →" })).toBeEnabled();
    expect(screen.getByTestId("drill-header").textContent).toContain("0 first-try");
    // Replay, miss: Next line goes until the replay's review is done.
    fireEvent.click(screen.getByText("Replay"));
    drop("a1a8");
    noWayPast();
    fireEvent.click(await screen.findByText("Try Again"));
    drop("a1a7");
    await screen.findByText("Your turn", { exact: true });
    drop("a7b7");
    for (let n = 1; n <= 3; n++) await reviewRight("a1a7", n);
    fireEvent.click(await screen.findByRole("button", { name: "Next line →" }));
    await screen.findByText("#12");
    // The last line: Finish (and so Done) only after its review.
    await missBothAndFinish();
    noWayPast();
    expect(screen.queryByTestId("drill-done")).toBeNull();
    await reviewBoth();
    fireEvent.click(await screen.findByRole("button", { name: "Finish →" }));
    const done = await screen.findByTestId("drill-done");
    expect(done.textContent).toContain("2 lines drilled · first try right 0 · wrong 2");
    await vi.waitFor(() => expect(posts(calls, 12)).toHaveLength(3));
    // Pass 1: two misses and the completion; the Replay: a miss and the completion. Line 12: as pass 1.
    expect(posts(calls, 11).map((c) => [c.body!.attempt_seq, c.body!.solved, c.body!.moves_played])).toEqual([
      [1, false, "Ra8#"],
      [2, false, "Ra7,Ra8+"],
      [3, true, "Ra7,Rb7"],
      [4, false, "Ra8#"],
      [5, true, "Ra7,Rb7"],
    ]);
    expect(posts(calls, 12).map((c) => [c.body!.attempt_seq, c.body!.solved, c.body!.moves_played])).toEqual([
      [1, false, "Ra8#"],
      [2, false, "Ra7,Ra8+"],
      [3, true, "Ra7,Rb7"],
    ]);
    expect(posts(calls, 11).every((c) => c.body!.mode === "drill")).toBe(true);
  }, 20_000); // fifteen review answers, each with its pause

  it("drill: Stop mid-review leaves without sending anything", async () => {
    const calls = stubFetch({ "/practice/drill": () => ({ status: 200, body: drill([puzzle(11, { is_repertoire: true, presentation_ply: 2, repertoire_line_id: 901 })]) }), "/practice/puzzles/11/attempt": attemptRoute });
    renderPage("/practice?drill=901");
    await screen.findByText("#11");
    await missBothAndFinish();
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(3));
    fireEvent.click(screen.getByRole("link", { name: "Stop" }));
    await screen.findByText("report page");
    await flush();
    expect(posts(calls, 11)).toHaveLength(3);
  });

  it("overlay: Close mid-review leaves without sending anything", async () => {
    queueMode();
    const p = puzzle(11);
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([]) }), // no solver under the dialog
      "/practice/puzzles/11": () => ({ status: 200, body: { id: 11, fen: p.fen, solution_line: p.solution_line, color: "w", acceptance_map: null, source_types: p.source_types, themes: [], is_repertoire: false, presentation_ply: null } }),
      "/practice/puzzles/11/attempt": attemptRoute,
    });
    renderPage("/practice?puzzle=11");
    await screen.findByRole("dialog");
    await missBothAndFinish();
    await vi.waitFor(() => expect(posts(calls, 11)).toHaveLength(3));
    await vi.waitFor(() => expect(screen.getByLabelText("Close")).toBeEnabled());
    fireEvent.click(screen.getByLabelText("Close"));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await flush();
    expect(posts(calls, 11)).toHaveLength(3);
  });
});
