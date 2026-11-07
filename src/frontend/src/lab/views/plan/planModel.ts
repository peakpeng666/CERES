/**
 * Pure planning-workspace logic: scenario grouping, instance editing, canvas
 * scene construction and result bookkeeping. No React, no fetch.
 */
import type {
  Algorithm,
  AlgorithmParam,
  CoverageInstance,
  EuclideanCvrpInstance,
  Instance,
  ProblemClass,
  RunState,
  Scenario,
  ScenarioSummary,
  SimSupport,
  Solution,
  TurnRoutingInstance,
  XY,
} from '../../types';
import type { CanvasMarker, CanvasNode, CanvasPath, CanvasPolygon, CanvasScene } from '../../canvas/types';

/** Field-operation instance as the canvas reads it. */
export interface FieldInstance {
  field: XY[]; obstacles: XY[][]; pitch_m: number; lines: { id: number; row: number; p1: XY; p2: XY; length_m: number }[]; stations: { id: string; xy: XY }[];
  rig: { name: string; tier: string; work_width_m: number; min_turn_radius_m: number; speed_work_mps: number; resource: { kind: string; name?: string; unit?: string; capacity: number | null; rate_per_ha: number | null } };
  headland_m: number; machines: number; objective: string; mission?: Record<string, unknown>;
}

export const CLASS_ORDER: ProblemClass[] = ['field_operation', 'fleet_routing', 'turn_routing', 'euclidean_cvrp', 'coverage', 'point_to_point'];
export const CLASS_LABEL: Record<ProblemClass, string> = {
  field_operation: '田间作业',
  fleet_routing: '多车任务',
  turn_routing: '转弯约束路网',
  euclidean_cvrp: '车辆路径 CVRP',
  coverage: '覆盖作业',
  point_to_point: '点到点规划',
};

export const COST_UNIT_LABEL: Record<string, string> = {
  cm_equivalent: 'cm 当量',
  synthetic_integer_cost: '合成整数成本',
  euclidean: '欧氏距离（无量纲）',
  m: 'm',
  unitless: '无量纲',
};
export const costUnit = (u: string | null | undefined) => (u ? COST_UNIT_LABEL[u] ?? u : '');
export const lengthUnit = (u: string | null | undefined) => (u === 'm' ? 'm' : u === 'unitless' ? '无量纲' : u ?? '');

export const RUN_STATE_LABEL: Record<RunState, string> = {
  queued: '排队中',
  running: '求解中',
  done: '完成',
  error: '出错',
  timeout: '超时',
  canceled: '已取消',
};
export const STATUS_LABEL: Record<string, string> = {
  optimal: '最优',
  feasible: '可行',
  infeasible: '不可行',
  no_solution: '未找到通过检查的解',
};
export const FAMILY_LABEL: Record<string, string> = {
  exact: '精确',
  metaheuristic: '元启发式',
  heuristic: '启发式',
  baseline: '基线',
  coverage: '覆盖规划',
  relaxation: '松弛',
};
export const GUARANTEE_LABEL: Record<string, string> = {
  optimal: '保证最优',
  optimal_for_relaxation: '仅对松弛问题最优',
  none: '无最优性保证',
};

export const TERMINAL: ReadonlySet<string> = new Set(['done', 'error', 'timeout', 'canceled']);
export const isTerminal = (state: string | null | undefined) => !!state && TERMINAL.has(state);

export function groupScenarios(list: readonly ScenarioSummary[]) {
  return CLASS_ORDER.map((cls) => ({ cls, label: CLASS_LABEL[cls], items: list.filter((s) => s.problem_class === cls) })).filter(
    (g) => g.items.length > 0,
  );
}

/* ------------------------------------------------------------ identity -- */

/** Deterministic JSON (sorted keys) for comparing instances. */
export function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj)
    .filter((k) => obj[k] !== undefined)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`)
    .join(',')}}`;
}

/** Short FNV-1a fingerprint of an instance (client-side identity only, not the gateway's sha256). */
export function instanceKey(instance: Instance | null | undefined): string {
  const text = stableStringify(instance ?? null);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0') + text.length.toString(16);
}

