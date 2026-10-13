import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EngineEval } from "../engine/useStockfish";
import type { GapCard } from "../repertoire";
import { AddLineModal } from "./AddLineModal";
import { seedFor } from "./lineSeed";

// A board whose "play" button drops the next queued move.
let nextDrop = { from: "e2", to: "e4" };
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position?: string; onPieceDrop?: (a: { sourceSquare: string; targetSquare: string }) => boolean } }) => (
    <div data-testid="board" data-position={options.position}>
      <button type="button" onClick={() => options.onPieceDrop?.({ sourceSquare: nextDrop.from, targetSquare: nextDrop.to })}>
        play
      </button>
    </div>
  ),
}));

const analyze = vi.fn();
vi.mock("../engine/useStockfish", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../engine/useStockfish")>();
  return {
    ...actual,
    useStockfish: () => {
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

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const GAP = "r1bqkbnr/ppp2ppp/2np4/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4";
const card = (over: Partial<GapCard> = {}): GapCard => ({
  fen: GAP,
  colour: "white",
  arrived_by: "d6",
  games: 5,
  wins: 2,
  draws: 1,
  losses: 2,
  score: 0.5,
  family: "Italian Game",
  context: { book_id: 1, book: "Italian", chapter_id: 1, chapter: "Giuoco", line_id: 1, line_name: "Main", expected: "Bc5", played: "d6", count: 5 },
  continuations: [],
  engine: { best_move: "c3", line: ["c3", "Be7", "d4", "exd4", "cxd4", "Nf6", "Nc3", "O-O", "O-O", "Bg4"], eval: 40, mate_in: null },
  last_played: null,
  stem: ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"],
  chapters: [
    { id: 1, title: "Giuoco", book_id: 1, book: "Italian" },
    { id: 9, title: "Other", book_id: 1, book: "Italian" },
  ],
  catch_all: { book: "Catch-all (white)", chapter: "Italian Game" },
  ...over,
});

const shownPly = () => Number(screen.getByTestId("solution-line").getAttribute("data-shown"));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  analyze.mockClear();
  nextDrop = { from: "e2", to: "e4" };
});

describe("the seed", () => {
  it("is the stem plus the engine's line, trimmed to end on the player's move, for both colours", () => {
    expect(seedFor(card(), 10, 60)).toEqual(["e4", "e5", "Nf3", "Nc6", "Bc4", "d6", "c3", "Be7", "d4", "exd4", "cxd4", "Nf6", "Nc3", "O-O", "O-O"]);
    expect(seedFor(card({ engine: null }), 10, 60)).toEqual(["e4", "e5", "Nf3", "Nc6", "Bc4", "d6"]);
    const black = card({ colour: "black", stem: ["d4"], engine: { best_move: "d5", line: ["d5", "c4", "e6", "Nc3"], eval: 0, mate_in: null } });
    expect(seedFor(black, 10, 60)).toEqual(["d4", "d5", "c4", "e6"]);
    expect(seedFor(black, 1, 60)).toEqual(["d4", "d5"]);
    expect(seedFor(card(), 10, 8)).toEqual(["e4", "e5", "Nf3", "Nc6", "Bc4", "d6", "c3"]); // the cap, then the trim
  });
});

describe("AddLineModal", () => {
  it("opens on the gap board with the whole seed listed, the context chapter preselected, and saves into the chosen chapter", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: { explore_engine_depth: 14 } }),
      "/repertoire/lines": () => ({ status: 201, body: { id: 77, chapter_id: 1, book_id: 1, rematch: { candidates: 1, matched: 1, no_match: 0, lines: 1 } } }),
    });
    const onAdded = vi.fn();
    const onClose = vi.fn();
    render(<AddLineModal card={card()} prefillPlies={10} maxPlies={60} onClose={onClose} onAdded={onAdded} />);
    expect(shownPly()).toBe(6);
    expect(screen.getByTestId("board")).toHaveAttribute("data-position", GAP);
    expect(screen.getByTestId("solution-line").textContent).toContain("7. Nc3 O-O 8. O-O");
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("1");
    expect(screen.getByLabelText("Line name")).toHaveValue("Italian Game — after d6");
    expect(screen.getByRole("button", { name: "Save line" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Book and chapter"), { target: { value: "9" } });
    fireEvent.change(screen.getByLabelText("Line name"), { target: { value: "  Italian: d6 sideline  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(onAdded).toHaveBeenCalled());
    const post = calls.find((c) => c.path === "/repertoire/lines");
    expect(post?.body).toEqual({ moves: ["e4", "e5", "Nf3", "Nc6", "Bc4", "d6", "c3", "Be7", "d4", "exd4", "cxd4", "Nf6", "Nc3", "O-O", "O-O"], line_name: "Italian: d6 sideline", chapter_id: 9 });
    expect(onClose).toHaveBeenCalled();
  });

  it("the catch-all is the last option and the default without a context chapter; Save waits for a move past the gap that ends on the player's", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: {} }),
      "/repertoire/lines": () => ({ status: 201, body: { id: 78, chapter_id: 5, book_id: 4, rematch: { candidates: 0, matched: 0, no_match: 0, lines: 0 } } }),
    });
    const c = card({ colour: "black", stem: ["d4"], engine: null, context: null, chapters: [], family: "Queen's Pawn", arrived_by: "d4", fen: "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1", catch_all: { book: "Catch-all (black)", chapter: "Queen's Pawn" } });
    render(<AddLineModal card={c} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("catch-all");
    expect(screen.getByRole("option", { name: "Catch-all (black) › Queen's Pawn (catch-all)" })).toBeInTheDocument();
    expect(screen.getByLabelText("Line name")).toHaveValue("Queen's Pawn — 1.d4");
    expect(screen.getByRole("button", { name: "Save line" })).toBeDisabled();
    expect(screen.getByText("Add at least one of your moves past the gap.")).toBeInTheDocument();
    expect(screen.getByText("Extend +1")).toBeEnabled();
    nextDrop = { from: "d7", to: "d5" };
    fireEvent.click(screen.getByText("play"));
    expect(screen.getByRole("button", { name: "Save line" })).toBeEnabled();
    nextDrop = { from: "c2", to: "c4" };
    fireEvent.click(screen.getByText("play"));
    expect(screen.getByRole("button", { name: "Save line" })).toBeDisabled();
    expect(screen.getByText("End on your move — Extend adds it.")).toBeInTheDocument();
    expect(screen.getByText("Extend +1")).toBeEnabled();
    nextDrop = { from: "e7", to: "e6" };
    fireEvent.click(screen.getByText("play"));
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(calls.some((x) => x.path === "/repertoire/lines")).toBe(true));
    expect(calls.find((x) => x.path === "/repertoire/lines")?.body).toEqual({ moves: ["d4", "d5", "c4", "e6"], line_name: "Queen's Pawn — 1.d4", catch_all: { colour: "black", family: "Queen's Pawn" } });
  });

  it("the destination defaults to the catch-all whenever the context chapter is not one, even with other chapters on offer", () => {
    stubFetch({ "/settings": () => ({ status: 200, body: {} }) });
    const others = [
      { id: 7, title: "Unrelated", book_id: 3, book: "Another course" },
      { id: 9, title: "Other", book_id: 1, book: "Italian" },
    ];
    const { unmount } = render(<AddLineModal card={card({ context: null, chapters: others })} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("catch-all");
    unmount();
    // The context chapter is tabiya-rooted (not among the destinations): the catch-all again.
    const tabiya = card({ context: { ...card().context!, chapter_id: 42, chapter: "Tabiya" }, chapters: others });
    const second = render(<AddLineModal card={tabiya} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("catch-all");
    second.unmount();
    // The context chapter is a destination: preselected, wherever it sits in the list.
    render(<AddLineModal card={card({ chapters: [...others, { id: 1, title: "Giuoco", book_id: 1, book: "Italian" }] })} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("1");
  });

  it("a duplicate and a conflict are shown in the modal and leave it open", async () => {
    let reply: Reply = { status: 409, body: { detail: "duplicate", duplicate: { line_id: 3, line_name: "Main", active: true, kind: "prefix" } } };
    stubFetch({ "/settings": () => ({ status: 200, body: {} }), "/repertoire/lines": () => reply });
    const onClose = vi.fn();
    render(<AddLineModal card={card()} prefillPlies={10} maxPlies={60} onClose={onClose} onAdded={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await screen.findByText("“Main” already teaches this and goes further.");
    reply = {
      status: 409,
      body: {
        detail: "activation_conflict",
        refusal: { line_id: 0, line_name: "x", chapter_title: "", fen: START, move: "Bb5", reason: "anchor", rivals: [{ line_id: 1, line_name: "Main", chapter_id: 1, chapter_title: "Giuoco", book_id: 1, book_title: "Italian", move: "Bc4" }] },
      },
    };
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await screen.findByText(/An active line prescribes a different move where this line plays Bb5: Italian › Giuoco › Main \(Bc4\)/);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save line" })).toBeEnabled();
  });
});
