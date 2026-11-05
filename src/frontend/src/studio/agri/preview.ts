/**
 * 导入前几何预览：从构建产物抽取可绘制对象，并做前端保守预检。
 * 只读取构建产物，不改写任何坐标；显示简化不影响提交内容。后端校验仍是最终判断。
 */
import type { BuiltScenario } from './taxonomy';
import { pointInPolygon, distToPolygon, placeable, selfIntersects, signedArea, type Ring, type XY } from './geometry';

export type PreviewKind = 'fleet' | 'coverage';
export interface PreviewTask { id: string; label: string; xy: XY; demand?: number }
export interface PreviewIssue { level: 'error' | 'warn'; message: string; target?: string }
export interface Preview {
  kind: PreviewKind;
  boundary: Ring;
  plots: { id: string; name: string; polygon: Ring }[];
  obstacles: { id: string; kind: string; polygon: Ring }[];
  tasks: PreviewTask[];
  depots: XY[];
  unload: XY[];
  start?: XY;
  headlandWidth?: number;
  swathWidth?: number;
  robotRadius?: number;
  bbox: [number, number, number, number];
}

const isXY = (v: unknown): v is XY => Array.isArray(v) && v.length === 2 && v.every((n) => typeof n === 'number');
const ring = (v: unknown): Ring => (Array.isArray(v) ? v.filter(isXY) : []);
const num = (v: unknown) => (typeof v === 'number' ? v : undefined);

export function extractPreview(built: BuiltScenario): Preview {
  const inst = built.instance as Record<string, unknown>;
  const coverage = Array.isArray(inst.field);
  const boundary = ring(coverage ? inst.field : inst.boundary);
  const obstacles = (Array.isArray(inst.obstacles) ? inst.obstacles : []).map((o, i) => {
    const rec = (o && typeof o === 'object' && !Array.isArray(o) ? o : { polygon: o }) as Record<string, unknown>;
    return { id: String(rec.id ?? `obs${i + 1}`), kind: String(rec.kind ?? 'obstacle'), polygon: ring(rec.polygon) };
  });
  const plots = (Array.isArray(inst.plots) ? inst.plots : []).map((p, i) => {
    const rec = p as Record<string, unknown>;
    return { id: String(rec.id ?? `plot${i + 1}`), name: String(rec.name ?? rec.id ?? ''), polygon: ring(rec.polygon) };
  });
  const tasks = (Array.isArray(inst.tasks) ? inst.tasks : []).flatMap((t) => {
    const rec = t as Record<string, unknown>;
    return isXY(rec.xy) ? [{ id: String(rec.id), label: String(rec.label ?? rec.id), xy: rec.xy, demand: num(rec.demand) }] : [];
  });
  const depots = [inst.depot, ...(Array.isArray(inst.depots) ? inst.depots : [])].filter(isXY);
  const unload = ring(inst.unload_points);
  const all = [...boundary, ...tasks.map((t) => t.xy), ...depots, ...unload];
  const xs = all.map((p) => p[0]); const ys = all.map((p) => p[1]);
  const bbox: Preview['bbox'] = all.length ? [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] : [0, 0, 1, 1];
  return {
    kind: coverage ? 'coverage' : 'fleet', boundary, plots, obstacles, tasks, depots, unload,
    start: isXY(inst.start) ? inst.start : undefined,
    headlandWidth: num(inst.headland_width), swathWidth: num(inst.swath_width),
    robotRadius: num(inst.robot_radius), bbox,
  };
}

