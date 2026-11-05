import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import type {
  Algorithm,
  AlgorithmHistory,
  AlgorithmStat,
  PhysicsRun,
  Instance,
  MapMeta,
  Recommendation,
  Scenario,
  ScenarioSummary,
  Service,
  SimMode,
  SimRun,
  SimState,
  Solution,
} from './types';

/** Error carrying the gateway's {error, message} body and HTTP status. */
export class LabApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'LabApiError';
  }
}

const BASE = '/lab/api';

async function request<T>(method: 'GET' | 'POST' | 'DELETE', path: string, body?: unknown, signal?: AbortSignal, timeoutMs = 30_000): Promise<T> {
  let response: Response;
  let text: string;
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    text = await response.text();
  } catch (cause) {
    if (signal?.aborted) throw cause;
    if (timedOut) throw new LabApiError(`网关请求超时（${timeoutMs} ms）：${path}。操作可能已到达后端，请先刷新状态再重试。`, 'timeout', 0);
    if ((cause as Error)?.name === 'AbortError') throw cause;
    throw new LabApiError('无法连接工作台网关（127.0.0.1:4100），请运行 bash setup/run.sh lab。', 'network', 0);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new LabApiError(`网关返回了非 JSON 内容（HTTP ${response.status}）。`, 'bad_response', response.status);
    }
  }
  if (!response.ok) {
    const e = (data ?? {}) as { error?: string; message?: string; detail?: unknown };
    const message = e.message ?? (typeof e.detail === 'string' ? e.detail : `请求失败（HTTP ${response.status}）`);
    throw new LabApiError(message, e.error ?? 'http_error', response.status);
  }
  return data as T;
}

export const lab = {
  health: () => request<{ status: string; version: string }>('GET', '/health'),
  services: () => request<{ services: Service[] }>('GET', '/services'),
  serviceAction: (id: string, action: 'start' | 'stop' | 'restart', body?: Record<string, unknown>) =>
    request<{ services: Service[] }>('POST', `/services/${encodeURIComponent(id)}/${action}`, body ?? {}),
  logs: () => request<{ logs: { log: string; path: string; bytes: number }[] }>('GET', '/logs'),
  log: (name: string, tail = 400) =>
    request<{ log: string; path: string; lines: string[] }>('GET', `/logs/${encodeURIComponent(name)}?tail=${tail}`),

  scenarios: () => request<{ scenarios: ScenarioSummary[] }>('GET', '/scenarios'),
  scenario: (id: string) => request<Scenario>('GET', `/scenarios/${encodeURIComponent(id)}`),
  importScenario: (scenario: unknown) => request<ScenarioSummary>('POST', '/scenarios', scenario),
  map: (world: string) => request<MapMeta>('GET', `/maps/${encodeURIComponent(world)}`),

  algorithms: () => request<{ algorithms: Algorithm[] }>('GET', '/algorithms'),
  algorithmStats: () => request<{ stats: Record<string, AlgorithmStat> }>('GET', '/algorithms/stats'),
  algorithmHistory: (id: string) => request<AlgorithmHistory>('GET', `/algorithms/${encodeURIComponent(id)}/history`),
  physicsEngines: () => request<{ engines: { id: string; name: string; available: boolean; reason: string | null; version: string | null }[] }>('GET', '/physics/engines'),
  physicsSubmit: (body: { run_id?: string; session_id?: string }) => request<PhysicsRun>('POST', '/physics/runs', body),
  physicsRuns: (run_id?: string) => request<{ runs: PhysicsRun[] }>('GET', `/physics/runs${run_id ? `?run_id=${encodeURIComponent(run_id)}` : ''}`),
  physicsRun: (id: string) => request<PhysicsRun>('GET', `/physics/runs/${encodeURIComponent(id)}`),
  recommend: (scenario_id: string, instance?: Instance) =>
    request<Recommendation>('POST', '/recommend', { scenario_id, instance }),
  solve: (scenario_id: string, algorithms: { id: string; params?: Record<string, unknown> }[], instance?: Instance, label?: string) =>
    request<{ job_id: string; runs: string[] }>('POST', '/solve', { scenario_id, algorithms, instance, label }),
  runs: (params: { scenario_id?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.scenario_id) q.set('scenario_id', params.scenario_id);
    if (params.limit) q.set('limit', String(params.limit));
    const s = q.toString();
    return request<{ runs: Solution[] }>('GET', `/runs${s ? `?${s}` : ''}`);
  },
  run: (run_id: string) => request<Solution>('GET', `/runs/${encodeURIComponent(run_id)}`),
  cancelRun: (run_id: string) => request<Solution>('POST', `/runs/${encodeURIComponent(run_id)}/cancel`, {}),

  simState: () => request<SimState>('GET', '/sim/state'),
  simStart: (body: { world: string; spawn?: { x: number; y: number; yaw: number }; gui: boolean; rviz: boolean; localization?: 'ground_truth'|'amcl' }) =>
    request<SimState>('POST', '/sim/session', body),
  simStop: () => request<SimState>('DELETE', '/sim/session', undefined, undefined, 90_000),
  simGui: (on: boolean) => request<SimState>('POST', '/sim/gui', { on }),
  simRviz: (on: boolean) => request<SimState>('POST', '/sim/rviz', { on }),
  simExecute: (run_id: string, mode: SimMode) => request<SimRun>('POST', '/sim/runs', { run_id, mode }),
  simRuns: () => request<{ sim_runs: SimRun[] }>('GET', '/sim/runs'),
  simRun: (id: string) => request<SimRun>('GET', `/sim/runs/${encodeURIComponent(id)}`),
  simCancel: (id: string) => request<SimRun>('POST', `/sim/runs/${encodeURIComponent(id)}/cancel`, {}),
};

