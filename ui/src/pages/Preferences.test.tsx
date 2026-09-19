import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import Preferences from "./Preferences";

const schema = {
  properties: {
    daily_puzzle_target: { type: "integer", minimum: 1, maximum: 200, description: "Puzzles per day." },
    cc0_difficulty_tier: { type: "string", enum: ["easier", "normal", "hard", "very_hard"], description: "Tier." },
    lichess_rating_offsets: { type: "object", additionalProperties: { type: "integer" }, description: "Offsets." },
  },
};
const values = { daily_puzzle_target: 10, cc0_difficulty_tier: "very_hard", lichess_rating_offsets: { rapid: -250 } };

let puts: unknown[] = [];

describe("Preferences", () => {
  beforeEach(() => {
    puts = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          puts.push(JSON.parse(String(init.body)));
          return { ok: true, json: async () => JSON.parse(String(init.body)) };
        }
        return { ok: true, json: async () => (String(url).endsWith("/settings/schema") ? schema : values) };
      }),
    );
  });

  it("renders one control per schema field, with its description", async () => {
    render(<Preferences />);
    expect(await screen.findByLabelText("daily puzzle target")).toHaveValue(10);
    expect(screen.getByLabelText("cc0 difficulty tier")).toHaveValue("very_hard");
    expect(screen.getByText("Puzzles per day.")).toBeInTheDocument();
  });

  it("refuses to save while a JSON field is malformed, and never sends the stale value", async () => {
    render(<Preferences />);
    const json = await screen.findByLabelText("lichess rating offsets");
    fireEvent.change(json, { target: { value: '{"rapid": -250' } });
    expect(await screen.findByText(/Not valid JSON/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(puts).toHaveLength(0);
    expect(json).toHaveValue('{"rapid": -250'); // the text the user typed stays visible
  });

  it("saves a valid JSON edit and shows the saved value", async () => {
    render(<Preferences />);
    const json = await screen.findByLabelText("lichess rating offsets");
    fireEvent.change(json, { target: { value: '{"rapid": -300}' } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Saved.")).toBeInTheDocument());
    expect(puts).toHaveLength(1);
    expect((puts[0] as { lichess_rating_offsets: unknown }).lichess_rating_offsets).toEqual({ rapid: -300 });
    expect(json).toHaveValue('{"rapid":-300}');
  });
});
