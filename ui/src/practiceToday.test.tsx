import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TodayCount from "./components/TodayCount";
import { ATTEMPT_SAVED_EVENT, recordAttempt } from "./practice";
import type { PracticeToday } from "./practice";
import { _resetPracticeTodayForTests, refreshPracticeToday } from "./practiceToday";

const today = (over: Partial<PracticeToday> = {}): PracticeToday => ({
  date: "2026-10-08",
  solved: 4,
  target: 10,
  next_day_at: new Date(Date.now() + 3_600_000).toISOString(),
  now: new Date(Date.now()).toISOString(),
  ...over,
});

/** An answer whose day ends `ms` after the server's clock, wherever this browser's clock is. */
const endsIn = (ms: number, serverNow = Date.now(), over: Partial<PracticeToday> = {}) =>
  today({ now: new Date(serverNow).toISOString(), next_day_at: new Date(serverNow + ms).toISOString(), ...over });

type Pending = { path: string; resolve: (status: number, body: unknown) => void };

/** A fetch stub whose answers the test releases one by one, in any order. */
function heldFetch() {
  const pending: Pending[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string) =>
        new Promise((done) => {
          pending.push({
            path: url.replace(/^.*\/api/, "").split("?")[0],
            resolve: (status, body) => done({ ok: status < 400, status, statusText: String(status), json: async () => body }),
          });
        }),
    ),
  );
  return pending;
}

const reads = (pending: Pending[]) => pending.filter((p) => p.path === "/practice/today");
const flush = () => act(async () => {});
const status = () => screen.queryByRole("status");