/* ------------------------------------------------------------- editing -- */

/** Rendering guard only. The backend performs full domain validation. */
export function parseEditableInstance(text: string, cls?: ProblemClass): Instance | null {
  try {
    const v=JSON.parse(text);
    const num=(x:unknown)=>typeof x==='number'&&Number.isFinite(x);
    const point=(x:unknown)=>Array.isArray(x)&&x.length===2&&x.every(num);
    const ring=(x:unknown)=>Array.isArray(x)&&x.length>=3&&x.every(point);
    if(!v||typeof v!=='object'||Array.isArray(v))return null;
    if(cls==='coverage')return ring(v.field)&&Array.isArray(v.obstacles)&&v.obstacles.every(ring)&&
      ['headland_width','swath_width','turn_radius','robot_width'].every(k=>num(v[k]))&&(!v.start||point(v.start))?v:null;
    if(!num(v.capacity)||!num(v.depot))return null;
    if(cls==='euclidean_cvrp')return Array.isArray(v.coordinates)&&v.coordinates.length>=2&&v.coordinates.every(point)&&
      Array.isArray(v.demands)&&v.demands.length===v.coordinates.length&&v.demands.every(num)&&
      (!v.labels||Array.isArray(v.labels)&&v.labels.every((x:unknown)=>typeof x==='string'))?v:null;
    if(cls==='turn_routing')return v.coordinates&&typeof v.coordinates==='object'&&!Array.isArray(v.coordinates)&&
      Object.values(v.coordinates).every(point)&&Array.isArray(v.arcs)&&Array.isArray(v.customers)&&
      v.customers.every((c:Record<string,unknown>)=>c&&num(c.id)&&num(c.node)&&num(c.demand)&&(c.label===undefined||typeof c.label==='string'))?v:null;
    return null;
  }catch{return null;}
}

export const asTurn = (i: Instance) => i as TurnRoutingInstance;
export const asCvrp = (i: Instance) => i as EuclideanCvrpInstance;
export const asCoverage = (i: Instance) => i as CoverageInstance;

const renumber = (customers: TurnRoutingInstance['customers']) => customers.map((c, id) => ({ ...c, id }));

/** Toggle a graph node as a service task (depot excluded). Task ids are renumbered 0..n-1. */
export function toggleTurnTask(inst: TurnRoutingInstance, node: number): TurnRoutingInstance {
  if (node === inst.depot) return inst;
  const existing = inst.customers.find((c) => c.node === node);
  const customers = existing
    ? inst.customers.filter((c) => c.node !== node)
    : [...inst.customers, { id: inst.customers.length, node, demand: 1, label: `N${node}` }];
  return { ...inst, customers: renumber(customers) };
}

export function setTurnDepot(inst: TurnRoutingInstance, node: number): TurnRoutingInstance {
  if (node === inst.depot) return inst;
  return { ...inst, depot: node, customers: renumber(inst.customers.filter((c) => c.node !== node)) };
}

export function addCvrpPoint(inst: EuclideanCvrpInstance, xy: XY, demand = 1): EuclideanCvrpInstance {
  const n = inst.coordinates.length;
  const labels = inst.labels ? [...inst.labels, String(n)] : undefined;
  return { ...inst, coordinates: [...inst.coordinates, roundXY(xy)], demands: [...inst.demands, demand], ...(labels ? { labels } : {}) };
}

export function removeCvrpPoint(inst: EuclideanCvrpInstance, index: number): EuclideanCvrpInstance {
  if (index <= 0 || index >= inst.coordinates.length) return inst;
  const drop = <T,>(a: T[]) => a.filter((_, i) => i !== index);
  return {
    ...inst,
    coordinates: drop(inst.coordinates),
    demands: drop(inst.demands),
    ...(inst.labels ? { labels: drop(inst.labels) } : {}),
  };
}

export function setCvrpDepot(inst: EuclideanCvrpInstance, xy: XY): EuclideanCvrpInstance {
  return { ...inst, coordinates: [roundXY(xy), ...inst.coordinates.slice(1)] };
}

export const setCoverageStart = (inst: CoverageInstance, xy: XY): CoverageInstance => ({ ...inst, start: roundXY(xy) });

