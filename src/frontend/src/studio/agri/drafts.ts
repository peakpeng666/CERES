/**
 * 农业目录草稿持久化：按模式保存参数与种子，跨刷新、路由与筛选切换保留。
 *
 * - schema 版本不符直接拒绝并说明，不静默套用；
 * - 构建器参数签名变化时只保留键名与类型都还匹配的值，其余回到默认并告知；
 * - 存储不可用、配额不足、内容损坏都不会抛到界面之外，只返回可读的提示；
 * - 草稿只是本机表单状态，不会覆盖服务器上的场景修订。
 */
import type { AgriTaskPattern, BuilderParam } from './taxonomy';

export const DRAFT_SCHEMA = 1;
const DRAFT_PREFIX = 'urp.agri.draft.v1:';
const FILTER_KEY = 'urp.agri.filters.v1';

export interface AgriDraft {
  schema: number;
  patternId: string;
  /** 构建器参数结构签名（键、类型、单位），用来识别过期草稿。 */
  signature: string;
  values: Record<string, unknown>;
  seed: string;
  savedAt: string;
}

export interface DraftLoad {
  values: Record<string, unknown>;
  seed: string;
  restored: boolean;
  notice: string;
}

export interface CatalogFilters { environment: string; operation: string; status: string }

export function paramSignature(params: BuilderParam[]): string {
  return params.map((p) => `${p.key}:${p.type}:${p.unit ?? ''}`).join('|');
}

export const draftKey = (patternId: string) => `${DRAFT_PREFIX}${patternId}`;

function storage(kind: 'local' | 'session'): Storage | null {
  try {
    const s = kind === 'local' ? globalThis.localStorage : globalThis.sessionStorage;
    if (!s) return null;
    const probe = '__urp_probe__';
    s.setItem(probe, '1'); s.removeItem(probe);
    return s;
  } catch { return null; }
}

function typeMatches(param: BuilderParam, value: unknown): boolean {
  if (param.type === 'number') return typeof value === 'number' || value === null;
  if (param.type === 'boolean') return typeof value === 'boolean';
  return typeof value === 'string' && (param.options?.some((o) => o.value === value) ?? false);
}

/** JSON 不能表示 NaN，空数值框以 null 保存，读回时还原成 NaN 以保持“未填写”语义。 */
function decodeValue(param: BuilderParam, value: unknown): unknown {
  return param.type === 'number' && value === null ? NaN : value;
}

export function loadDraft(pattern: AgriTaskPattern, defaults: Record<string, unknown>): DraftLoad {
  const fresh: DraftLoad = { values: { ...defaults }, seed: '42', restored: false, notice: '' };
  const s = storage('local');
  if (!s) return fresh;
  let raw: string | null;
  try { raw = s.getItem(draftKey(pattern.id)); } catch { return fresh; }
  if (raw === null) return fresh;
  let parsed: Partial<AgriDraft>;
  try { parsed = JSON.parse(raw) as Partial<AgriDraft>; } catch { parsed = {}; }
  if (!parsed || typeof parsed !== 'object' || parsed.patternId !== pattern.id || typeof parsed.values !== 'object' || parsed.values === null) {
    try { s.removeItem(draftKey(pattern.id)); } catch { /* 只读存储时保持原样 */ }
    return { ...fresh, notice: '本机草稿已损坏，已改用默认参数。' };
  }
  if (parsed.schema !== DRAFT_SCHEMA) {
    return { ...fresh, notice: `本机草稿格式版本 ${String(parsed.schema)} 与当前版本 ${DRAFT_SCHEMA} 不兼容，未自动套用；清除草稿后可继续。` };
  }
  const params = pattern.runnable?.params ?? [];
  const values: Record<string, unknown> = { ...defaults };
  let kept = 0;
  let dropped = 0;
  for (const param of params) {
    if (!(param.key in parsed.values)) continue;
    const v = (parsed.values as Record<string, unknown>)[param.key];
    if (typeMatches(param, v)) { values[param.key] = decodeValue(param, v); kept += 1; } else dropped += 1;
  }
  const seed = typeof parsed.seed === 'string' ? parsed.seed : '42';
  const when = typeof parsed.savedAt === 'string' ? parsed.savedAt : '';
  const migrated = parsed.signature !== paramSignature(params);
  const notice = migrated
    ? `构建器参数已变更：从草稿保留 ${kept} 项${dropped ? `，${dropped} 项类型不符已回到默认` : ''}，其余为默认值。`
    : `已恢复本机草稿${when ? `（保存于 ${when}）` : ''}。`;
  return { values, seed, restored: true, notice };
}

