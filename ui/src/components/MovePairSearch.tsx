import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { Chessboard } from "react-chessboard";
import { useSearchParams } from "react-router";
import { getMovePair } from "../repertoire";
import type { MovePairPiece, MovePairQuery, MovePairResponse, MovePairRow } from "../repertoire";
import { describe, formatPair, parsePair, PIECES, SQUARE } from "../movePair";
import { ARROWS, SQUARES } from "../utils/board";
import { numberedLine } from "../utils/chess";

const PAGE = 50;

/**
 * "Find a move pair": one side moves a piece to a square, the other side answers, anywhere in a
 * line of the chosen colour's repertoire. Move 2 may be left open (any reply), and then the
 * replies are listed with their counts; a reply chip fills move 2 and searches. Rows the player
 * practises (the pair inside the segment their puzzle serves) come first.
 *
 * The search lives in the URL (`?pair=white.opponent.Qd2.Bb4`, `any` for an open move 2), so a
 * reload or Back shows the same search; a search runs from the URL, only on submit, never per
 * keystroke; Search on the URL's own search asks again, and so does any change to the repertoire on
 * the page (`revision`). An answer is shown only for its own request identity (the search, the
 * re-ask, the repertoire revision): Back to a search may show that same request's earlier answer
 * while it is asked again, never an answer from before a change to the repertoire.
 */

function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex overflow-hidden rounded border border-zinc-300 text-sm dark:border-zinc-700">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)} className={`px-2.5 py-1 ${value === o.value ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

const fieldClass = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";

function ResultRow({ row, orientation, onWalk }: { row: MovePairRow; orientation: "white" | "black"; onWalk: (row: MovePairRow) => void }) {
  const [board, setBoard] = useState(false);
  const boardId = "pair" + useId().replace(/[^a-zA-Z0-9-]/g, "");
  const arrow = { startSquare: row.move2Uci.slice(0, 2), endSquare: row.move2Uci.slice(2, 4), color: ARROWS.book };
  return (
    <li className="py-2" data-testid="move-pair-row">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <span className="font-mono text-sm">{row.label}</span>
          <span className="ml-2 text-xs text-zinc-500">
            {row.book} › {row.chapter} › {row.line}
          </span>
        </div>
        <div className="flex gap-2 text-xs">
          <button type="button" onClick={() => onWalk(row)} className="rounded border border-zinc-300 px-2 py-0.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
            Walk through
          </button>
          <button type="button" aria-expanded={board} onClick={() => setBoard((b) => !b)} className="rounded border border-zinc-300 px-2 py-0.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
            Board
          </button>
        </div>
      </div>
      {board && (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row" data-testid="move-pair-board">
          <div className="aspect-square w-full max-w-[280px] self-start">
            <Chessboard options={{ id: boardId, position: row.fenBefore2, boardOrientation: orientation, allowDragging: false, arrows: [arrow], animationDurationInMs: 0, boardStyle: { borderRadius: "6px" }, ...SQUARES }} />
          </div>
          <p className="font-mono text-xs leading-relaxed text-zinc-600 dark:text-zinc-400" data-testid="move-pair-moves">
            {numberedLine(row.rootFen, row.movesToPair).join(" ")}
          </p>
        </div>
      )}
    </li>
  );
}

function Section({ title, rows, orientation, onWalk }: { title: string; rows: MovePairRow[]; orientation: "white" | "black"; onWalk: (row: MovePairRow) => void }) {
  if (rows.length === 0) return null;
  return (
    <section aria-label={title} className="mt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</h3>
      <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
        {rows.map((r) => (
          <ResultRow key={`${r.lineId}:${r.ply}`} row={r} orientation={orientation} onWalk={onWalk} />
        ))}
      </ul>
    </section>
  );
}

/** `revision` changes whenever the repertoire on the page changes (a toggle, a deletion): the
 *  search on show is asked again, since it reads only effectively active lines. */
