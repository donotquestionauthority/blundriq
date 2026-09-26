/**
 * The layer with the real engine hook under StrictMode, which runs every effect, its cleanup and
 * the effect again: the worker effect tears down and recreates the engine, and the seeding effect
 * must run again after it — a ran-once guard would leave the second worker with nothing to analyse.
 */
import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExploreLayer } from "./ExploreLayer";

vi.mock("react-chessboard", () => ({ Chessboard: ({ options }: { options: { position?: string } }) => <div data-testid="board" data-position={options.position} /> }));

class MockWorker {
  static instances: MockWorker[] = [];
  posted: string[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  terminated = false;
  constructor() {
    MockWorker.instances.push(this);
  }
  postMessage(cmd: string) {
    this.posted.push(cmd);
  }
  terminate() {
    this.terminated = true;
  }
  emit(line: string) {
    this.onmessage?.({ data: line });
  }
}
const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

beforeEach(() => {
  MockWorker.instances = [];
  vi.stubGlobal("Worker", MockWorker as unknown as typeof Worker);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({ explore_engine_depth: 20 }) })));
});
afterEach(() => vi.unstubAllGlobals());

describe("ExploreLayer under StrictMode", () => {
  it("ends with one live worker and the seed searched exactly once, at the saved depth", async () => {
    render(
      <StrictMode>
        <ExploreLayer fen={START} orientation="white" onClose={() => {}} />
      </StrictMode>,
    );
    expect(MockWorker.instances).toHaveLength(2);
    const live = MockWorker.instances.filter((w) => !w.terminated);
    expect(live).toHaveLength(1);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 150)); // the settings answer and the debounce
    });
    act(() => live[0].emit("uciok"));
    act(() => live[0].emit("readyok"));
    expect(live[0].posted.filter((c) => c.startsWith("position "))).toEqual([`position fen ${START}`]);
    expect(live[0].posted.filter((c) => c.startsWith("go "))).toEqual(["go depth 20"]);
    expect(screen.getByTestId("board").dataset.position).toBe(START);
  });
});
