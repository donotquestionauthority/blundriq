import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi, describe, it, expect } from "vitest";
import App from "./App";

describe("App", () => {
  it("shows the login page when the session check returns 401", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401, statusText: "Unauthorized", json: async () => ({ detail: "not logged in" }) })));
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
  });
});

describe("App routes", () => {
  it("a Review position's path is the position page, never a game's review", async () => {
    const node = { colour: "black", key: "-5", line_san: ["d4", "d5"], fen: null, last_move: null, n: 3, score: 0.5, expected: 0.5, current_score: 0.5, current_expected: 0.5, long_deficit: 0, current_deficit: 0, leak_per_month: 0, recent_games: 3, status: null, es_at_node: null, trend: [], parent_key: null, rob_to_move: false, ply: 2 };
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        requested.push(url);
        const body = url.endsWith("/me") ? { authenticated: true } : url.includes("/review/positions/") ? { node, children: [], games: { rows: [], total: 0, page: 1, page_size: 50, total_pages: 1 }, older_games: 3 } : {};
        return { ok: true, status: 200, statusText: "OK", json: async () => body };
      }),
    );
    render(
      <MemoryRouter initialEntries={["/review/positions/black/-5"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByText("What happens next")).toBeInTheDocument();
    expect(requested.some((u) => u.includes("/review/positions/black/-5?"))).toBe(true);
    expect(requested.some((u) => u.includes("/games/"))).toBe(false);
  });
});