export function setDemand(cls: ProblemClass, inst: Instance, taskId: number, demand: number): Instance {
  const d = Math.max(0, Math.round(demand));
  if (cls === 'turn_routing') {
    const t = asTurn(inst);
    return { ...t, customers: t.customers.map((c) => (c.id === taskId ? { ...c, demand: d } : c)) };
  }
  if (cls === 'euclidean_cvrp') {
    const c = asCvrp(inst);
    if (taskId <= 0 || taskId >= c.demands.length) return inst;
    return { ...c, demands: c.demands.map((x, i) => (i === taskId ? d : x)) };
  }
  return inst;
}

export function setCapacity(inst: Instance, capacity: number): Instance {
  return { ...(inst as TurnRoutingInstance | EuclideanCvrpInstance), capacity: Math.max(1, Math.round(capacity)) } as Instance;
}

const roundXY = (p: XY): XY => [Math.round(p[0] * 100) / 100, Math.round(p[1] * 100) / 100];

/* --------------------------------------------------------------- tasks -- */

export interface TaskRow {
  id: number;
  label: string;
  node: number | null;
  xy: XY | null;
  demand: number;
}

export function taskRows(cls: ProblemClass, inst: Instance | null): TaskRow[] {
  if (!inst) return [];
  if (cls === 'turn_routing') {
    const t = asTurn(inst);
    return t.customers.map((c) => ({
      id: c.id,
      label: c.label ?? `T${c.id}`,
      node: c.node,
      xy: t.coordinates?.[String(c.node)] ?? null,
      demand: c.demand,
    }));
  }
  if (cls === 'euclidean_cvrp') {
    const c = asCvrp(inst);
    return c.coordinates.slice(1).map((xy, k) => ({ id: k + 1, label: c.labels?.[k + 1] ?? String(k + 1), node: k + 1, xy, demand: c.demands[k + 1] ?? 0 }));
  }
  return [];
}

export function totalDemand(cls: ProblemClass, inst: Instance | null): number | null {
  if (!inst || cls === 'coverage') return null;
  return taskRows(cls, inst).reduce((s, t) => s + t.demand, 0);
}

/** World position of a task id as used in Route.services / Route.sequence. */
export function taskXY(cls: ProblemClass, inst: Instance, task: number): XY | null {
  if (cls === 'turn_routing') {
    const t = asTurn(inst);
    const c = t.customers.find((x) => x.id === task);
    return c ? t.coordinates?.[String(c.node)] ?? null : null;
  }
  if (cls === 'euclidean_cvrp') return asCvrp(inst).coordinates[task] ?? null;
  return null;
}

/* --------------------------------------------------------------- scene -- */

const ringOK = (r: unknown): r is XY[] => Array.isArray(r) && r.length >= 3;

export function scenarioPolygons(scenario: Scenario, inst: Instance): CanvasPolygon[] {
  const out: CanvasPolygon[] = [];
  const L = scenario.layers ?? {};
  const push = (rings: unknown, kind: CanvasPolygon['kind']) => {
    if (!Array.isArray(rings)) return;
    rings.forEach((r, i) => ringOK(r) && out.push({ id: `${kind}:${i}`, ring: r, kind }));
  };
  if (ringOK(L.field_polygon)) out.push({ id: 'field', ring: L.field_polygon, kind: 'field' });
  push(L.headland, 'headland');
  push(L.row_polygons, 'row');
  push(L.obstacles, 'obstacle');
  if (scenario.problem_class === 'coverage') {
    const c = asCoverage(inst);
    if (!ringOK(L.field_polygon) && ringOK(c.field)) out.push({ id: 'cov-field', ring: c.field, kind: 'field' });
    push(c.obstacles, 'obstacle');
  }
  if (scenario.problem_class === 'field_operation') {
    const f = inst as unknown as FieldInstance;
    if (ringOK(f.field)) out.push({ id: 'op-field', ring: f.field, kind: 'field' });
    push(f.obstacles, 'obstacle');
  }
  return out;
}

