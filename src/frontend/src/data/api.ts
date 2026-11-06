import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJSON } from '@core/data-client.mjs';
import { cryptoForClient } from '../lib/sha256';
import type { Catalog, Cell, Decision, Meta, ProjectValidation, RunRecord, Stream } from './types';

/**
 * Loaders for the static /data interface. Shared resources are memoised as
 * promises; a failed load is evicted so "retry" really retries.
 */
const memo = new Map<string, Promise<unknown>>();

function memoLoad<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = memo.get(key);
  if (hit) return hit as Promise<T>;
  const p = load().catch((error) => {
    memo.delete(key);
    throw error;
  });
  memo.set(key, p);
  return p;
}

export function loadJSON<T>(url: string, sha256: string | null = null): Promise<T> {
  return memoLoad(`${url}#${sha256 ?? ''}`, () =>
    fetchJSON(url, { sha256, cryptoImpl: cryptoForClient() }) as Promise<T>,
  );
}

export const loadMeta = () => loadJSON<Meta>('/data/meta.json');
export const loadCatalog = () => loadJSON<Catalog>('/data/catalog.json');
export const loadCells = () => loadJSON<{ schema_version: number; cells: Cell[] }>('/data/cells.json');
export const loadValidation = () => loadJSON<ProjectValidation>('/data/validation.json');
export const loadDecision = (url: string) => loadJSON<Decision>(url);

const abortError = () => new DOMException('The request was cancelled', 'AbortError');

/**
 * Reject when `signal` aborts, without disturbing the shared promise `p`. The
 * cached run promise must never carry a single consumer's AbortSignal: if it
 * did, one consumer cancelling would poison the cache for the next (both would
 * get AbortError and only one fetch would ever run). Here each consumer gets
 * its own cancellable view while the underlying fetch always completes once.
 */
function raceWithAbort<T>(p: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener('abort', onAbort, { once: true });
    p.then(
      (v) => {
        signal.removeEventListener('abort', onAbort);
        resolve(v);
      },
      (e) => {
        signal.removeEventListener('abort', onAbort);
        reject(e);
      },
    );
  });
}

/** Run records are large (~140 KB); keep a small LRU of the shared promises. */
const runCache = new Map<string, Promise<RunRecord>>();
const RUN_CACHE_LIMIT = 8;

export function loadRun(url: string, sha256: string | null, signal?: AbortSignal): Promise<RunRecord> {
  const key = `${url}#${sha256 ?? ''}`;
  let shared = runCache.get(key);
  if (shared) {
    runCache.delete(key);
    runCache.set(key, shared);
  } else {
    // Fetch with NO consumer signal so the cached promise is cancel-safe.
    shared = (fetchJSON(url, { sha256, cryptoImpl: cryptoForClient() }) as Promise<RunRecord>).catch((e) => {
      runCache.delete(key);
      throw e;
    });
    runCache.set(key, shared);
    while (runCache.size > RUN_CACHE_LIMIT) {
      const oldest = runCache.keys().next().value as string;
      runCache.delete(oldest);
    }
  }
  return signal ? raceWithAbort(shared, signal) : shared;
}

export async function loadBytes(url: string, signal?: AbortSignal): Promise<Uint8Array> {
  const response = await fetch(url, { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status}：${url}`);
  return new Uint8Array(await response.arrayBuffer());
}

export async function loadText(url: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(url, { signal, cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status}：${url}`);
  return response.text();
}

let catalogIndex: Promise<Map<string, Stream>> | null = null;
export function loadStreamIndex(): Promise<Map<string, Stream>> {
  if (!catalogIndex) {
    catalogIndex = loadCatalog()
      .then((c) => new Map(c.streams.map((s) => [s.instance_id, s])))
      .catch((e) => {
        catalogIndex = null;
        throw e;
      });
  }
  return catalogIndex;
}

export type AsyncState<T> =
  | { status: 'loading'; data: null; error: null }
  | { status: 'ready'; data: T; error: null }
  | { status: 'error'; data: null; error: Error };

/**
 * Async resource keyed by `key`. A response for an older key never replaces
 * the state of a newer one (revision guard), and in-flight work is aborted.
 */
export function useAsync<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
): AsyncState<T> & { retry: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading', data: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const revision = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (key === null) return undefined;
    const rev = ++revision.current;
    const controller = new AbortController();
    let cancelled = false;
    setState((s) => (s.status === 'loading' ? s : { status: 'loading', data: null, error: null }));
    loadRef.current(controller.signal).then(
      (data) => {
        // `cancelled` covers a resolve that arrives after unmount (shared,
        // signal-less promises resolve regardless of this effect's abort).
        if (!cancelled && rev === revision.current) setState({ status: 'ready', data, error: null });
      },
      (error: Error) => {
        if (cancelled || rev !== revision.current || error?.name === 'AbortError' || error?.name === 'StaleResponseError') return;
        setState({ status: 'error', data: null, error });
      },
    );
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [key, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, retry };
}

/** Human-readable reason for a data failure, with the fix when we know it. */
export function explainLoadError(error: Error | null): { title: string; detail: string } {
  if (!error) return { title: '未知错误', detail: '' };
  const code = (error as Error & { code?: string }).code;
  const url = (error as Error & { url?: string }).url ?? '';
  if (code === 'HTTP_ERROR' && /404/.test(error.message)) {
    if (url === '/data/meta.json') return {
      title: '无历史数据',
      detail: '本机还没有历史实验数据。在 设置 → 数据 下载历史档案后，这里显示结果、回放和核验结论。',
    };
    return {
      title: `找不到数据文件 ${url}`,
      detail: '派生数据尚未生成或已被移动。请在项目根目录运行 bash scripts/console.sh data-convert，然后刷新。',
    };
  }
  if (code === 'HASH_MISMATCH') {
    return {
      title: '数据哈希不一致，已停止使用该文件',
      detail: `${url} 的 SHA-256 与目录记录不符，可能已损坏或被改动。请重新运行 data-convert；原始 ZIP 不会被修改。`,
    };
  }
  if (code === 'INVALID_JSON') return { title: '数据文件不是有效 JSON', detail: `${url} 可能被截断。请重新运行 data-convert。` };
  if (code === 'NETWORK_ERROR') return { title: '无法连接本地服务', detail: '开发或预览服务可能已停止。重新启动后刷新页面。' };
  if (error.name === 'ReplayDataError') return { title: '轨迹与记录不一致，回放已停止', detail: error.message };
  return { title: '数据加载失败', detail: error.message };
}
