import { Link } from "react-router";
import { ago, getHome, plural } from "../home";
import type { HomePage } from "../home";
import { useApi } from "../hooks/useApi";

/**
 * What to do today, and what changed since the last visit. Reads one endpoint and changes
 * nothing: "new" is since the Blunders list was last looked at, and only that page moves the
 * marker, so a new blunder keeps waiting here until it has been seen.
 */

const tile = "rounded-lg border border-zinc-200 p-4 dark:border-zinc-800";
const linkBtn = "inline-block rounded border border-zinc-900 bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";

function Progress({ done, target }: { done: number; target: number }) {
  const pct = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;
  return (
    <div role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={target} className="h-1.5 w-full overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
      <div className={`h-full ${pct >= 100 ? "bg-emerald-500" : "bg-zinc-900 dark:bg-zinc-100"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function Streak({ days }: { days: number }) {
  return <p className="text-xs text-zinc-500">{days === 0 ? "No streak yet" : `${days}-day streak`}</p>;
}

function Puzzles({ p }: { p: HomePage["puzzles"] }) {
  const met = p.solved_today >= p.target;
  return (
    <section className={tile} aria-labelledby="home-puzzles">
      <h2 id="home-puzzles" className="text-sm font-medium text-zinc-500">
        Puzzles
      </h2>
      <p className="mt-1 text-3xl font-bold">
        {p.due} <span className="text-base font-normal text-zinc-500">due</span>
      </p>
      <p className="mt-2 text-sm">
        {p.solved_today} of {p.target} solved today{met ? " — done" : ""}
      </p>
      <div className="mt-1.5">
        <Progress done={p.solved_today} target={p.target} />
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <Streak days={p.streak} />
        <Link to="/practice" className={linkBtn}>
          Practice →
        </Link>
      </div>
    </section>
  );
}

function Play({ g }: { g: HomePage["games"] }) {
  const met = g.target === 0 || g.today >= g.target;
  return (
    <section className={tile} aria-labelledby="home-play">
      <h2 id="home-play" className="text-sm font-medium text-zinc-500">
        Play
      </h2>
      <p className="mt-1 text-3xl font-bold">
        {g.today} <span className="text-base font-normal text-zinc-500">{g.today === 1 ? "game" : "games"} today</span>
      </p>
      <p className="mt-2 text-sm">
        {plural(g.week, "game")} this week
        {g.target > 0 && (met ? " — today's game is in" : ` — ${plural(g.target - g.today, "more game")} today ${g.streak > 0 ? "keeps" : "starts"} the streak`)}
      </p>
      {g.target > 0 && (
        <div className="mt-1.5">
          <Progress done={g.today} target={g.target} />
        </div>
      )}
      <div className="mt-3">{g.target > 0 ? <Streak days={g.streak} /> : <p className="text-xs text-zinc-500">No daily target set</p>}</div>
    </section>
  );
}

function ActivityStrip({ a }: { a: HomePage["activity"] }) {
  return (
    <section className={`${tile} mt-4`} aria-label="Played">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <span className="text-sm font-medium text-zinc-500">Played</span>
        {(
          [
            ["24 h", a.last_1],
            ["7 d", a.last_7],
            ["30 d", a.last_30],
            ["all", a.total],
          ] as const
        ).map(([label, n]) => (
          <span key={label} className="text-sm">
            <span className="text-zinc-500">{label} </span>
            <span className="font-semibold">{n}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

function LookedAt({ at }: { at: string | null }) {
  return <span className="text-xs text-zinc-500">{at ? `last looked ${new Date(at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` : "never looked"}</span>;
}

function SinceLastVisit({ d }: { d: HomePage }) {
  const nothingYet = d.since === null && d.deviations_since === null;
  return (
    <section className={tile} aria-labelledby="home-since">
      <h2 id="home-since" className="text-sm font-medium text-zinc-500">
        Since your last visit
      </h2>
      {nothingYet ? (
        <p className="mt-2 text-sm text-zinc-500">Nothing to compare against yet: open Blunders and Deviations once, and from then on this shows what the pipeline found since you last looked.</p>
      ) : (
        <div className="mt-2 space-y-2 text-sm">
          <p>
            {d.since === null ? (
              <span className="text-zinc-500">Open Blunders once to start counting</span>
            ) : d.new_blunders > 0 ? (
              <Link to="/blunders" className="underline decoration-zinc-400 underline-offset-2 hover:decoration-zinc-900 dark:hover:decoration-zinc-100">
                {plural(d.new_blunders, "new recurring blunder")}
              </Link>
            ) : (
              <span className="text-zinc-500">No new recurring blunders</span>
            )}
            <br />
            <LookedAt at={d.since} />
          </p>
          <p>
            {d.deviations_since === null ? (
              <span className="text-zinc-500">Open Deviations once to start counting</span>
            ) : d.new_deviations > 0 ? (
              <Link to="/deviations" className="underline decoration-zinc-400 underline-offset-2 hover:decoration-zinc-900 dark:hover:decoration-zinc-100">
                {plural(d.new_deviations, "new deviation pattern")}
              </Link>
            ) : (
              <span className="text-zinc-500">No new deviation patterns</span>
            )}
            <br />
            <LookedAt at={d.deviations_since} />
          </p>
        </div>
      )}
    </section>
  );
}

function Pipeline({ p }: { p: HomePage["pipeline"] }) {
  return (
    <div className="mt-6 space-y-1 text-xs text-zinc-500">
      <p>Pipeline: last successful run {ago(p.last_ok_at)}.</p>
      {p.failed.map((f) => (
        <p key={f.step} role="alert" className="text-red-600 dark:text-red-400">
          {f.step} failed {ago(f.started_at)}
          {f.error ? `: ${f.error}` : ""}
        </p>
      ))}
    </div>
  );
}

export default function Home() {
  const { data, error, isLoading } = useApi(getHome);
  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Home</h1>
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
      {isLoading && !data && <p className="mt-2 text-sm text-zinc-500">Loading…</p>}
      {data && (
        <>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <Puzzles p={data.puzzles} />
            <Play g={data.games} />
            <SinceLastVisit d={data} />
          </div>
          <ActivityStrip a={data.activity} />
          <Pipeline p={data.pipeline} />
        </>
      )}
    </div>
  );
}
