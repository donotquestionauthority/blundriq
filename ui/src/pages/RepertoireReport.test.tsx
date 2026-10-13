import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import RepertoireReport from "./RepertoireReport";
import type { GapCard, GapsResponse, ReportBook, ReportLine, ReportTally } from "../repertoire";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string } }) => <div data-testid="board" data-id={options.id} data-position={options.position} />,
}));
vi.mock("../components/CreatePuzzleModal", () => ({
  CreatePuzzleModal: ({ source, onClose, onCreated }: { source: { title?: string | null; engineLine?: string[] | null; prefillPlies?: number | null }; onClose: () => void; onCreated: (r: { id: number; visible: boolean }) => void }) => (
    <div role="dialog" aria-label="Create puzzle" data-title={source.title} data-line={(source.engineLine ?? []).join(" ")} data-prefill={source.prefillPlies}>
      <button type="button" onClick={() => onCreated({ id: 42, visible: true })}>
        fake-save-visible
      </button>
      <button type="button" onClick={() => onCreated({ id: 43, visible: false })}>
        fake-save-hidden
      </button>
      <button type="button" onClick={onClose}>
        fake-close
      </button>
    </div>
  ),
}));

type Reply = { status: number; body: unknown };
function stubFetch(routes: Record<string, (query: URLSearchParams) => Reply>) {
  const calls: Array<{ path: string; query: URLSearchParams }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const [rawPath, rawQuery] = url.replace(/^.*\/api/, "").split("?");
      const call = { path: rawPath, query: new URLSearchParams(rawQuery ?? "") };
      calls.push(call);
      const h = routes[rawPath];
      if (!h) return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no route" }) };
      const r = h(call.query);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

const tally = (over: Partial<ReportTally> = {}): ReportTally => ({
  games: 3,
  wins: 2,
  draws: 0,
  losses: 1,
  score: 0.667,
  left_by_me: 1,
  left_by_opponent: 2,
  ended: 0,
  ran_out: 0,
  faithful: 2,
  depth_reached: { "2": 1, "3": 2 },
  slip: { own_move: 3, expected: "Bc4", played: "d4", count: 1 },
  last_played: "2026-10-10T10:00:00+00:00",
  puzzles: 0,
  ...over,
});
const line = (over: Partial<ReportLine> = {}): ReportLine => ({ ...tally(), id: 1, title: "Main", is_alternative: false, depth_total: 3, shared: false, ...over });
const books = (): ReportBook[] => [
  {
    ...tally({ games: 4, puzzles: 2 }),
    id: 1,
    title: "Italian",
    color: "white",
    chapters: [{ ...tally({ puzzles: 2, ended: 1 }), id: 1, title: "Giuoco", lines: [line({ puzzles: 2 }), line({ id: 2, title: "Evans", shared: true, slip: null, left_by_me: 0, faithful: 3 })] }],
  },
];

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
  continuations: [{ moves: ["c3", "Be7", "d4", "exd4"], games: 3, score: 0.667 }],
  engine: { best_move: "c3", line: ["c3", "Be7", "d4", "exd4", "cxd4"], eval: 40, mate_in: null },
  last_played: "2026-10-10T10:00:00+00:00",
  puzzle: null,
  ...over,
});
const gaps = (colour: "white" | "black", over: Partial<GapsResponse> = {}): GapsResponse => ({ colour, no_book: false, prepared: [], gaps: [], prefill_plies: 10, ...over });

