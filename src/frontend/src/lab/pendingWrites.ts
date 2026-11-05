/**
 * Create-once writes (fork, draft copy, scenario/GeoJSON import, agri catalog import).
 *
 * Every create is keyed by its submitted content and carries a client-chosen scenario id. The record is
 * persisted in sessionStorage *before* the request leaves, so a lost response, a timeout or a page reload
 * leaves enough to ask the server for the original id before anything is written again.
 * Storage may be unavailable (node, privacy mode, quota) or corrupt: both degrade to in-memory records.
 */
import { lab, LabApiError } from './api';
import { stableStringify } from './views/plan/planModel';

export const PENDING_STORAGE_KEY = 'urp.studio.pending-writes';
export const PENDING_VERSION = 1;
const MAX_AGE_MS = 24 * 3600_000;
const ID_RE = /^[a-z0-9][a-z0-9_.-]{1,63}$/;

export interface PendingWrite {
  id: string;
  kind: string;
  created: number;
  /** Optional replayable payload (agri catalog keeps the exact built scenario). */
  payload?: unknown;
}

let memory: Record<string, PendingWrite> = {};

function storage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

function validEntry(e: unknown): e is PendingWrite {
  const x = e as PendingWrite;
  return !!x && typeof x === 'object' && typeof x.id === 'string' && ID_RE.test(x.id)
    && typeof x.kind === 'string' && typeof x.created === 'number' && Number.isFinite(x.created);
}

function readAll(): Record<string, PendingWrite> {
  const s = storage();
  if (!s) return memory;
  let parsed: { v?: unknown; entries?: unknown } | null;
  try {
    const raw = s.getItem(PENDING_STORAGE_KEY);
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    return memory;
  }
  if (!parsed || parsed.v !== PENDING_VERSION || !parsed.entries || typeof parsed.entries !== 'object') {
    return memory;
  }
  const now = Date.now();
  const out: Record<string, PendingWrite> = {};
  for (const [key, entry] of Object.entries(parsed.entries as Record<string, unknown>)) {
    if (validEntry(entry) && now - entry.created < MAX_AGE_MS) out[key] = entry;
  }
  return { ...memory, ...out };
}

function writeAll(entries: Record<string, PendingWrite>) {
  memory = entries;
  const s = storage();
  if (!s) return;
  try {
    s.setItem(PENDING_STORAGE_KEY, JSON.stringify({ v: PENDING_VERSION, entries }));
  } catch {
    // Quota or disabled storage: the in-memory record still protects this page lifetime.
  }
}

export function pendingWrite(key: string): PendingWrite | undefined {
  return readAll()[key];
}

export function rememberWrite(key: string, entry: PendingWrite): void {
  writeAll({ ...readAll(), [key]: entry });
}

export function forgetWrite(key: string): void {
  const all = { ...readAll() };
  if (!(key in all)) return;
  delete all[key];
  writeAll(all);
}

/** Test hook: forget in-memory records (sessionStorage is the caller's to reset). */
export function resetPendingMemory(): void {
  memory = {};
  inFlight.clear();
}

/** Short stable content digest (FNV-1a, 2 × 32 bit) used only as a dedupe key, never as identity proof. */
export function contentDigest(value: unknown): string {
  const text = stableStringify(value ?? null);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193 ^ text.length;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ c, 0x5bd1e995) >>> 0;
  }
  return h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0');
}

export function randomSuffix(bytes = 6): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** A server-legal scenario id derived from the source id plus a random suffix. */
export function clientScenarioId(prefix: string): string {
  const base = prefix.toLowerCase().replace(/[^a-z0-9_.-]/g, '-').replace(/^[^a-z0-9]+/, '').slice(0, 40) || 'sc';
  return `${base}-my-${randomSuffix()}`;
}

/** The outcome is unknown: the server may or may not have written. Definite 4xx answers are not. */
export function outcomeUncertain(e: unknown): boolean {
  if (!(e instanceof LabApiError)) return true;
  return e.status === 0 || e.status === 408 || e.status >= 500 || e.code === 'bad_response';
}

/** Thrown by recover(): the id exists but holds different content. Definite, never retried. */
export const MISMATCH = 'scenario_mismatch';
export const mismatch = (id: string) => new LabApiError(`场景 ${id} 已存在但内容与本次提交不一致；未覆盖，请核对后重试`, MISMATCH, 409);

