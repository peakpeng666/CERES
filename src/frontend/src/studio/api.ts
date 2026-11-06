import { LabApiError } from '../lab/api';
import type { Instance, PlanningContext, Scenario } from '../lab/types';
import type {
  Analysis,
  CvrplibInstance,
  CvrplibSet,
  FieldEntry,
  FieldPhysics,
  FieldSimState,
  FieldSimSummary,
  FieldTrafficMode,
  FleetInstance,
  FleetScenario,
  InstanceSpace,
  LaneInfo,
  ParseResult,
  Reference,
  Study,
  StudySpec,
} from './types';
import type { SimResult } from './experiment/replay';
import type { AgentSession, AgentSessionSummary, AgentStatus } from './agent/types';

const BASE = '/lab/api';

export interface CallOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface ExperimentRecordEnvelope {
  record: { schema?: string; run_id?: string; [key: string]: unknown };
  integrity: { algorithm?: string; sha256: string; canonicalization?: string; scope?: string; [key: string]: unknown };
  assembled_utc: string;
}

export interface FieldPhysicsResponse {
  default: FieldPhysics;
  override_keys: string[];
  scope?: string;
  execution?: {
    ground_presets?: Record<string, { label?: string }>;
    localization_presets?: Record<string, { label?: string }>;
    provenance?: Record<string, string>;
    scope?: string;
  };
}

export async function call<T>(method: string, path: string, body?: unknown, options: CallOptions & { preserveJsonText?: boolean } = {}): Promise<T> {
  const controller = new AbortController();
  const onAbort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) onAbort();
  else options.signal?.addEventListener('abort', onAbort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? 30_000);
  let response: Response;
  let text: string;
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
    if (options.signal?.aborted) throw cause;
    if (timedOut) throw new LabApiError(`请求超时（${options.timeoutMs ?? 30_000} ms）：${path}`, 'timeout', 0);
    if ((cause as Error)?.name === 'AbortError') throw cause;
    throw new LabApiError('连接不上工作台服务（127.0.0.1:4100）', 'network', 0);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', onAbort);
  }
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new LabApiError(`服务返回了非 JSON 内容（HTTP ${response.status}）：${path}`, 'bad_response', response.status);
    }
  }
  if (!response.ok) {
    const e = (data ?? {}) as { error?: string; message?: string; detail?: unknown };
    const message = e.message ?? (typeof e.detail === 'string' ? e.detail : `请求失败（HTTP ${response.status}）`);
    throw new LabApiError(message, e.error ?? 'http_error', response.status);
  }
  return (options.preserveJsonText ? text : data) as T;
}

