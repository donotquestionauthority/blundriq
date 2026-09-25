import { describe, expect, it } from "vitest";
import { branchArrows, distanceLabel, groupKey, neighbourArrows, prepLabel } from "./compare";
import type { CompareBranch, PrepGroup, SimilarNeighbour } from "./repertoire";
import { ARROWS } from "./utils/board";
import { branchCompareTarget } from "./utils/chess";

const AFTER_BC5 = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const AFTER_BC4 = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3";

const group = (over: Partial<PrepGroup> = {}): PrepGroup => ({
  prep_move: "c3",
  prep_status: "move",
  prep_raw_token: null,
  is_queried_move: false,
  arriving: { san: "Bc5", from: "f8", to: "c5", promotion: null, is_castling: false, is_en_passant: false },
  book_title: "Italian",
  chapter_title: "Giuoco",
  line_name: "Main",
  line_id: 1,
  line_ply: 6,
  is_alternative: false,
  carried_by_line_count: 1,
  ...over,
});

const neighbour = (over: Partial<SimilarNeighbour> = {}): SimilarNeighbour => ({
  fen: AFTER_BC5,
  distance: 0,
  same_material: true,
  castling_delta: [],
  is_castle_shape: false,
  diff_squares: [],
  board_prep_divergent: false,
  groups: [group()],
  ...over,
});

describe("neighbourArrows", () => {
  it("draws one blue per distinct arriving move and one orange per readable book move, from wire squares", () => {
    const n = neighbour({ groups: [group(), group({ prep_move: "d3", line_id: 2 }), group({ prep_status: "end_of_line", prep_move: null, line_id: 3 }), group({ prep_status: "unreadable", prep_move: null, prep_raw_token: "Qxh7", line_id: 4 })] });
    const arrows = neighbourArrows(n);
    expect(arrows.filter((a) => a.color === ARROWS.opponent)).toEqual([{ startSquare: "f8", endSquare: "c5", color: ARROWS.opponent }]);
    expect(arrows.filter((a) => a.color === ARROWS.book).map((a) => a.startSquare + a.endSquare)).toEqual(["c2c3", "d2d3"]);
  });
  it("a book SAN that is not legal on the neighbour's board draws nothing rather than guessing", () => {
    expect(neighbourArrows(neighbour({ groups: [group({ prep_move: "Qxh7" })] })).filter((a) => a.color === ARROWS.book)).toEqual([]);
  });
});

const branch = (sources: CompareBranch["sources"]): CompareBranch => ({ child_fen: AFTER_BC5, opponent_move: { san: "Bc5", from: "f8", to: "c5", promotion: null, is_castling: false, is_en_passant: false }, sources });
const rep = { reply_san: "c3", reply_squares: { from: "c2", to: "c3" }, end_of_line: false, line_count: 1, board_prep_divergent: false, groups: [group()] };
const blu = { games: 2, worst: { move_played_san: "Nxe5", move_played_squares: { from: "f3", to: "e5" }, best_move_san: "c3", best_move_squares: { from: "c2", to: "c3" }, centipawn_loss: 300 } };
const sco = { total_games: 3, profiles: [{ name: "x", games: 3 }], best_move_san: "d3", best_move_squares: { from: "d2", to: "d3" } };

