import { usePracticeToday } from "../practiceToday";

/**
 * Today's puzzles: solved toward the daily target, and tried. Two presentations of one shared
 * count — `toolbar` (with a slim progress bar) on the Practice page, `dialog` (one line) in the
 * puzzle dialog that covers it. Renders nothing while the count is unknown.
 *
 * `className` places it; nothing is rendered, so nothing takes space, while the count is unknown.
 * `quiet` silences a copy that is covered (the toolbar's, while a dialog is open), so a screen
 * reader announces one update, not two.
 */
export default function TodayCount({ variant, quiet = false, className = "" }: { variant: "toolbar" | "dialog"; quiet?: boolean; className?: string }) {
  const today = usePracticeToday();
  if (today === null) return null;
  const met = today.solved >= today.target;
  const tone = met ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-500";
  const label = `Puzzles today: ${today.solved} of ${today.target} solved, ${today.tried} tried`;
  const text = (
    <span className={`whitespace-nowrap text-xs ${tone}`}>
      {met && <span aria-hidden>✓ </span>}
      <span className="font-medium">
        {today.solved} / {today.target}
      </span>{" "}
      solved · {today.tried} tried
    </span>
  );
  const live = quiet ? { "aria-hidden": true as const } : { role: "status", "aria-live": "polite" as const, "aria-label": label };
  if (variant === "dialog") {
    return (
      <div className={className} {...live}>
        {text}
      </div>
    );
  }
  const pct = today.target > 0 ? Math.min(100, Math.round((today.solved / today.target) * 100)) : 0;
  return (
    <div className={`flex flex-col gap-1 ${className}`} {...live}>
      {text}
      <div className="h-1 w-full overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
        <div className={`h-full ${met ? "bg-emerald-500" : "bg-zinc-900 dark:bg-zinc-100"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
