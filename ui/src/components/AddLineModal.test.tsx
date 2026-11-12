import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EngineEval } from "../engine/useStockfish";
import type { GapCard } from "../repertoire";
import { AddLineModal, SAVE_BOUND_MS } from "./AddLineModal";
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
/** `"reject"`: the request fails without an answer (fetch rejects); `"hang"`: no answer until the
 *  caller aborts it. */
type Outcome = Reply | "reject" | "hang";
function stubFetch(routes: Record<string, (method: string, body: Record<string, unknown> | null) => Outcome | Promise<Reply>>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const [rawPath] = url.replace(/^.*\/api/, "").split("?");
      const call = { path: rawPath, method: init?.method ?? "GET", body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = await h(call.method, call.body);
      if (r === "reject") throw new TypeError("Load failed");
      if (r === "hang")
        return new Promise((_, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
        });
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
  catch_all: { book: "Catch-all (white)", chapter: "Italian Game" },
  ...over,
});
const DESTINATIONS = [
  { id: 1, title: "Giuoco", book_id: 1, book: "Italian" },
  { id: 9, title: "Other", book_id: 1, book: "Italian" },
];

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
    render(<AddLineModal card={card()} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={onClose} onAdded={onAdded} />);
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

  it("names the line by the family alone when the context line was followed to its end (no played move)", () => {
    stubFetch({ "/settings": () => ({ status: 200, body: {} }) });
    const ended = card({ context: { book_id: 1, book: "Italian", chapter_id: 1, chapter: "Giuoco", line_id: 1, line_name: "Short", expected: null, played: null, count: 1 } });
    render(<AddLineModal card={ended} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Line name")).toHaveValue("Italian Game — 1.d6");
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("1"); // the chapter is still the destination
    cleanup();
    render(<AddLineModal card={{ ...ended, arrived_by: null }} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Line name")).toHaveValue("Italian Game");
  });

  it("the catch-all is the last option and the default without a context chapter; Save waits for a move past the gap that ends on the player's", async () => {
    const calls = stubFetch({
      "/settings": () => ({ status: 200, body: {} }),
      "/repertoire/lines": () => ({ status: 201, body: { id: 78, chapter_id: 5, book_id: 4, rematch: { candidates: 0, matched: 0, no_match: 0, lines: 0 } } }),
    });
    const c = card({ colour: "black", stem: ["d4"], engine: null, context: null, family: "Queen's Pawn", arrived_by: "d4", fen: "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1", catch_all: { book: "Catch-all (black)", chapter: "Queen's Pawn" } });
    render(<AddLineModal card={c} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
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
    const { unmount } = render(<AddLineModal card={card({ context: null })} destinations={others} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("catch-all");
    unmount();
    // The context chapter is tabiya-rooted (not among the destinations): the catch-all again.
    const tabiya = card({ context: { ...card().context!, chapter_id: 42, chapter: "Tabiya" } });
    const second = render(<AddLineModal card={tabiya} destinations={others} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("catch-all");
    second.unmount();
    // The context chapter is a destination: preselected, wherever it sits in the list.
    render(<AddLineModal card={card()} destinations={[...others, { id: 1, title: "Giuoco", book_id: 1, book: "Italian" }]} prefillPlies={10} maxPlies={60} onClose={vi.fn()} onAdded={vi.fn()} />);
    expect(screen.getByLabelText("Book and chapter")).toHaveValue("1");
  });

  it("a duplicate and a conflict are shown in the modal and leave it open", async () => {
    let reply: Reply = { status: 409, body: { detail: "duplicate", duplicate: { line_id: 3, line_name: "Main", active: true, kind: "prefix" } } };
    stubFetch({ "/settings": () => ({ status: 200, body: {} }), "/repertoire/lines": () => reply });
    const onClose = vi.fn();
    render(<AddLineModal card={card()} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={onClose} onAdded={vi.fn()} />);
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

describe("a save that gets no answer", () => {
  const MADE = { id: 77, chapter_id: 1, book_id: 1, rematch: { candidates: 5, matched: 2, no_match: 0, lines: 9 } };
  const identical = (name: string) => ({ status: 409, body: { detail: "duplicate", duplicate: { line_id: 77, line_name: name, active: true, kind: "identical" } } });
  /** `/repertoire/lines` answers each try with the next outcome. */
  function tries(...outcomes: Outcome[]) {
    const queue = [...outcomes];
    const calls = stubFetch({ "/settings": () => ({ status: 200, body: {} }), "/repertoire/lines": () => queue.shift() ?? "reject" });
    return () => calls.filter((c) => c.path === "/repertoire/lines");
  }
  function open(onAdded = vi.fn(), onClose = vi.fn()) {
    render(<AddLineModal card={card()} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={onClose} onAdded={onAdded} />);
    return { onAdded, onClose };
  }

  it("is retried once; a 201 then is a normal save", async () => {
    const posts = tries("reject", { status: 201, body: MADE });
    const { onAdded, onClose } = open();
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onAdded).toHaveBeenCalledWith({ kind: "saved", lineId: 77, chapterId: 1, bookId: 1, rematched: 2 });
    expect(posts()).toHaveLength(2);
    expect(posts()[1].body).toEqual(posts()[0].body);
  });

  it("is confirmed by the retry finding an identical line of the same name in the chosen chapter", async () => {
    const posts = tries("reject", identical("Italian: d6 sideline"));
    const { onAdded, onClose } = open();
    fireEvent.change(screen.getByLabelText("Line name"), { target: { value: "  Italian:   d6 sideline " } });
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onAdded).toHaveBeenCalledWith({ kind: "recovered", lineId: 77, chapterId: 1 });
    expect(posts()).toHaveLength(2);
    expect(screen.getByRole("alert")).toHaveTextContent("");
  });

  it("names no chapter when the catch-all was sent", async () => {
    tries("reject", identical("Italian Game — after d6"));
    const { onAdded } = open();
    fireEvent.change(screen.getByLabelText("Book and chapter"), { target: { value: "catch-all" } });
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith({ kind: "recovered", lineId: 77, chapterId: null }));
  });

  it("a switched-off identical line of the same name is the duplicate it always was", async () => {
    tries("reject", { status: 409, body: { detail: "duplicate", duplicate: { line_id: 5, line_name: "Italian Game — after d6", active: false, kind: "identical" } } });
    const { onAdded } = open();
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await screen.findByText("This line is already in the chapter as “Italian Game — after d6” (switched off).");
    expect(onAdded).not.toHaveBeenCalled();
  });

  it("says it is checking while the retry is out", async () => {
    let answer: (r: Reply) => void = () => undefined;
    const second = new Promise<Reply>((resolve) => (answer = resolve));
    const queue: Array<Outcome | Promise<Reply>> = ["reject", second];
    stubFetch({ "/settings": () => ({ status: 200, body: {} }), "/repertoire/lines": () => queue.shift() ?? "reject" });
    const { onAdded } = open();
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    expect(await screen.findByRole("button", { name: "Checking…" })).toBeDisabled();
    expect(screen.getByText("Checking whether the line was saved…")).toBeInTheDocument();
    answer({ status: 201, body: MADE });
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith(expect.objectContaining({ kind: "saved" })));
  });

  it("an identical line under another name is the duplicate it always was", async () => {
    tries("reject", identical("Older"));
    const { onAdded, onClose } = open();
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await screen.findByText("This line is already in the chapter as “Older”.");
    expect(onAdded).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("twice says so and never that the line could not be added", async () => {
    const posts = tries("reject", "reject");
    const { onAdded, onClose } = open();
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await screen.findByText("The save did not answer, so the line may or may not be in. Check the chapter before saving again.");
    expect(screen.queryByText("Could not add the line.")).toBeNull();
    expect(screen.getByRole("button", { name: "Save line" })).toBeEnabled();
    expect(posts()).toHaveLength(2);
    expect(onAdded).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("is given up at the bound, then retried", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const posts = tries("hang", { status: 201, body: MADE });
      const { onAdded } = open();
      fireEvent.click(screen.getByRole("button", { name: "Save line" }));
      await vi.advanceTimersByTimeAsync(SAVE_BOUND_MS - 1000);
      expect(posts()).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
      await vi.advanceTimersByTimeAsync(1000);
      await waitFor(() => expect(onAdded).toHaveBeenCalledWith(expect.objectContaining({ kind: "saved" })));
      expect(posts()).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("an answer on the first try is never retried", async () => {
    const posts = tries(identical("Main"));
    open();
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await screen.findByText("This line is already in the chapter as “Main”.");
    expect(posts()).toHaveLength(1);
  });
});

describe("a confirmed save", () => {
  it("closes the dialog without an error when the page's own handler throws", async () => {
    stubFetch({ "/settings": () => ({ status: 200, body: {} }), "/repertoire/lines": () => ({ status: 201, body: { id: 77, chapter_id: 1, book_id: 1, rematch: { candidates: 1, matched: 1, no_match: 0, lines: 1 } } }) });
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onClose = vi.fn();
    const onAdded = vi.fn(() => {
      throw new RangeError("the page broke with a message that must not be logged");
    });
    render(<AddLineModal card={card()} destinations={DESTINATIONS} prefillPlies={10} maxPlies={60} onClose={onClose} onAdded={onAdded} />);
    fireEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.getByRole("alert")).toHaveTextContent("");
    expect(logged).toHaveBeenCalledWith("the page could not take the saved line:", "RangeError");
  });
});

