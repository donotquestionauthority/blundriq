/**
 * attemptQueue: durable persistence for puzzle attempts.
 *
 * localStorage is the source of truth for "an attempt exists but the server has not confirmed
 * it". Puzzle navigation and component unmounts never affect attempt durability.
 *
 * INVARIANT, enqueue before navigation. Navigation must not be enabled before `enqueue()` has
 * returned. The queue is the durability boundary; the foreground POST that follows is only an
 * optimisation so the user sees confirmation sooner than the next queue tick. A call site is
 *
 *     await enqueue(...)   // throws or commits to storage
 *     showOptimisticUI()   // only after the commit
 *     recordAttempt(...)   // background POST; markCompleted on success
 *
 * Two persistence modes. Queue mode needs BOTH localStorage (durable per-origin storage) and
 * navigator.locks (cross-tab serialisation: two tabs writing localStorage concurrently are
 * last-writer-wins and silently drop records). Call sites dispatch on isQueueModeAvailable(),
 * never on storage alone. When either is missing the call site runs in blocking mode: disable
 * navigation until the POST settles and show an explicit error with Retry if it fails. The mode
 * does not switch mid-session; a runtime enqueue failure (quota, lock rejected) throws and the
 * call site treats it like the initial-probe failure.
 *
 * Server idempotency contract: the server enforces uniqueness on attempt_id and a same-UUID
 * replay returns the original verdict without re-applying SRS. The queue replays the same UUID
 * until it succeeds; the server dedups.
 *
 * Dead letters: pending records older than EXPIRY_MS, and records that fail shape validation on
 * a strict read, are dropped with a console.error breadcrumb and forwarded to the injected drop
 * logger when one is set.
 */

export const QUEUE_STORE = "blundriq_pending_attempts_v1";
const EXPIRY_MS = 7 * 24 * 60 * 60 * 1000;
const LOG_TAG = "[attemptQueue]";

export interface PendingAttempt {
  attempt_id: string; // stable across retries
  puzzle_id: number;
  solved: boolean; // client claim; the server still validates
  moves_played: string; // comma-joined, ready for the POST body
  enqueued_at: number; // epoch ms
  last_attempted_at: number | null;
  attempt_count: number;
  session_id?: string | null; // per play-through; null on older records
}

export interface DropLogPayload {
  reason: "expired_max_age" | "malformed_record_shape";
  attempt_id?: string | null;
  puzzle_id?: number | null;
  enqueued_at?: number | null;
  last_attempted_at?: number | null;
  attempt_count?: number | null;
  raw?: string | null;
  client_logged_at: number;
}

export type DropLogger = (payload: DropLogPayload) => void;

let _dropLogger: DropLogger | null = null;

/** Injected so this module stays free of API and React imports. Unset = console only. */
export function setDropLogger(logger: DropLogger | null): void {
  _dropLogger = logger;
}

function _fireDropLog(payload: DropLogPayload): void {
  if (_dropLogger === null) return;
  try {
    _dropLogger(payload);
  } catch (err) {
    console.warn(`${LOG_TAG} dropLogger threw; breadcrumb lost`, err);
  }
}

// ─── Cross-tab serialisation ────────────────────────────────────────────────

const QUEUE_LOCK_NAME = "blundriq-attempt-queue-v1";

let _lockingAvailableCache: boolean | null = null;

function isCrossTabLockingAvailable(): boolean {
  if (_lockingAvailableCache === null) {
    _lockingAvailableCache = typeof navigator !== "undefined" && typeof navigator.locks !== "undefined" && typeof navigator.locks.request === "function";
  }
  return _lockingAvailableCache;
}

/**
 * Run `fn` under the exclusive origin-scoped queue lock. Held only for the synchronous
 * read-modify-write; the surrounding async work (POST, expiry sweep) runs lock-free. Throws when
 * the Web Locks API is unavailable, which callers treat as "cannot proceed in queue mode".
 */