const renderPage = () =>
  render(
    <MemoryRouter>
      <RepertoireReport />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the line report", () => {
  it("renders the tree collapsed, with the numbers, the slip, Drill only where puzzles are served, and re-reads on the time class", async () => {
    const calls = stubFetch({
      "/repertoire/report/lines": () => ({ status: 200, body: { books: books() } }),
      "/repertoire/report/gaps": (q) => ({ status: 200, body: gaps(q.get("colour") as "white" | "black") }),
    });
    renderPage();
    await screen.findByText(/^Italian/);
    expect(screen.getByText("Giuoco", { exact: false })).toBeInTheDocument();
    expect(screen.queryByTestId("report-line")).toBeNull();
    expect(screen.getByText("opponent left 2 · you left 1 · ended in book 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Giuoco/ }));
    const rows = screen.getAllByTestId("report-line");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText(/You left at your move 3 in 1 of 1/)).toBeInTheDocument();
    expect(within(rows[0]).getByText("3 of 3 of your moves")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("link", { name: "Drill (2)" })).toHaveAttribute("href", "/practice?type=repertoire&subtype=line:1");
    expect(within(rows[1]).getByText("no puzzles yet")).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("link")).toBeNull();
    expect(within(rows[1]).getByText("shared")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Drill the gaps →" })).toHaveAttribute("href", "/practice?type=custom&subtype=gap");
    expect(calls.filter((c) => c.path === "/repertoire/report/lines").map((c) => c.query.get("time_class"))).toEqual(["focus"]);
    fireEvent.change(screen.getByLabelText("Time class"), { target: { value: "all" } });
    await waitFor(() => expect(calls.filter((c) => c.path === "/repertoire/report/lines").map((c) => c.query.get("time_class"))).toEqual(["focus", "all"]));
    expect(calls.filter((c) => c.path === "/repertoire/report/gaps").map((c) => c.query.get("time_class"))).toEqual(["focus", "focus", "all", "all"]);
  });

  it("drops a late answer for the previous time class", async () => {
    let release: (() => void) | null = null;
    const slow = new Promise<void>((r) => {
      release = r;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const [path, q] = url.replace(/^.*\/api/, "").split("?");
        const tc = new URLSearchParams(q).get("time_class");
        if (path === "/repertoire/report/lines" && tc === "focus") await slow;
        const body = path === "/repertoire/report/lines" ? { books: tc === "focus" ? books() : [] } : gaps(new URLSearchParams(q).get("colour") as "white");
        return { ok: true, status: 200, statusText: "200", json: async () => body };
      }),
    );
    renderPage();
    fireEvent.change(screen.getByLabelText("Time class"), { target: { value: "all" } });
    await screen.findByText("No games in the window go through an active line.");
    release!();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/^Italian/)).toBeNull();
  });

  it("shows the gaps for the colour with more of them, the three card states, Prepared but not played, and the no-book notice", async () => {
    stubFetch({
      "/repertoire/report/lines": () => ({ status: 200, body: { books: [] } }),
      "/repertoire/report/gaps": (q) =>
        q.get("colour") === "white"
          ? { status: 200, body: gaps("white", { no_book: true }) }
          : {
              status: 200,
              body: gaps("black", {
                prepared: [{ fen: "x", book_move: "d5", played: "c5", games: 9, wins: 4, draws: 1, losses: 4, line_id: 5, line_name: "Main", book: "Scandi", chapter: "Main", puzzles: 1, last_played: null }],
                gaps: [
                  card({ colour: "black", fen: "a", family: "Queen's Pawn", context: null, arrived_by: "d4", engine: null }),
                  card({ colour: "black", fen: "b", puzzle: { id: 9, playable: true } }),
                  card({ colour: "black", fen: "c", puzzle: { id: 10, playable: false } }),
                ],
              }),
            },
    });
    renderPage();
    const black = await screen.findByRole("tab", { name: "Black" });
    expect(black).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByTestId("gap-card")).toHaveLength(3);
    const row = screen.getByTestId("prepared-row");
    expect(row.textContent).toContain("Your book plays d5; you played c5 in 9 games (4–1–4)");
    expect(within(row).getByRole("link", { name: "Drill (1)" })).toHaveAttribute("href", "/practice?type=repertoire&subtype=line:5");
    const cards = screen.getAllByTestId("gap-card");
    expect(within(cards[0]).getByText("No book reaches this board.")).toBeInTheDocument();
    expect(within(cards[0]).getByText("No stored engine line yet for this board.")).toBeInTheDocument();
    expect(within(cards[0]).getByRole("button", { name: "Add as custom puzzle" })).toBeInTheDocument();
    expect(within(cards[1]).getByRole("link", { name: "Drill" })).toHaveAttribute("href", "/practice?puzzle=9");
    expect(within(cards[2]).getByText(/Practice hides it/)).toBeInTheDocument();
    expect(within(cards[2]).queryByRole("link")).toBeNull();
    expect(within(cards[2]).queryByRole("button")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "White" }));
    expect(screen.getByText("No active White line: every White game opens outside your repertoire.")).toBeInTheDocument();
  });

  it("opens the modal with the engine line and the title, and honours the save's visibility", async () => {
    const calls = stubFetch({
      "/repertoire/report/lines": () => ({ status: 200, body: { books: [] } }),
      "/repertoire/report/gaps": (q) => ({ status: 200, body: q.get("colour") === "white" ? gaps("white", { gaps: [card(), card({ fen: "z", family: null, context: null, arrived_by: "d4" })] }) : gaps("black") }),
    });
    renderPage();
    const cards = await screen.findAllByTestId("gap-card");
    fireEvent.click(within(cards[0]).getByRole("button", { name: "Add as custom puzzle" }));
    const dialog = screen.getByRole("dialog", { name: "Create puzzle" });
    expect(dialog).toHaveAttribute("data-title", "Italian Game: after d6");
    expect(dialog).toHaveAttribute("data-line", "c3 Be7 d4 exd4 cxd4");
    expect(dialog).toHaveAttribute("data-prefill", "10"); // the server's constant, not a copy
    fireEvent.click(screen.getByText("fake-save-visible"));
    fireEvent.click(screen.getByText("fake-close"));
    expect(within(screen.getAllByTestId("gap-card")[0]).getByRole("link", { name: "Drill" })).toHaveAttribute("href", "/practice?puzzle=42");
    // Closing the modal re-reads the gaps, so a board taken behind the page's back (a 409 in the
    // modal, which the modal's own test covers) shows its real owner afterwards.
    await waitFor(() => expect(calls.filter((c) => c.path === "/repertoire/report/gaps")).toHaveLength(4));
    fireEvent.click(within(screen.getAllByTestId("gap-card")[1]).getByRole("button", { name: "Add as custom puzzle" }));
    expect(screen.getByRole("dialog")).toHaveAttribute("data-title", "(unnamed opening): 1.d4");
    fireEvent.click(screen.getByText("fake-save-hidden"));
    fireEvent.click(screen.getByText("fake-close"));
    expect(within(screen.getAllByTestId("gap-card")[1]).getByText(/^Saved\. Practice will not serve it/)).toBeInTheDocument();
  });
});
