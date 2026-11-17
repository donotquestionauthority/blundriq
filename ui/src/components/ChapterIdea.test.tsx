import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { ChapterIdeaDialog, SegmentText } from "./ChapterIdea";
import type { ChapterConversation, ChapterIdeaRead, ChapterMoveLink, ChapterTurn, LineReaderLine } from "../repertoire";
import { CHAPTER_MESSAGES, sideLineBoards } from "../utils/chapterIdea";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string } }) => <div data-testid="board" data-position={options.position} />,
}));
vi.mock("./ExploreLayer", () => ({
  ExploreLayer: ({ fen, onClose }: { fen: string; onClose: () => void }) => (
    <div data-testid="explore-layer" data-fen={fen}>
      <button type="button" onClick={onClose}>
        close explore
      </button>
    </div>
  ),
}));

const MAIN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6"];
const fens = (moves: string[]) => {
  const g = new Chess();
  const out = [g.fen()];
  for (const m of moves) {
    g.move(m);
    out.push(g.fen());
  }
  return out;
};
const F = fens(MAIN);
const afterB4 = fens([...MAIN.slice(0, 6), "b4"]);
const afterBxb4 = fens([...MAIN.slice(0, 6), "b4", "Bxb4"]);

const lineLink = (ply: number, label: string): ChapterMoveLink => ({ label, line: "L1", line_id: 7, ply, anchor: label, anchor_fen: F[ply], branch: [], fen: F[ply] });
const sideLink: ChapterMoveLink = { label: "4…Bxb4", line: "L1", line_id: 7, ply: 6, anchor: "3…Bc5", anchor_fen: F[6], branch: ["b4", "Bxb4"], fen: afterBxb4[8] };

const turn = (seq: number, over: Partial<ChapterTurn> = {}): ChapterTurn => ({
  seq,
  question: seq === 0 ? "What is the general idea of this chapter?" : `follow ${seq}`,
  segments: [{ text: "**Idea.** After " }, { move: lineLink(5, "3.Bc4") }, { text: " and later " }, { move: lineLink(7, "4.c3") }, { text: ", or " }, { move: sideLink }, { text: ".\n\nSecond paragraph." }],
  input_tokens: 10,
  cache_read_tokens: 0,
  cache_write_tokens: 29000,
  output_tokens: 4000,
  created_at: "2026-11-16T20:00:00+00:00",
  ...over,
});

const conversation = (over: Partial<ChapterConversation> = {}): ChapterConversation => ({
  id: 5,
  chapter_id: 1,
  created_at: "2026-11-16T20:00:00+00:00",
  model: "claude-opus-5-5",
  turns: [turn(0)],
  lines: { L1: { line_id: 7, name: "Main line", exists: true } },
  stale: false,
  stale_reason: null,
  prompt_current: true,
  full: false,
  full_reason: null,
  blocked: null,
  ...over,
});

const read = (conv: ChapterConversation | null, over: Partial<ChapterIdeaRead> = {}): ChapterIdeaRead => ({
  chapter: { id: 1, title: "1) Giuoco", book_title: "Italian", color: "white", active_lines: 3 },
  conversations: conv ? [{ id: conv.id, created_at: conv.created_at, turns: conv.turns.length }] : [],
  conversation: conv,
  ...over,
});

const line = (): LineReaderLine => ({
  line_id: 7,
  line_name: "Main line",
  color: "white",
  book_title: "Italian",
  chapter_title: "1) Giuoco",
  positions: F.map((fen, i) => ({ ply: i, fen, move: MAIN[i] ?? null, annotation: null })),
});

type Reply = { status: number; body: unknown };
function stubFetch(routes: Record<string, (method: string, body: Record<string, unknown> | null) => Reply | Promise<Reply>>) {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | null }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const call = { path, method: init?.method ?? "GET", body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null };
      calls.push(call);
      const h = routes[path];
      if (!h) return { ok: true, status: 200, statusText: "OK", json: async () => (path.startsWith("/ai/history") ? { items: [] } : {}) };
      const r = await h(call.method, call.body);
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  return calls;
}