describe("today's puzzle count", () => {
  beforeEach(() => _resetPracticeTodayForTests());
  afterEach(() => {
    _resetPracticeTodayForTests();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows solved toward the target and nothing else, with a tick and the met tone once the target is reached", async () => {
    const pending = heldFetch();
    render(<TodayCount variant="toolbar" />);
    expect(status()).toBeNull(); // unknown until read
    reads(pending)[0].resolve(200, today());
    await flush();
    expect(screen.getByRole("status", { name: "Puzzles today: 4 of 10 solved" })).toHaveTextContent(/^4 \/ 10 solved$/);
    expect(screen.queryByText(/✓/)).toBeNull();

    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    reads(pending)[1].resolve(200, today({ solved: 10 }));
    await flush();
    const met = screen.getByRole("status", { name: "Puzzles today: 10 of 10 solved" });
    expect(met).toHaveTextContent(/^✓ 10 \/ 10 solved$/);
    expect(met.querySelector(".text-emerald-600")).not.toBeNull();
  });

  it("two presentations share one read, and an acknowledged attempt updates both", async () => {
    const pending = heldFetch();
    render(
      <>
        <TodayCount variant="toolbar" quiet />
        <TodayCount variant="dialog" />
      </>,
    );
    expect(reads(pending)).toHaveLength(1);
    reads(pending)[0].resolve(200, today());
    await flush();
    expect(screen.getAllByText(/4 \/ 10/)).toHaveLength(2);
    expect(screen.getAllByRole("status")).toHaveLength(1); // the covered copy does not announce
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    expect(reads(pending)).toHaveLength(2);
    reads(pending)[1].resolve(200, today({ solved: 5 }));
    await flush();
    expect(screen.getAllByText(/5 \/ 10/)).toHaveLength(2);
  });

  it("the newest read wins: an older answer arriving last is dropped", async () => {
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    const [older, newer] = reads(pending);
    newer.resolve(200, today({ solved: 6 }));
    await flush();
    older.resolve(200, today({ solved: 4 }));
    await flush();
    expect(status()).toHaveTextContent(/^6 \/ 10 solved$/);
  });

  it("an older read that fails after a newer one answered does not wipe the newer count", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    const [older, newer] = reads(pending);
    newer.resolve(200, today({ solved: 6 }));
    await flush();
    older.resolve(500, { detail: "boom" });
    await flush();
    expect(status()).toHaveTextContent("6 / 10");
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("a failed read shows nothing rather than a stale count, and the next trigger reads again", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    reads(pending)[0].resolve(200, today());
    await flush();
    expect(status()).not.toBeNull();
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    reads(pending)[1].resolve(500, { detail: "boom" });
    await flush();
    expect(status()).toBeNull();
    expect(console.warn).toHaveBeenCalledWith("Today's puzzle count could not be read (ApiError)"); // the class, never the message
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    reads(pending)[2].resolve(200, today({ solved: 5 }));
    await flush();
    expect(status()).toHaveTextContent("5 / 10");
  });

  it("reads again when the tab becomes visible", async () => {
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    reads(pending)[0].resolve(200, today());
    await flush();
    const visibility = vi.spyOn(document, "visibilityState", "get");
    visibility.mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(reads(pending)).toHaveLength(1);
    visibility.mockReturnValue("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(reads(pending)).toHaveLength(2);
  });

  it("a visible tab reads once, just after the start of the next day, and not before", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    reads(pending)[0].resolve(200, endsIn(5000));
    await flush();
    await act(async () => vi.advanceTimersByTime(5500)); // the boundary, not yet a second past it
    expect(reads(pending)).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(600));
    expect(reads(pending)).toHaveLength(2);
    reads(pending)[1].resolve(200, endsIn(86_400_000, Date.now(), { date: "2026-10-09", solved: 0 }));
    await flush();
    expect(status()).toHaveTextContent(/^0 \/ 10 solved$/);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(reads(pending)).toHaveLength(2);
  });

  it("the rollover is timed by the server's clock: a browser two hours ahead neither reads early nor loops", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    const serverNow = Date.now() - 2 * 3_600_000; // this clock thinks midnight passed an hour ago
    reads(pending)[0].resolve(200, endsIn(3_600_000, serverNow));
    await flush();
    await act(async () => vi.advanceTimersByTime(3_599_000));
    expect(reads(pending)).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(3000));
    expect(reads(pending)).toHaveLength(2);
  });

  it("a wait beyond setTimeout's ceiling is clamped, not fired at once", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const pending = heldFetch();
    render(<TodayCount variant="dialog" />);
    reads(pending)[0].resolve(200, endsIn(40 * 86_400_000));
    await flush();
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(reads(pending)).toHaveLength(1);
  });

  it("when the last presentation leaves, the timer and listeners go, and a read still in flight answers no one", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const pending = heldFetch();
    const view = render(<TodayCount variant="dialog" />);
    reads(pending)[0].resolve(200, endsIn(5000));
    await flush();
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT)); // a read goes out ...
    view.unmount(); // ... and everyone leaves before it lands
    reads(pending)[1].resolve(200, endsIn(1000, Date.now(), { solved: 9 }));
    await flush();
    expect(vi.getTimerCount()).toBe(0);
    window.dispatchEvent(new Event(ATTEMPT_SAVED_EVENT));
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => vi.advanceTimersByTime(10_000));
    expect(reads(pending)).toHaveLength(2);
    await refreshPracticeToday(); // nothing showing it: no read
    expect(reads(pending)).toHaveLength(2);

    render(<TodayCount variant="dialog" />); // a new visit starts from nothing, not the disowned answer
    expect(status()).toBeNull();
    expect(reads(pending)).toHaveLength(3);
  });

  it("recordAttempt announces an acknowledged attempt and never a failed one", async () => {
    const pending = heldFetch();
    const heard = vi.fn();
    window.addEventListener(ATTEMPT_SAVED_EVENT, heard);
    try {
      const body = { solved: true, moves_played: "Ra8#", attempt_id: "a", session_id: "s", presentation_ply: null };
      const failed = recordAttempt(11, body);
      pending[0].resolve(500, { detail: "boom" });
      await expect(failed).rejects.toThrow();
      expect(heard).not.toHaveBeenCalled();
      const saved = recordAttempt(11, body);
      pending[1].resolve(200, { detail: "attempt recorded", solved: true, attempt_summary: { total: 1, solved: 1, streak: 1 }, srs: null });
      await saved;
      expect(heard).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(ATTEMPT_SAVED_EVENT, heard);
    }
  });
});