/** 前端预检：只定位问题，不修改实例。 */
export function previewIssues(p: Preview): PreviewIssue[] {
  const out: PreviewIssue[] = [];
  const finite = (r: Ring) => r.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  if (p.boundary.length < 3) { out.push({ level: 'error', message: '边界少于 3 个顶点', target: 'boundary' }); return out; }
  if (!finite(p.boundary)) out.push({ level: 'error', message: '边界含非有限坐标', target: 'boundary' });
  if (Math.abs(signedArea(p.boundary)) < 1e-6) out.push({ level: 'error', message: '边界面积退化为 0', target: 'boundary' });
  if (selfIntersects(p.boundary)) out.push({ level: 'error', message: '边界自交', target: 'boundary' });
  for (const o of p.obstacles) {
    if (o.polygon.length < 3 || Math.abs(signedArea(o.polygon)) < 1e-6) out.push({ level: 'error', message: `障碍 ${o.id} 面积退化`, target: `obstacle:${o.id}` });
    else if (selfIntersects(o.polygon)) out.push({ level: 'error', message: `障碍 ${o.id} 自交`, target: `obstacle:${o.id}` });
    else if (o.polygon.some((v) => !pointInPolygon(v, p.boundary) && distToPolygon(v, p.boundary) > 1e-6)) out.push({ level: 'warn', message: `障碍 ${o.id} 越出边界`, target: `obstacle:${o.id}` });
  }
  const r = p.robotRadius ?? 0;
  const obs = p.obstacles.map((o) => o.polygon).filter((g) => g.length >= 3);
  for (const t of p.tasks) {
    if (!Number.isFinite(t.xy[0]) || !Number.isFinite(t.xy[1])) out.push({ level: 'error', message: `${t.label} 坐标非有限值`, target: `task:${t.id}` });
    else if (!placeable(t.xy, p.boundary, obs, r)) out.push({ level: 'error', message: `${t.label} 落在障碍内、越界或车体净空不足 ${r} m`, target: `task:${t.id}` });
  }
  for (const [i, d] of p.depots.entries()) {
    if (!pointInPolygon(d, p.boundary)) out.push({ level: 'error', message: `库点 ${i + 1} 不在边界内`, target: `depot:${i}` });
  }
  if (p.kind === 'coverage' && p.swathWidth !== undefined && p.headlandWidth !== undefined && p.headlandWidth < p.swathWidth) {
    out.push({ level: 'warn', message: `地头宽 ${p.headlandWidth} m 小于作业幅宽 ${p.swathWidth} m，地头转弯可能不足一个幅宽`, target: 'boundary' });
  }
  return out;
}

/** What a picked issue target points at, so the map can highlight and describe it; null if it no longer exists. */
export function describeTarget(p: Preview, target: string): { layer: 'boundary' | 'obstacles' | 'tasks' | 'depots'; text: string } | null {
  const fmt = (q: XY) => `(${q[0]}, ${q[1]}) m`;
  if (target === 'boundary') return { layer: 'boundary', text: `作业边界，${p.boundary.length} 个顶点` };
  const [kind, ...rest] = target.split(':'); const id = rest.join(':');
  if (kind === 'obstacle') { const o = p.obstacles.find((x) => x.id === id); return o ? { layer: 'obstacles', text: `障碍 ${o.id}（${o.kind}），${o.polygon.length} 个顶点` } : null; }
  if (kind === 'task') { const t = p.tasks.find((x) => x.id === id); return t ? { layer: 'tasks', text: `${t.label}，坐标 ${fmt(t.xy)}${t.demand !== undefined ? `，需求 ${t.demand}` : ''}` } : null; }
  if (kind === 'depot') { const d = p.depots[Number(id)]; return d ? { layer: 'depots', text: `库点 ${Number(id) + 1}，坐标 ${fmt(d)}` } : null; }
  return null;
}

/** Form parameters that drive an issue's target, most direct first; only keys the pattern actually has. */
export function issueFields(issue: PreviewIssue, keys: string[]): string[] {
  const t = issue.target ?? '';
  const want: RegExp[] = /地头/.test(issue.message) ? [/^headland_width$/, /^(swath_width|row_spacing|rows_per_pass|implement_width)$/]
    : t === 'boundary' ? [/^field_[wh]$/, /boundary|shape|notch/]
    : t.startsWith('obstacle:') ? [/obstacle|pond|tree|pole|ditch/, /^field_[wh]$/]
    : t.startsWith('task:') ? [/^robot_radius$|robot/, /task|row|spacing|tree/, /obstacle/]
    : t.startsWith('depot:') ? [/depot|unload/, /^field_[wh]$/] : [];
  const out: string[] = [];
  for (const re of want) for (const k of keys) if (re.test(k) && !out.includes(k)) out.push(k);
  return out;
}