async function withQueueLock<T>(fn: () => T): Promise<T> {
  if (!isCrossTabLockingAvailable()) {
    throw new Error("Web Locks API unavailable");
  }
  // With default options (exclusive, blocking) request resolves with the callback's value.
  return (await navigator.locks.request(QUEUE_LOCK_NAME, async () => fn())) as T;
}

// ─── Storage availability probe (cached at first use) ───────────────────────

let _storageAvailableCache: boolean | null = null;

function probeStorage(): boolean {
  try {
    if (typeof window === "undefined" || !window.localStorage) return false;
    const probeKey = "__blundriq_storage_probe__";
    window.localStorage.setItem(probeKey, "1");
    const ok = window.localStorage.getItem(probeKey) === "1";
    window.localStorage.removeItem(probeKey);
    return ok;
  } catch {
    return false;
  }
}

export function isStorageAvailable(): boolean {
  if (_storageAvailableCache === null) {
    _storageAvailableCache = probeStorage();
  }
  return _storageAvailableCache;
}

/** The single dispatch decision between queue mode and blocking mode. */
export function isQueueModeAvailable(): boolean {
  return isStorageAvailable() && isCrossTabLockingAvailable();
}

/** Tests only: forget the cached probes so a fake storage/locks setup is re-detected. */
export function _resetProbesForTests(): void {
  _storageAvailableCache = null;
  _lockingAvailableCache = null;
}

// ─── Queue read / write ─────────────────────────────────────────────────────

/**
 * Strict read. Throws on storage-unavailable, parse failure or a non-array root: every path that
 * goes on to write must not treat an unreadable queue as empty, or the next write would
 * overwrite legitimate pending records. Individual malformed records are dead-lettered (breadcrumb
 * with the raw JSON, then dropped) rather than thrown on, so one bad row from a future schema
 * change cannot wedge the whole queue.
 */
function readQueueStrict(): PendingAttempt[] {
  if (!isStorageAvailable()) {
    throw new Error("localStorage unavailable");
  }
  const raw = window.localStorage.getItem(QUEUE_STORE);
  if (!raw) return [];
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("queue storage root is not an array");
  }
  const valid: PendingAttempt[] = [];
  for (const r of parsed) {
    if (isValidPendingAttempt(r)) {
      valid.push(r);
      continue;
    }
    let rawStr: string;
    try {
      rawStr = JSON.stringify(r).slice(0, 500);
    } catch {
      rawStr = "<unserializable>";
    }
    console.error(`${LOG_TAG} dropped malformed pending record from queue`, { raw: rawStr, reason: "malformed_record_shape" });
    _fireDropLog({ reason: "malformed_record_shape", raw: rawStr, client_logged_at: Date.now() });
  }
  return valid;
}

function isValidPendingAttempt(r: unknown): r is PendingAttempt {
  if (typeof r !== "object" || r === null) return false;
  const rec = r as Record<string, unknown>;
  return (
    typeof rec.attempt_id === "string" &&
    typeof rec.puzzle_id === "number" &&
    typeof rec.solved === "boolean" &&
    typeof rec.moves_played === "string" &&
    typeof rec.enqueued_at === "number" &&
    (rec.last_attempted_at === null || typeof rec.last_attempted_at === "number") &&
    typeof rec.attempt_count === "number" &&
    (rec.session_id === undefined || rec.session_id === null || typeof rec.session_id === "string")
  );
}

function writeQueue(records: PendingAttempt[]): void {
  if (!isStorageAvailable()) {
    throw new Error("localStorage unavailable");
  }
  // Quota errors propagate: the call site must fall back to blocking mode.
  window.localStorage.setItem(QUEUE_STORE, JSON.stringify(records));
}

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Add a pending attempt. Throws when storage or locking is unavailable, when the write fails, or
 * when the existing queue cannot be read safely; the call site must then block navigation.
 * Idempotent on attempt_id: an already-queued UUID keeps its record (enqueued_at and
 * attempt_count are not reset). A new gameplay attempt on the same puzzle uses a fresh UUID.
 */
