import { locateScope } from "../practice";
import type { RepertoireScopeBook } from "../practice";

const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";

/** Book → Chapter → Line, each optional. The SubType is the narrowest choice; clearing a level
 *  falls back to the one above it, and choosing a level clears everything below it. */
export function RepertoireScopeFilter({ books, error, subtype, onChange }: { books: RepertoireScopeBook[] | null; error?: string | null; subtype: string | null; onChange: (v: string | null) => void }) {
  const at = locateScope(books ?? [], subtype);
  const book = books?.find((b) => b.id === at.book) ?? null;
  const chapter = book?.chapters.find((c) => c.id === at.chapter) ?? null;
  const loading = books == null && !error;
  // A filter the tree does not hold (switched off since, or no puzzle left under it) is still
  // filtering: say so, with a way out, rather than showing "All" over it.
  const orphan = books != null && subtype != null && at.book == null;
  return (
    <>
      {error && (
        <p role="alert" className="text-xs text-rose-600">
          Couldn't load your books: {error}
        </p>
      )}
      {orphan && (
        <p className="flex items-center justify-between gap-2 text-xs text-amber-700 dark:text-amber-400">
          This filter is no longer in your repertoire.
          <button type="button" className="underline" onClick={() => onChange(null)}>
            Clear
          </button>
        </p>
      )}
      <label className="flex flex-col gap-1 text-xs text-zinc-500">
        Book
        <select className={select} disabled={loading} value={at.book ?? ""} onChange={(e) => onChange(e.target.value ? `book:${e.target.value}` : null)}>
          <option value="">{loading ? "…" : "All books"}</option>
          {(books ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.title} ({b.count})
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-zinc-500">
        Chapter
        <select className={select} disabled={!book} value={at.chapter ?? ""} onChange={(e) => onChange(e.target.value ? `chapter:${e.target.value}` : `book:${at.book}`)}>
          <option value="">All chapters</option>
          {(book?.chapters ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.title} ({c.count})
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-zinc-500">
        Line
        <select className={select} disabled={!chapter} value={at.line ?? ""} onChange={(e) => onChange(e.target.value ? `line:${e.target.value}` : `chapter:${at.chapter}`)}>
          <option value="">All lines</option>
          {(chapter?.lines ?? []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.title} ({l.count})
            </option>
          ))}
        </select>
      </label>
    </>
  );
}
