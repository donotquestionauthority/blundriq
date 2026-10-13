import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EngineEval } from "../engine/useStockfish";
import { CreatePuzzleModal } from "./CreatePuzzleModal";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position?: string } }) => <div data-testid="board" data-position={options.position} />,
}));

// The engine is a fake; the test only cares that a seeded modal enables it and an ordinary one does not.
const analyze = vi.fn();
const enabledWith: boolean[] = [];
vi.mock("../engine/useStockfish", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../engine/useStockfish")>();
  return {
    ...actual,
    useStockfish: ({ enabled }: { enabled: boolean }) => {
      enabledWith.push(enabled);
      const [s] = useState<{ ready: boolean; evalState: EngineEval | null }>({ ready: true, evalState: null });
      return { ...s, analyze, reset: vi.fn(), stop: vi.fn() };
    },
  };
});

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

const GAP = "r1bqkbnr/ppp2ppp/2np4/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  enabledWith.length = 0;
});

describe("CreatePuzzleModal with a gap source", () => {
  it("seeds the title, notes and line, opens at the starting board, enables the engine and sends the gap tag", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: { explore_engine_depth: 20 } }),
      "/puzzles": () => ({ status: 200, body: { id: 7, visible: false } }),
    });
    const onCreated = vi.fn();
    const onClose = vi.fn();
    render(
      <CreatePuzzleModal
        source={{ source: "gap", fen: GAP, color: "w", engineLine: ["c3", "Nf6", "d4", "exd4", "cxd4"], prefillPlies: 3, title: "Italian: after d6", description: "The engine's line." }}
        onClose={onClose}
        onCreated={onCreated}
      />,
    );
    expect(screen.getByDisplayValue("Italian: after d6")).toBeInTheDocument();
    expect(screen.getByDisplayValue("The engine's line.")).toBeInTheDocument();
    expect(screen.getByTestId("solution-line").textContent).toBe("4. c3 Nf6 5. d4");
    expect(screen.getByTestId("board").getAttribute("data-position")).toBe(GAP);
    expect(enabledWith[0]).toBe(true);
    expect(screen.getByText("+2 plies")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create puzzle" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 7, visible: false }));
    const post = calls.find((c) => c.path === "/puzzles" && c.method === "POST");
    expect(post?.body).toEqual({ fen: GAP, solution_line: ["c3", "Nf6", "d4"], color: "w", source_types: ["gap"], title: "Italian: after d6", description: "The engine's line." });
    expect(onClose).toHaveBeenCalled();
  });

  it("a visible save is reported as such", async () => {
    stubFetch({
      "/settings": () => ({ status: 200, body: {} }),
      "/puzzles": () => ({ status: 200, body: { id: 8, visible: true } }),
    });
    const onCreated = vi.fn();
    render(<CreatePuzzleModal source={{ source: "gap", fen: GAP, color: "w", engineLine: ["c3"], prefillPlies: 10 }} onClose={vi.fn()} onCreated={onCreated} />);
    fireEvent.click(screen.getByRole("button", { name: "Create puzzle" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 8, visible: true }));
  });

  it("an ordinary source neither seeds nor runs the engine", () => {
    stubFetch({});
    render(<CreatePuzzleModal source={{ source: "blunder", fen: GAP, color: "w" }} onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(screen.getByTestId("solution-line").textContent).toContain("Click or drag");
    expect(enabledWith[0]).toBe(false);
    expect(screen.queryByText("+2 plies")).toBeNull();
    expect(screen.getByRole("button", { name: "Create puzzle" })).toBeDisabled();
  });

  it("a 409 is shown in the modal and leaves it open", async () => {
    stubFetch({
      "/settings": () => ({ status: 200, body: {} }),
      "/puzzles": () => ({ status: 409, body: { detail: "You already have a puzzle for this position" } }),
    });
    const onClose = vi.fn();
    render(<CreatePuzzleModal source={{ source: "gap", fen: GAP, color: "w", engineLine: ["c3"], prefillPlies: 10 }} onClose={onClose} onCreated={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Create puzzle" }));
    await screen.findByText("You already have a puzzle for this position");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create puzzle" })).toBeEnabled();
  });
});
