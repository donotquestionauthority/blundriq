import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "./Home";
import { POLL_MS, START_WAIT_MS, ago, plural } from "../home";
import type { HomePage, RunStatus } from "../home";

const page = (over: Partial<HomePage> = {}): HomePage => ({
  since: "2026-09-20T14:00:00+00:00",
  today: "2026-09-22",
  timezone: "America/New_York",
  new_blunders: 2,
  deviations_since: "2026-09-21T10:00:00+00:00",
  new_deviations: 1,
  puzzles: { due: 14, solved_today: 3, target: 10, streak: 4 },
  games: { today: 0, week: 5, target: 1, streak: 2 },
  activity: { last_1: 1, last_7: 6, last_30: 21, total: 1234 },
  pipeline: { last_run: null, last_ok_at: new Date(Date.now() - 23 * 60_000).toISOString(), failed: [] },
  ...over,
});

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
const run = (status: RunStatus, startedMinutesAgo: number, failed_step: string | null = null): NonNullable<HomePage["pipeline"]["last_run"]> => ({
  started_at: minutesAgo(startedMinutesAgo),
  status,
  failed_step,
});

function stub(body: HomePage | (() => Promise<HomePage>)) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url.replace(/^.*\/api/, ""));
      const b = typeof body === "function" ? await body() : body;
      return { ok: true, status: 200, json: async () => b };
    }),
  );
  return calls;
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Home helpers", () => {
  it("says how long ago, coarsely", () => {
    const now = Date.parse("2026-09-22T12:00:00Z");
    expect(ago(null, now)).toBe("never");
    expect(ago("2026-09-22T11:59:40Z", now)).toBe("just now");
    expect(ago("2026-09-22T11:37:00Z", now)).toBe("23 minutes ago");
    expect(ago("2026-09-22T10:59:00Z", now)).toBe("1 hour ago");
    expect(ago("2026-09-19T12:00:00Z", now)).toBe("3 days ago");
    expect(plural(1, "new deviation")).toBe("1 new deviation");
    expect(plural(2, "day")).toBe("2 days");
  });
});

describe("Home page", () => {
  it("shows the day's numbers, the streaks, and links the new blunders with the visit boundary", async () => {
    const calls = stub(page());
    renderPage();
    expect(await screen.findByText("14")).toBeInTheDocument();
    expect(calls).toEqual(["/home"]);
    expect(screen.getByText("3 of 10 solved today")).toBeInTheDocument();
    expect(screen.getByText("4-day streak")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Practice →" })).toHaveAttribute("href", "/practice");
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("5 games this week — 1 more game today keeps the streak")).toBeInTheDocument();
    expect(screen.getByText("2-day streak")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2 new recurring blunders" })).toHaveAttribute("href", "/blunders");
    expect(screen.getAllByText(/last looked/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: "1 new deviation pattern" })).toHaveAttribute("href", "/deviations");
    expect(calls).toEqual(["/home"]); // Home reads; it never marks anything
    expect(screen.getByText("Last successful run 23 minutes ago")).toBeInTheDocument();
    expect(screen.getByText("Last run: never")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("marks a met target as done and a first visit as such", async () => {
    stub(page({ since: null, deviations_since: null, puzzles: { due: 0, solved_today: 10, target: 10, streak: 1 }, games: { today: 1, week: 1, target: 1, streak: 1 } }));
    renderPage();
    expect(await screen.findByText("10 of 10 solved today — done")).toBeInTheDocument();
    expect(screen.getByText("1 game this week — today's game is in")).toBeInTheDocument();
    expect(screen.getByText(/Nothing to compare against yet/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /blunder/ })).not.toBeInTheDocument();
  });

  it("counts each list from its own first look", async () => {
    stub(page({ since: null, new_deviations: 3 }));
    renderPage();
    expect(await screen.findByText("Open Blunders once to start counting")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "3 new deviation patterns" })).toHaveAttribute("href", "/deviations");
  });

  it("says when nothing is new, and hides the game target when there is none", async () => {
    stub(page({ new_blunders: 0, games: { today: 0, week: 0, target: 0, streak: 0 } }));
    renderPage();
    expect(await screen.findByText("No new recurring blunders")).toBeInTheDocument();
    expect(screen.getByText("No daily target set")).toBeInTheDocument();
    expect(screen.getByText("0 games this week")).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar")).toHaveLength(1); // puzzles only
  });

  it("invites a first game to start a streak rather than keep one", async () => {
    stub(page({ games: { today: 0, week: 0, target: 1, streak: 0 } }));
    renderPage();
    expect(await screen.findByText("0 games this week — 1 more game today starts the streak")).toBeInTheDocument();
    expect(screen.getAllByText("No streak yet")).toHaveLength(1);
  });

  it("shows a red line per failed step, with the error", async () => {
    stub(page({ pipeline: { last_run: run("failed", 180, "analyze"), last_ok_at: null, failed: [{ step: "analyze", started_at: minutesAgo(180), error: "engine missing" }] } }));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("analyze failed 3 hours ago: engine missing");
    expect(screen.getByText("Last successful run never")).toBeInTheDocument();
    expect(screen.getByText("Last run: started 3 hours ago · failed (analyze)")).toBeInTheDocument();
  });

  it("reports a failed load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500, statusText: "boom", json: async () => ({ detail: "database away" }) })));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("database away");
  });
});

