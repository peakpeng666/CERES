import type { Catalog, Crop, Environment, Implement, Mission, Operation, Platform, Sourced } from './types';

export const SHAPES: { id: string; label: string; d: string }[] = [
  { id: 'rect', label: '矩形', d: 'M3 5h18v14H3z' },
  { id: 'trapezoid', label: '梯形', d: 'M3 19 5.5 5h13L21 19z' },
  { id: 'l_shape', label: 'L 形', d: 'M3 5h10v7h8v7H3z' },
  { id: 'pentagon', label: '不规则', d: 'M3 19V9l8-5 10 3-2 12z' },
];

export const STAGES: { id: string; label: string }[] = [
  { id: 'pre_crop', label: '耕种' }, { id: 'in_crop', label: '田间管理' }, { id: 'harvest', label: '收获' }, { id: 'any', label: '设施作业' },
];

/** Starting points for the jobs people actually come with. */
export const PRESETS: { id: string; label: string; hint: string; mission: Partial<Mission> }[] = [
  { id: 'boom', label: '大田植保', hint: '12 m 喷杆 · 800 L', mission: { environment: 'open_field', crop: 'wheat', operation: 'spraying_blanket', platform: 'row_tractor_120', implement: 'boom_mounted', field: { shape: 'rect', width_m: 400, height_m: 250 }, overrides: { rate_per_ha: 150 } } },
  { id: 'orchard', label: '果园植保', hint: '苹果 · 窄型车', mission: { environment: 'orchard', crop: 'apple', operation: 'spraying_blanket', platform: 'jd_5075gn', implement: 'air_blast', field: { shape: 'trapezoid', width_m: 220, height_m: 120 }, overrides: { rate_per_ha: 500 } } },
  { id: 'tillage', label: '履带耕整', hint: '3 m 圆盘耙', mission: { environment: 'open_field', crop: null, operation: 'tillage', platform: 'agxeed_5115t2', implement: 'disk_harrow', field: { shape: 'pentagon', width_m: 300, height_m: 200 }, overrides: {} } },
  { id: 'harvest', label: '小麦收获', hint: '6 m 割台', mission: { environment: 'open_field', crop: 'wheat', operation: 'combine_harvest', platform: 'combine_generic', implement: 'combine_header', field: { shape: 'rect', width_m: 500, height_m: 300 }, overrides: {} } },
  { id: 'robot', label: '机器人田块', hint: '可进仿真', mission: { environment: 'open_field', crop: 'wheat', operation: 'spraying_blanket', platform: 'field_diffdrive', implement: 'spray_module', field: { shape: 'rect', width_m: 36, height_m: 24 }, overrides: { rate_per_ha: 200 }, fleet: 2 } },
];

export const BASE: Mission = {
  environment: 'open_field', crop: 'wheat', operation: 'spraying_blanket', platform: 'row_tractor_120', implement: 'boom_mounted',
  field: { shape: 'rect', width_m: 400, height_m: 250 }, angle_deg: 0, headland_m: null, fleet: 1, objective: null, station: 'sw', overrides: { rate_per_ha: 150 },
};

const by = <T extends { id: string }>(list: T[], id: string | null | undefined) => list.find((x) => x.id === id);

export function implementFits(i: Implement, p: Platform, operation: string): boolean {
  return i.operations.includes(operation) && p.hitch.includes(i.hitch) && (!i.for || i.for.includes(p.id));
}

export const lineOperations = (c: Catalog, env: Environment) => c.operations.filter((o) => env.line_operations.includes(o.id) && c.implements.some((i) => i.operations.includes(o.id)));

export function platformsFor(c: Catalog, env: Environment, operation: string): { platform: Platform; ok: boolean; why?: string }[] {
  return c.platforms.map((platform) => {
    if (platform.tier === 'display') return { platform, ok: false, why: '只有展示模型' };
    if (!env.platforms.includes(platform.class)) return { platform, ok: false, why: `不适用${env.name}` };
    if (!c.implements.some((i) => implementFits(i, platform, operation))) return { platform, ok: false, why: '没有可挂机具' };
    return { platform, ok: true };
  }).sort((a, b) => Number(b.ok) - Number(a.ok));
}

/** After one choice changes, move the dependent choices to something that still fits; never touch what still fits. */
export function reconcile(c: Catalog, m: Mission): Mission {
  const env = by(c.environments, m.environment) ?? c.environments[0];
  const next = { ...m, overrides: { ...m.overrides } };
  const crops = env.default_crops;
  const opKeepsBareSoil = by(c.operations, m.operation)?.stage === 'pre_crop';
  if (!crops.length) next.crop = null;
  else if (!m.crop || !crops.includes(m.crop)) next.crop = opKeepsBareSoil && !m.crop ? null : crops[0];
  const ops = lineOperations(c, env);
  if (!ops.length) return m;
  if (!ops.some((o) => o.id === m.operation)) next.operation = ops[0].id;
  const list = platformsFor(c, env, next.operation);
  const current = list.find((x) => x.platform.id === m.platform);
  if (!current?.ok) {
    const pick = list.find((x) => x.ok);
    if (pick) next.platform = pick.platform.id;
  }
  const platform = by(c.platforms, next.platform)!;
  const implement = by(c.implements, m.implement);
  if (!implement || !implementFits(implement, platform, next.operation)) {
    const pick = c.implements.find((i) => implementFits(i, platform, next.operation));
    if (pick) {
      next.implement = pick.id;
      next.overrides.work_width_m = null;
      next.overrides.capacity = null;
    }
  }
  const op = by(c.operations, next.operation);
  if (op?.resource === 'consume' && !next.overrides.rate_per_ha) next.overrides.rate_per_ha = op.default_rate ?? null;
  if (op?.resource !== 'consume') next.overrides.rate_per_ha = null;
  return next;
}

export function sourceOf(c: Catalog, id?: string) { return id ? by(c.sources, id) : undefined; }

export function basisOf(c: Catalog, node?: Sourced | { s: string } | null): { label: string; kind: string; title: string } {
  const src = sourceOf(c, node?.s);
  const kind = src?.kind ?? 'assumption';
  return { kind, label: c.labels.basis[kind] ?? kind, title: src ? `${src.title}${src.scope ? `\n${src.scope}` : ''}${src.checked === 'search_summary' ? '\n来源核对：检索摘要' : ''}` : '未登记' };
}

export const hectares = (w: number, h: number) => w * h / 10000;

export function fmt(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

export function hours(h: number | null | undefined): string {
  if (h == null) return '—';
  if (h < 1) return `${Math.round(h * 60)} 分钟`;
  return `${fmt(h, 1)} 小时`;
}

export function missionKey(m: Mission) { return JSON.stringify(m); }

const STORE = 'lab:field-mission';
export function loadMission(): Mission | null {
  try {
    const raw = localStorage.getItem(STORE);
    return raw ? { ...BASE, ...(JSON.parse(raw) as Mission) } : null;
  } catch { return null; }
}
export function saveMission(m: Mission) {
  try { localStorage.setItem(STORE, JSON.stringify(m)); } catch { /* a draft is a convenience */ }
}

export interface Fit { op: Operation; env: Environment; crop: Crop | null }
