import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import Preferences from "./Preferences";

const schema = {
  properties: {
    daily_puzzle_target: { type: "integer", minimum: 1, maximum: 200, description: "Puzzles per day." },
    cc0_difficulty_tier: { type: "string", enum: ["easier", "normal", "hard", "very_hard"], description: "Tier." },
  },
};
const values = { daily_puzzle_target: 10, cc0_difficulty_tier: "very_hard" };

describe("Preferences", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (String(url).endsWith("/settings/schema") ? schema : values),
      })),
    );
  });

  it("renders one control per schema field, with its description", async () => {
    render(<Preferences />);
    expect(await screen.findByLabelText("daily puzzle target")).toHaveValue(10);
    expect(screen.getByLabelText("cc0 difficulty tier")).toHaveValue("very_hard");
    expect(screen.getByText("Puzzles per day.")).toBeInTheDocument();
  });
});