describe("the activity strip", () => {
  it("shows the four counts", async () => {
    stub(page());
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );
    const strip = await screen.findByLabelText("Played");
    expect(strip.textContent).toContain("24 h 1");
    expect(strip.textContent).toContain("7 d 6");
    expect(strip.textContent).toContain("30 d 21");
    expect(strip.textContent).toContain("all 1234");
  });
});

describe("the pipeline block", () => {
  /** `fetch` that answers GET /home from a queue of pages (the last one repeats) and POST
   *  /home/pipeline/run with 202, or the given failure. */
  function sequence(pages: HomePage[], post: { status: number; body: unknown } = { status: 202, body: { requested_at: new Date().toISOString() } }) {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = url.replace(/^.*\/api/, "");
        calls.push(`${init?.method ?? "GET"} ${path}`);
        if (init?.method === "POST") return { ok: post.status < 400, status: post.status, statusText: "x", json: async () => post.body };
        const body = pages.length > 1 ? pages.shift()! : pages[0];
        return { ok: true, status: 200, json: async () => body };
      }),
    );
    return calls;
  }
  const withRun = (r: HomePage["pipeline"]["last_run"]) => page({ pipeline: { last_run: r, last_ok_at: minutesAgo(70), failed: [] } });
  const gets = (calls: string[]) => calls.filter((c) => c.startsWith("GET")).length;
  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  afterEach(() => {
    vi.useRealTimers();
  });

  it("words every status of the latest run", async () => {
    stub(withRun(run("incomplete", 90)));
    renderPage();
    expect(await screen.findByText("Last run: started 1 hour ago · did not finish")).toBeInTheDocument();
    expect(screen.getByText("Last successful run 1 hour ago")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("disables the button and polls while a run the schedule started is going, and stops on its result", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = sequence([withRun(run("running", 3)), withRun(run("running", 3)), withRun(run("ok", 3))]);
    renderPage();
    expect(await screen.findByText("Last run: started 3 minutes ago · running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
    expect(gets(calls)).toBe(1);
    await tick(POLL_MS);
    expect(gets(calls)).toBe(2); // still running (a between-steps read looks the same to the page)
    await tick(POLL_MS);
    expect(gets(calls)).toBe(3);
    expect(await screen.findByText("Last run: started 3 minutes ago · ok")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
    await tick(POLL_MS * 4);
    expect(gets(calls)).toBe(3); // a result stops the polling
  });

  it("requests a run, waits for its first row, follows it, and shows where it failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const before = run("ok", 50);
    // The request is answered as of two minutes ago, so the rows that follow are newer than it.
    const calls = sequence([withRun(before), withRun(before), withRun(run("running", 1)), withRun(run("running", 1)), withRun(run("failed", 1, "analyze"))], {
      status: 202,
      body: { requested_at: minutesAgo(2) },
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Requested…" })).toBeDisabled();
    expect(calls.filter((c) => c === "POST /home/pipeline/run")).toHaveLength(1);
    await tick(POLL_MS); // the old run is still the newest: keep waiting
    expect(screen.getByRole("button", { name: "Requested…" })).toBeDisabled();
    await tick(POLL_MS); // the run's first row
    expect(await screen.findByText(/^Last run: started .* · running$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
    await tick(POLL_MS); // between steps, still running
    await tick(POLL_MS); // the result
    expect(await screen.findByText(/^Last run: started .* · failed \(analyze\)$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
    const n = gets(calls);
    await tick(POLL_MS * 4);
    expect(gets(calls)).toBe(n);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("gives up waiting for a first row after the start wait, and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = sequence([withRun(run("ok", 50))]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Requested…" })).toBeDisabled();
    await tick(START_WAIT_MS - POLL_MS);
    expect(screen.getByRole("button", { name: "Requested…" })).toBeDisabled();
    await tick(POLL_MS + 1);
    expect(await screen.findByRole("status")).toHaveTextContent("No run started — check the Actions tab.");
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
    const n = gets(calls);
    await tick(POLL_MS * 4);
    expect(gets(calls)).toBe(n);
  });

  it("shows the server's reason when the request is refused", async () => {
    sequence([withRun(run("ok", 50))], { status: 502, body: { detail: "GitHub refused the dispatch (HTTP 401)" } });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("GitHub refused the dispatch (HTTP 401)");
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("clears its timers on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = sequence([withRun(run("running", 3))]);
    const { unmount } = renderPage();
    await screen.findByText("Last run: started 3 minutes ago · running");
    unmount();
    await tick(POLL_MS * 3);
    expect(gets(calls)).toBe(1);
  });
});
