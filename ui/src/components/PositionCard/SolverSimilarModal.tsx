/**
 * Similar positions from inside the solver: the compare view over the search for the board the
 * player is deciding at (or just decided at) and the line's move there. The solver owns the search
 * (`useSimilarPositions`, enabled while this is open), so closing and reopening on the same board
 * shows the remembered answer, and a change of target while it is open aborts the outstanding
 * request and asks about the new one. Opens at once and shows the search's own states. The pinned
 * board draws the line's move in the book's colour, as the neighbours draw theirs.
 */
import { useMemo } from "react";
import type { SimilarPositions } from "../../hooks/useSimilarPositions";
import { ARROWS } from "../../utils/board";
import { moveArrows } from "../../utils/chess";
import { SimilarCompareView } from "./SimilarCompareView";

export function SolverSimilarModal({ fen, move, search, orientation, onClose }: { fen: string; move: string; search: SimilarPositions; orientation: "white" | "black"; onClose: () => void }) {
  const { status, data, retry } = search;
  const mainArrows = useMemo(() => moveArrows(fen, [{ move, inPlay: true, color: ARROWS.book }]), [fen, move]);
  return (
    <SimilarCompareView
      fen={fen}
      mainArrows={mainArrows}
      neighbours={status === "loaded" ? (data?.neighbours ?? []) : []}
      orientation={orientation}
      onClose={onClose}
      status={status === "idle" ? "loading" : status}
      onRetry={retry}
      maxDistance={data?.query.max_distance ?? null}
    />
  );
}
