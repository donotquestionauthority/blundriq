/**
 * Display formatting for a note's prose. Imported course notes wrap inline move references as
 * `@@SANStart@@<san>@@SANEnd@@`; the markup stays in storage so the walk-through can make the
 * moves clickable, and every other surface renders the bare move through this one function.
 */
import { unwrapBrackets } from "./lineReader";

export function formatAnnotationText(raw: string | null | undefined): string {
  if (!raw) return "";
  return unwrapBrackets(raw)
    .replace(/@@SANStart@@(.*?)@@SANEnd@@/g, "$1")
    .replace(/@@SAN(?:Start|End)@@/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}