export async function enqueue(record: Omit<PendingAttempt, "enqueued_at" | "last_attempted_at" | "attempt_count">): Promise<void> {
  await withQueueLock(() => {
    const queue = readQueueStrict();
    if (queue.some((r) => r.attempt_id === record.attempt_id)) return;
    const fresh: PendingAttempt = { ...record, enqueued_at: Date.now(), last_attempted_at: null, attempt_count: 0 };
    writeQueue([...queue, fresh]);
  });
}

/**
 * Remove a record once the server has confirmed the attempt (fresh write or idempotent replay).
 * Never throws: the attempt is server-confirmed regardless, and a record left behind is simply
 * retried later and deduped by the server.
 */
export async function markCompleted(attempt_id: string): Promise<void> {
  if (!isStorageAvailable()) return;
  if (!isCrossTabLockingAvailable()) {
    console.warn(`${LOG_TAG} markCompleted skipped; cross-tab locking unavailable`);
    return;
  }
  try {
    await withQueueLock(() => {
      let queue: PendingAttempt[];
      try {
        queue = readQueueStrict();
      } catch (err) {
        console.warn(`${LOG_TAG} markCompleted skipped; read failed`, err);
        return;
      }
      const filtered = queue.filter((r) => r.attempt_id !== attempt_id);
      if (filtered.length === queue.length) return;
      try {
        writeQueue(filtered);
      } catch (err) {
        console.warn(`${LOG_TAG} failed to remove completed attempt from queue`, err);
      }
    });
  } catch (err) {
    console.warn(`${LOG_TAG} markCompleted lock acquisition failed`, err);
  }
}

/**
 * True if the queue holds this attempt_id, or if the read failed (fail-pessimistic: when in
 * doubt the attempt is still pending, so the caller shows the retry banner rather than hiding an
 * unresolved error). False without storage: no queued record can exist in blocking mode.
 */
/** Any attempt on this puzzle still waiting in the durable queue, from this page load or an
 *  earlier one. A read failure counts as pending: the safe answer for anything that would
 *  make such an attempt unsaveable. */
export function hasPendingAttemptForPuzzle(puzzle_id: number): boolean {
  if (!isStorageAvailable()) return false;
  try {
    return readQueueStrict().some((r) => r.puzzle_id === puzzle_id);
  } catch (err) {
    console.warn(`${LOG_TAG} hasPendingAttemptForPuzzle read failed; assuming still pending`, err);
    return true;
  }
}

export function hasPendingAttempt(attempt_id: string): boolean {
  if (!isStorageAvailable()) return false;
  try {
    return readQueueStrict().some((r) => r.attempt_id === attempt_id);
  } catch (err) {
    console.warn(`${LOG_TAG} hasPendingAttempt read failed; assuming still pending`, err);
    return true;
  }
}

/**
 * Drop records older than EXPIRY_MS and return them. Called at processPending boundaries, never
 * on a timer, so a record is not expired while its POST is mid-flight. Any failure is a soft
 * skip: expiry can wait for the next tick, and overwriting the queue would be far worse.
 */
