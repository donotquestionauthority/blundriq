import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "./Home";
import { ago, plural } from "../home";
import type { HomePage } from "../home";

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
  pipeline: { last_ok_at: new Date(Date.now() - 23 * 60_000).toISOString(), failed: [] },
  ...over,
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
    expect(screen.getByText("Pipeline: last successful run 23 minutes ago.")).toBeInTheDocument();
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
    stub(page({ pipeline: { last_ok_at: null, failed: [{ step: "analyze", started_at: new Date(Date.now() - 3 * 3_600_000).toISOString(), error: "engine missing" }] } }));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("analyze failed 3 hours ago: engine missing");
    expect(screen.getByText("Pipeline: last successful run never.")).toBeInTheDocument();
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
