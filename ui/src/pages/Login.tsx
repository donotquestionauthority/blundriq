import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router";
import { api, ApiError } from "../api";

export default function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/login", { password });
      onLoggedIn();
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Wrong password." : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">BlundrIQ</h1>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm text-zinc-600 dark:text-zinc-400" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-900"
          autoFocus
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={busy || password.length === 0}
          className="rounded bg-zinc-900 px-3 py-2 text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          Log in
        </button>
      </form>
    </main>
  );
}
