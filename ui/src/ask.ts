/** Types and calls for "Ask Opus about this position" (Review and Explore). */
import { api } from "./api";
import type { DryRun } from "./blunders";
import type { EngineSnapshot } from "./engine/eval";

export interface AskAnswer {
  explanation: string;
  cached: boolean;
  model: string;
}

/** The move a question names; `engine` is the snapshot after it, absent when the move ends the game. */
export interface AskAlternative {
  move: string;
  engine?: EngineSnapshot;
}

export interface ExploreAskRequest {
  seed_fen: string;
  orientation: "white" | "black";
  moves: string[];
  engine: EngineSnapshot;
  game?: { id: number; ply: number };
}

type Body = Record<string, unknown>;

const review = (ply: number, question: string, alternative: AskAlternative | null, dry_run: boolean): Body => ({ ply, question, alternative, dry_run });
const explore = (req: ExploreAskRequest, question: string, alternative: AskAlternative | null, dry_run: boolean): Body => ({ ...req, question, alternative, dry_run });

export const askReview = (gameId: number, ply: number, question: string, alternative: AskAlternative | null) => api.post<AskAnswer>(`/games/${gameId}/ask`, review(ply, question, alternative, false));
export const askReviewDryRun = (gameId: number, ply: number, question: string, alternative: AskAlternative | null) => api.post<DryRun>(`/games/${gameId}/ask`, review(ply, question, alternative, true));
export const askExplore = (req: ExploreAskRequest, question: string, alternative: AskAlternative | null) => api.post<AskAnswer>("/explore/ask", explore(req, question, alternative, false));
export const askExploreDryRun = (req: ExploreAskRequest, question: string, alternative: AskAlternative | null) => api.post<DryRun>("/explore/ask", explore(req, question, alternative, true));
