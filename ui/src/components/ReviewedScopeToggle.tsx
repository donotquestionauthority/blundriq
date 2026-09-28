import type { ReviewedScope } from "../review";

const OPTIONS: Array<{ key: ReviewedScope; label: string }> = [
  { key: "to_review", label: "To review" },
  { key: "all", label: "All" },
];

/** To review (default) / All. Client-owned: switching never refetches the page, only open drill-downs. */
export function ReviewedScopeToggle({ value, onChange }: { value: ReviewedScope; onChange: (scope: ReviewedScope) => void }) {
  return (
    <div className="text-xs text-zinc-500">
      Show
      <div role="tablist" aria-label="Reviewed scope" className="mt-1 inline-flex rounded border border-zinc-300 p-0.5 dark:border-zinc-700">
        {OPTIONS.map((o) => {
          const active = o.key === value;
          return (
            <button key={o.key} type="button" role="tab" aria-selected={active} onClick={() => onChange(o.key)} className={`rounded px-3 py-0.5 text-sm ${active ? "bg-zinc-900 font-medium text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"}`}>
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