const inFlight = new Map<string, Promise<unknown>>();

export interface CreateOnce<T> {
  key: string;
  kind: string;
  id: () => string;
  send: (id: string) => Promise<T>;
  /** Read the original id back; throw on mismatch. LabApiError 404 means "never written". */
  recover: (id: string) => Promise<T>;
  /** Fork on an older gateway may answer with its own id; default requires the submitted id. */
  acceptForeignId?: boolean;
  payload?: unknown;
}

/** At most one create per key: in-flight dedupe, persisted uncertainty, read-before-retry. */
export function createOnce<T extends { id: string }>(op: CreateOnce<T>): Promise<T> {
  const running = inFlight.get(op.key) as Promise<T> | undefined;
  if (running) return running;
  const request = (async () => {
    const prior = pendingWrite(op.key);
    const id = prior?.id ?? op.id();
    if (prior) {
      try {
        const found = await op.recover(id);
        forgetWrite(op.key);
        return found;
      } catch (e) {
        if (e instanceof LabApiError && e.code === MISMATCH) forgetWrite(op.key);
        if (!(e instanceof LabApiError && e.status === 404)) throw e;
      }
    }
    rememberWrite(op.key, { id, kind: op.kind, created: prior?.created ?? Date.now(), payload: op.payload });
    try {
      const saved = await op.send(id);
      if (!saved || typeof saved.id !== 'string') throw new LabApiError('请先核对保存结果：响应缺少有效场景标识', 'bad_response', 200);
      if (saved.id !== id && !op.acceptForeignId) throw new LabApiError('返回的场景标识与本次提交不一致', 'bad_response', 200);
      forgetWrite(op.key);
      return saved;
    } catch (e) {
      if (e instanceof LabApiError && e.status === 409) {
        // Our own earlier write may have landed; only an identical stored record counts as success.
        try {
          const found = await op.recover(id);
          forgetWrite(op.key);
          return found;
        } catch (r) {
          if (outcomeUncertain(r) && !(r instanceof LabApiError && r.status === 404)) throw r;
          forgetWrite(op.key);
          throw e;
        }
      }
      if (!outcomeUncertain(e)) forgetWrite(op.key);
      throw e;
    }
  })();
  inFlight.set(op.key, request);
  const clear = () => { if (inFlight.get(op.key) === request) inFlight.delete(op.key); };
  request.then(clear, clear);
  return request;
}

/** True when every submitted (expected) value is present unchanged in the stored record. */
export function containsSubmittedValue(actual: unknown, expected: unknown): boolean {
  if (Array.isArray(expected)) return Array.isArray(actual) && actual.length === expected.length
    && expected.every((value, i) => containsSubmittedValue(actual[i], value));
  if (expected !== null && typeof expected === 'object') return actual !== null && typeof actual === 'object'
    && Object.entries(expected).every(([key, value]) => containsSubmittedValue((actual as Record<string, unknown>)[key], value));
  return actual === expected;
}

/**
 * Scenario JSON import (lab and studio entry points). The dedupe key ignores the document's own id, so a
 * caller that mints a fresh id per click still reuses the original id while an outcome is uncertain.
 */
export function importScenarioOnce<T extends { id: string }>(obj: unknown, send: (doc: unknown) => Promise<T>): Promise<T> {
  const doc = obj as { id?: unknown; problem_class?: unknown; instance?: unknown } | null;
  const own = typeof doc?.id === 'string' ? doc.id.trim().toLowerCase() : '';
  if (!doc || typeof doc !== 'object' || !own) return send(obj); // the server reports what is missing
  const { id: _id, ...content } = doc as Record<string, unknown>;
  void _id;
  return createOnce<T>({
    key: `import:${contentDigest(content)}`,
    kind: 'import',
    id: () => own,
    send: (id) => send({ ...(doc as object), id }),
    recover: async (sid) => {
      const found = await lab.scenario(sid);
      if (found.id !== sid || found.problem_class !== doc.problem_class
        || (found as { origin?: string }).origin !== 'user'
        || !containsSubmittedValue(found.instance, doc.instance)) throw mismatch(sid);
      return found as unknown as T;
    },
  });
}
