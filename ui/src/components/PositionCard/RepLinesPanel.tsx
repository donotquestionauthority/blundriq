import { useState } from "react";
import { daysAgo } from "../../blunders";
import type { RepLine } from "../../repertoire";
import { buildPgn } from "../../utils/chess";

/** The repertoire lines through a card's position, grouped by book, with the move each
 *  recommends from here and how the player's games have fared at this board. */

function PgnBlock({ line }: { line: RepLine }) {
  const [copied, setCopied] = useState(false);
  const { line_moves: moves, line_ply: ply } = line;
  if (!moves.length || ply < 0) return null;
  const pgn = buildPgn(moves, ply);
  return (
    <div className="mt-1 rounded border border-zinc-200 px-2 py-1.5 dark:border-zinc-800">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">Line to here + continuation</span>
        {pgn && (
          <button
            type="button"
            className="text-[10px] text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
            onClick={async (e) => {
              e.stopPropagation();
              await navigator.clipboard.writeText(pgn);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied ? "✓ copied" : "Copy PGN"}
          </button>
        )}
      </div>
      <p className="break-all font-mono text-[10px] leading-relaxed">
        {moves.map((m, i) => (
          <span key={i}>
            {i % 2 === 0 && <span className="mr-0.5 text-zinc-400">{i / 2 + 1}.</span>}
            <span className={`mr-1 ${i === ply ? "font-bold text-emerald-600 underline dark:text-emerald-400" : i < ply ? "text-zinc-500" : ""}`}>{m}</span>
          </span>
        ))}
      </p>
    </div>
  );
}

function devDetail(expected: string | null, played: string | null, ply: number | null): string {
  const moveNum = ply != null ? ` (move ${Math.ceil((ply + 1) / 2)})` : "";
  if (expected && played && expected !== played) return `expected ${expected}, played ${played}${moveNum}`;
  if (played) return `played ${played}${moveNum}`;
  if (expected) return `expected ${expected}${moveNum}`;
  return moveNum.trim();
}

export function RepLinesPanel({ lines }: { lines: RepLine[] }) {
  const [expanded, setExpanded] = useState(false);
  const showToggle = lines.length > 1;
  const visible = showToggle && !expanded ? lines.slice(0, 1) : lines;
  const byBook = new Map<string, RepLine[]>();
  for (const l of visible) byBook.set(l.book, [...(byBook.get(l.book) ?? []), l]);
  return (
    <div className="space-y-3 rounded border border-amber-300/60 bg-amber-50/40 p-3 dark:border-amber-700/40 dark:bg-amber-900/10" data-testid="rep-lines">
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-wide text-amber-800 dark:text-amber-300">Repertoire lines ({lines.length})</p>
        {showToggle && (
          <button type="button" className="text-xs text-amber-800 hover:underline dark:text-amber-300" onClick={() => setExpanded((e) => !e)}>
            {expanded ? "Show fewer" : `Show all ${lines.length} lines`}
          </button>
        )}
      </div>
      {[...byBook.entries()].map(([book, bookLines]) => (
        <div key={book}>
          <p className="mb-1 text-xs font-medium">📖 {book}</p>
          <div className="ml-3 space-y-3">
            {bookLines.map((l, i) => (
              <div key={`${l.line_id}-${l.line_ply}-${i}`} className="space-y-0.5 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-zinc-600 dark:text-zinc-400">{l.chapter}</span>
                  {l.line_name && (
                    <span className="italic text-zinc-500">
                      · {l.line_name}
                      {l.is_alternative ? " (alt)" : ""}
                    </span>
                  )}
                  {l.expected_move && <span className="font-mono font-medium text-emerald-600 dark:text-emerald-400">→ {l.expected_move}</span>}
                </div>
                <PgnBlock line={l} />
                <div className="flex flex-wrap gap-x-3 text-zinc-500">
                  {l.followed > 0 && (
                    <span>
                      ✓ Followed {l.followed}×{l.last_followed ? ` · last ${daysAgo(l.last_followed)}` : ""}
                    </span>
                  )}
                  {l.deviated_by_me > 0 && (
                    <span>
                      ↗ I deviated {l.deviated_by_me}×{l.last_deviated ? ` · last ${daysAgo(l.last_deviated)}` : ""} — {devDetail(l.me_dev_expected, l.me_dev_played, l.me_dev_ply)}
                    </span>
                  )}
                  {l.deviated_by_opp > 0 && (
                    <span>
                      ↗ Opponent deviated {l.deviated_by_opp}× — {devDetail(l.opp_dev_expected, l.opp_dev_played, l.opp_dev_ply)}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
