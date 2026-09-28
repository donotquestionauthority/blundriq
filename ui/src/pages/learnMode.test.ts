/** The pure half of Learn mode: eligibility (no termination test), the prompt plies under both
 *  filter states, the stepper, the pinned copy and the elapsed format. */
import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";
import { LEARN_COMMIT_PROMPT, LEARN_PROMPTS_IN_CHECK, LEARN_PROMPTS_STANDARD, fenActiveColor, formatElapsed, isEligiblePly, learnPromptPlies, learnStepPly } from "./learnMode";

function spine(sans: string[], startFen?: string) {
  const c = startFen ? new Chess(startFen) : new Chess();
  const fenSequence = [c.fen()];
  const moves: string[] = [];
  for (const san of sans) {
    moves.push(c.move(san).san);
    fenSequence.push(c.fen());
  }
  return { fenSequence, moves };
}

// K+B vs K: chess.js calls it over (insufficient material); three legal moves exist and play went on.
const INSUFFICIENT = "8/8/8/4k3/8/8/4KB2/8 w - - 0 50";
// Halfmove clock at 100: chess.js calls it a draw; it is claimable, and play went on.
const HALFMOVE_100 = "7r/8/8/4k3/8/8/4K3/7R w - - 100 60";

describe("eligibility", () => {
  it("a position chess.js calls over is still a decision when a move was played from it", () => {
    expect(new Chess(INSUFFICIENT).isGameOver()).toBe(true);
    const a = spine(["Bd4", "Kd6"], INSUFFICIENT);
    expect(isEligiblePly({ ply: 0, ...a, playerColor: "white" })).toBe(true);
    expect(new Chess(HALFMOVE_100).isDraw()).toBe(true);
    const b = spine(["Rxh8", "Kd5"], HALFMOVE_100);
    expect(isEligiblePly({ ply: 0, ...b, playerColor: "white" })).toBe(true);
    const c = spine(["Nf3", "Nf6", "Ng1", "Ng8", "Nf3", "Nf6"]);
    expect(isEligiblePly({ ply: 4, ...c, playerColor: "white" })).toBe(true);
  });
  it("the final position is ineligible whatever ended the game, and so is a deep link to it", () => {
    const { fenSequence, moves } = spine(["e4", "e5", "Nf3"]);
    expect(new Chess(fenSequence[3]).moves().length).toBeGreaterThan(0);
    expect(isEligiblePly({ ply: 3, fenSequence, moves, playerColor: "black" })).toBe(false);
  });
  it("length drift makes no ply eligible", () => {
    const { fenSequence, moves } = spine(["e4", "e5", "Nf3"]);
    const drifted = fenSequence.slice(0, -1);
    for (let p = 0; p < moves.length; p++) expect(isEligiblePly({ ply: p, fenSequence: drifted, moves, playerColor: "white" })).toBe(false);
  });
  it("an opponent-turn ply is ineligible, and the turn comes from the FEN, never ply parity", () => {
    const { fenSequence, moves } = spine(["e4", "e5", "Nf3"]);
    expect(isEligiblePly({ ply: 1, fenSequence, moves, playerColor: "white" })).toBe(false);
    expect(isEligiblePly({ ply: 1, fenSequence, moves, playerColor: "black" })).toBe(true);
    const blackToMove = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
    expect(fenActiveColor(blackToMove)).toBe("black");
    const b = spine(["e5", "Nf3"], blackToMove);
    expect(isEligiblePly({ ply: 0, ...b, playerColor: "black" })).toBe(true);
    expect(isEligiblePly({ ply: 0, ...b, playerColor: "white" })).toBe(false);
    expect(fenActiveColor(null)).toBeNull();
    expect(fenActiveColor("not a fen")).toBeNull();
  });
});

describe("prompt plies and the stepper", () => {
  const { fenSequence, moves } = spine(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]);
  it("unchecked prompts at every eligible ply and no opponent-turn ply; checked only at classified plies", () => {
    expect(learnPromptPlies({ fenSequence, moves, playerColor: "white", inaccOnly: false, blunderPlies: [] })).toEqual([0, 2, 4, 6]);
    expect(learnPromptPlies({ fenSequence, moves, playerColor: "black", inaccOnly: false, blunderPlies: [] })).toEqual([1, 3, 5]);
    expect(learnPromptPlies({ fenSequence, moves, playerColor: "white", inaccOnly: true, blunderPlies: [6, 2] })).toEqual([2, 6]);
  });
  it("checked with no classified plies is the empty set, never every ply; a classified opponent ply is still out", () => {
    expect(learnPromptPlies({ fenSequence, moves, playerColor: "white", inaccOnly: true, blunderPlies: [] })).toEqual([]);
    expect(learnPromptPlies({ fenSequence, moves, playerColor: "white", inaccOnly: true, blunderPlies: [3] })).toEqual([]);
    const drifted = fenSequence.slice(0, -1);
    expect(learnPromptPlies({ fenSequence: drifted, moves, playerColor: "white", inaccOnly: false, blunderPlies: [] })).toEqual([]);
  });
  it("the stepper lands only on prompt plies and never past the ends", () => {
    const plies = [2, 6];
    expect(learnStepPly(0, 1, plies)).toBe(2);
    expect(learnStepPly(2, 1, plies)).toBe(6);
    expect(learnStepPly(6, 1, plies)).toBe(6);
    expect(learnStepPly(6, -1, plies)).toBe(2);
    expect(learnStepPly(2, -1, plies)).toBe(2);
    expect(learnStepPly(4, -1, plies)).toBe(2);
  });
});

describe("the copy", () => {
  it("four standard questions, three in-check ones that replace them, and an imperative commit step", () => {
    expect(LEARN_PROMPTS_STANDARD).toEqual(["What is your opponent threatening?", "What are your checks?", "What are your captures?", "What can you attack?"]);
    expect(LEARN_PROMPTS_IN_CHECK).toEqual(["Can you take the checking piece with something other than your king?", "Can you block the check?", "Where can your king go? It may be able to capture."]);
    for (const p of [...LEARN_PROMPTS_STANDARD, ...LEARN_PROMPTS_IN_CHECK]) {
      expect(p.endsWith("?") || p.endsWith("capture.")).toBe(true);
      expect(p.toLowerCase()).not.toContain("you are in check");
    }
    expect(LEARN_COMMIT_PROMPT).toBe("Play the move you would actually make.");
  });
  it("elapsed reads as m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(42_400)).toBe("0:42");
    expect(formatElapsed(61_000)).toBe("1:01");
    expect(formatElapsed(-5)).toBe("0:00");
  });
});