/** Download URLs (served with Content-Disposition by the gateway). */
export const exportUrl = {
  run: (run_id: string, format: 'json' | 'csv' | 'geojson') =>
    `${BASE}/runs/${encodeURIComponent(run_id)}/export?format=${format}`,
  simRun: (id: string, format: 'json' | 'csv') => `${BASE}/sim/runs/${encodeURIComponent(id)}/export?format=${format}`,
};

/** POST-based compare export: fetches the file and triggers a browser download. */
export async function downloadCompare(run_ids: string[], format: 'csv' | 'json') {
  const response = await fetch(`${BASE}/compare/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_ids, format }),
  });
  if (!response.ok) throw new LabApiError(`导出失败（HTTP ${response.status}）`, 'export', response.status);
  const blob = await response.blob();
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(response.headers.get('Content-Disposition') ?? '')?.[1];
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name ? decodeURIComponent(name) : `plan-compare.${format}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ---------------------------------------------------------------- events -- */

type Listener = (event: string, data: unknown) => void;

/**
 * One shared EventSource for the whole app. EventSource reconnects by itself;
 * `connected` is exposed so views can show a "网关断开" state instead of stale data.
 *
 * The stream is closed while the page is hidden or cached (bfcache): browsers allow only six
 * HTTP/1.1 connections per host, and every tab or cached page holding a stream takes one, so a
 * few background tabs could otherwise stall every request of the visible one.
 */
class EventHub {
  private source: EventSource | null = null;
  private listeners = new Set<Listener>();
  private stateListeners = new Set<() => void>();
  private hiddenTimer = 0;
  connected = false;
  private readonly types = ['services', 'job', 'run', 'sim_state', 'sim_run', 'log', 'ping', 'agent_session', 'physics_run'];

  constructor() {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    window.addEventListener('pagehide', () => this.close());
    window.addEventListener('pageshow', (e) => {
      if ((e as PageTransitionEvent).persisted) this.ensure();
    });
    document.addEventListener('visibilitychange', () => {
      window.clearTimeout(this.hiddenTimer);
      if (document.visibilityState === 'hidden') this.hiddenTimer = window.setTimeout(() => this.close(), 10_000);
      else this.ensure();
    });
  }

  private close() {
    window.clearTimeout(this.hiddenTimer);
    this.source?.close();
    this.source = null;
    this.setConnected(false);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    this.ensure();
    return () => {
      this.listeners.delete(listener);
    };
  }

  onConnection(listener: () => void): () => void {
    this.stateListeners.add(listener);
    this.ensure();
    return () => {
      this.stateListeners.delete(listener);
    };
  }

  private setConnected(v: boolean) {
    if (this.connected === v) return;
    this.connected = v;
    for (const l of this.stateListeners) l();
  }

  private ensure() {
    if (this.source || typeof EventSource === 'undefined') return;
    if (!this.listeners.size && !this.stateListeners.size) return;
    const source = new EventSource(`${BASE}/events`);
    this.source = source;
    source.onopen = () => this.setConnected(true);
    source.onerror = () => this.setConnected(source.readyState === EventSource.OPEN);
    for (const type of this.types) {
      source.addEventListener(type, (e) => {
        let data: unknown = null;
        try {
          data = JSON.parse((e as MessageEvent).data);
        } catch {
          return;
        }
        this.setConnected(true);
        for (const l of this.listeners) l(type, data);
      });
    }
  }
}

export const events = new EventHub();

export function useLabEvent(type: string, handler: (data: unknown) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => events.subscribe((t, d) => t === type && ref.current(d)), [type]);
}

export function useGatewayConnected(): boolean {
  return useSyncExternalStore(
    (cb) => events.onConnection(cb),
    () => events.connected,
    () => false,
  );
}

/** Loads once, then keeps the value fresh from an SSE event type when given. */
export function useLabResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  options: { event?: string; select?: (data: unknown) => T | null; pollMs?: number; reloadOn?: string } = {},
): { data: T | null; error: Error | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const selectRef = useRef(options.select);
  selectRef.current = options.select;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    loadRef
      .current(controller.signal)
      .then((v) => {
        if (controller.signal.aborted) return;
        setData(v);
        setError(null);
      })
      .catch((e: Error) => {
        if (e.name !== 'AbortError') setError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [key, nonce]);

  useEffect(() => {
    if (!options.event) return;
    return events.subscribe((type, payload) => {
      if (type !== options.event) return;
      const next = selectRef.current ? selectRef.current(payload) : (payload as T);
      if (next !== null && next !== undefined) {
        setData(next);
        setError(null);
      }
    });
  }, [options.event]);

  useEffect(() => {
    if (!options.reloadOn) return;
    let timer = 0;
    const off = events.subscribe((type) => {
      if (type !== options.reloadOn) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setNonce((n) => n + 1), 400);
    });
    return () => { off(); window.clearTimeout(timer); };
  }, [options.reloadOn]);

  useEffect(() => {
    if (!options.pollMs) return;
    const id = window.setInterval(() => setNonce((n) => n + 1), options.pollMs);
    return () => window.clearInterval(id);
  }, [options.pollMs]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}

/* ---------------------------------------------------------------- format -- */

export const fmt = {
  algorithm(a: {id:string;name:string;version:string|null}): string {
    return a.id.startsWith('pyvrp.')&&a.version==='0.14.0'&&a.name.includes('HGS')
      ? a.name.replace('HGS','ILS')+'（原记录名称已纠正）' : a.name;
  },
  num(v: number | null | undefined, digits = 2): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    return v.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  },
  sec(v: number | null | undefined): string {
    if (v === null || v === undefined || !Number.isFinite(v)) return '—';
    if (v < 1) return `${(v * 1000).toFixed(0)} ms`;
    if (v < 120) return `${v.toFixed(v < 10 ? 2 : 1)} s`;
    return `${Math.floor(v / 60)} min ${Math.round(v % 60)} s`;
  },
  time(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false });
  },
  sourceLabel(source: string): string {
    return (
      { project: '本项目', third_party: '第三方', panel_baseline: '基线', nav2: 'Nav2' } as Record<string, string>
    )[source] ?? source;
  },
};
