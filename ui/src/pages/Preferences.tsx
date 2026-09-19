import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError } from "../api";

/**
 * The Preferences page is generated from the settings JSON schema served by the
 * API (core/settings.py). A new setting appears here the moment it is declared
 * as a field; nothing in this file names a specific setting.
 */

type JsonSchema = {
  properties: Record<string, PropSchema>;
};
type PropSchema = {
  type?: string;
  description?: string;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  items?: { type?: string };
  prefixItems?: unknown[];
  anyOf?: PropSchema[];
  const?: unknown;
  additionalProperties?: { type?: string };
};
type Values = Record<string, unknown>;

function groupOf(key: string): string {
  const head = key.split("_")[0];
  const named: Record<string, string> = {
    daily: "Home",
    timezone: "General",
    time: "General",
    analysis: "Analysis",
    blunder: "Analysis",
    mistake: "Analysis",
    inaccuracy: "Analysis",
    miss: "Analysis",
    max: "Analysis",
    missed: "Analysis",
    motif: "Analysis",
    blunders: "Page defaults",
    deviations: "Page defaults",
    scout: "Scout",
    repertoire: "Repertoire",
    branch: "Scout",
    reply: "Scout",
    puzzle: "Puzzles",
    weak: "Puzzles",
    coverage: "Puzzles",
    cc0: "Corpus puzzles",
    lichess: "Corpus puzzles",
    srs: "Spaced repetition",
    review: "Review",
    explore: "Explore & AI",
    ai: "Explore & AI",
  };
  return named[head] ?? "Other";
}

function Field({ name, schema, value, onChange }: { name: string; schema: PropSchema; value: unknown; onChange: (v: unknown) => void }) {
  const id = `f-${name}`;
  const base = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  let control: ReactNode;
  const enumValues = schema.enum ?? schema.anyOf?.flatMap((a) => (a.const !== undefined ? [String(a.const)] : []));
  if (enumValues && enumValues.length > 0) {
    control = (
      <select id={id} className={base} value={String(value)} onChange={(e) => onChange(e.target.value)}>
        {enumValues.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  } else if (schema.type === "integer" || schema.type === "number") {
    control = (
      <input
        id={id}
        type="number"
        className={`${base} w-28`}
        value={value as number}
        min={schema.minimum}
        max={schema.maximum}
        onChange={(e) => onChange(schema.type === "integer" ? parseInt(e.target.value, 10) : parseFloat(e.target.value))}
      />
    );
  } else if (schema.type === "boolean") {
    control = <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
  } else if (schema.type === "array" || schema.type === "object") {
    // Lists, tuples and maps are edited as JSON; the server validates.
    control = (
      <input
        id={id}
        type="text"
        className={`${base} w-full font-mono`}
        defaultValue={JSON.stringify(value)}
        onBlur={(e) => {
          try {
            onChange(JSON.parse(e.target.value));
          } catch {
            /* keep previous value; server-side validation reports on save */
          }
        }}
      />
    );
  } else {
    control = <input id={id} type="text" className={`${base} w-64`} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <div className="grid grid-cols-1 gap-1 py-2 sm:grid-cols-[16rem_1fr] sm:items-start">
      <label htmlFor={id} className="text-sm font-medium">
        {name.replaceAll("_", " ")}
      </label>
      <div>
        {control}
        {schema.description && <p className="mt-1 text-xs text-zinc-500">{schema.description}</p>}
      </div>
    </div>
  );
}

export default function Preferences() {
  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [values, setValues] = useState<Values | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.get<JsonSchema>("/settings/schema"), api.get<Values>("/settings")]).then(([s, v]) => {
      setSchema(s);
      setValues(v);
    });
  }, []);

  const groups = useMemo(() => {
    if (!schema) return [];
    const map = new Map<string, string[]>();
    for (const key of Object.keys(schema.properties)) {
      const g = groupOf(key);
      map.set(g, [...(map.get(g) ?? []), key]);
    }
    return [...map.entries()];
  }, [schema]);

  async function save() {
    if (!values) return;
    setStatus("Saving…");
    try {
      const saved = await api.put<Values>("/settings", values);
      setValues(saved);
      setStatus("Saved.");
    } catch (e) {
      setStatus(e instanceof ApiError ? `Not saved: ${e.message}` : "Not saved.");
    }
  }

  if (!schema || !values) return <p className="text-sm text-zinc-500">Loading…</p>;

  return (
    <div>
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-semibold tracking-tight">Preferences</h1>
        <button onClick={save} className="rounded bg-zinc-900 px-3 py-1 text-sm text-white dark:bg-zinc-100 dark:text-zinc-900">
          Save
        </button>
        {status && <span className="text-sm text-zinc-500">{status}</span>}
      </div>
      {groups.map(([group, keys]) => (
        <section key={group} className="mt-6">
          <h2 className="mb-1 border-b border-zinc-200 pb-1 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-800">
            {group}
          </h2>
          {keys.map((k) => (
            <Field key={k} name={k} schema={schema.properties[k]} value={values[k]} onChange={(v) => setValues({ ...values, [k]: v })} />
          ))}
        </section>
      ))}
    </div>
  );
}