describe("branchArrows", () => {
  const colours = (b: CompareBranch) => branchArrows(b).map((a) => a.color);
  it("repertoire: blue and orange only; a divergent or terminal branch has no reply arrow", () => {
    expect(colours(branch({ repertoire: rep, blunders: null, scout: null }))).toEqual([ARROWS.opponent, ARROWS.book]);
    expect(colours(branch({ repertoire: { ...rep, reply_san: null, reply_squares: null, board_prep_divergent: true }, blunders: null, scout: null }))).toEqual([ARROWS.opponent]);
  });
  it("blunder-only: blue, the move played, and the best move only when recorded", () => {
    expect(colours(branch({ repertoire: null, blunders: blu, scout: null }))).toEqual([ARROWS.opponent, ARROWS.played, ARROWS.engine]);
    expect(colours(branch({ repertoire: null, blunders: { ...blu, worst: { ...blu.worst, best_move_san: null, best_move_squares: null } }, scout: null }))).toEqual([ARROWS.opponent, ARROWS.played]);
  });
  it("scout-only draws its best move in green when it has one; beside a repertoire it does not", () => {
    expect(colours(branch({ repertoire: null, blunders: null, scout: sco }))).toEqual([ARROWS.opponent, ARROWS.engine]);
    expect(colours(branch({ repertoire: null, blunders: null, scout: { ...sco, best_move_squares: null } }))).toEqual([ARROWS.opponent]);
    expect(colours(branch({ repertoire: rep, blunders: null, scout: sco }))).toEqual([ARROWS.opponent, ARROWS.book]);
  });
  it("nothing covered: blue alone", () => {
    expect(colours(branch({ repertoire: null, blunders: null, scout: null }))).toEqual([ARROWS.opponent]);
  });
});

describe("labels and keys", () => {
  it("groupKey distinguishes status, move, line and ply", () => {
    expect(groupKey(group())).toBe("move:c3:1:6");
    expect(groupKey(group({ prep_status: "unreadable", prep_move: null, prep_raw_token: "Qx" }))).toBe("unreadable:Qx:1:6");
    expect(groupKey(group({ prep_status: "end_of_line", prep_move: null }))).toBe("end_of_line::1:6");
  });
  it("prepLabel and distanceLabel", () => {
    expect(prepLabel(group())).toBe("c3");
    expect(prepLabel(group({ prep_status: "end_of_line", prep_move: null }))).toBe("line ends here");
    expect(prepLabel(group({ prep_status: "unreadable", prep_move: null, prep_raw_token: "Qx" }))).toBe("unreadable (Qx)");
    expect(distanceLabel(neighbour())).toBe("this position");
    expect(distanceLabel(neighbour({ distance: 2 }))).toBe("2 squares (~one piece)");
    expect(distanceLabel(neighbour({ distance: 4 }))).toBe("4 squares (~2 pieces)");
    expect(distanceLabel(neighbour({ distance: 4, is_castle_shape: true }))).toBe("4 squares differ · castling");
    expect(distanceLabel(neighbour({ distance: 3 }))).toBe("3 squares differ");
    expect(distanceLabel(neighbour({ distance: 8 }))).toBe("8 squares differ");
  });
});

describe("branchCompareTarget", () => {
  it("is the latest player-decision node on the walked path and its parent", () => {
    // White to move at the start; the solver has walked c3 (player) and Nf6 (opponent): the decision node is
    // the start itself, the parent the position before Bc5.
    expect(branchCompareTarget(AFTER_BC4, ["Bc5", "c3", "Nf6"], 0, "w")).toBeNull(); // no player node yet
    expect(branchCompareTarget(AFTER_BC4, ["Bc5", "c3", "Nf6"], 1, "w")).toEqual({ fen: AFTER_BC5, preFen: AFTER_BC4 });
    const t = branchCompareTarget(AFTER_BC4, ["Bc5", "c3", "Nf6"], 3, "w");
    expect(t?.fen.split(" ")[1]).toBe("w");
    expect(t?.preFen.split(" ")[1]).toBe("b");
    expect(t?.fen).toContain("2n2n2"); // after ...Nf6
  });
  it("a puzzle that starts on the player's move has no parent to compare from until the opponent has replied", () => {
    expect(branchCompareTarget(AFTER_BC5, ["c3", "Nf6", "d4"], 0, "w")).toBeNull();
    expect(branchCompareTarget(AFTER_BC5, ["c3", "Nf6", "d4"], 1, "w")).toBeNull();
    expect(branchCompareTarget(AFTER_BC5, ["c3", "Nf6", "d4"], 2, "w")?.preFen).toContain(" b ");
  });
  it("fails closed on a malformed line", () => {
    expect(branchCompareTarget(AFTER_BC4, ["Bc5", "Qxh7"], 2, "w")).toBeNull();
    expect(branchCompareTarget("not a fen", ["Bc5"], 1, "w")).toBeNull();
  });
});
