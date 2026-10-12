import { describe, expect, it } from "vitest";
import type { RepertoireEntry, RepertoireStatus } from "../games";
import { walkOpenAt, walkSurvives } from "./reviewWalk";

const entry = (over: Partial<RepertoireEntry>): RepertoireEntry => ({ status: "none", book_move: null, book: null, chapter: null, line_name: null, line_id: null, line_ply: null, plan: [], more_lines: 0, transposed: null, conflict: null, ...over });
const group = (line_id: number | null) => ({ move: "Nf3", book: "B", chapter: "C", line_name: "L", line_id, line_ply: 4, more_lines: 0 });

describe("walkSurvives: the open line's walk-through outlives exactly the plies that still offer its line", () => {
  it("an opponent-turn ply says nothing about the line", () => {
    expect(walkSurvives(entry({ status: "not_your_turn" }), 7)).toBe(true);
  });
  it.each<RepertoireStatus>(["match", "agree", "end_of_line"])("%s keeps it when it selected the same line, not another", (status) => {
    expect(walkSurvives(entry({ status, line_id: 7, line_ply: 4 }), 7)).toBe(true);
    expect(walkSurvives(entry({ status, line_id: 8, line_ply: 4 }), 7)).toBe(false);
    expect(walkSurvives(entry({ status, line_id: null }), 7)).toBe(false);
  });
  it("a conflict keeps it when one of its groups names the line", () => {
    expect(walkSurvives(entry({ status: "conflict", conflict: [group(3), group(7)] }), 7)).toBe(true);
    expect(walkSurvives(entry({ status: "conflict", conflict: [group(3), group(null)] }), 7)).toBe(false);
    expect(walkSurvives(entry({ status: "conflict", conflict: [] }), 7)).toBe(false);
    expect(walkSurvives(entry({ status: "conflict", conflict: null }), 7)).toBe(false);
  });
  it.each<RepertoireStatus>(["none", "unreadable"])("%s offers no line", (status) => {
    expect(walkSurvives(entry({ status }), 7)).toBe(false);
  });
  it("no projection, no line", () => {
    expect(walkSurvives(null, 7)).toBe(false);
  });
});

describe("walkOpenAt: on the prescribed move, or on the board at the end of a line", () => {
  it.each<RepertoireStatus>(["match", "agree", "conflict"])("%s opens one ply on, the start of a line included", (status) => {
    expect(walkOpenAt(status, 0)).toBe(1);
    expect(walkOpenAt(status, 4)).toBe(5);
  });
  it("end_of_line opens on the board", () => {
    expect(walkOpenAt("end_of_line", 4)).toBe(4);
  });
});
