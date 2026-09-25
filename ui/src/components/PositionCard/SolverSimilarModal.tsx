/**
 * Similar positions from inside the solver: the compare view over the search for the board the
 * player is deciding at (or just decided at) and the line's move there. Opens at once and shows the
 * search's own states; a change of target while it is open aborts the outstanding request and asks
 * about the new one (the hook's rule). The pinned board draws the line's move in the book's colour,
 * as the neighbours draw theirs.
 */
import { useMemo } from "react";
import { useSimilarPositions } from "../../hooks/useSimilarPositions";
import { ARROWS } from "../../utils/board";
import { moveArrows } from "../../utils/chess";
import { SimilarCompareView } from "./SimilarCompareView";

export function SolverSimilarModal({ fen, move, orientation, onClose }: { fen: string; move: string; orientation: "white" | "black"; onClose: () => void }) {
  const { status, data, retry } = useSimilarPositions(fen, move, true);
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
