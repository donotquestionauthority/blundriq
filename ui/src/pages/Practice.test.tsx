import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import Layout from "../components/Layout";
import { _resetUnsavedAttemptForTests, disownUnsavedAttempt, getUnsavedAttempt, holdUnsavedAttempt, isUnsavedAttemptOwned, releaseUnsavedAttempt } from "../utils/unsavedAttempt";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Practice from "./Practice";
import { _resetProbesForTests, QUEUE_STORE } from "../utils/attemptQueue";
import { _resetRemovalsForTests } from "../utils/puzzleRemoval";
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

type Reply = { status: number; body: unknown };
type Handler = (method: string, body: unknown) => Reply | Promise<Reply>;

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
      const r = await h(method, body);
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
    _resetUnsavedAttemptForTests();
    _resetRemovalsForTests();
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

  it("renders migrated rows whose themes are null, in the queue and the list", async () => {
    const raw = { ...puzzle(11, 1), themes: null } as unknown as Puzzle;
    stubFetch({ "/practice/puzzles": () => ({ status: 200, body: serve([raw], 1, 1) }) });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    renderPage("/practice?srs=all");
    expect((await screen.findAllByText("#11")).length).toBeGreaterThan(0);
  });

  it("keeps an unsaved attempt in reach: Previous, the type tabs and the filters wait for the save", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let attemptCalls = 0;
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }),
      "/practice/skip": () => ({ status: 200, body: { status: "DEFERRED" } }),
      "/practice/puzzles/12/attempt": () => {
        attemptCalls += 1;
        return attemptCalls === 1
          ? { status: 500, body: { detail: "boom" } }
          : { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };
      },
    });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Skip →"));
    expect(await screen.findByText("#12")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    expect(screen.getByText("← Previous")).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Motif" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Filters/ })).toBeDisabled();
    fireEvent.click(screen.getByText("← Previous"));
    expect(screen.getByText("#12")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    expect(screen.getByText("← Previous")).not.toBeDisabled();
    expect(screen.getByRole("tab", { name: "Motif" })).not.toBeDisabled();
    const attempts = calls.filter((c) => c.path === "/practice/puzzles/12/attempt");
    expect(attempts).toHaveLength(2);
    expect(attempts[1].body!.attempt_id).toBe(attempts[0].body!.attempt_id);
  });

  it("asks again once a late save lands while the queue waits at its end", async () => {
    Object.defineProperty(navigator, "locks", { value: { request: (_n: string, cb: () => Promise<unknown>) => cb() }, configurable: true });
    _resetProbesForTests();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let saved = false;
    let serves = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = url.replace(/^.*\/api/, "").split("?")[0];
        if (path === "/practice/puzzles") {
          serves += 1;
          // Until the attempt is acknowledged the server can only return the same batch;
          // afterwards it mints the next one.
          return { ok: true, status: 200, json: async () => (saved ? serve([puzzle(12, 2)], 2, 1) : serve([puzzle(11, 1)], 1, 1)) };
        }
        if (path === "/practice/puzzles/11/attempt") {
          await gate;
          saved = true;
          const body = JSON.parse(String(init?.body));
          return { ok: true, status: 200, json: async () => ({ detail: "attempt recorded", solved: body.solved, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } }) };
        }
        return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      }),
    );
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    fireEvent.click(await screen.findByText("Next Puzzle →"));
    // The boundary refetch ran before the save committed: nothing new, the page waits.
    expect(await screen.findByText(/Loading next puzzle/)).toBeInTheDocument();
    const servesBefore = serves;
    release();
    expect(await screen.findByText("#12")).toBeInTheDocument();
    expect(serves).toBeGreaterThan(servesBefore);
  });

  it("holds an unsaved attempt across the whole app: the header waits, and leaving and returning offers Retry", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let attemptCalls = 0;
    const calls = stubFetch({
      "/me": () => ({ status: 200, body: { authenticated: true } }),
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }),
      "/practice/puzzles/11/attempt": () => {
        attemptCalls += 1;
        return attemptCalls === 1
          ? { status: 500, body: { detail: "boom" } }
          : { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };
      },
    });
    const view = render(
      <MemoryRouter initialEntries={["/practice"]}>
        <Routes>
          <Route element={<Layout onLoggedOut={() => {}} />}>
            <Route path="/practice" element={<Practice />} />
            <Route path="/games" element={<p>games page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    // The header no longer offers a way out; the unsaved attempt is the reason.
    const games = screen.getByText("Games");
    expect(games.tagName).toBe("SPAN");
    fireEvent.click(games);
    expect(screen.queryByText("games page")).not.toBeInTheDocument();
    expect(screen.getByText("#11")).toBeInTheDocument();
    expect(screen.getByText("Log out")).toBeDisabled();

    // Leave anyway (the browser's own history, say) and come back: the attempt is still
    // offered, with the same idempotency key, and saving it frees the header.
    view.unmount();
    render(
      <MemoryRouter initialEntries={["/practice"]}>
        <Routes>
          <Route element={<Layout onLoggedOut={() => {}} />}>
            <Route path="/practice" element={<Practice />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/attempt on puzzle #11 has not been saved/)).toBeInTheDocument();
    expect(screen.getByText("Games").tagName).toBe("SPAN");
    // Nothing else is offered until it is saved: no board, no Skip, no queue.
    await flush();
    expect(screen.queryByText("drop")).not.toBeInTheDocument();
    expect(screen.queryByText("Skip →")).not.toBeInTheDocument();
    expect(screen.queryByText("#11")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    await vi.waitFor(() => expect(screen.queryByText(/has not been saved/)).not.toBeInTheDocument());
    expect(screen.getByText("Games").tagName).toBe("A");
    // ... and the queue comes back once it is.
    expect(await screen.findByText("#11")).toBeInTheDocument();
    const attempts = calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    expect(attempts).toHaveLength(2);
    expect(attempts[1].body!.attempt_id).toBe(attempts[0].body!.attempt_id);
    expect(attempts[1].body!.session_id).toBe(attempts[0].body!.session_id);
  });

  it("holds a blocking-mode attempt from the moment its request goes out: leaving mid-save opens no second one", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let resolveFirst: (r: Reply) => void = () => {};
    let attemptCalls = 0;
    const calls = stubFetch({
      "/me": () => ({ status: 200, body: { authenticated: true } }),
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }),
      "/practice/puzzles/11/attempt": () => {
        attemptCalls += 1;
        if (attemptCalls === 1) return new Promise<Reply>((r) => (resolveFirst = r));
        return { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };
      },
    });
    const app = (
      <Routes>
        <Route element={<Layout onLoggedOut={() => {}} />}>
          <Route path="/practice" element={<Practice />} />
          <Route path="/games" element={<p>games page</p>} />
        </Route>
      </Routes>
    );
    const view = render(<MemoryRouter initialEntries={["/practice"]}>{app}</MemoryRouter>);
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    await flush();
    // The request is out and unanswered; the app already treats the attempt as unsaved.
    const attempts = () => calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    expect(attempts()).toHaveLength(1);
    const first = attempts()[0].body!;
    expect(getUnsavedAttempt()?.attempt_id).toBe(first.attempt_id);
    expect(screen.getByText("Games").tagName).toBe("SPAN");
    expect(screen.getByText("Log out")).toBeDisabled();

    // Leave through history regardless and come back while it is still pending: no board,
    // no second submission, only the held attempt.
    view.unmount();
    render(<MemoryRouter initialEntries={["/games", "/practice"]} initialIndex={1}>{app}</MemoryRouter>);
    expect(await screen.findByText(/attempt on puzzle #11 has not been saved/)).toBeInTheDocument();
    await flush();
    expect(screen.queryByText("drop")).not.toBeInTheDocument();
    expect(attempts()).toHaveLength(1);

    // The original request now fails: still one held attempt, the original.
    resolveFirst({ status: 500, body: { detail: "boom" } });
    await flush();
    expect(getUnsavedAttempt()?.attempt_id).toBe(first.attempt_id);
    expect(screen.getByText(/has not been saved/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Retry"));
    await vi.waitFor(() => expect(screen.queryByText(/has not been saved/)).not.toBeInTheDocument());
    expect(await screen.findByText("#11")).toBeInTheDocument();
    expect(attempts()).toHaveLength(2);
    expect(attempts()[1].body!.attempt_id).toBe(first.attempt_id);
    expect(attempts()[1].body!.session_id).toBe(first.session_id);
    expect(getUnsavedAttempt()).toBeNull();
  });

  it("never lets a second unsaved attempt displace the one it holds", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const first = { puzzle_id: 11, attempt_id: "a-1", session_id: "s-1", solved: true, moves_played: "Ra8#" };
    holdUnsavedAttempt(first);
    holdUnsavedAttempt({ ...first, attempt_id: "a-2", solved: false });
    expect(getUnsavedAttempt()).toEqual(first);
    // The same attempt may be re-held (a retry that failed again); a foreign id may not release it.
    holdUnsavedAttempt({ ...first, moves_played: "Ra8#" });
    releaseUnsavedAttempt("a-2");
    expect(getUnsavedAttempt()).toEqual(first);
    releaseUnsavedAttempt("a-1");
    expect(getUnsavedAttempt()).toBeNull();
    // Ownership: held by a solver until that solver disowns it; only the owner can.
    const solver = {};
    holdUnsavedAttempt(first, solver);
    expect(isUnsavedAttemptOwned()).toBe(true);
    disownUnsavedAttempt({});
    expect(isUnsavedAttemptOwned()).toBe(true);
    disownUnsavedAttempt(solver);
    expect(isUnsavedAttemptOwned()).toBe(false);
    expect(getUnsavedAttempt()).toEqual(first);
    releaseUnsavedAttempt();
    expect(isUnsavedAttemptOwned()).toBe(false);
  });

  it("holds an unsaved attempt from the overlay too", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1)], 1, 1) }),
      "/practice/puzzles/11": () => ({ status: 200, body: { id: 11, fen: puzzle(11, 1).fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["blunder"], themes: [], is_repertoire: false, presentation_ply: null } }),
      "/practice/puzzles/11/attempt": () => ({ status: 500, body: { detail: "boom" } }),
    });
    renderPage("/practice?puzzle=11");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("drop")[0]);
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Motif" })).toBeDisabled();
  });

  const failThenSucceed = () => {
    let n = 0;
    return () => {
      n += 1;
      return n === 1
        ? { status: 500, body: { detail: "boom" } }
        : { status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: { level: "knight", correct_at_level: 0, advance_threshold: 1, transition: null } } };
    };
  };

  it("after a failed save, Replay cannot open a second attempt until the first is saved", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }),
      "/practice/puzzles/11/attempt": failThenSucceed(),
    });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    nextDrop = { from: "a1", to: "a8" };
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    expect(screen.getByText("Replay")).toBeDisabled();
    fireEvent.click(screen.getByText("Replay"));
    fireEvent.click(screen.getByText("drop"));
    await flush();
    const attempts = () => calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    expect(attempts()).toHaveLength(1);
    const held = getUnsavedAttempt()!;
    expect(held.attempt_id).toBe(attempts()[0].body!.attempt_id);

    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    expect(attempts()).toHaveLength(2);
    expect(attempts()[1].body!.attempt_id).toBe(held.attempt_id);
    expect(getUnsavedAttempt()).toBeNull();
    expect(screen.getByText("Replay")).not.toBeDisabled();
  });

  it("after a failed save of a wrong attempt, Try Again and the board wait for the save", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1), puzzle(12, 1)], 1, 1) }),
      "/practice/puzzles/11/attempt": failThenSucceed(),
    });
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    nextDrop = { from: "a1", to: "b1" };
    fireEvent.click(screen.getByText("drop"));
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    const attempts = () => calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    expect(attempts()[0].body).toMatchObject({ solved: false, moves_played: "Rb1" });
    expect(screen.getByText("Try Again")).toBeDisabled();
    fireEvent.click(screen.getByText("Try Again"));
    nextDrop = { from: "a1", to: "a8" };
    fireEvent.click(screen.getByText("drop"));
    await flush();
    expect(attempts()).toHaveLength(1);
    expect(getUnsavedAttempt()!.attempt_id).toBe(attempts()[0].body!.attempt_id);

    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("Next Puzzle →")).toBeInTheDocument();
    expect(attempts()).toHaveLength(2);
    expect(attempts()[1].body!.attempt_id).toBe(attempts()[0].body!.attempt_id);
    expect(getUnsavedAttempt()).toBeNull();
    expect(screen.getByText("Try Again")).not.toBeDisabled();
  });

  it("the overlay's Replay and Try Again wait for a failed save as well", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1)], 1, 1) }),
      "/practice/puzzles/11": () => ({ status: 200, body: { id: 11, fen: puzzle(11, 1).fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["blunder"], themes: [], is_repertoire: false, presentation_ply: null } }),
      "/practice/puzzles/11/attempt": failThenSucceed(),
    });
    renderPage("/practice?puzzle=11");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    nextDrop = { from: "a1", to: "a8" };
    fireEvent.click(screen.getAllByText("drop")[0]);
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    expect(screen.getByText("Replay")).toBeDisabled();
    fireEvent.click(screen.getByText("Replay"));
    fireEvent.click(screen.getAllByText("drop")[0]);
    await flush();
    const attempts = () => calls.filter((c) => c.path === "/practice/puzzles/11/attempt");
    expect(attempts()).toHaveLength(1);
    fireEvent.click(screen.getByText("Retry"));
    await flush();
    await flush();
    expect(attempts()).toHaveLength(2);
    expect(attempts()[1].body!.attempt_id).toBe(attempts()[0].body!.attempt_id);
    expect(getUnsavedAttempt()).toBeNull();
    expect(screen.getByText("Replay")).not.toBeDisabled();
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

  it("retires a hand-made puzzle from the overlay after a confirmation, and offers it for no other kind", async () => {
    const custom = puzzle(21, 1, { source_types: ["blunder", "custom"] });
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { ...serve([custom, puzzle(22, 1)], 1), batch_id: null } }),
      "/puzzles/21": (method) => (method === "DELETE" ? { status: 200, body: { detail: "removed" } } : { status: 405, body: {} }),
    });
    renderPage("/practice?srs=all");
    fireEvent.click(await screen.findByText("#22"));
    expect(await screen.findByRole("dialog", { name: "Puzzle 22" })).toBeInTheDocument();
    expect(screen.queryByText("Remove puzzle")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Close"));

    fireEvent.click(screen.getByText("#21"));
    fireEvent.click(await screen.findByText("Remove puzzle"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false); // the first click only asks
    fireEvent.click(screen.getByText("Keep"));
    fireEvent.click(screen.getByText("Remove puzzle"));
    const lists = calls.filter((c) => c.path === "/practice/puzzles").length;
    fireEvent.click(screen.getByText("Yes, remove"));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/puzzles/21"]);
    expect(calls.filter((c) => c.path === "/practice/puzzles").length).toBe(lists + 1); // the list is refetched
  });

  it("will not retire a puzzle while an attempt on it is unsaved", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1)], 1, 1) }),
      "/practice/puzzles/11": () => ({ status: 200, body: { id: 11, fen: puzzle(11, 1).fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["custom"], themes: [], is_repertoire: false, presentation_ply: null } }),
      "/practice/puzzles/11/attempt": () => ({ status: 500, body: { detail: "boom" } }),
    });
    renderPage("/practice?puzzle=11");
    expect(await screen.findByText("Remove puzzle")).toBeEnabled();
    fireEvent.click(screen.getByText("Remove puzzle"));
    fireEvent.click(screen.getAllByText("drop")[0]);
    expect(await screen.findByText(/Couldn't save your attempt/)).toBeInTheDocument();
    expect(screen.getByText("Yes, remove")).toBeDisabled();
    fireEvent.click(screen.getByText("Yes, remove"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("locks the board while the removal is in flight, so no attempt can start on a puzzle that is going away", async () => {
    let finishDelete: (() => void) | null = null;
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1)], 1, 1) }),
      "/practice/puzzles/11": () => ({ status: 200, body: { id: 11, fen: puzzle(11, 1).fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["custom"], themes: [], is_repertoire: false, presentation_ply: null } }),
      "/puzzles/11": () => new Promise<Reply>((resolve) => (finishDelete = () => resolve({ status: 200, body: { detail: "removed" } }))),
    });
    renderPage("/practice?puzzle=11");
    fireEvent.click(await screen.findByText("Remove puzzle"));
    fireEvent.click(screen.getByText("Yes, remove"));
    expect(await screen.findByText("Removing…")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog")).getByText("drop")); // the overlay's board is locked: nothing happens
    expect(calls.some((c) => c.path === "/practice/puzzles/11/attempt")).toBe(false);
    finishDelete!();
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.some((c) => c.path === "/practice/puzzles/11/attempt")).toBe(false);
  });

  it("will not retire a puzzle while an attempt on it waits in the durable queue, even after the overlay is reopened", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    // Queue mode: localStorage and Web Locks are available, so a failed save is kept in the queue.
    Object.defineProperty(navigator, "locks", { value: { request: (_n: string, cb: () => Promise<unknown>) => cb() }, configurable: true });
    _resetProbesForTests();
    const custom = puzzle(21, 1, { source_types: ["custom"] });
    let attempts = 0;
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { ...serve([custom], 1), batch_id: null } }),
      "/practice/puzzles/21/attempt": () => ((attempts += 1), { status: 500, body: { detail: "boom" } }),
      "/puzzles/21": () => ({ status: 200, body: { detail: "removed" } }),
    });
    renderPage("/practice?srs=all");
    fireEvent.click(await screen.findByText("#21"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByText("drop"));
    await vi.waitFor(() => expect(attempts).toBeGreaterThan(0));
    expect(JSON.parse(window.localStorage.getItem(QUEUE_STORE) ?? "[]")).toHaveLength(1); // still queued
    fireEvent.click(screen.getByLabelText("Close"));
    fireEvent.click(screen.getByText("#21")); // a fresh overlay, no memory of that attempt
    fireEvent.click(await screen.findByText("Remove puzzle"));
    expect(screen.getByText("Yes, remove")).toBeDisabled();
    expect(screen.getByText("An attempt on it is still being saved.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Yes, remove"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("keeps a reopened solver locked while a removal started elsewhere is still in flight", async () => {
    let finishDelete: (() => void) | null = null;
    const custom = puzzle(21, 1, { source_types: ["custom"] });
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { ...serve([custom], 1), batch_id: null } }),
      "/puzzles/21": () => new Promise<Reply>((resolve) => (finishDelete = () => resolve({ status: 200, body: { detail: "removed" } }))),
    });
    renderPage("/practice?srs=all");
    fireEvent.click(await screen.findByText("#21"));
    fireEvent.click(await screen.findByText("Remove puzzle"));
    fireEvent.click(screen.getByText("Yes, remove"));
    expect(await screen.findByText("Removing…")).toBeInTheDocument();
    expect(screen.getByLabelText("Close")).toBeDisabled(); // the overlay waits for the answer
    expect(screen.getByRole("tab", { name: "Motif" })).toBeDisabled(); // and so does the page
    finishDelete!();
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });

  it("a failed removal unlocks the board again; a solver mounted mid-removal waits for the answer", async () => {
    const { beginRemoval, removalFailed } = await import("../utils/puzzleRemoval");
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([puzzle(11, 1, { source_types: ["custom"] })], 1, 1) }),
      "/practice/puzzles/11/attempt": () => ({ status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: null } }),
    });
    beginRemoval(11); // started from another overlay, or the page before this one
    renderPage();
    expect(await screen.findByText("#11")).toBeInTheDocument();
    fireEvent.click(screen.getByText("drop"));
    await flush();
    expect(calls.some((c) => c.path === "/practice/puzzles/11/attempt")).toBe(false);
    removalFailed(11); // the server refused: the puzzle is still there
    await flush();
    fireEvent.click(screen.getByText("drop"));
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/practice/puzzles/11/attempt")).toBe(true));
  });

  for (const status of [200, 404]) {
    it(`a puzzle removed from a deep link (${status}) leaves the queue underneath: no copy can be played`, async () => {
      const custom = puzzle(21, 1, { source_types: ["custom"] });
      const calls = stubFetch({
        "/practice/puzzles": () => ({ status: 200, body: serve([custom, puzzle(22, 1)], 1, 1) }), // the stale queue still lists 21
        "/practice/puzzles/21": () => ({ status: 200, body: { id: 21, fen: custom.fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["custom"], themes: [], is_repertoire: false, presentation_ply: null } }),
        "/practice/puzzles/21/attempt": () => ({ status: 404, body: { detail: "puzzle not found" } }),
        "/practice/puzzles/22/attempt": () => ({ status: 200, body: { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: null } }),
        "/puzzles/21": () => (status === 200 ? { status, body: { detail: "removed" } } : { status, body: { detail: "puzzle not found" } }),
      });
      renderPage("/practice?puzzle=21");
      expect(await screen.findByRole("dialog", { name: "Puzzle 21" })).toBeInTheDocument();
      expect(await screen.findAllByText("#21")).toHaveLength(2); // the overlay and the queue copy underneath
      fireEvent.click(screen.getByText("Remove puzzle"));
      fireEvent.click(screen.getByText("Yes, remove"));
      await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      // The queue has moved past the retired puzzle to the next one.
      expect(await screen.findByText("#22")).toBeInTheDocument();
      expect(screen.queryByText("#21")).not.toBeInTheDocument();
      fireEvent.click(screen.getByText("drop"));
      await vi.waitFor(() => expect(calls.some((c) => c.path === "/practice/puzzles/22/attempt")).toBe(true));
      expect(calls.some((c) => c.path === "/practice/puzzles/21/attempt")).toBe(false);
      await vi.waitFor(() => expect(getUnsavedAttempt()).toBeNull()); // 22's attempt saved normally
    });
  }

  it("a retired puzzle stays unplayable after leaving and re-entering the page, whenever the answer arrives", async () => {
    let finishDelete: (() => void) | null = null;
    const custom = puzzle(21, 1, { source_types: ["custom"] });
    const calls = stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: serve([custom], 1, 1) }),
      "/practice/puzzles/21": () => ({ status: 200, body: { id: 21, fen: custom.fen, solution_line: ["Ra8#"], color: "w", acceptance_map: null, source_types: ["custom"], themes: [], is_repertoire: false, presentation_ply: null } }),
      "/practice/puzzles/21/attempt": () => ({ status: 404, body: { detail: "puzzle not found" } }),
      "/puzzles/21": () => new Promise<Reply>((resolve) => (finishDelete = () => resolve({ status: 200, body: { detail: "removed" } }))),
    });
    const first = renderPage("/practice?puzzle=21");
    fireEvent.click(await screen.findByText("Remove puzzle"));
    fireEvent.click(screen.getByText("Yes, remove"));
    expect(await screen.findByText("Removing…")).toBeInTheDocument();
    first.unmount(); // the browser's back button: the request is still out

    renderPage("/practice?puzzle=21"); // and forward again, before the answer
    expect(await screen.findByRole("dialog", { name: "Puzzle 21" })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog")).getByText("drop")); // still pending: locked
    await flush();
    finishDelete!();
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); // gone: the copy closes itself
    expect(await screen.findByText("You're all caught up")).toBeInTheDocument(); // and the queue dropped it
    expect(calls.some((c) => c.path === "/practice/puzzles/21/attempt")).toBe(false);
    expect(getUnsavedAttempt()).toBeNull();
  });

  it("the header links wait while a removal is pending", async () => {
    let finishDelete: (() => void) | null = null;
    const custom = puzzle(21, 1, { source_types: ["custom"] });
    stubFetch({
      "/practice/puzzles": () => ({ status: 200, body: { ...serve([custom], 1), batch_id: null } }),
      "/puzzles/21": () => new Promise<Reply>((resolve) => (finishDelete = () => resolve({ status: 200, body: { detail: "removed" } }))),
    });
    render(
      <MemoryRouter initialEntries={["/practice?srs=all"]}>
        <Routes>
          <Route element={<Layout onLoggedOut={() => {}} />}>
            <Route path="/practice" element={<Practice />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("#21"));
    fireEvent.click(await screen.findByText("Remove puzzle"));
    fireEvent.click(screen.getByText("Yes, remove"));
    expect(await screen.findByText("Removing…")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Games" })).not.toBeInTheDocument(); // a disabled span instead
    finishDelete!();
    expect(await screen.findByRole("link", { name: "Games" })).toBeInTheDocument();
    expect(screen.queryByText("#21")).not.toBeInTheDocument();
  });
});
