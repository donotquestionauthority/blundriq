import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LineReaderLine } from "../../repertoire";
import { LineWalkthrough } from "./LineReaderPanel";

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { id?: string; position?: string } }) => <div data-testid={`board-${options.id}`} data-position={options.position} />,
}));

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
const AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";

const line = (): LineReaderLine => ({
  line_id: 1,
  line_name: "Main",
  color: "white",
  book_title: "Italian",
  chapter_title: "Giuoco",
  positions: [
    { ply: 0, fen: START, move: "e4", annotation: null },
    { ply: 1, fen: AFTER_E4, move: "e5", annotation: null },
    { ply: 2, fen: AFTER_E5, move: null, annotation: null },
  ],
});

type Reply = { status: number; body: unknown };
type Call = { method: string; path: string; body: Record<string, unknown> | null };

/** Every explain request waits until the test settles it, in any order. */
function stubFetch() {
  const calls: Call[] = [];
  const waiting: ((r: Reply) => void)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^.*\/api/, "").split("?")[0];
      const method = init?.method ?? "GET";
      calls.push({ method, path, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null });
      let r: Reply;
      if (/^\/repertoire\/lines\/\d+\/annotated$/.test(path)) r = { status: 200, body: line() };
      else if (path === "/repertoire/annotation") r = { status: 200, body: { detail: "saved" } };
      else if (path === "/repertoire/lines/1/explain") r = await new Promise<Reply>((resolve) => waiting.push(resolve));
      else r = { status: 404, body: { detail: "no route" } };
      return { ok: r.status < 400, status: r.status, statusText: String(r.status), json: async () => r.body };
    }),
  );
  const explains = () => calls.filter((c) => c.path === "/repertoire/lines/1/explain");
  const settle = async (i: number, r: Reply) => {
    await act(async () => {
      waiting[i](r);
    });
  };
  return { calls, explains, settle };
}

const answer = (text: string, cached = false): Reply => ({ status: 200, body: { explanation: text, cached, model: "claude-opus-5-5", prompt_label: "x" } });
const ask = () => screen.getByRole("button", { name: "Ask Opus why this move matters" });
const next = () => fireEvent.click(screen.getByRole("button", { name: "Next ›" }));
const prev = () => fireEvent.click(screen.getByRole("button", { name: "‹ Prev" }));

async function saveANote() {
  fireEvent.click(screen.getByRole("button", { name: "＋ Add note here" }));
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "mine" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByLabelText("Note")).not.toBeInTheDocument());
}

afterEach(() => vi.unstubAllGlobals());

