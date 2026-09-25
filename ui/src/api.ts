/** Minimal API client. Same-origin in production (api.blundriq.com under blundriq.com),
 *  proxied under /api in dev. Cookies carry the session; nothing is stored in JS. */

const BASE = import.meta.env.VITE_API_URL ?? "/api";

export class ApiError extends Error {
  status: number;
  /** The parsed JSON body, when there was one, for a caller that needs more than `detail`. */
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    let body: unknown;
    try {
      body = await res.json();
      const d = (body as { detail?: unknown }).detail;
      detail = typeof d === "string" ? d : JSON.stringify(d);
    } catch {
      /* no body */
    }
    throw new ApiError(res.status, detail, body);
  }
  return (await res.json()) as T;
}

export const api = {
  /** `signal` lets a fetch-on-expand panel abort a request it no longer wants. */
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, signal ? { signal } : undefined),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(path: string, body?: unknown) => request<T>(path, body === undefined ? { method: "DELETE" } : { method: "DELETE", body: JSON.stringify(body) }),
};
