/**
 * The promotion chooser. react-chessboard has no promotion dialog of its own, so a board that
 * accepts a pawn's move to the last rank snaps it back, records the squares and opens this; the
 * chosen piece applies the real move. From/to squares cannot tell a queen from a knight, so
 * without it a legal underpromotion could not be entered — and Learn mode stores the committed
 * move as a chess fact.
 */
export type PromotionPiece = "q" | "r" | "b" | "n";

const PIECE_LABEL = { q: "Q", r: "R", b: "B", n: "N" } as const;
const PIECE_NAME = { q: "queen", r: "rook", b: "bishop", n: "knight" } as const;

export function PromotionChooser({ onChoose }: { onChoose: (p: PromotionPiece) => void }) {
  return (
    <div className="mt-2 flex items-center justify-center gap-2">
      <span className="text-sm text-zinc-500">Promote to:</span>
      {(["q", "r", "b", "n"] as const).map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => onChoose(p)}
          className="h-9 w-9 rounded border border-zinc-300 bg-white text-base font-semibold hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:bg-zinc-800"
          aria-label={`Promote to ${PIECE_NAME[p]}`}
        >
          {PIECE_LABEL[p]}
        </button>
      ))}
    </div>
  );
}