function phone() {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: !q.includes("min-width: 1024px"), addEventListener: () => {}, removeEventListener: () => {} }));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const posted = (calls: { method: string }[]) => calls.filter((c) => c.method === "POST");

describe("SegmentText", () => {
  it("renders paragraphs, bold and one button per link", () => {
    const onMove = vi.fn();
    render(<SegmentText segments={turn(0).segments} selected={null} onMove={onMove} />);
    expect(screen.getByText("Idea.").tagName).toBe("STRONG");
    expect(screen.getAllByTestId("move-link").map((b) => b.textContent)).toEqual(["3.Bc4", "4.c3", "4…Bxb4"]);
    expect(screen.getByText("Second paragraph.").closest("p")).not.toBe(screen.getByText("Idea.").closest("p"));
    fireEvent.click(screen.getByRole("button", { name: "Show 4.c3 on the board" }));
    expect(onMove).toHaveBeenCalledWith(lineLink(7, "4.c3"));
  });

  it("replays a side line through legal moves only", () => {
    expect(sideLineBoards(sideLink).map((b) => b.label)).toEqual(["3…Bc5", "4.b4", "4…Bxb4"]);
    expect(sideLineBoards({ ...sideLink, branch: ["b4", "--", "Bxb4"] }).map((b) => b.fen)).toEqual([F[6], afterB4[7]]);
  });
});

