import { ApiError } from "../../api";

/** A refusal's text. The cap's refusal is an object with a message; everything else is a string. */
export function messageOf(err: unknown): string {
  if (!(err instanceof ApiError)) return "AI call failed";
  try {
    const detail: unknown = JSON.parse(err.message);
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  } catch {
    /* a plain string */
  }
  return err.message || "AI call failed";
}
