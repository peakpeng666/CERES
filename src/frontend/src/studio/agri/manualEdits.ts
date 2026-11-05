/**
 * 已导入场景与“按其记录的参数和种子重新生成”的结果逐对象比较，找出地图上的手工编辑。
 * 只读：不改任何一方。用于再次生成前说明会影响哪些手工成果，以及把场景参数带回表单。
 */
import type { AgriTaskPattern } from './taxonomy';

export interface ManualEditReport {
  /** 能否按记录复现：缺种子、参数或构建器版本不符时为 false，并给出原因。 */
  reproducible: boolean;
  reason: string;
  params: Record<string, unknown>;
  seed: number | null;
  changes: string[];
}

type Rec = Record<string, unknown>;
/**
 * 规范化后再比较：后端保存时会补默认值（空的 corridors、默认车名 车1…车N）、改键序、
 * 序列化浮点数，这些都不算手工编辑。空数组 / null 视同缺省，键排序，数值取 1e-6 精度。
 */
function canon(v: unknown): unknown {
  if (v === null || v === undefined) return undefined;
  if (typeof v === 'number') return Math.round(v * 1e6) / 1e6;
  if (Array.isArray(v)) return v.length ? v.map(canon) : undefined;
  if (typeof v === 'object') {
    const out: Rec = {};
    for (const k of Object.keys(v as Rec).sort()) { const c = canon((v as Rec)[k]); if (c !== undefined) out[k] = c; }
    return Object.keys(out).length ? out : undefined;
  }
  return v;
}
/** 车队：未给 fleet 时，names 等于默认的 车1…车count 视同未填。 */
function canonVehicles(v: unknown): unknown {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return canon(v);
  const r = { ...(v as Rec) };
  const n = Number(r.count);
  if (!r.fleet && Array.isArray(r.names) && r.names.length === n && r.names.every((x, i) => x === `车${i + 1}`)) delete r.names;
  return canon(r);
}
const key = (v: unknown) => JSON.stringify(canon(v));
const near = (a: unknown, b: unknown) => Array.isArray(a) && Array.isArray(b) && a.length === 2 && b.length === 2
  && Math.abs(Number(a[0]) - Number(b[0])) < 0.01 && Math.abs(Number(a[1]) - Number(b[1])) < 0.01;

function diffById(label: string, gen: unknown, cur: unknown, field: string): string[] {
  const g = new Map((Array.isArray(gen) ? gen : []).map((x) => [String((x as Rec).id), x as Rec]));
  const c = new Map((Array.isArray(cur) ? cur : []).map((x) => [String((x as Rec).id), x as Rec]));
  const out: string[] = [];
  const moved = [...c.keys()].filter((id) => g.has(id) && key(g.get(id)![field]) !== key(c.get(id)![field]) && !near(g.get(id)![field], c.get(id)![field]));
  const added = [...c.keys()].filter((id) => !g.has(id));
  const removed = [...g.keys()].filter((id) => !c.has(id));
  if (moved.length) out.push(`${label}改动 ${moved.length} 个（${moved.slice(0, 5).join('、')}${moved.length > 5 ? ' 等' : ''}）`);
  if (added.length) out.push(`${label}新增 ${added.length} 个`);
  if (removed.length) out.push(`${label}删除 ${removed.length} 个`);
  return out;
}

export function manualEdits(pattern: AgriTaskPattern, scenario: { instance: unknown; provenance?: unknown }): ManualEditReport {
  const prov = (scenario.provenance ?? {}) as Rec;
  const recorded = (prov.params ?? {}) as Rec;
  const seed = typeof prov.seed === 'number' ? prov.seed : null;
  const params = Object.fromEntries((pattern.runnable?.params ?? []).filter((p) => p.key in recorded).map((p) => [p.key, recorded[p.key]]));
  const base = { reproducible: false, params, seed, changes: [] as string[] };
  if (!pattern.runnable) return { ...base, reason: '提案模式没有构建器' };
  if (recorded.pattern_id !== undefined && recorded.pattern_id !== pattern.id) return { ...base, reason: '场景来自其他模式' };
  if (seed === null) return { ...base, reason: '场景未记录随机种子，无法复现生成结果' };
  if (Object.keys(params).length !== pattern.runnable.params.length) return { ...base, reason: '场景记录的参数与当前构建器不一致，可能来自旧版本构建器' };
  let gen: Rec;
  try { gen = pattern.runnable.build(params, seed).instance as Rec; }
  catch (e) { return { ...base, reason: `按记录参数无法重新生成：${e instanceof Error ? e.message : String(e)}` }; }
  const cur = (scenario.instance ?? {}) as Rec;
  const changes = [
    ...(key(gen.boundary ?? gen.field) !== key(cur.boundary ?? cur.field) ? ['边界已改动'] : []),
    ...diffById('任务点', gen.tasks, cur.tasks, 'xy'),
    ...diffById('障碍', gen.obstacles, cur.obstacles, 'polygon'),
    ...diffById('地块', gen.plots, cur.plots, 'polygon'),
    ...(key(gen.depot) !== key(cur.depot) && !near(gen.depot, cur.depot) ? ['库点已移动'] : []),
    ...(key(gen.unload_points) !== key(cur.unload_points) ? ['卸货点已改动'] : []),
    ...(key(gen.corridors) !== key(cur.corridors) ? ['通道已改动'] : []),
    ...(JSON.stringify(canonVehicles(gen.vehicles)) !== JSON.stringify(canonVehicles(cur.vehicles)) ? ['车队配置已改动'] : []),
  ];
  return { ...base, reproducible: true, reason: '', changes };
}