/** Base canvas scene: map underlay, areas, graph, nodes and task glyphs for the current (possibly edited) instance. */
export function baseScene(scenario: Scenario, inst: Instance): CanvasScene {
  const frame = scenario.frame;
  const scene: CanvasScene = {
    units: frame.units === 'm' ? 'm' : 'unitless',
    map: frame.kind === 'map' ? frame.map : null,
    polygons: scenarioPolygons(scenario, inst),
    labels: Array.isArray(scenario.layers?.labels) ? scenario.layers!.labels : [],
  };
  if (scenario.problem_class === 'field_operation') {
    const f = inst as unknown as FieldInstance;
    scene.nodes = f.stations.map((x) => ({ id: x.id, xy: x.xy, kind: 'depot', label: '补给站' }));
    scene.paths = f.lines.map((x) => ({ id: `line:${x.id}`, points: [x.p1, x.p2], color: '#7f9a68', width: 1, opacity: .55, swath: f.pitch_m, title: `第 ${x.row + 1} 行 · ${x.length_m.toFixed(0)} m` }));
    return scene;
  }
  if (scenario.problem_class === 'turn_routing') {
    const t = asTurn(inst);
    const coords = t.coordinates ?? {};
    const at = (n: number) => coords[String(n)];
    const has = new Set(t.arcs.map(([u, v]) => `${u}>${v}`));
    scene.arcs = [];
    for (const [u, v] of t.arcs) {
      const a = at(u);
      const b = at(v);
      if (!a || !b) continue;
      const twoWay = has.has(`${v}>${u}`);
      if (twoWay && u > v) continue;
      scene.arcs.push({ a, b, oneWay: !twoWay });
    }
    const byNode = new Map(t.customers.map((c) => [c.node, c]));
    const forbidden = new Map<string, XY[][]>();
    const penalties = new Map<string, number>();
    const toward = (from: XY, to: XY, f = 0.55): XY => [from[0] + (to[0] - from[0]) * f, from[1] + (to[1] - from[1]) * f];
    for (const [u, v, w] of t.forbidden_turns ?? []) {
      const pu = at(u);
      const pv = at(v);
      const pw = at(w);
      if (!pu || !pv || !pw) continue;
      const line: XY[] = u === w ? [toward(pv, pu), pv, toward(pv, pu, 0.35)] : [toward(pv, pu), pv, toward(pv, pw)];
      const key = String(v);
      forbidden.set(key, [...(forbidden.get(key) ?? []), line]);
    }
    for (const [, v] of t.turn_penalties ?? []) penalties.set(String(v), (penalties.get(String(v)) ?? 0) + 1);
    scene.turnHints = (id) => forbidden.get(id) ?? [];
    scene.nodes = Object.entries(coords).map(([id, xy]): CanvasNode => {
      const node = Number(id);
      const c = byNode.get(node);
      const role = t.node_kinds?.[id];
      const extra = penalties.get(id) ? [`经此节点的转弯惩罚 ${penalties.get(id)} 处`] : undefined;
      if (node === t.depot) return { id, xy, kind: 'depot', role, label: '仓库', extra };
      if (c) return { id, xy, kind: 'task', role, label: c.label ?? `T${c.id}`, demand: c.demand, extra };
      return { id, xy, kind: 'other', role, extra };
    });
  } else if (scenario.problem_class === 'euclidean_cvrp') {
    const c = asCvrp(inst);
    scene.nodes = c.coordinates.map((xy, i): CanvasNode =>
      i === 0
        ? { id: '0', xy, kind: 'depot', label: c.labels?.[0] ?? '仓库' }
        : { id: String(i), xy, kind: 'task', label: c.labels?.[i] ?? String(i), demand: c.demands[i] ?? null },
    );
  } else {
    const c = asCoverage(inst);
    scene.nodes = c.start ? [{ id: 'start', xy: c.start, kind: 'start', label: '起点' }] : [];
  }
  return scene;
}

export const routeColor = (i: number) => `var(--route-${(((i % 6) + 6) % 6) + 1})`;

