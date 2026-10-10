import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";
import { namedMove } from "./namedMove";

const START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const STALEMATE_TRAP = "7k/8/5KQ1/8/8/8/8/8 w - - 0 1"; // Qf7 stalemates, Qg7 mates, Qh6+ plays on
const after = (fen: string, san: string) => {
  const g = new Chess(fen);
  g.move(san);
  return g.fen();
};

describe("namedMove", () => {
  it("is the first token that is a legal move and not the engine's", () => {
    expect(namedMove("Why can't I play Nf3 here?", START, "e4")).toEqual({ san: "Nf3", fenAfter: after(START, "Nf3"), outcome: null });
    expect(namedMove("I don't get this part", START, "e4")).toBeNull();
    expect(namedMove("Why is e4 best? Not d4?", START, "e4")).toEqual({ san: "d4", fenAfter: after(START, "d4"), outcome: null });
  });
  it("ignores a token that does not play here: an illegal square, a capture of nothing, the null move", () => {
    expect(namedMove("Nf9?", START, null)).toBeNull();
    expect(namedMove("Bxe5", START, null)).toBeNull();
    expect(namedMove("-- or 0000", START, null)).toBeNull();
    expect(namedMove("Nf3", "not a fen", null)).toBeNull();
  });
  it("recognises castling in both spellings and a capture with check, with the punctuation around them", () => {
    const castle = "r3k2r/pppqppbp/2np1np1/8/3PP3/2N2N2/PPPBBPPP/R3K2R w KQkq - 0 9";
    expect(namedMove("why not O-O?", castle, "e4")?.san).toBe("O-O");
    expect(namedMove("(0-0-0!)", castle, "e4")?.san).toBe("O-O-O");
    const capture = "rnbqkbnr/ppp2ppp/8/3pp3/3PP3/8/PPP2PPP/RNBQKBNR w KQkq - 0 3";
    expect(namedMove("Is exd5 bad, or dxe5?", capture, "Nf3")?.san).toBe("exd5");
    expect(namedMove("what about 'Qh5+'?", "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", null)?.san).toBe("Qh5");
  });
  it("names a move that ends the game with its outcome and no board to search", () => {
    expect(namedMove("Why can't I play Qf7?", STALEMATE_TRAP, "Qh6+")).toEqual({ san: "Qf7", fenAfter: null, outcome: "stalemate" });
    expect(namedMove("Qg7", STALEMATE_TRAP, "Qh6+")).toEqual({ san: "Qg7#", fenAfter: null, outcome: "checkmate" });
    expect(namedMove("Kxe2?", "8/8/8/8/8/8/4n3/k3K3 w - - 0 1", null)?.outcome).toBe("draw");
    expect(namedMove("d1=Q", "8/8/8/8/8/8/3p4/k3K3 b - - 0 1", null)).toEqual({ san: "d1=Q+", fenAfter: "8/8/8/8/8/8/8/k2qK3 w - - 0 2", outcome: null });
  });
  it("a skipped move gives way to the next one named", () => {
    expect(namedMove("Nf3 or Nc3?", START, "e4", ["Nf3"])?.san).toBe("Nc3");
    expect(namedMove("Nf3?", START, "e4", ["Nf3"])).toBeNull();
  });
  it("the engine's own move is not an alternative, however it is spelt", () => {
    expect(namedMove("Qh6+", STALEMATE_TRAP, "Qh6")).toBeNull();
    expect(namedMove("Qh6", STALEMATE_TRAP, "Qh6+")).toBeNull();
  });
});
