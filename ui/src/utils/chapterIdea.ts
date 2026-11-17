import { Chess } from "chess.js";
import { ApiError } from "../api";
import type { ChapterMoveLink } from "../repertoire";
import { legalMove } from "./chess";

/** The chapter-idea dialog's words for each state the server reports. */
export const CHAPTER_MESSAGES = {
  stale: "This chapter's lines or notes have changed since this conversation began. Answers here are about the chapter as it was; Start over to ask about it as it is now.",
  emptied: "This chapter has no active lines now; answers here are about the chapter as it was.",
  noLines: "This chapter has no active lines now.",
  oldPrompt: "Asked with an earlier version of the chapter prompt; Start over uses the current one.",
  full: "This conversation is as long as it can get. Start over to keep asking.",
  conversation_params_invalid: "This conversation can't be continued; Start over.",
  model_retired: "This conversation's model is no longer available; Start over to ask with the current one.",
  unsaved: "This answer could not be saved; it will be gone when you close this.",
  deleted: "This line has been deleted.",
  thinking: "Thinking… this can take a minute or two.",
};

/** The server's refusal text: `{code, message}` for the dialog's own refusals, a string otherwise. */
export function refusalOf(e: unknown): { code: string | null; message: string } {
  if (!(e instanceof ApiError)) return { code: null, message: "The request failed." };
  const detail = (e.body as { detail?: unknown } | undefined)?.detail;
  if (detail && typeof detail === "object") {
    const d = detail as { code?: unknown; message?: unknown };
    if (typeof d.message === "string") return { code: typeof d.code === "string" ? d.code : null, message: d.message };
  }
  return { code: null, message: e.message || "The request failed." };
}

export function when(iso: string): string {
  const d = new Date(iso);
  return `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

/** The boards of a side line: the anchor's, then one after each branch move that plays. */
export function sideLineBoards(link: ChapterMoveLink): { fen: string; label: string }[] {
  const out = [{ fen: link.anchor_fen, label: link.anchor }];
  let g: Chess;
  try {
    g = new Chess(link.anchor_fen);
  } catch {
    return out;
  }
  for (const san of link.branch) {
    let played = null;
    try {
      const number = g.moveNumber();
      const white = g.turn() === "w";
      played = legalMove(g, san);
      if (!played) break;
      out.push({ fen: g.fen(), label: `${number}${white ? "." : "…"}${played.san}` });
    } catch {
      break;
    }
  }
  return out;
}