/** Run records carry the solver's fixed vehicle colours; the screen shows them through the theme's validated palette. */
const SOLVER_COLORS = ['#007aff', '#ff6b00', '#34a853', '#a142f4', '#e8336d', '#00a6a6', '#c49000', '#5856d6', '#8e5a2b', '#2f7d32'];
export const shownColor = (color?: string | null) => {
  const i = SOLVER_COLORS.indexOf((color ?? '').toLowerCase());
  return i < 0 ? (color ?? routeColor(0)) : routeColor(i);
};

/** Polyline for a route; falls back to node coordinates for turn routing when the polyline is missing. */
export function routePoints(cls: ProblemClass, inst: Instance, route: Solution['routes'][number]): XY[] {
  if (route.polyline?.length) return route.polyline;
  if (cls === 'turn_routing' && route.nodes) {
    const coords = asTurn(inst).coordinates ?? {};
    return route.nodes.map((n) => coords[String(n)]).filter((p): p is XY => !!p);
  }
  if (cls === 'euclidean_cvrp' && route.nodes) {
    const coords = asCvrp(inst).coordinates;
    return route.nodes.map((n) => coords[n]).filter((p): p is XY => !!p);
  }
  return [];
}

/** Service-order badges (1, 2, …) for a route, resolved against the instance the run was solved on. */
export function serviceOrder(cls: ProblemClass, inst: Instance, route: Solution['routes'][number]): { xy: XY; text: string }[] {
  let tasks: number[];
  if (route.services?.length) tasks = [...route.services].sort((a, b) => a.position - b.position).map((s) => s.task);
  else if (cls === 'euclidean_cvrp' && route.nodes?.length) tasks = route.nodes.filter((n) => n !== 0);
  else tasks = route.sequence ?? [];
  const out: { xy: XY; text: string }[] = [];
  tasks.forEach((t, i) => {
    const xy = taskXY(cls, inst, t);
    if (xy) out.push({ xy, text: String(i + 1) });
  });
  return out;
}

export interface OverlayEntry {
  solution: Solution;
  colorIndex: number;
  /** Solved on the instance currently shown (order badges are only meaningful then). */
  current: boolean;
  focused: boolean;
}

/** Route paths for visible results: per-route colours for a single result, per-result colours when comparing. */
export function solutionPaths(entries: readonly OverlayEntry[], cls: ProblemClass, inst: Instance, swath: number | null): CanvasPath[] {
  const compare = entries.length > 1 && cls !== 'field_operation';
  const out: CanvasPath[] = [];
  for (const e of entries) {
    if (cls === 'field_operation' && !e.focused) continue;   // rows of several plans overlap into noise; the legend switches plans
    e.solution.routes.forEach((r, ri) => {
      const segments = (r as unknown as { segments?: { kind: string; polyline: XY[]; turn?: string; length_m: number }[] }).segments;
      if (cls === 'field_operation' && segments && (e.focused || !compare)) {
        // Work is what the field pays for; turns and transfers are what planning can reduce, so they carry the colour.
        segments.forEach((seg, si) => {
          if (seg.polyline.length < 2) return;
          out.push({
            id: `${e.solution.run_id}:${ri}:${si}`, points: seg.polyline,
            color: seg.kind === 'work' ? routeColor(ri) : seg.kind === 'turn' ? '#b5622f' : '#7f7b6e',
            width: seg.kind === 'work' ? 2.2 : 1.8, opacity: e.current ? 1 : .45, dash: seg.kind === 'transfer' ? [6, 4] : undefined,
            chevrons: seg.kind === 'work' && segments.length < 20, title: seg.kind === 'work' ? `作业 ${seg.length_m.toFixed(0)} m` : seg.kind === 'turn' ? `${seg.turn} 转弯 ${seg.length_m.toFixed(1)} m` : `转场 ${seg.length_m.toFixed(1)} m`,
          });
        });
        return;
      }
      const points = routePoints(cls, inst, r);
      if (points.length < 2) return;
      out.push({
        id: `${e.solution.run_id}:${ri}`,
        points,
        color: routeColor(compare ? e.colorIndex : ri),
        width: e.focused || !compare ? 3 : 2.4,
        opacity: e.current ? (compare && !e.focused ? 0.8 : 1) : 0.45,
        dash: e.current ? undefined : [7, 5],
        chevrons: true,
        swath: cls === 'coverage' ? swath : null,
        order: e.current && (e.focused || !compare) && cls !== 'coverage' && cls !== 'field_operation' ? serviceOrder(cls, inst, r) : undefined,
        title: `${e.solution.algorithm.name} · 路线 ${ri + 1}`,
      });
    });
  }
  return out;
}

