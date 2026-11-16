import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import type { PositionCardData } from "../components/PositionCard/types";

/**
 * A list page's way back from a game's review. A game's date (`GamesTable`) replaces the state of
 * the list's own history entry with the page's snapshot and opens the review with `from.back`;
 * the review leaves by `leaveReview`, which goes back one entry (`navigate(-1)`), as the browser's
 * Back does. Either way the list mounts on its own entry, reads the snapshot once, and removes it
 * from the entry, so the same list comes back with the same card open, and a later reload or Back
 * to that entry finds nothing and starts a fresh visit. `kind` names the page and the snapshot's
 * shape; a page that changes the shape changes its kind, so a snapshot written by an older build
 * is never read.
 */
export interface CardReturn<K extends string, F> {
  kind: K;
  filters: F;
  page: number;
  /** The card that was showing, by its identity in the list (`cardIdentity`). */
  openKey: string;
}

/** A card's identity in its list: the key `PositionList` renders it under. */
export const cardIdentity = (item: Pick<PositionCardData, "key" | "fen">): string => item.key ?? item.fen;

/** The snapshot of `kind` a history entry carries, or null. */
export function readCardReturn<T extends { kind: string }>(state: unknown, kind: T["kind"]): T | null {
  const card = (state as { card?: { kind?: unknown } } | null)?.card;
  return card && typeof card === "object" && card.kind === kind ? (card as T) : null;
}

/** The snapshot of `kind` this entry arrived with, read once at mount and then removed from the
 *  entry (the rest of its state kept). */
export function useCardReturn<T extends { kind: string }>(kind: T["kind"]): T | null {
  const location = useLocation();
  const navigate = useNavigate();
  const [restore] = useState(() => readCardReturn<T>(location.state, kind));
  useEffect(() => {
    if (!restore) return;
    const { card: _spent, ...rest } = (location.state ?? {}) as { card?: unknown };
    navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: Object.keys(rest).length ? rest : null });
    // Once, for the entry the page mounted on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return restore;
}
