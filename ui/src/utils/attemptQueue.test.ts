import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetProbesForTests, enqueue, hasPendingAttempt, isQueueModeAvailable, markCompleted, processPending, QUEUE_STORE } from "./attemptQueue";

function fakeLocks() {
  // A serialising fake: callbacks run one after another, like an exclusive lock.
  let chain: Promise<unknown> = Promise.resolve();
  return {
    request: (_name: string, cb: () => Promise<unknown>) => {
      const next = chain.then(cb);
      chain = next.catch(() => {});
      return next;
    },
  };
}

describe("attemptQueue", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.defineProperty(navigator, "locks", { value: fakeLocks(), configurable: true });
    _resetProbesForTests();
  });
  afterEach(() => {
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
    _resetProbesForTests();
    vi.restoreAllMocks();
  });

  it("is in queue mode only with both storage and locks", () => {
    expect(isQueueModeAvailable()).toBe(true);
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
    _resetProbesForTests();
    expect(isQueueModeAvailable()).toBe(false);
  });

  it("enqueue commits to localStorage and is idempotent on attempt_id", async () => {
    await enqueue({ attempt_id: "a1", puzzle_id: 7, solved: true, moves_played: "Nf3,Bc4", session_id: "s1" });
    await enqueue({ attempt_id: "a1", puzzle_id: 7, solved: true, moves_played: "Nf3,Bc4", session_id: "s1" });
    await enqueue({ attempt_id: "a2", puzzle_id: 8, solved: false, moves_played: "e4", session_id: "s2" });
    const stored = JSON.parse(window.localStorage.getItem(QUEUE_STORE)!);
    expect(stored.map((r: { attempt_id: string }) => r.attempt_id)).toEqual(["a1", "a2"]);
    expect(stored[0]).toMatchObject({ puzzle_id: 7, solved: true, attempt_count: 0, last_attempted_at: null });
    expect(hasPendingAttempt("a1")).toBe(true);
    expect(hasPendingAttempt("zz")).toBe(false);
  });

  it("enqueue throws without locks so the caller falls back to blocking mode", async () => {
    Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
    _resetProbesForTests();
    await expect(enqueue({ attempt_id: "a1", puzzle_id: 7, solved: true, moves_played: "" })).rejects.toThrow();
    expect(window.localStorage.getItem(QUEUE_STORE)).toBeNull();
  });

  it("markCompleted removes one record and leaves the rest", async () => {
    await enqueue({ attempt_id: "a1", puzzle_id: 7, solved: true, moves_played: "" });
    await enqueue({ attempt_id: "a2", puzzle_id: 8, solved: true, moves_played: "" });
    await markCompleted("a1");
    await markCompleted("missing");
    expect(hasPendingAttempt("a1")).toBe(false);
    expect(hasPendingAttempt("a2")).toBe(true);
  });

  it("processPending drains successes, keeps failures with a bumped counter", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    await enqueue({ attempt_id: "ok", puzzle_id: 1, solved: true, moves_played: "" });
    await enqueue({ attempt_id: "bad", puzzle_id: 2, solved: false, moves_played: "" });
    const handled: string[] = [];
    const resolved: string[] = [];
    await processPending(
      async (r) => {
        handled.push(r.attempt_id);
        if (r.attempt_id === "bad") throw new Error("network");
      },
      (r) => resolved.push(r.attempt_id),
    );
    expect(handled).toEqual(["ok", "bad"]);
    expect(resolved).toEqual(["ok"]);
    expect(hasPendingAttempt("ok")).toBe(false);
    const stored = JSON.parse(window.localStorage.getItem(QUEUE_STORE)!);
    expect(stored).toHaveLength(1);
    expect(stored[0]).toMatchObject({ attempt_id: "bad", attempt_count: 1 });
    expect(typeof stored[0].last_attempted_at).toBe("number");
  });

  it("keeps the attempt's sequence and segment with the record, and a record without them is still valid", async () => {
    await enqueue({ attempt_id: "a1", puzzle_id: 7, solved: false, moves_played: "Nf3", session_id: "s1", presentation_ply: 4, attempt_seq: 1 });
    await enqueue({ attempt_id: "a2", puzzle_id: 7, solved: true, moves_played: "Nf3,Bc4", session_id: "s1", presentation_ply: 4, attempt_seq: 2 });
    window.localStorage.setItem(QUEUE_STORE, JSON.stringify([...JSON.parse(window.localStorage.getItem(QUEUE_STORE)!), { attempt_id: "old", puzzle_id: 7, solved: true, moves_played: "", enqueued_at: 1, last_attempted_at: null, attempt_count: 0 }]));
    await enqueue({ attempt_id: "a3", puzzle_id: 8, solved: true, moves_played: "" });
    const stored = JSON.parse(window.localStorage.getItem(QUEUE_STORE)!) as { attempt_id: string; attempt_seq?: number; presentation_ply?: number }[];
    expect(stored.map((r) => [r.attempt_id, r.attempt_seq ?? null, r.presentation_ply ?? null])).toEqual([
      ["a1", 1, 4],
      ["a2", 2, 4],
      ["old", null, null],
      ["a3", null, null],
    ]);
    window.localStorage.setItem(QUEUE_STORE, JSON.stringify([{ ...stored[0], attempt_seq: "one" }]));
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    await enqueue({ attempt_id: "a4", puzzle_id: 8, solved: true, moves_played: "" });
    expect(err).toHaveBeenCalledTimes(1); // a sequence that is not a number is a malformed record
    expect((JSON.parse(window.localStorage.getItem(QUEUE_STORE)!) as { attempt_id: string }[]).map((r) => r.attempt_id)).toEqual(["a4"]);
  });

  it("dead-letters a malformed record instead of wedging the queue", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    window.localStorage.setItem(QUEUE_STORE, JSON.stringify([{ attempt_id: "a1", puzzle_id: "not a number" }]));
    await enqueue({ attempt_id: "a2", puzzle_id: 3, solved: true, moves_played: "" });
    expect(err).toHaveBeenCalledTimes(1);
    const stored = JSON.parse(window.localStorage.getItem(QUEUE_STORE)!);
    expect(stored.map((r: { attempt_id: string }) => r.attempt_id)).toEqual(["a2"]);
  });
});