export function violationMarkers(solutions: readonly Solution[]): CanvasMarker[] {
  const out: CanvasMarker[] = [];
  for (const s of solutions) {
    const mc = s.map_check;
    if (!mc?.violations?.length) continue;
    mc.violations.forEach((v, i) =>
      out.push({
        id: `${s.run_id}:v${i}`,
        xy: v.point,
        kind: 'violation',
        title: `${s.algorithm.name}：路线 ${v.route + 1} 第 ${v.segment + 1} 段，净空 ${v.clearance_m.toFixed(2)} m（机器人半径 ${mc.robot_radius_m} m）`,
      }),
    );
  }
  return out;
}

/* ---------------------------------------------------------- algorithms -- */

export type ParamValue = number | string | boolean;

export function paramDefaults(alg: Algorithm): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const p of alg.params) if (p.default !== null && p.default !== undefined) out[p.name] = p.default;
  return out;
}

/** Validation message for a parameter value, or null when valid. */
export function paramError(p: AlgorithmParam, v: ParamValue | undefined): string | null {
  if (v === undefined || v === '') return p.default === null ? null : '必填';
  if (p.type === 'number' || p.type === 'integer') {
    const n = typeof v === 'number' ? v : Number(v);
    if (!Number.isFinite(n)) return '不是数字';
    if (p.type === 'integer' && !Number.isInteger(n)) return '需要整数';
    if (p.min !== undefined && p.min !== null && n < p.min) return `不小于 ${p.min}`;
    if (p.max !== undefined && p.max !== null && n > p.max) return `不大于 ${p.max}`;
  }
  return null;
}

export function coerceParams(alg: Algorithm, values: Record<string, ParamValue> | undefined): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = { ...paramDefaults(alg) };
  for (const p of alg.params) {
    const v = values?.[p.name];
    if (v === undefined || v === '') continue;
    out[p.name] = p.type === 'number' || p.type === 'integer' ? Number(v) : p.type === 'boolean' ? Boolean(v) : v;
  }
  return out;
}

/* ------------------------------------------------------------ simulate -- */

/** Why a result cannot be sent to simulation, or null when it can. */
export function simBlockReason(sol: Solution | null, sim: SimSupport | null | undefined): string | null {
  if (!sol) return '结果尚未返回';
  if (sol.frame?.kind !== 'map') return '基准坐标（无量纲）结果不能送入仿真：没有物理地图与单位';
  if (sol.state !== 'done') return '求解尚未完成';
  if (!sol.routes.length) return '没有路线（不可行或无解），无可执行内容';
  if (!sol.check) return '结果未经独立检查器检查，不能仿真';
  if (!sol.check.valid) return '检查器判定结果无效，不能仿真';
  if (sim && !sim.supported) return sim.reason ?? '该场景不支持仿真';
  return null;
}

/** Map-check caveat that only blocks follow_path mode (gateway answers 422 there). */
export function mapCheckCaveat(sol: Solution | null): string | null {
  const mc = sol?.map_check;
  if (!sol || sol.frame?.kind !== 'map') return null;
  if (!mc) return '缺少地图检查结果';
  if (!mc.checked) return '地图检查未执行';
  if (mc.collision_free === false) return `地图检查发现 ${mc.violations.length} 处净空不足：沿规划路径行驶会被拒绝，只能用 Nav2 逐点导航`;
  return null;
}

/** Relation of a result to the current instance. */
export type InstanceRelation = 'current' | 'stale' | 'unknown';

export function relation(
  entryKey: string | null,
  currentKey: string,
  sha: string | null | undefined,
  shaByKey: ReadonlyMap<string, string>,
): InstanceRelation {
  if (entryKey) return entryKey === currentKey ? 'current' : 'stale';
  const known = shaByKey.get(currentKey);
  if (sha && known) return sha === known ? 'current' : 'stale';
  return 'unknown';
}