export const studio = {
  examples: () => call<{ examples: string[] }>('GET', '/compose/examples'),
  parse: (text: string) => call<ParseResult>('POST', '/compose/parse', { text }),
  compose: (text: string, opts: { seed?: number; field_id?: string; overrides?: Record<string, unknown> } = {}) =>
    call<FleetScenario>('POST', '/compose', { text, ...opts }),
  validateInstance: (scenario_id: string, instance: Instance) => call<{ instance: Instance; planning: PlanningContext }>('POST', '/planning/validate', { scenario_id, instance }),
  saveScenario: (id: string, body: { instance?: FleetInstance | unknown; title?: string; summary?: string; layers?: Record<string, unknown>; base_revision?: number }) =>
    call<FleetScenario>('PUT', `/scenarios/${encodeURIComponent(id)}`, body),
  /** client_id makes the fork idempotent: same id + same content returns the stored copy. */
  forkScenario: (id: string, body: { instance?: unknown; title?: string; client_id?: string } = {}) =>
    call<Scenario>('POST', `/scenarios/${encodeURIComponent(id)}/fork`, body),
  lanes: (instance: FleetInstance) => call<LaneInfo>('POST', '/fleet/lanes', { instance }),
  importGeojson: (geojson: unknown, title?: string) => call<FleetScenario>('POST', '/scenarios/import/geojson', { geojson, title }),
  importScenario: (scenario: unknown) => call<Scenario>('POST', '/scenarios', scenario),
  library: () => call<{ cvrplib: Record<string, number | string>; fields: Record<string, unknown>; references: { count: number } }>('GET', '/library'),
  cvrplibSets: () => call<{ sets: CvrplibSet[] }>('GET', '/library/cvrplib/sets'),
  cvrplibInstances: (set?: string, q?: string) => {
    const p = new URLSearchParams();
    if (set) p.set('set', set);
    if (q) p.set('q', q);
    return call<{ instances: CvrplibInstance[] }>('GET', `/library/cvrplib/instances?${p}`);
  },
  fields: (country?: string, q?: string) => {
    const p = new URLSearchParams();
    if (country) p.set('country', country);
    if (q) p.set('q', q);
    return call<{ status: { downloaded: boolean; fields: number; images: boolean }; fields: FieldEntry[] }>('GET', `/library/fields?${p}`);
  },
  fetchFields: (images = false) => call<Record<string, unknown>>('POST', '/library/fields/fetch', { images }),
  references: (topic?: string, q?: string) => {
    const p = new URLSearchParams();
    if (topic) p.set('topic', topic);
    if (q) p.set('q', q);
    return call<{ references: Reference[]; topics: Record<string, string> }>('GET', `/references?${p}`);
  },
  source: (path: string) => call<{ path: string; language: string; text: string }>('GET', `/source?path=${encodeURIComponent(path)}`),
  agentStatus: () => call<AgentStatus>('GET', '/research/agent'),
  agentSessions: () => call<{ sessions: AgentSessionSummary[] }>('GET', '/research/agent/sessions'),
  agentSession: (id: string, options: CallOptions = {}) => call<AgentSession>('GET', `/research/agent/sessions/${encodeURIComponent(id)}`, undefined, options),
  agentStart: (message: string) => call<AgentSession>('POST', '/research/agent/sessions', { message }),
  agentSend: (id: string, message: string) => call<AgentSession>('POST', `/research/agent/sessions/${encodeURIComponent(id)}/messages`, { message }),
  agentDecide: (id: string, call_id: string, approve: boolean, note?: string) =>
    call<AgentSession>('POST', `/research/agent/sessions/${encodeURIComponent(id)}/approval`, { call_id, approve, note }),
  agentCancel: (id: string) => call<AgentSession>('POST', `/research/agent/sessions/${encodeURIComponent(id)}/cancel`, {}),
  studies: () => call<{ studies: Study[] }>('GET', '/studies'),
  study: (id: string) => call<Study>('GET', `/studies/${encodeURIComponent(id)}`),
  createStudy: (spec: StudySpec) => call<Study>('POST', '/studies', spec),
  cancelStudy: (id: string) => call<Study>('POST', `/studies/${encodeURIComponent(id)}/cancel`, {}),
  analysis: (id: string, signal?: AbortSignal) => call<Analysis>('GET', `/studies/${encodeURIComponent(id)}/analysis`, undefined, { signal }),
  instanceSpace: (id: string, signal?: AbortSignal) => call<InstanceSpace>('GET', `/studies/${encodeURIComponent(id)}/instance-space`, undefined, { signal }),
  analyzeRuns: (run_ids: string[]) => call<Analysis>('POST', '/analysis', { run_ids }),
  fieldsim: () => call<FieldSimState>('GET', '/fieldsim'),
  fieldsimPhysics: () => call<FieldPhysicsResponse>('GET', '/fieldsim/physics'),
  startFieldsim: (run_id: string, opts: { gui?: boolean; rviz?: boolean; real_time_factor?: number; traffic?: FieldTrafficMode; physics?: Record<string, unknown>; execution?: { ground_preset?: string; localization_preset?: string; seed?: number; slip_protection?: boolean } } = {}) =>
    call<FieldSimState>('POST', '/fieldsim', { run_id, ...opts }),
  stopFieldsim: (options: CallOptions = {}) => call<FieldSimState>('POST', '/fieldsim/stop', {}, { timeoutMs: 90_000, ...options }),
  fieldsimWindow: (kind: 'gui' | 'rviz', on: boolean) => call<FieldSimState>('POST', '/fieldsim/window', { kind, on }),
  fieldsimResults: (run_id?: string) =>
    call<{ results: FieldSimSummary[] }>('GET', `/fieldsim/results${run_id ? `?run_id=${encodeURIComponent(run_id)}` : ''}`),
  fieldsimResult: (sessionId: string, options: CallOptions = {}) =>
    call<SimResult>('GET', `/fieldsim/results/${encodeURIComponent(sessionId)}`, undefined, options),
  fieldsimEvidence: (sessionId: string, options: CallOptions = {}) =>
    call<SimResult>('GET', `/fieldsim/results/${encodeURIComponent(sessionId)}?evidence=true`, undefined, options),
  runRecord: (runId: string, options: CallOptions = {}) =>
    call<ExperimentRecordEnvelope>('GET', `/runs/${encodeURIComponent(runId)}/record`, undefined, { timeoutMs: 60_000, ...options }),
  runRecordText: (runId: string, options: CallOptions = {}) =>
    call<string>('GET', `/runs/${encodeURIComponent(runId)}/record`, undefined, { timeoutMs: 60_000, ...options, preserveJsonText: true }),
};

export const urls = {
  referencesBib: (keys?: string[]) => `${BASE}/references.bib${keys?.length ? `?keys=${keys.join(',')}` : ''}`,
  fieldImage: (id: string) => `${BASE}/library/fields/${encodeURIComponent(id)}/image`,
  fieldOutline: (id: string) => `${BASE}/library/fields/${encodeURIComponent(id)}/outline.svg`,
  scenarioExport: (id: string, format: 'json' | 'geojson') => `${BASE}/scenarios/${encodeURIComponent(id)}/export?format=${format}`,
  studyExport: (id: string, format: 'csv' | 'json' | 'latex') => `${BASE}/studies/${encodeURIComponent(id)}/export?format=${format}`,
  fieldsimResult: (sid: string) => `${BASE}/fieldsim/results/${encodeURIComponent(sid)}`,
  robotUrdf: (model = 'turtlebot4') => `${BASE}/ros/robots/${model}.urdf`,
  rosPackage: (pkg: string) => `${BASE}/ros/package/${pkg}`,
};
