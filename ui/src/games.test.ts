/** The settings-row preference writes: serialised and coalesced against an API that only
 *  replaces the whole row. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setReviewPref } from "./games";

type Row = Record<string, unknown>;

/** A settings API whose reads and writes are held until released, in any order. */
function heldApi(initial: Row) {
  let row = { ...initial };
  const releases: Array<() => void> = [];
  const hold = <T>(value: () => T) => new Promise<T>((resolve) => releases.push(() => resolve(value())));
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init?: RequestInit) => {
      const ok = (body: Row) => ({ ok: true, status: 200, json: async () => body });
      if (init?.method === "PUT") {
        return hold(() => {
          row = JSON.parse(String(init.body)) as Row;
          return ok(row);
        });
      }
      return hold(() => ok({ ...row }));
    }),
  );
  return { get row() { return row; }, release: () => releases.shift()?.(), pending: () => releases.length };
}

afterEach(() => vi.unstubAllGlobals());

describe("setReviewPref", () => {
  it("two patches issued while a save is in flight both land, whatever the completion order", async () => {
    const api = heldApi({ review_default_mode: "review", review_show_timer: true, other: 7 });
    const a = setReviewPref({ review_default_mode: "learn" });
    const b = setReviewPref({ review_show_timer: false });
    // The first read and write; the second patch waits for them rather than reading the stale row.
    await vi.waitFor(() => expect(api.pending()).toBe(1));
    api.release(); // read
    await vi.waitFor(() => expect(api.pending()).toBe(1));
    api.release(); // write of the first patch
    await a;
    await vi.waitFor(() => expect(api.pending()).toBe(1));
    api.release(); // the second patch's read sees the written row
    await vi.waitFor(() => expect(api.pending()).toBe(1));
    api.release();
    await b;
    expect(api.row).toEqual({ review_default_mode: "learn", review_show_timer: false, other: 7 });
  });
  it("a failed write rejects only the calls it carried; a later patch still goes out", async () => {
    let calls = 0;
    let row: Row = { review_default_mode: "review", review_show_timer: true };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          calls += 1;
          if (calls === 1) return { ok: false, status: 500, statusText: "boom", json: async () => ({ detail: "boom" }) };
          row = JSON.parse(String(init.body)) as Row;
        }
        return { ok: true, status: 200, json: async () => ({ ...row }) };
      }),
    );
    await expect(setReviewPref({ review_default_mode: "learn" })).rejects.toBeTruthy();
    await setReviewPref({ review_show_timer: false });
    expect(row).toEqual({ review_default_mode: "review", review_show_timer: false });
  });
});
