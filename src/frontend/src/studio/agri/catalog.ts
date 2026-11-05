import { studio } from '../api';
import { lab, LabApiError } from '../../lab/api';
import { forgetWrite, pendingWrite, rememberWrite } from '../../lab/pendingWrites';
import { AGRI_PATTERNS, patternStatus, type PatternStatus } from './patterns';
import type { AgriTaskPattern } from './taxonomy';

export const STATUS_LABELS: Record<PatternStatus, string> = { runnable: '可构建模板', approx: '近似模板', proposal: '提案（不可提交）' };
export function filterPatterns(environment = '', operation = '', status = '') {
  return AGRI_PATTERNS.filter((p) => (!environment || p.environments.includes(environment))
    && (!operation || p.operation === operation) && (!status || patternStatus(p) === status));
}
export function defaultValues(pattern: AgriTaskPattern): Record<string, unknown> {
  return Object.fromEntries((pattern.runnable?.params ?? []).map((p) => [p.key, p.default]));
}
/** Validate editable values before building; the existing import endpoint validates real geometry/contracts. */
// getRandomValues also works on the existing LAN HTTP deployment (randomUUID does not).
function uniqueScenarioSuffix(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function buildPattern(pattern: AgriTaskPattern, values: Record<string, unknown>, seed: number) {
  if (patternStatus(pattern) === 'proposal' || !pattern.runnable) throw new Error('提案尚无可执行契约，不能提交求解');
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > 0xffffffff) throw new Error('随机种子须为 0–4294967295 的整数');
  for (const p of pattern.runnable.params) {
    const value = values[p.key];
    if (p.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value)
      || (p.min !== undefined && value < p.min) || (p.max !== undefined && value > p.max))) {
      throw new Error(`${p.label}须为有效数值${p.min !== undefined ? `，至少 ${p.min}` : ''}${p.max !== undefined ? `，至多 ${p.max}` : ''}`);
    }
    if (p.type === 'boolean' && typeof value !== 'boolean') throw new Error(`${p.label}须为开关值`);
    if (p.type === 'select' && !p.options?.some((o) => o.value === value)) throw new Error(`${p.label}选项无效`);
  }
  const built = pattern.runnable.build(values, seed);
  return { ...built, id: `agri-${pattern.id.slice(0, 20)}-${uniqueScenarioSuffix()}`, provenance: { ...built.provenance, params: { ...values, pattern_id: pattern.id, builder_version: 'urp.agri.builder/3' } } };
}
const importsInFlight = new Map<string, ReturnType<typeof studio.importScenario>>();
// Keep uncertain writes stable until an explicit retry reconciles their outcome. Persisted per tab
// (sessionStorage via pendingWrites) so a reload cannot turn a lost response into a second scenario.
type BuiltScenario = ReturnType<typeof buildPattern>;
const agriKey = (key: string) => `agri:${key}`;
const uncertainImports = {
  get(key: string): BuiltScenario | undefined {
    const entry = pendingWrite(agriKey(key));
    const built = entry?.payload as BuiltScenario | undefined;
    return built && typeof built === 'object' && built.id === entry!.id && built.provenance && typeof built.provenance === 'object'
      ? built : undefined;
  },
  set(key: string, built: BuiltScenario) { rememberWrite(agriKey(key), { id: built.id, kind: 'agri', created: Date.now(), payload: built }); },
  delete(key: string) { forgetWrite(agriKey(key)); },
};
function validateSaved(scenario: Awaited<ReturnType<typeof studio.importScenario>>) {
  if (!scenario || typeof scenario.id !== 'string' || !/^[a-z0-9][a-z0-9_.-]{1,63}$/.test(scenario.id)
    || typeof scenario.title !== 'string') throw new Error('请先核对保存结果：响应缺少有效场景标识');
  return scenario;
}
function containsSubmittedValue(actual: unknown, expected: unknown): boolean {
  if (Array.isArray(expected)) return Array.isArray(actual) && actual.length === expected.length
    && expected.every((value, i) => containsSubmittedValue(actual[i], value));
  if (expected !== null && typeof expected === 'object') return actual !== null && typeof actual === 'object'
    && Object.entries(expected).every(([key, value]) => containsSubmittedValue((actual as Record<string, unknown>)[key], value));
  return actual === expected;
}
function recoveredScenario(scenario: Awaited<ReturnType<typeof lab.scenario>>, built: ReturnType<typeof buildPattern>) {
  validateSaved(scenario);
  const provenance = scenario.provenance as { seed?: number; params?: Record<string, unknown> };
  if (scenario.id !== built.id || scenario.problem_class !== built.problem_class
    || !containsSubmittedValue(scenario.instance, built.instance) || !containsSubmittedValue(scenario.frame, built.frame)
    || provenance?.seed !== built.provenance.seed
    || !Object.entries(built.provenance.params).every(([key, value]) => provenance.params?.[key] === value)) {
    throw new Error('该场景与当前草稿不一致，请先核对保存结果');
  }
  return scenario;
}
export async function importPattern(pattern: AgriTaskPattern, values: Record<string, unknown>, seed: number) {
  const fresh = buildPattern(pattern, values, seed);
  const key = JSON.stringify([pattern.id, seed, pattern.runnable!.params.map((p) => [p.key, values[p.key]])]);
  const existing = importsInFlight.get(key);
  if (existing) return existing;
  const retry = uncertainImports.get(key);
  const built = retry ?? fresh;
  const request = (async () => {
    let definiteWriteFailure = false;
    // Recorded before the write leaves so a reload mid-request still reads the original id first.
    if (!retry) uncertainImports.set(key, built);
    try {
      if (retry) {
        try { return recoveredScenario(await lab.scenario(built.id), built); }
        catch (e) { if (!(e instanceof LabApiError && e.status === 404)) throw e; }
      }
      try {
        const saved = validateSaved(await studio.importScenario(built));
        if (saved.id !== built.id) throw new Error('返回的场景标识与本次提交不一致');
        return saved;
      }
      catch (e) {
        // The original write may finish between the read and this explicit retry.
        if (retry && e instanceof LabApiError && e.status === 409) return recoveredScenario(await lab.scenario(built.id), built);
        definiteWriteFailure = e instanceof LabApiError && e.code !== 'bad_response' && e.status >= 400 && e.status < 500 && e.status !== 408;
        throw e;
      }
    } catch (e) {
      if (definiteWriteFailure) {
        uncertainImports.delete(key); throw e;
      }
      uncertainImports.set(key, built);
      throw new Error(`请先核对保存结果（${built.id}）：${e instanceof Error ? e.message : String(e)}。再次点击将先查询该场景。`);
    }
  })().then((scenario) => { uncertainImports.delete(key); return scenario; });
  importsInFlight.set(key, request);
  try { return await request; }
  finally { if (importsInFlight.get(key) === request) importsInFlight.delete(key); }
}

/** One request per mounted card; navigation is invalidated even after an away/back round trip. */
export function createImportGuard() {
  let pending = false;
  let epoch = 0;
  return {
    begin(): number | null { if (pending) return null; pending = true; return epoch; },
    invalidateNavigation() { epoch += 1; },
    canNavigate(ticket: number) { return pending && ticket === epoch; },
    finish() { pending = false; },
  };
}