describe("ChapterIdeaDialog", () => {
  it("opens on the stored conversation with reads only, and a line link opens the walk-through at its ply", async () => {
    const calls = stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation()) }), "/repertoire/lines/7/annotated": () => ({ status: 200, body: line() }) });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    await screen.findByText("Idea.");
    fireEvent.click(screen.getByRole("button", { name: "Show 3.Bc4 on the board" }));
    const board = within(screen.getByTestId("chapter-board"));
    await waitFor(() => expect(board.getByTestId("board").dataset.position).toBe(F[5]));
    fireEvent.click(screen.getByRole("button", { name: "Show 4.c3 on the board" })); // the same line: it jumps
    await waitFor(() => expect(board.getByTestId("board").dataset.position).toBe(F[7]));
    fireEvent.click(screen.getByRole("button", { name: "Show 3.Bc4 on the board" }));
    await waitFor(() => expect(board.getByTestId("board").dataset.position).toBe(F[5]));
    expect(posted(calls)).toEqual([]);
  });

  it("steps a side line, opens its line at the anchor and explores the board shown", async () => {
    stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation()) }), "/repertoire/lines/7/annotated": () => ({ status: 200, body: line() }) });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Show 4…Bxb4 on the board" }));
    const board = within(screen.getByTestId("chapter-board"));
    expect(board.getByTestId("side-line-caption").textContent).toBe("Not in your repertoire — from Main line after 3…Bc5");
    expect(board.getByTestId("board").dataset.position).toBe(afterBxb4[8]);
    fireEvent.click(board.getByRole("button", { name: "‹ Prev" }));
    expect(board.getByTestId("board").dataset.position).toBe(afterB4[7]);
    expect(board.getByTestId("side-line-position").textContent).toBe("4.b4");
    fireEvent.click(board.getByRole("button", { name: "Explore from here" }));
    expect(screen.getByTestId("explore-layer").dataset.fen).toBe(afterB4[7]);
    fireEvent.click(screen.getByRole("button", { name: "close explore" }));
    fireEvent.click(board.getByRole("button", { name: "Open the line here" }));
    await waitFor(() => expect(board.getByTestId("board").dataset.position).toBe(F[6]));
    expect(board.getByTestId("walkthrough-position").textContent).toContain("Bc5");
  });

  it("opens a deleted line's link on the stored board", async () => {
    stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation({ lines: { L1: { line_id: 7, name: "Main line", exists: false } } })) }) });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Show 3.Bc4 on the board" }));
    const board = within(screen.getByTestId("chapter-board"));
    expect(board.getByTestId("side-line-caption").textContent).toBe(CHAPTER_MESSAGES.deleted);
    expect(board.getByTestId("board").dataset.position).toBe(F[5]);
    expect(board.queryByRole("button", { name: "Open the line here" })).toBeNull();
  });

  it("shows the stale and emptied notices and greys the follow-up when the conversation is full", async () => {
    stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation({ stale: true, stale_reason: "changed", full: true, full_reason: "conversation_full", prompt_current: false })) }) });
    const { unmount } = render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    expect(await screen.findByText(CHAPTER_MESSAGES.stale)).toBeTruthy();
    expect(screen.getByText(CHAPTER_MESSAGES.oldPrompt)).toBeTruthy();
    expect(screen.getByText(CHAPTER_MESSAGES.full)).toBeTruthy();
    expect((screen.getByLabelText("Your follow-up question") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Start over" }) as HTMLButtonElement).disabled).toBe(false);
    unmount();
    stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation({ stale: true, stale_reason: "empty" }), { chapter: { id: 1, title: "1) Giuoco", book_title: "Italian", color: "white", active_lines: 0 } }) }) });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    expect(await screen.findByText(CHAPTER_MESSAGES.emptied)).toBeTruthy();
    expect((screen.getByLabelText("Your follow-up question") as HTMLInputElement).disabled).toBe(false); // the stored text still answers
    expect((screen.getByRole("button", { name: "Start over" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(CHAPTER_MESSAGES.noLines)).toBeTruthy();
  });

  it("asks, follows up, and shows what a POST says about staleness and fullness", async () => {
    const calls = stubFetch({
      "/repertoire/chapters/1/idea": (method) => (method === "GET" ? { status: 200, body: read(null) } : { status: 200, body: { saved: true, ...read(conversation()) } }),
      "/repertoire/chapter-ideas/5/followup": () => ({ status: 200, body: { saved: true, ...read(conversation({ turns: [turn(0), turn(1)], stale: true, stale_reason: "changed", full: true, full_reason: "conversation_full" })) } }),
    });
    const counts: number[] = [];
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} onCount={(n) => counts.push(n)} />);
    expect(await screen.findByText("Ask Opus what this chapter is about")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("Idea.");
    expect(posted(calls)[0]).toMatchObject({ path: "/repertoire/chapters/1/idea", body: { question: "" } });
    fireEvent.change(screen.getByLabelText("Your follow-up question"), { target: { value: "why c3?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask a follow-up" }));
    expect(await screen.findByText(CHAPTER_MESSAGES.stale)).toBeTruthy();
    expect(posted(calls)[1]).toMatchObject({ path: "/repertoire/chapter-ideas/5/followup", body: { question: "why c3?" } });
    expect(screen.getAllByTestId("chapter-turn")).toHaveLength(2);
    expect((screen.getByLabelText("Your follow-up question") as HTMLInputElement).disabled).toBe(true);
    expect(counts).toEqual([0, 1, 1]);
  });

  it("greys the box after the count refuses a follow-up, from the conversation read again", async () => {
    const calls = stubFetch({
      "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation()) }),
      "/repertoire/chapter-ideas/5/followup": () => ({ status: 422, body: { detail: { code: "conversation_full", message: CHAPTER_MESSAGES.full } } }),
      "/repertoire/chapter-ideas/5": () => ({ status: 200, body: read(conversation({ full: true, full_reason: "conversation_full" })) }),
    });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText("Your follow-up question"), { target: { value: "more" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask a follow-up" }));
    await waitFor(() => expect((screen.getByLabelText("Your follow-up question") as HTMLInputElement).disabled).toBe(true));
    expect(screen.getAllByText(CHAPTER_MESSAGES.full).length).toBeGreaterThan(0);
    expect(calls.filter((c) => c.path === "/repertoire/chapter-ideas/5" && c.method === "GET")).toHaveLength(1);
  });

  it("shows an answer the server could not store, once, with no follow-up from it", async () => {
    const unsaved = turn(1, { question: "why?", segments: [{ text: "Unsaved answer." }] });
    stubFetch({
      "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation()) }),
      "/repertoire/chapter-ideas/5/followup": () => ({ status: 200, body: { saved: false, unsaved_turn: unsaved, conversation_id: 5 } }),
    });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText("Your follow-up question"), { target: { value: "why?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask a follow-up" }));
    expect(await screen.findByText("Unsaved answer.")).toBeTruthy();
    expect(screen.getByText(CHAPTER_MESSAGES.unsaved)).toBeTruthy();
    expect((screen.getByLabelText("Your follow-up question") as HTMLInputElement).disabled).toBe(true);
  });

  it("starts over and opens an earlier conversation", async () => {
    const older = conversation({ id: 3, created_at: "2026-11-02T10:00:00+00:00", turns: [turn(0, { question: "older question", created_at: "2026-11-02T10:00:00+00:00" })] });
    const newer = conversation();
    const listing = [
      { id: 5, created_at: newer.created_at, turns: 1 },
      { id: 3, created_at: older.created_at, turns: 1 },
    ];
    const calls = stubFetch({
      "/repertoire/chapters/1/idea": (method) => (method === "GET" ? { status: 200, body: read(newer, { conversations: listing }) } : { status: 200, body: { saved: true, ...read(conversation({ id: 6 }), { conversations: [{ id: 6, created_at: newer.created_at, turns: 1 }, ...listing] }) } }),
      "/repertoire/chapter-ideas/3": () => ({ status: 200, body: read(older, { conversations: listing }) }),
    });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    const earlier = within(await screen.findByTestId("chapter-earlier"));
    expect(earlier.getByText("Earlier:")).toBeTruthy();
    fireEvent.click(earlier.getByRole("button"));
    expect(await screen.findByText("older question")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    fireEvent.change(screen.getByLabelText("Your question about the chapter"), { target: { value: "the d4 break?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(posted(calls)).toHaveLength(1));
    expect(posted(calls)[0]).toMatchObject({ path: "/repertoire/chapters/1/idea", body: { question: "the d4 break?" } });
    await waitFor(() => expect(within(screen.getByTestId("chapter-earlier")).getAllByRole("button")).toHaveLength(2));
  });

  it("on a phone a link opens the board as a sheet and Back returns to the same text", async () => {
    phone();
    stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation()) }), "/repertoire/lines/7/annotated": () => ({ status: 200, body: line() }) });
    const onClose = vi.fn();
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={onClose} />);
    const text = await screen.findByTestId("chapter-conversation");
    expect(screen.queryByTestId("chapter-board")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 3.Bc4 on the board" }));
    const sheet = within(screen.getByTestId("chapter-board-sheet"));
    await waitFor(() => expect(sheet.getByTestId("board").dataset.position).toBe(F[5]));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(screen.queryByTestId("chapter-board-sheet")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("chapter-conversation")).toBe(text); // never unmounted: the scroll stays
    fireEvent.click(screen.getByRole("button", { name: "Show 4.c3 on the board" }));
    fireEvent.click(within(screen.getByTestId("chapter-board-sheet")).getByRole("button", { name: "← Back to the text" }));
    expect(screen.queryByTestId("chapter-board-sheet")).toBeNull();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("on a laptop the first Escape after a move closes the dialog", async () => {
    stubFetch({ "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation()) }), "/repertoire/lines/7/annotated": () => ({ status: 200, body: line() }) });
    const onClose = vi.fn();
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={onClose} />);
    fireEvent.click(await screen.findByRole("button", { name: "Show 3.Bc4 on the board" }));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("holds Earlier while a question is out, and its answer lands on the conversation it was asked from", async () => {
    let answer: (r: Reply) => void = () => {};
    const older = conversation({ id: 3, created_at: "2026-11-02T10:00:00+00:00" });
    const listing = [
      { id: 5, created_at: "2026-11-16T20:00:00+00:00", turns: 1 },
      { id: 3, created_at: older.created_at, turns: 1 },
    ];
    stubFetch({
      "/repertoire/chapters/1/idea": () => ({ status: 200, body: read(conversation(), { conversations: listing }) }),
      "/repertoire/chapter-ideas/5/followup": () => new Promise<Reply>((r) => (answer = r)),
      "/repertoire/chapter-ideas/3": () => ({ status: 200, body: read(older, { conversations: listing }) }),
    });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText("Your follow-up question"), { target: { value: "why?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask a follow-up" }));
    expect(await screen.findByText(CHAPTER_MESSAGES.thinking)).toBeTruthy();
    const earlier = within(screen.getByTestId("chapter-earlier")).getByRole("button");
    expect(earlier).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start over" })).toBeDisabled();
    await act(async () => answer({ status: 200, body: { saved: true, ...read(conversation({ turns: [turn(0), turn(1, { question: "why?" })] }), { conversations: listing }) } }));
    expect(screen.queryByText(CHAPTER_MESSAGES.thinking)).toBeNull();
    expect(screen.getAllByTestId("chapter-turn")).toHaveLength(2);
    expect(within(screen.getByTestId("chapter-earlier")).getByRole("button")).toBeEnabled();
  });

  it("shows an unsaved first answer on its own, with its own lines", async () => {
    const unsaved = turn(0, { question: "fresh", segments: [{ text: "See " }, { move: lineLink(5, "3.Bc4") }] });
    stubFetch({
      "/repertoire/chapters/1/idea": (method) => (method === "GET" ? { status: 200, body: read(conversation({ stale: true, stale_reason: "changed", lines: { L1: { line_id: 99, name: "old", exists: false } } })) } : { status: 200, body: { saved: false, unsaved_turn: unsaved, conversation_id: null, lines: { L1: { line_id: 7, name: "Main line", exists: true } } } }),
      "/repertoire/lines/7/annotated": () => ({ status: 200, body: line() }),
    });
    render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} />);
    expect(await screen.findByText(CHAPTER_MESSAGES.stale)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(CHAPTER_MESSAGES.unsaved)).toBeTruthy();
    expect(screen.queryByText(CHAPTER_MESSAGES.stale)).toBeNull();
    expect(screen.getAllByTestId("chapter-turn")).toHaveLength(1);
    expect(within(screen.getByTestId("chapter-earlier")).getAllByRole("button")).toHaveLength(1); // the stored one stays listed
    fireEvent.click(screen.getByRole("button", { name: "Show 3.Bc4 on the board" }));
    await waitFor(() => expect(within(screen.getByTestId("chapter-board")).getByTestId("walkthrough-position")).toBeTruthy()); // its own line map: the line exists
  });

  it("ignores a question's answer that lands after the dialog closed", async () => {
    let answer: (r: Reply) => void = () => {};
    stubFetch({
      "/repertoire/chapters/1/idea": (method) => (method === "GET" ? { status: 200, body: read(null) } : new Promise<Reply>((r) => (answer = r))),
    });
    const counts: number[] = [];
    const { unmount } = render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} onCount={(n) => counts.push(n)} />);
    fireEvent.click(await screen.findByRole("button", { name: "Ask" }));
    unmount();
    await act(async () => answer({ status: 200, body: { saved: true, ...read(conversation()) } }));
    expect(counts).toEqual([0]);
  });

  it("ignores a response that lands after the dialog closed", async () => {
    let answer: (r: Reply) => void = () => {};
    stubFetch({ "/repertoire/chapters/1/idea": () => new Promise<Reply>((r) => (answer = r)) });
    const counts: number[] = [];
    const { unmount } = render(<ChapterIdeaDialog chapterId={1} title="1) Giuoco" onClose={() => {}} onCount={(n) => counts.push(n)} />);
    unmount();
    await act(async () => answer({ status: 200, body: read(conversation()) }));
    expect(counts).toEqual([]);
  });
});
