import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import defaults from "./__fixtures__/settings-defaults.json";
import schema from "./__fixtures__/settings-schema.json";
import Preferences from "./Preferences";

/** The real schema and defaults (tests/test_settings.py keeps both files equal to the server's). */
type Values = typeof defaults;
let stored: Values;
let puts: Values[];
let refuse: string | null;

beforeEach(() => {
  stored = structuredClone(defaults);
  puts = [];
  refuse = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as Values;
        puts.push(body);
        if (refuse) return { ok: false, status: 422, statusText: "422", json: async () => ({ detail: refuse }) };
        // The server answers with the stored (canonical) row; mark it to tell it from the request.
        stored = { ...body, ai_line_prompt: { ...body.ai_line_prompt, label: "as stored" } };
        return { ok: true, json: async () => structuredClone(stored) };
      }
      return { ok: true, json: async () => structuredClone(String(url).endsWith("/settings/schema") ? schema : stored) };
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("Preferences on the real settings schema", () => {
  it("edits the line prompt as an object and saves it as one", async () => {
    render(<Preferences />);
    const field = await screen.findByLabelText("ai line prompt");
    expect(field).not.toHaveValue("[object Object]");
    const prompt = JSON.parse((field as HTMLInputElement).value) as Values["ai_line_prompt"];
    expect(prompt.model).toBe("claude-opus-5-5");
    fireEvent.change(field, { target: { value: JSON.stringify({ ...prompt, max_tokens: 20000 }) } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Saved.")).toBeInTheDocument());
    expect(puts[0].ai_line_prompt).toEqual({ ...prompt, max_tokens: 20000 });
    expect(typeof puts[0].ai_line_prompt).toBe("object");
    expect(JSON.parse((field as HTMLInputElement).value)).toEqual({ ...prompt, max_tokens: 20000, label: "as stored" });
  });

  it("blocks Save on malformed JSON", async () => {
    render(<Preferences />);
    const field = await screen.findByLabelText("ai line prompt");
    fireEvent.change(field, { target: { value: '{"model": "claude-opus-5-5"' } });
    expect(await screen.findByText(/Not valid JSON/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(puts).toHaveLength(0);
  });

  it("a refused save says so and the confirmed value is what the server still holds", async () => {
    refuse = "ai_line_prompt: claude-opus-5-5 always thinks; turn thinking on";
    render(<Preferences />);
    const field = await screen.findByLabelText("ai line prompt");
    const prompt = JSON.parse((field as HTMLInputElement).value) as Values["ai_line_prompt"];
    fireEvent.change(field, { target: { value: JSON.stringify({ ...prompt, thinking_enabled: false }) } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Not saved: ai_line_prompt: claude-opus-5-5 always thinks/)).toBeInTheDocument();
    expect(puts).toHaveLength(1);
    cleanup();
    render(<Preferences />); // a fresh read shows what the server still holds
    const again = await screen.findByLabelText("ai line prompt");
    expect(JSON.parse((again as HTMLInputElement).value)).toEqual(prompt);
  });
});