export function MovePairSearch({ onWalk, revision }: { onWalk: (row: MovePairRow) => void; revision: number }) {
  const [params, setParams] = useSearchParams();
  const pairParam = params.get("pair");
  const current = parsePair(pairParam);
  const [open, setOpen] = useState(current !== null);
  const [colour, setColour] = useState<"white" | "black">(current?.colour ?? "white");
  const [first, setFirst] = useState<"me" | "opponent">(current?.first ?? "opponent");
  const [p1, setP1] = useState<MovePairPiece>(current?.p1 ?? "Q");
  const [s1, setS1] = useState(current?.s1 ?? "");
  const [p2, setP2] = useState<MovePairPiece | "any">(current?.move2?.piece ?? "any");
  const [s2, setS2] = useState(current?.move2?.square ?? "");
  const [result, setResult] = useState<{ key: string; data: MovePairResponse } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  // Bumped by Search on the URL's own search: the same URL is asked again (after a failure, or
  // after the repertoire changed elsewhere).
  const [ask, setAsk] = useState(0);
  const [more, setMore] = useState<{ key: string | null; n: number }>({ key: null, n: PAGE });
  const key = current ? formatPair(current) : null;
  // One request's identity: the search, the explicit re-ask and the repertoire it read. A result
  // or failure shows only for the request on screen, never for an earlier one of the same search.
  const req = key === null ? null : `${key}#${ask}#${revision}`;
  const limit = more.key === key ? more.n : PAGE;

  // The form follows the URL (Back, a reply chip), so what is shown is what was searched.
  const [synced, setSynced] = useState(pairParam);
  if (synced !== pairParam) {
    setSynced(pairParam);
    if (current) {
      setOpen(true);
      setColour(current.colour);
      setFirst(current.first);
      setP1(current.p1);
      setS1(current.s1);
      setP2(current.move2?.piece ?? "any");
      setS2(current.move2?.square ?? "");
    }
  }

  useEffect(() => {
    const q = parsePair(pairParam);
    if (!q) return;
    const k = `${formatPair(q)}#${ask}#${revision}`;
    const ctrl = new AbortController();
    getMovePair(q, ctrl.signal).then(
      (data) => {
        if (!ctrl.signal.aborted) setResult({ key: k, data });
      },
      () => {
        if (!ctrl.signal.aborted) setFailure({ key: k, message: "Could not search the repertoire." });
      },
    );
    return () => {
      ctrl.abort();
      setFailure(null); // a failure belongs to its request: a later visit to the same search asks afresh
    };
  }, [pairParam, ask, revision]);

  const square1 = s1.trim().toLowerCase();
  const square2 = s2.trim().toLowerCase();
  const valid = SQUARE.test(square1) && (p2 === "any" || SQUARE.test(square2));

  const search = (q: MovePairQuery) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("pair", formatPair(q));
        return next;
      },
      { preventScrollReset: true },
    );
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    const q: MovePairQuery = { colour, first, p1, s1: square1, move2: p2 === "any" ? null : { piece: p2, square: square2 } };
    if (formatPair(q) === key) setAsk((n) => n + 1);
    else search(q);
  };

  const shown = result && result.key === req ? result.data : null;
  const failed = failure && failure.key === req && !shown ? failure.message : null;
  const loading = key !== null && !shown && !failed;
  const rows = shown ? shown.rows.slice(0, limit) : [];
  const second = first === "me" ? "Opponent" : "Me";

  return (
    <section aria-label="Find a move pair" className="mb-4 rounded border border-zinc-200 dark:border-zinc-800">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium">
        Find a move pair <span className="text-zinc-400">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="border-t border-zinc-200 px-3 pb-3 pt-2 dark:border-zinc-800">
          <form onSubmit={submit} className="flex flex-col gap-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="w-24 text-zinc-500">I play</span>
              <Segmented label="I play" value={colour} onChange={setColour} options={[{ value: "white", label: "White" }, { value: "black", label: "Black" }]} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="w-24 text-zinc-500">Move 1 by</span>
              <Segmented label="Move 1 by" value={first} onChange={setFirst} options={[{ value: "me", label: "Me" }, { value: "opponent", label: "Opponent" }]} />
              <select aria-label="Move 1 piece" value={p1} onChange={(e) => setP1(e.target.value as MovePairPiece)} className={fieldClass}>
                {PIECES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              <span className="text-zinc-500">to</span>
              <input aria-label="Move 1 square" value={s1} onChange={(e) => setS1(e.target.value)} placeholder="d2" maxLength={2} autoCapitalize="none" autoCorrect="off" spellCheck={false} className={`${fieldClass} w-14 font-mono`} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="w-24 text-zinc-500">Move 2 by</span>
              <span className="rounded bg-zinc-100 px-2.5 py-1 dark:bg-zinc-800" data-testid="move2-player">
                {second}
              </span>
              <select aria-label="Move 2 piece" value={p2} onChange={(e) => setP2(e.target.value as MovePairPiece | "any")} className={fieldClass}>
                <option value="any">Any reply</option>
                {PIECES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              {p2 !== "any" && (
                <>
                  <span className="text-zinc-500">to</span>
                  <input aria-label="Move 2 square" value={s2} onChange={(e) => setS2(e.target.value)} placeholder="b4" maxLength={2} autoCapitalize="none" autoCorrect="off" spellCheck={false} className={`${fieldClass} w-14 font-mono`} />
                </>
              )}
            </div>
            <div>
              <button type="submit" disabled={!valid} className="rounded bg-zinc-900 px-3 py-1 text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900">
                Search
              </button>
            </div>
          </form>
          {loading && <p className="mt-3 text-sm text-zinc-500">Searching…</p>}
          {failed && (
            <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
              {failed}
            </p>
          )}
          {shown && current && (
            <div data-testid="move-pair-results">
              {shown.replies && shown.replies.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="Replies">
                  <span className="text-xs text-zinc-500">Replies:</span>
                  {shown.replies.map((r) => (
                    <button key={`${r.piece}${r.square}`} type="button" onClick={() => search({ ...current, move2: { piece: r.piece, square: r.square } })} className="rounded-full border border-zinc-300 px-2 py-0.5 font-mono text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
                      {r.sans.join("/")} · {r.count}
                      {r.inPractice > 0 && <span className="ml-1 rounded bg-violet-100 px-1 text-violet-800 dark:bg-violet-900 dark:text-violet-200">{r.inPractice} in practice</span>}
                    </button>
                  ))}
                </div>
              )}
              {shown.total === 0 ? (
                <p className="mt-3 text-sm text-zinc-500">
                  No line in your {current.colour === "white" ? "White" : "Black"} repertoire has {describe(current)}.
                </p>
              ) : (
                <>
                  <Section title="In practice" rows={rows.filter((r) => r.inPractice)} orientation={current.colour} onWalk={onWalk} />
                  <Section title="Rest of the repertoire" rows={rows.filter((r) => !r.inPractice)} orientation={current.colour} onWalk={onWalk} />
                  <p className="mt-2 text-xs text-zinc-500">
                    {shown.truncated ? `${shown.rows.length} of ${shown.total} matches loaded — narrow the search to see the rest.` : `${shown.total} ${shown.total === 1 ? "match" : "matches"}.`}
                  </p>
                  {shown.rows.length > limit && (
                    <button type="button" onClick={() => setMore({ key, n: limit + PAGE })} className="mt-1 text-sm text-zinc-600 underline hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100">
                      Show more
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