describe("LineAskPanel in the walk-through", () => {
  it("cannot ask at the start, where no move has arrived", async () => {
    stubFetch();
    render(<LineWalkthrough lineId={1} />);
    expect(await screen.findByText(/Start ·/)).toBeInTheDocument();
    expect(ask()).toBeDisabled();
    next();
    expect(ask()).toBeEnabled();
  });

  it("sends the arrival ply and the trimmed question, and labels the answer with what it answered", async () => {
    const f = stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={1} />);
    await screen.findByText(/1\.e4/, { selector: "span" });
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "  why not d4?  " } });
    fireEvent.click(ask());
    expect(ask()).toBeDisabled();
    expect(screen.getByText(/Thinking…/)).toBeInTheDocument();
    expect(f.explains()[0].body).toEqual({ ply: 1, question: "why not d4?" });
    await f.settle(0, answer("It takes the centre."));
    const shown = screen.getByTestId("line-answer");
    expect(shown).toHaveTextContent("Answer to: why not d4?");
    expect(shown).toHaveTextContent("It takes the centre.");
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "something else" } });
    expect(screen.getByTestId("line-answer")).toHaveTextContent("Answer to: why not d4?"); // still says what it answered
    next();
    expect(screen.queryByTestId("line-answer")).not.toBeInTheDocument(); // it belongs to ply 1
    prev();
    expect(screen.getByTestId("line-answer")).toHaveTextContent("It takes the centre.");
  });

  it("an empty question is answered as 'why this move matters'", async () => {
    const f = stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={2} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.click(ask());
    expect(f.explains()[0].body).toEqual({ ply: 2, question: "" });
    await f.settle(0, answer("Cached one.", true));
    expect(screen.getByTestId("line-answer")).toHaveTextContent("Answer to: why this move matters");
    expect(screen.getByTestId("line-answer")).toHaveTextContent("claude-opus-5-5 · cached");
  });

  it("stepping away during a request does not move the answer to another ply", async () => {
    const f = stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={1} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.click(ask());
    next();
    expect(ask()).toBeEnabled(); // ply 2 has nothing pending
    await f.settle(0, answer("About e4."));
    expect(screen.queryByTestId("line-answer")).not.toBeInTheDocument();
    prev();
    expect(screen.getByTestId("line-answer")).toHaveTextContent("About e4.");
  });

  it("a saved note clears the answers and drops a response still in flight", async () => {
    const f = stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={1} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.click(ask());
    await f.settle(0, answer("Old answer."));
    expect(screen.getByTestId("line-answer")).toBeInTheDocument();
    await saveANote();
    expect(screen.queryByTestId("line-answer")).not.toBeInTheDocument();

    fireEvent.click(ask()); // request 1, about to be overtaken by a note change
    await saveANote();
    expect(ask()).toBeEnabled(); // the dropped request no longer holds the button
    fireEvent.click(ask()); // request 2, under the new notes
    await f.settle(2, answer("New answer."));
    await f.settle(1, answer("Stale answer."));
    expect(screen.getByTestId("line-answer")).toHaveTextContent("New answer.");
    expect(screen.queryByText("Stale answer.")).not.toBeInTheDocument();
  });

  it("a response that lands after a note changed is dropped even with no newer request", async () => {
    const f = stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={1} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.click(ask());
    await saveANote();
    await f.settle(0, answer("Asked about the old notes."));
    expect(screen.queryByTestId("line-answer")).not.toBeInTheDocument();
    expect(screen.queryByText(/Thinking…/)).not.toBeInTheDocument();
  });

  it("answers stay with their line when the walk-through moves to another one", async () => {
    const f = stubFetch();
    const { rerender } = render(<LineWalkthrough lineId={1} initialPly={1} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.click(ask());
    await f.settle(0, answer("About line 1."));
    fireEvent.click(ask()); // a second request on line 1, still in flight when the line changes
    expect(ask()).toBeDisabled();
    rerender(<LineWalkthrough lineId={2} initialPly={1} />);
    await waitFor(() => expect(screen.queryByTestId("line-answer")).not.toBeInTheDocument());
    await waitFor(() => expect(ask()).toBeEnabled());
    await f.settle(1, answer("Late, about line 1."));
    expect(screen.queryByTestId("line-answer")).not.toBeInTheDocument();
  });

  it("says when the cap is reached, in the cap's words", async () => {
    const f = stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={1} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.click(ask());
    await f.settle(0, { status: 429, body: { detail: { window: "hourly", message: "Limit reached: 20 AI calls per hour." } } });
    expect(screen.getByRole("alert")).toHaveTextContent("Limit reached: 20 AI calls per hour.");
    expect(ask()).toBeEnabled();
  });

  it("refuses a question longer than the server takes", async () => {
    stubFetch();
    render(<LineWalkthrough lineId={1} initialPly={1} />);
    await waitFor(() => expect(ask()).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "♞".repeat(500) } });
    expect(ask()).toBeEnabled(); // counted in characters, not UTF-16 units
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "𝄞".repeat(500) } });
    expect(ask()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "x".repeat(501) } });
    expect(ask()).toBeDisabled();
    expect(screen.getByText("At most 500 characters.")).toBeInTheDocument();
  });
});
