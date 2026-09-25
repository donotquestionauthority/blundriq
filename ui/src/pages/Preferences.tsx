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
  if (key.startsWith("similar_") || key.startsWith("branch_compare_")) return "Repertoire";
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

type FieldProps = {
  name: string;
  schema: PropSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  onParseError: (message: string | null) => void;
  parseError: string | null;
};

function Field({ name, schema, value, onChange, onParseError, parseError }: FieldProps) {
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
    // Lists, tuples and maps are edited as JSON text. Malformed text is a visible
    // error that blocks Save; it is never silently replaced by the previous value.
    control = <JsonField id={id} className={`${base} w-full font-mono`} value={value} onChange={onChange} onParseError={onParseError} parseError={parseError} />;
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
        {parseError && <p className="mt-1 text-xs text-red-600">{parseError}</p>}
        {schema.description && <p className="mt-1 text-xs text-zinc-500">{schema.description}</p>}
      </div>
    </div>
  );
}

function JsonField({
  id,
  className,
  value,
  onChange,
  onParseError,
  parseError,
}: {
  id: string;
  className: string;
  value: unknown;
  onChange: (v: unknown) => void;
  onParseError: (message: string | null) => void;
  parseError: string | null;
}) {
  const [text, setText] = useState(() => JSON.stringify(value));
  const [synced, setSynced] = useState(value);
  // When a save returns the canonical value (or the value changes elsewhere), show it —
  // derived during render, so the user's in-progress (possibly malformed) text is kept.
  if (value !== synced && !parseError) {
    setSynced(value);
    setText(JSON.stringify(value));
  }
  return (
    <input
      id={id}
      type="text"
      className={className}
      value={text}
      aria-invalid={parseError ? true : undefined}
      onChange={(e) => {
        setText(e.target.value);
        try {
          onChange(JSON.parse(e.target.value));
          onParseError(null);
        } catch (err) {
          onParseError(err instanceof Error ? `Not valid JSON: ${err.message}` : "Not valid JSON");
        }
      }}
    />
  );
}

export default function Preferences() {
  const [schema, setSchema] = useState<JsonSchema | null>(null);
  const [values, setValues] = useState<Values | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [parseErrors, setParseErrors] = useState<Record<string, string>>({});
  const hasParseErrors = Object.keys(parseErrors).length > 0;

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

  function setParseError(key: string, message: string | null) {
    setParseErrors((prev) => {
      const next = { ...prev };
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });
  }

  async function save() {
    if (!values) return;
    if (hasParseErrors) {
      setStatus("Not saved: fix the fields marked in red.");
      return;
    }
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
        <button
          onClick={save}
          disabled={hasParseErrors}
          className="rounded bg-zinc-900 px-3 py-1 text-sm text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
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
            <Field
              key={k}
              name={k}
              schema={schema.properties[k]}
              value={values[k]}
              onChange={(v) => setValues({ ...values, [k]: v })}
              onParseError={(m) => setParseError(k, m)}
              parseError={parseErrors[k] ?? null}
            />
          ))}
        </section>
      ))}
    </div>
  );
}
