import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreatePuzzleModal } from "./CreatePuzzleModal";

// A board whose "play" button drops c3 (the move to make from the fixture board).
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position?: string; onPieceDrop?: (a: { sourceSquare: string; targetSquare: string }) => boolean } }) => (
    <div data-testid="board" data-position={options.position}>
      <button type="button" onClick={() => options.onPieceDrop?.({ sourceSquare: "c2", targetSquare: "c3" })}>
        play
      </button>
    </div>
  ),
}));

type Reply = { status: number; body: unknown };
function stubFetch(routes: Record<string, (method: string, body: Record<string, unknown> | null) => Reply>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [rawPath] = url.replace(/^.*\/api/, "").split("?");
      const call = { path: rawPath, method: init?.method ?? "GET", body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = h(call.method, call.body);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const BOARD = "r1bqkbnr/ppp2ppp/2np4/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("CreatePuzzleModal", () => {
  it("opens empty, saves the line played with its source tag, and reports the save's visibility", async () => {
    const calls = stubFetch({ "/puzzles": () => ({ status: 200, body: { id: 8, visible: true } }) });
    const onCreated = vi.fn();
    render(<CreatePuzzleModal source={{ source: "blunder", fen: BOARD, color: "w" }} onClose={vi.fn()} onCreated={onCreated} />);
    expect(screen.getByTestId("solution-line").textContent).toContain("Click or drag");
    expect(screen.queryByText(/Extend/)).toBeNull();
    expect(screen.getByRole("button", { name: "Create puzzle" })).toBeDisabled();
    fireEvent.click(screen.getByText("play"));
    fireEvent.click(screen.getByRole("button", { name: "Create puzzle" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 8, visible: true }));
    expect(calls[0].body).toEqual({ fen: BOARD, solution_line: ["c3"], color: "w", source_types: ["blunder"] });
  });

  it("a 409 is shown in the modal and leaves it open", async () => {
    stubFetch({ "/puzzles": () => ({ status: 409, body: { detail: "You already have a puzzle for this position" } }) });
    const onClose = vi.fn();
    render(<CreatePuzzleModal source={{ source: "scout", fen: BOARD, color: "w" }} onClose={onClose} onCreated={vi.fn()} />);
    fireEvent.click(screen.getByText("play"));
    fireEvent.click(screen.getByRole("button", { name: "Create puzzle" }));
    await screen.findByText("You already have a puzzle for this position");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create puzzle" })).toBeEnabled();
  });
});