export async function expireOldEntries(): Promise<{ expired: PendingAttempt[] }> {
  if (!isStorageAvailable()) return { expired: [] };
  if (!isCrossTabLockingAvailable()) {
    console.warn(`${LOG_TAG} expireOldEntries skipped; cross-tab locking unavailable`);
    return { expired: [] };
  }
  try {
    return await withQueueLock(() => {
      let queue: PendingAttempt[];
      try {
        queue = readQueueStrict();
      } catch (err) {
        console.warn(`${LOG_TAG} expireOldEntries skipped; read failed`, err);
        return { expired: [] as PendingAttempt[] };
      }
      const now = Date.now();
      const expired = queue.filter((r) => now - r.enqueued_at > EXPIRY_MS);
      if (expired.length === 0) return { expired: [] as PendingAttempt[] };
      const remaining = queue.filter((r) => now - r.enqueued_at <= EXPIRY_MS);
      try {
        writeQueue(remaining);
      } catch (err) {
        console.warn(`${LOG_TAG} failed to write expiry-pruned queue`, err);
        return { expired: [] as PendingAttempt[] };
      }
      const breadcrumbAt = Date.now();
      for (const record of expired) {
        console.error(`${LOG_TAG} expired pending attempt without persisting`, {
          attempt_id: record.attempt_id,
          puzzle_id: record.puzzle_id,
          enqueued_at: record.enqueued_at,
          last_attempted_at: record.last_attempted_at,
          attempt_count: record.attempt_count,
          reason: "expired_max_age",
        });
        _fireDropLog({
          reason: "expired_max_age",
          attempt_id: record.attempt_id,
          puzzle_id: record.puzzle_id,
          enqueued_at: record.enqueued_at,
          last_attempted_at: record.last_attempted_at,
          attempt_count: record.attempt_count,
          client_logged_at: breadcrumbAt,
        });
      }
      return { expired };
    });
  } catch (err) {
    console.warn(`${LOG_TAG} expireOldEntries lock acquisition failed`, err);
    return { expired: [] };
  }
}

/**
 * Drive the queue: call `handler` for every pending record and remove each on success. Failures
 * stay queued for the next tick. Only one invocation runs at a time; a concurrent call is a
 * no-op. attempt_count / last_attempted_at are bumped BEFORE the handler runs so they record
 * "we tried", not "we succeeded".
 */
let _processingLock = false;

export async function processPending(handler: (record: PendingAttempt) => Promise<void>, onSuccess?: (record: PendingAttempt) => void): Promise<void> {
  if (!isStorageAvailable()) return;
  if (_processingLock) return;
  _processingLock = true;
  try {
    await expireOldEntries();
    let queue: PendingAttempt[];
    try {
      queue = readQueueStrict();
    } catch (err) {
      console.warn(`${LOG_TAG} processPending skipped; read failed`, err);
      return;
    }
    for (const record of queue) {
      const updatedRecord: PendingAttempt = { ...record, last_attempted_at: Date.now(), attempt_count: record.attempt_count + 1 };
      // The counter bump is a read-modify-write, so it holds the lock. If the record is gone by
      // now (another tab, or our own markCompleted) there is nothing to retry.
      let recordAlreadyRemoved = false;
      if (isCrossTabLockingAvailable()) {
        try {
          await withQueueLock(() => {
            const current = readQueueStrict();
            const idx = current.findIndex((r) => r.attempt_id === record.attempt_id);
            if (idx === -1) {
              recordAlreadyRemoved = true;
              return;
            }
            current[idx] = updatedRecord;
            writeQueue(current);
          });
        } catch (err) {
          console.warn(`${LOG_TAG} failed to bump attempt counters`, err);
        }
      }
      if (recordAlreadyRemoved) continue;

      try {
        await handler(updatedRecord);
        await markCompleted(record.attempt_id);
        if (onSuccess) {
          try {
            onSuccess(updatedRecord);
          } catch (cbErr) {
            console.warn(`${LOG_TAG} onSuccess callback threw`, cbErr);
          }
        }
      } catch (err) {
        console.warn(`${LOG_TAG} retry failed for attempt ${record.attempt_id}`, err);
      }
    }
  } finally {
    _processingLock = false;
  }
}

/**
 * Wire the events that drive processPending (an initial sweep, visibilitychange to visible, and
 * online) and return the cleanup. A no-op when queue mode is unavailable. `onSuccess` fires after
 * each background success so a page can clear a "pending retry" state for that attempt.
 */
export function initQueueTriggers(handler: (record: PendingAttempt) => Promise<void>, onSuccess?: (record: PendingAttempt) => void): () => void {
  if (!isQueueModeAvailable()) return () => {};

  void processPending(handler, onSuccess);

  const onVisibility = () => {
    if (document.visibilityState === "visible") {
      void processPending(handler, onSuccess);
    }
  };
  const onOnline = () => {
    void processPending(handler, onSuccess);
  };

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("online", onOnline);

  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("online", onOnline);
  };
}