/** 保存草稿；失败时返回可展示的原因，成功返回空串。 */
export function saveDraft(pattern: AgriTaskPattern, values: Record<string, unknown>, seed: string, now = new Date()): string {
  const s = storage('local');
  if (!s) return '浏览器存储不可用，草稿只在本页有效。';
  const encoded = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, typeof v === 'number' && !Number.isFinite(v) ? null : v]));
  const draft: AgriDraft = {
    schema: DRAFT_SCHEMA, patternId: pattern.id, signature: paramSignature(pattern.runnable?.params ?? []),
    values: encoded, seed, savedAt: now.toISOString(),
  };
  try { s.setItem(draftKey(pattern.id), JSON.stringify(draft)); return ''; }
  catch (e) {
    const quota = e instanceof DOMException && (e.name === 'QuotaExceededError' || e.code === 22);
    return quota ? '浏览器存储已满，草稿未能保存。' : '草稿保存失败，只在本页有效。';
  }
}

export function clearDraft(patternId: string): void {
  try { storage('local')?.removeItem(draftKey(patternId)); } catch { /* 忽略 */ }
}

/** 其他标签页改了同一模式的草稿时回调；返回取消订阅函数。 */
export function onForeignDraftChange(patternId: string, cb: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const key = draftKey(patternId);
  const handler = (e: StorageEvent) => { if (e.key === key) cb(); };
  window.addEventListener('storage', handler);
  return () => window.removeEventListener('storage', handler);
}

export function loadFilters(): CatalogFilters {
  const empty = { environment: '', operation: '', status: '' };
  try {
    const raw = storage('session')?.getItem(FILTER_KEY);
    if (!raw) return empty;
    const v = JSON.parse(raw) as Partial<CatalogFilters>;
    return {
      environment: typeof v.environment === 'string' ? v.environment : '',
      operation: typeof v.operation === 'string' ? v.operation : '',
      status: typeof v.status === 'string' ? v.status : '',
    };
  } catch { return empty; }
}

export function saveFilters(f: CatalogFilters): void {
  try { storage('session')?.setItem(FILTER_KEY, JSON.stringify(f)); } catch { /* 忽略 */ }
}

/** 每个模式最近一次导入的场景，用于再次生成前提示：新导入只会新建场景，不会覆盖已在地图上手工编辑过的那份。 */
export interface LastImport { scenarioId: string; title: string; seed: string; at: string }
const lastKey = (patternId: string) => `urp.agri.lastImport.v1:${patternId}`;

export function recordImport(patternId: string, rec: LastImport): void {
  try { storage('local')?.setItem(lastKey(patternId), JSON.stringify(rec)); } catch { /* 忽略 */ }
}

export function lastImport(patternId: string): LastImport | null {
  try {
    const raw = storage('local')?.getItem(lastKey(patternId));
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<LastImport>;
    return typeof v.scenarioId === 'string' && typeof v.title === 'string' ? { scenarioId: v.scenarioId, title: v.title, seed: String(v.seed ?? ''), at: String(v.at ?? '') } : null;
  } catch { return null; }
}

/** Runs `write` once after `delayMs`; `flush` runs a pending write immediately and is a no-op otherwise. */
export function debouncedDraftWriter(write: () => void, delayMs: number) {
  let timer: ReturnType<typeof setTimeout> | null = setTimeout(() => { timer = null; write(); }, delayMs);
  return {
    flush() { if (timer === null) return; clearTimeout(timer); timer = null; write(); },
    /** Drops a pending write (superseded by a newer edit). */
    cancel() { if (timer !== null) clearTimeout(timer); timer = null; },
    pending: () => timer !== null,
  };
}

const FOCUS_KEY = 'urp.agri.focus.v1';
export const FOCUS_EVENT = 'urp-agri-focus';
/** Ask the catalog to open one pattern card (used when the plan page sends the user back to edit parameters). */
export function requestCatalogFocus(patternId: string) {
  storage('session')?.setItem(FOCUS_KEY, patternId);
  globalThis.dispatchEvent?.(new CustomEvent(FOCUS_EVENT, { detail: patternId }));
}
export function peekCatalogFocus(): string { return storage('session')?.getItem(FOCUS_KEY) ?? ''; }
/** Read and clear the pending focus request, so it applies once. */
export function takeCatalogFocus(): string {
  const id = peekCatalogFocus();
  if (id) storage('session')?.removeItem(FOCUS_KEY);
  return id;
}

/** The pattern and parameters an imported scenario was generated from, or null if it was not built by a catalog pattern. */
export function generatedFrom(provenance: unknown): { patternId: string; params: Record<string, unknown>; seed: number | null } | null {
  const prov = (provenance ?? {}) as Record<string, unknown>;
  const params = prov.params as Record<string, unknown> | undefined;
  const id = params?.pattern_id;
  if (typeof id !== 'string' || !id) return null;
  return { patternId: id, params, seed: typeof prov.seed === 'number' ? prov.seed : null };
}
