/**
 * 可运行模式（14 个）：契约内可直接提交求解的农业任务模板。
 *
 * 建模纪律：
 * - 点集需求是真实的带需求点集（容量/卸载/趟数），不以轨迹点数代替工作量；
 * - 覆盖模板按作业幅宽计算覆盖面积，区分全田覆盖率与内缩作业区达标率（地头单列）；
 * - 构建器使用既有 FleetInstance 契约；tools/check-agri-contract.mjs
 *   将真实构建产物交给后端 validate_instance 做跨语言回归；
 * - 未关联到当前实例的持久化运行证据时，不预先宣称实验通过。
 */
import type { FleetInstance } from '../../types';
import type { AgriCapabilities, AgriTaskPattern, BuilderParam, BuiltScenario } from '../taxonomy';
import { disc, placeable, rng, round2, validateSeed, type Ring, type XY } from '../geometry';

const num = (v: Record<string, unknown>, k: string) => Number(v[k]);
const str = (v: Record<string, unknown>, k: string) => String(v[k]);

/** 参数表公共片段。 */
export const P = {
  size: (w: number, h: number): BuilderParam[] => [
    { key: 'field_w', label: '田块宽度', unit: 'm', type: 'number', default: w, min: 20, max: 600 },
    { key: 'field_h', label: '田块高度', unit: 'm', type: 'number', default: h, min: 20, max: 600 },
  ],
  rows: (deg: number): BuilderParam[] => [
    { key: 'row_angle', label: '行向角', unit: '°', type: 'number', default: deg, min: 0, max: 179 },
  ],
  tasks: (n: number, demand: number): BuilderParam[] => [
    { key: 'task_count', label: '任务点数', type: 'number', default: n, min: 4, max: 200 },
    { key: 'demand_per_task', label: '单点需求', type: 'number', default: demand, min: 1, max: 500 },
  ],
  fleet: (count: number, capacity: number, speed: number): BuilderParam[] => [
    { key: 'vehicles', label: '车辆数', type: 'number', default: count, min: 1, max: 15 },
    { key: 'capacity', label: '单车容量', type: 'number', default: capacity, min: 1, max: 5000 },
    { key: 'speed', label: '行驶速度', unit: 'm/s', type: 'number', default: speed, min: 0.3, max: 3 },
  ],
  robot: (r: number): BuilderParam[] => [
    { key: 'robot_radius', label: '机器人半径', unit: 'm', type: 'number', default: r, min: 0.3, max: 2 },
  ],
  depot: (at: 'sw' | 'n' | 'e'): BuilderParam[] => [
    {
      key: 'depot_at', label: '车库位置', type: 'select', default: at,
      options: [
        { value: 'sw', label: '西南角' }, { value: 'n', label: '北侧机耕路' }, { value: 'e', label: '东侧机耕路' },
      ],
    },
  ],
  objective: (o: 'makespan' | 'total_distance'): BuilderParam[] => [
    {
      key: 'objective', label: '优化目标', type: 'select', default: o,
      options: [
        { value: 'makespan', label: '均衡完成时间' }, { value: 'total_distance', label: '总里程' },
      ],
    },
  ],
  refill: (on: boolean): BuilderParam[] => [
    { key: 'refill_point', label: '田头补给/卸载点', type: 'boolean', default: on },
  ],
  obstacles: (n: number, r: number): BuilderParam[] => [
    { key: 'obstacle_count', label: '内部障碍数', type: 'number', default: n, min: 0, max: 6 },
    { key: 'obstacle_r', label: '障碍半径', unit: 'm', type: 'number', default: r, min: 0, max: 40 },
  ],
};

interface FleetSpec {
  word: string;
  crop: string;
  distribution: 'rows' | 'uniform';
  travel: 'rows' | 'free';
  plots: { cols: number; rows: number; margin: number } | null;
  unload?: 0 | 1;
  /** 后端按 max(类别半径) 校验任务/车库位置（validate.py 约 316–322 行）。 */
  placeRadius?: number;
}

/** 构建点集需求实例；放置校验镜像后端（半径取 max(基础半径, fleet 类别半径)）。 */
export function buildFleetInstance(
  v: Record<string, unknown>, seed: number, spec: FleetSpec,
): FleetInstance {
  const rand = rng(seed);
  const W = bounded(v, 'field_w', 20, 600);
  const H = bounded(v, 'field_h', 20, 600);
  const boundary: Ring = [[0, 0], [W, 0], [W, H], [0, H]];
  const r = Math.max(bounded(v, 'robot_radius', 0.3, 2), spec.placeRadius ?? 0);
  const demand = bounded(v, 'demand_per_task', 1, 500, true);
  const want = bounded(v, 'task_count', 4, 200, true);
  const capacity = bounded(v, 'capacity', 1, 5000, true);
  if (capacity < demand) throw new Error('单车容量不能小于单点需求；请修改容量或需求，不会自动增容。');
  const count = bounded(v, 'vehicles', 1, 15, true);
  const speed = bounded(v, 'speed', 0.3, 3);
  const angle = v.row_angle === undefined ? 0 : bounded(v, 'row_angle', 0, 179);
  const objective = str(v, 'objective');
  if (objective !== 'makespan' && objective !== 'total_distance') throw new Error('未知优化目标');
  if (!['sw', 'n', 'e'].includes(str(v, 'depot_at'))) throw new Error('未知车库方位');
  const margin = spec.plots?.margin ?? 0;
  if (W <= 2 * margin || H <= 2 * margin) throw new Error(`田块宽高须大于两侧边距 ${2 * margin} m`);
  const plots: FleetInstance['plots'] = [];
  if (spec.plots) {
    const { cols, rows } = spec.plots;
    const cw = (W - 2 * margin) / cols;
    const ch = (H - 2 * margin) / rows;
    for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
      plots.push({ id: `P${plots.length + 1}`, name: `地块${plots.length + 1}`, crop: spec.crop, rows_deg: angle,
        polygon: [[margin + i * cw, margin + j * ch], [margin + (i + 1) * cw, margin + j * ch],
          [margin + (i + 1) * cw, margin + (j + 1) * ch], [margin + i * cw, margin + (j + 1) * ch]] });
    }
  }
  const obstacles: FleetInstance['obstacles'] = [];
  const obstacleCenters: XY[] = [];
  const obN = v.obstacle_count === undefined ? 0 : bounded(v, 'obstacle_count', 0, 6, true);
  const obR = v.obstacle_r === undefined ? 0 : bounded(v, 'obstacle_r', 0, 40);
  if (obN > 0 && obR <= 0) throw new Error('存在障碍时，障碍半径须大于0');
  for (let i = 0; i < obN; i++) {
    let polygon: Ring | null = null;
    for (let t = 0; t < 200; t++) {
      const center: XY = [round2(W * (0.3 + 0.5 * rand())), round2(H * (0.3 + 0.5 * rand()))];
      // Distinct pond obstacles must remain disjoint, with enough passage for the effective footprint.
      if (obstacleCenters.some(c => Math.hypot(c[0] - center[0], c[1] - center[1]) < 2 * obR + 2 * r + 0.5)) continue;
      const ob = disc(center[0], center[1], obR, 14);
      if (ob.every(p => placeable(p, boundary, [], 0.5))) { polygon = ob; obstacleCenters.push(center); break; }
    }
    if (!polygon) throw new Error('未能放置全部互不重叠且留有通道的障碍；请减少障碍、减小半径或增大田块。');
    obstacles.push({ id: `O${i + 1}`, kind: 'pond', polygon });
  }
  const rings = obstacles.map(o => o.polygon);
  const noGo = spec.travel === 'rows' ? [...plots.map(p => p.polygon), ...rings] : rings;
  // The backend owns the actual lane graph. These lines only distribute service points;
  // crop and rows_deg above are the canonical inputs to that graph, not a second rows schema.
  const rowLines: [XY, XY][] = [];
  if (spec.distribution === 'rows') {
    for (const pl of plots.length ? plots.map(p => p.polygon) : [boundary]) {
      const x0 = Math.min(...pl.map(p => p[0])) + 1;
      const x1 = Math.max(...pl.map(p => p[0])) - 1;
      const y0 = Math.min(...pl.map(p => p[1])) + 1;
      const y1 = Math.max(...pl.map(p => p[1])) - 1;
      if (x1 <= x0 || y1 <= y0) throw new Error('地块过窄，无法布置作业点');
      const theta = angle * Math.PI / 180, dx = Math.cos(theta), dy = Math.sin(theta);
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, extent = Math.hypot(x1 - x0, y1 - y0);
      for (let offset = -extent; offset <= extent; offset += 6) {
        const x = cx - dy * offset, y = cy + dx * offset;
        let lo = -Infinity, hi = Infinity;
        for (const [origin, direction, lower, upper] of [[x, dx, x0, x1], [y, dy, y0, y1]]) {
          if (Math.abs(direction) < 1e-10) { if (origin < lower || origin > upper) hi = -Infinity; }
          else { const a = (lower - origin) / direction, b = (upper - origin) / direction;
            lo = Math.max(lo, Math.min(a, b)); hi = Math.min(hi, Math.max(a, b)); }
        }
        if (hi > lo) rowLines.push([[round2(x + lo * dx), round2(y + lo * dy)], [round2(x + hi * dx), round2(y + hi * dy)]]);
      }
    }
    if (!rowLines.length) throw new Error('地块无法布置指定方向的作业点');
  }
  const tasks: FleetInstance['tasks'] = [];
  for (let guard = 0; tasks.length < want && guard < want * 200; guard++) {
    let p: XY;
    if (rowLines.length) {
      const line = rowLines[Math.floor(rand() * rowLines.length)], t = 0.08 + 0.84 * rand();
      p = [round2(line[0][0] + (line[1][0] - line[0][0]) * t), round2(line[0][1] + (line[1][1] - line[0][1]) * t)];
    } else p = [round2(margin + (W - 2 * margin) * rand()), round2(margin + (H - 2 * margin) * rand())];
    if (placeable(p, boundary, rings, r) && !tasks.some(t => Math.hypot(t.xy[0] - p[0], t.xy[1] - p[1]) < 0.01)) {
      tasks.push({ id: tasks.length + 1, label: `${spec.word}${tasks.length + 1}`, xy: p, demand });
    }
  }
  if (tasks.length !== want) throw new Error('可放置任务点不足；请减少任务/障碍或增大田块。');
  const station = (base: XY): XY => {
    if (placeable(base, boundary, noGo, r + 0.5)) return base;
    // Include exact inset corners/edge projections: a radial lattice can miss a narrow feasible corner.
    const inset = r + 0.5;
    const x = Math.max(inset, Math.min(W - inset, base[0]));
    const y = Math.max(inset, Math.min(H - inset, base[1]));
    const anchors: XY[] = [[inset, inset], [W - inset, inset], [inset, H - inset], [W - inset, H - inset],
      [inset, y], [W - inset, y], [x, inset], [x, H - inset]];
    anchors.sort((a, b) => Math.hypot(a[0] - base[0], a[1] - base[1]) - Math.hypot(b[0] - base[0], b[1] - base[1]));
    for (const anchor of anchors) if (placeable(anchor, boundary, noGo, inset)) return anchor;
    for (let rad = 2; rad <= Math.hypot(W, H); rad += 2) for (let k = 0; k < 32; k++) {
      const a = 2 * Math.PI * k / 32;
      const p: XY = [round2(base[0] + rad * Math.cos(a)), round2(base[1] + rad * Math.sin(a))];
      if (placeable(p, boundary, noGo, r + 0.5)) return p;
    }
    throw new Error('没有满足净空的车库/补给点；请增大边距或减小机器人半径。');
  };
  const depot = station(str(v, 'depot_at') === 'n' ? [round2(W / 2), round2(H - margin / 2)]
    : str(v, 'depot_at') === 'e' ? [round2(W - margin / 2), round2(H / 2)]
      : [round2(Math.max(margin / 2, 4)), round2(Math.max(margin / 2, 4))]);
  const unload = (spec.unload ?? 1) === 1 && v.refill_point !== false
    ? [station([round2(Math.min(W - 6, depot[0] + 12)), depot[1]])] : [];
  return { boundary, plots, obstacles, depot, unload_points: unload.length ? unload : undefined, tasks,
    vehicles: { count, capacity, speed_mps: speed, trips: 'multi', travel: spec.travel },
    objective, robot_radius: r, units: 'm', meta: { task_word: spec.word, margin_m: margin, road_m: margin } };
}

/** Validate instead of silently changing the user's experiment. */
function bounded(v: Record<string, unknown>, key: string, min: number, max: number, integer = false): number {
  const value = v[key];
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    throw new Error(`${key} 须为 ${min}–${max} 范围内的${integer ? '整数' : '有限数值'}`);
  }
  return value;
}

function coverageGeometry(v: Record<string, unknown>, pond = false) {
  const W = bounded(v, 'field_w', 20, 600), H = bounded(v, 'field_h', 20, 600);
  const head = bounded(v, 'headland_width', 0, 20), width = bounded(v, 'robot_width', 0.5, 8);
  const swath = bounded(v, 'swath_width', 0.5, 12);
  bounded(v, 'turn_radius', 1, 12);
  if (Math.min(W, H) <= 2 * head) throw new Error('地头宽度过大，内缩作业区为空');
  if (W * H / (swath * swath) > 200000) throw new Error('田块相对作业幅宽过大，请增大幅宽或缩小田块');
  if (pond) { const radius = bounded(v, 'pond_r', 5, 40);
    if (radius + head + width / 2 >= Math.min(W * 0.45, H * 0.5)) throw new Error('池塘及安全间距超出田块；请减小池塘或增大田块'); }
  const inset = Math.max(head / 2, width / 2 + 0.01);
  return { W, H, start: [inset, inset] as XY };
}

function wrapFleet(
  id: string, title: string, summary: string, seed: number,
  inst: FleetInstance, v: Record<string, unknown>, note: string,
  params: BuilderParam[],
): BuiltScenario {
  return {
    id: `agri-${id}-${Date.now().toString(36)}-${seed}`,
    title, summary,
    problem_class: 'fleet_routing',
    frame: { kind: 'field', units: 'm', crs: 'local' },
    cost_unit: inst.objective === 'makespan' ? 's' : 'm',
    provenance: { generator: 'urp.studio.agri', seed, pattern: id, note },
    sim: { supported: false, world: null, spawn: null, reason: '实地田块坐标，尺度与仿真世界不同' },
    instance: inst as unknown as Record<string, unknown>,
    editable: { params, values: { ...v } },
    layers: {
      boundary: inst.boundary,
      plots: inst.plots.map(p => p.polygon),
      obstacles: inst.obstacles.map(o => o.polygon),
      rows: [],
      depot: inst.depot,
      unload_points: inst.unload_points ?? [],
    },
  };
}

function wrapCoverage(
  id: string, title: string, summary: string, seed: number,
  inst: Record<string, unknown>, v: Record<string, unknown>, note: string,
  params: BuilderParam[],
): BuiltScenario {
  validateSeed(seed);
  return {
    id: `agri-${id}-${Date.now().toString(36)}-${seed}`,
    title, summary,
    problem_class: 'coverage',
    frame: { kind: 'field', units: 'm', crs: 'local' },
    cost_unit: 'm',
    provenance: { generator: 'urp.studio.agri', seed, pattern: id, note },
    sim: { supported: false, world: null, spawn: null, reason: 'coverage 问题类不在田间仿真准入范围' },
    instance: inst,
    editable: { params, values: { ...v } },
    layers: {
      boundary: inst.field,
      obstacles: inst.obstacles ?? [],
    },
  };
}

const cap = (
  defined: string, editable: string, solvable: [AgriCapabilities['solvable']['level'], string],
  checkable: string | [AgriCapabilities['checkable']['level'], string], sim: [AgriCapabilities['simulatable']['level'], string],
  experimented: [AgriCapabilities['experimented']['level'], string],
): AgriCapabilities => ({
  defined: { level: 'yes', note: defined },
  editable: { level: 'yes', note: editable },
  solvable: { level: solvable[0], note: solvable[1] },
  checkable: typeof checkable === 'string' ? { level: 'yes', note: checkable } : { level: checkable[0], note: checkable[1] },
  simulatable: { level: sim[0], note: sim[1] },
  experimented: { level: experimented[0], note: experimented[1] },
});

const SIM_DIFF_NOTE = '同构差速、无曲率约束、≤15 台时可申请进入生成的田间 Gazebo 世界（串行预约执行）；其余组合执行入口按契约返回 422。';
const FLEET_CHECK = 'fleet.check 独立复算容量、趟次、作业位置与通行约束（rows 时重生成作物行核对路线不斜穿作物）。';

// No historical run IDs or immutable input/response bundle were delivered with these templates.
// Capability of a solver is not evidence of an experiment for this generated instance.
const UNVERIFIED: [AgriCapabilities['experimented']['level'], string] = ['no', '本版本模板尚无关联的持久化运行证据；请提交真实求解并检查独立验算结果，不将历史文案视为通过。'];
const EXP = {
  orchard_harvest: UNVERIFIED,
  orchard_spray: UNVERIFIED,
  spot_spray: UNVERIFIED,
  field_sampling: UNVERIFIED,
  grain_cart: UNVERIFIED,
  mixed_fleet: UNVERIFIED,
  tea_leaf: UNVERIFIED,
  dubins: UNVERIFIED,
  cov_tillage: UNVERIFIED,
  cov_obstacle: UNVERIFIED,
  greenhouse: UNVERIFIED,
  multi_field: UNVERIFIED,
  pasture: UNVERIFIED,
  bale: UNVERIFIED,
};

export const RUNNABLE_PATTERNS: AgriTaskPattern[] = [
  {
    id: 'orchard-harvest-transport', name: '果园采收集运',
    environments: ['orchard'], operation: 'picking', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'cvrp',
    summary: '采摘点（树位/行间集中点）带载果需求，车辆沿行间通道收集并卸至田头装车点，多趟往返。',
    modeling: '采摘点是真实的带需求点集（点集 + 容量 + 卸载），不是行服务的近似；行间通行用 travel=rows 的真实通道图，地头/机耕路为开阔区。',
    refs: ['seyyedhasani2017', 'urp_fleet144', 'urp_p510'],
    capabilities: cap(
      '任务、容量、卸载点、行间通行均有契约字段。',
      '任务点、车库、容量、需求在既有地图编辑器全部可改。',
      ['yes', 'fleet_routing 原生语义：点集需求 + 容量 + unload_points 卸货 + rows 通行。'],
      FLEET_CHECK,
      ['partial', SIM_DIFF_NOTE], EXP.orchard_harvest,
    ),
    metrics: ['service_completion', 'trips', 'capacity_utilization', 'makespan', 'deadhead_distance', 'refill_overhead'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(180, 120), ...P.rows(0),
        ...P.tasks(36, 200), ...P.fleet(3, 1200, 0.8), ...P.robot(0.75),
        ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(1, 12),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '采摘点', crop: '苹果', distribution: 'rows', travel: 'rows',
          plots: { cols: 1, rows: 1, margin: 8 }, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('orchard-harvest-transport', '果园采收集运',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个采摘点 · ${num(v, 'field_w')}×${num(v, 'field_h')} m 果园 · 行间通行`,
          seed, inst, v, '采摘点沿作物行布置；空车沿通道进园，满载后回车库或田头装车点卸载。',
          RUNNABLE_PATTERNS[0].runnable!.params);
      },
    },
    experimentFactors: [
      { name: '车队规模', range: '1–6 台', basis: 'seyyedhasani2017：多机分工显著缩短完工时间' },
      { name: '容量/需求比', range: '3–10', basis: '决定趟数与补给开销占比' },
      { name: '行向与场形', range: '0–179° × 3 种形状', basis: '通道图随几何变化' },
    ],
  },
  {
    id: 'orchard-spray-points', name: '果园植保（离散喷施点近似）',
    environments: ['orchard'], operation: 'spraying_spot', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'multi_trip_refill',
    summary: '把果树行上的喷药对象离散为带药量需求的点，药箱容量驱动多趟补给。',
    modeling: '近似：连续行喷施退化为点需求代理，不表达全行服务或沉积；文献仅提供选择性施药路线规划背景，连续行服务见提案「行服务 CARP」。',
    refs: ['conesa2016', 'jensen2015', 'urp_p510'],
    capabilities: cap(
      '点需求 + 药箱容量 + 补给点语义完整。',
      '全部参数与几何可编辑。',
      ['partial', '仅对代理点访问求解；“整行连续喷施”被离散为点集，漏喷/重喷与沉积不在验算范围。'],
      ['partial', `${FLEET_CHECK} 仅验算代理模型，不含 approximation 所列缺失约束。`],
      ['partial', SIM_DIFF_NOTE], EXP.orchard_spray,
    ),
    approximation: '行服务 → 点集：每棵树/每个喷施位一个点；不能表达“沿整行必须连续开启喷头”的约束。',
    metrics: ['service_completion', 'trips', 'refill_overhead', 'capacity_utilization', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(180, 120), ...P.rows(0),
        ...P.tasks(48, 25), ...P.fleet(2, 600, 0.8), ...P.robot(0.75),
        ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(1, 12),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '喷施点', crop: '苹果', distribution: 'rows', travel: 'rows',
          plots: { cols: 1, rows: 1, margin: 8 }, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('orchard-spray-points', '果园植保（离散喷施点）',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个喷施点 · 药箱容量驱动多趟`,
          seed, inst, v, '喷施点沿作物行布置；药尽回补给点。',
          RUNNABLE_PATTERNS[1].runnable!.params);
      },
    },
  },
  {
    id: 'field-grain-cart', name: '大田接粮运输（固定装载点）',
    environments: ['open_field'], operation: 'grain_cart', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'pickup_delivery',
    summary: '接粮车在固定装载点接粮并运往粮库卸载；收割机—接粮车动态汇合见提案。',
    modeling: '装载点是真实带需求点，粮库是卸载点；多趟往返由容量驱动。动态汇合（跟机接粮）超出点集语义，另列提案。',
    refs: ['jensen2015', 'urp_fleet144', 'urp_p510'],
    capabilities: cap(
      '装载点需求 + 容量 + 卸载点完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 原生语义。'],
      FLEET_CHECK,
      ['partial', SIM_DIFF_NOTE], EXP.grain_cart,
    ),
    metrics: ['service_completion', 'trips', 'capacity_utilization', 'makespan', 'deadhead_distance'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(240, 160),
        ...P.tasks(24, 300), ...P.fleet(2, 1500, 1.0), ...P.robot(0.8),
        ...P.depot('n'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '装载点', crop: '小麦', distribution: 'uniform', travel: 'free',
          plots: null, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('field-grain-cart', '大田接粮运输',
          `${inst.vehicles.count} 台接粮车 · ${inst.tasks.length} 个装载点`,
          seed, inst, v, '装载点均匀散布；固定装载点语义，收割机位置不随时间变化。',
          RUNNABLE_PATTERNS[2].runnable!.params);
      },
    },
    experimentFactors: [
      { name: '车队规模', range: '1–6 台', basis: '接粮车数量决定收割机等待' },
    ],
  },
  {
    id: 'field-sampling', name: '土壤与作物采样',
    environments: ['open_field', 'paddy'], operation: 'sampling', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'multi_visit',
    summary: '网格/随机布点采样，车辆依次访问采样点；无容量约束的多点访问。',
    modeling: '采样点是无需求点集（多点访问）；水田样本同样点可复用但下陷风险另列提案。',
    refs: ['urp_p510'],
    capabilities: cap(
      '点集完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 无容量即多点访问。'],
      FLEET_CHECK,
      ['partial', SIM_DIFF_NOTE], EXP.field_sampling,
    ),
    metrics: ['service_completion', 'total_distance', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(200, 140),
        ...P.tasks(40, 1), ...P.fleet(2, 400, 0.8), ...P.robot(0.6),
        ...P.depot('sw'), ...P.objective('total_distance'), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '采样点', crop: '土壤', distribution: 'uniform', travel: 'free', plots: null,
        });
        return wrapFleet('field-sampling', '土壤与作物采样',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个采样点`,
          seed, inst, v, '采样点均匀散布；无补给语义。',
          RUNNABLE_PATTERNS[3].runnable!.params);
      },
    },
  },
  {
    id: 'spot-spray-weeding', name: '定点变量除草',
    environments: ['open_field'], operation: 'spraying_spot', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'cvrp',
    summary: '杂草斑块的定点变量喷施：每点带药量需求，药箱容量驱动回站补给。',
    modeling: '点集带需求是真实语义（杂草斑块定位来自视觉识别）；机械除草（刀辊行服务）不在此模式。',
    refs: ['conesa2016', 'urp_p510'],
    capabilities: cap(
      '点需求 + 容量 + 补给完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 原生语义。'],
      FLEET_CHECK,
      ['partial', SIM_DIFF_NOTE], EXP.spot_spray,
    ),
    metrics: ['service_completion', 'refill_overhead', 'total_distance'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(160, 120),
        ...P.tasks(30, 8), ...P.fleet(2, 80, 0.8), ...P.robot(0.6),
        ...P.depot('sw'), ...P.objective('total_distance'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '杂草点', crop: '玉米', distribution: 'uniform', travel: 'free',
          plots: null, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('spot-spray-weeding', '定点变量除草',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个杂草斑块`,
          seed, inst, v, '斑块定位来自识别结果；药量需求驱动补给。',
          RUNNABLE_PATTERNS[4].runnable!.params);
      },
    },
  },
  {
    id: 'greenhouse-harvest-logistics', name: '温室采收物流',
    environments: ['greenhouse'], operation: 'picking', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'cvrp',
    summary: '栽培床间的采收点集货，运至温室集货口；通道窄、对机器人半径敏感。',
    modeling: '点集需求 + 容量 + 卸载；通道宽度通过 robot_radius 与放置校验表达。',
    refs: ['urp_fleet144', 'urp_p510'],
    capabilities: cap(
      '点需求 + 容量 + 卸载完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 原生语义。'],
      FLEET_CHECK,
      ['no', '生成世界是露天农田，温室结构不生成；室内执行未适配。'], EXP.greenhouse,
    ),
    metrics: ['service_completion', 'trips', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(80, 48),
        ...P.tasks(40, 20), ...P.fleet(2, 160, 0.6), ...P.robot(0.4),
        ...P.depot('e'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '采收点', crop: '番茄', distribution: 'rows', travel: 'rows',
          plots: { cols: 1, rows: 1, margin: 5 }, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('greenhouse-harvest-logistics', '温室采收物流',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个采收点 · 窄通道`,
          seed, inst, v, '栽培床行内布点；通道窄，半径校验严格。',
          RUNNABLE_PATTERNS[5].runnable!.params);
      },
    },
  },
  {
    id: 'multi-field-fleet', name: '多田块机群作业分配',
    environments: ['multi_field'], operation: 'transfer', serviceObject: 'multi_region',
    vehicleClasses: ['diff_drive'], planningProblem: 'multi_field_alloc',
    summary: '多台车服务多个田块的任务点：田块间转场走开阔近似（真实路网见缺口说明）。',
    modeling: '多田块用多边形分区表达；田块间通行取欧氏近似，未接真实道路网。',
    refs: ['urp_fleet144', 'urp_p510'],
    capabilities: cap(
      '多区域 + 点集 + 容量齐备；路网缺失显式声明。',
      '全部可编辑。',
      ['yes', 'fleet_routing 原生语义（free 通行）。'],
      ['partial', `${FLEET_CHECK} 仅验算代理模型，不含 approximation 所列缺失约束。`],
      ['partial', SIM_DIFF_NOTE], EXP.multi_field,
    ),
    approximation: '田块间转场为欧氏近似；真实农场路网（机耕道宽度/限高/单行）不在契约内。',
    metrics: ['service_completion', 'makespan', 'deadhead_distance'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(300, 200),
        ...P.tasks(48, 50), ...P.fleet(3, 400, 1.0), ...P.robot(0.8),
        ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '作业点', crop: '多田块', distribution: 'uniform', travel: 'free',
          plots: { cols: 2, rows: 2, margin: 20 }, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('multi-field-fleet', '多田块机群作业分配',
          `${inst.vehicles.count} 台车 · ${inst.plots.length} 个田块 · ${inst.tasks.length} 个作业点`,
          seed, inst, v, '4 田块分区；转场为欧氏近似。',
          RUNNABLE_PATTERNS[6].runnable!.params);
      },
    },
  },
];

export const RUNNABLE_PATTERNS_REST: AgriTaskPattern[] = [
  {
    id: 'pasture-monitoring', name: '牧场与草地巡检',
    environments: ['pasture'], operation: 'monitoring', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'multi_visit',
    summary: '饮水点/围栏门/草场监测位的巡回检查，无容量约束。',
    modeling: '监测位是真实点集；牲畜活动区作为动态障碍另列提案。',
    refs: ['urp_p510'],
    capabilities: cap(
      '点集完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 无容量即多点访问。'],
      FLEET_CHECK,
      ['partial', SIM_DIFF_NOTE], EXP.pasture,
    ),
    metrics: ['service_completion', 'total_distance', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(260, 180),
        ...P.tasks(24, 1), ...P.fleet(2, 1000, 1.0), ...P.robot(0.7),
        ...P.depot('sw'), ...P.objective('total_distance'), ...P.obstacles(2, 15),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '监测位', crop: '草场', distribution: 'uniform', travel: 'free', plots: null,
        });
        return wrapFleet('pasture-monitoring', '牧场与草地巡检',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个监测位`,
          seed, inst, v, '饮水点/围栏门等固定监测位巡回。',
          RUNNABLE_PATTERNS_REST[0].runnable!.params);
      },
    },
  },
  {
    id: 'tea-leaf-transport', name: '茶园鲜叶集运',
    environments: ['tea_garden'], operation: 'picking', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'cvrp',
    summary: '条垄茶园的集叶点收集运输；坡地风险不在本模式（见提案）。',
    modeling: '集叶点是真实带需求点集；条垄按行布置。坡度/侧翻风险无模型，显式列为近似。',
    refs: ['seyyedhasani2017', 'urp_p510'],
    capabilities: cap(
      '点需求 + 容量 + 卸载完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 原生语义。'],
      ['partial', `${FLEET_CHECK} 仅验算代理模型，不含 approximation 所列缺失约束。`],
      ['partial', SIM_DIFF_NOTE], EXP.tea_leaf,
    ),
    approximation: '无坡度近似：茶园多在坡地，本模式平面化；坡地风险约束路径见提案。',
    metrics: ['service_completion', 'trips', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(150, 90),
        ...P.tasks(30, 40), ...P.fleet(2, 300, 0.7), ...P.robot(0.6),
        ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '集叶点', crop: '茶树', distribution: 'rows', travel: 'rows',
          plots: { cols: 1, rows: 1, margin: 7 }, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('tea-leaf-transport', '茶园鲜叶集运',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个集叶点`,
          seed, inst, v, '条垄行内布点；坡度未建模。',
          RUNNABLE_PATTERNS_REST[1].runnable!.params);
      },
    },
  },
  {
    id: 'bale-collection', name: '草捆收集运输',
    environments: ['open_field', 'pasture'], operation: 'bale_collection', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'cvrp',
    summary: '打捆后散布田间的草捆收集：每捆一个点，单车按捆数容量多趟运输。',
    modeling: '草捆是真实带需求点（1 捆 = 1 需求）；容量以捆计。',
    refs: ['urp_fleet144', 'urp_p510'],
    capabilities: cap(
      '点需求 + 容量 + 卸载完整。',
      '全部可编辑。',
      ['yes', 'fleet_routing 原生语义。'],
      FLEET_CHECK,
      ['partial', SIM_DIFF_NOTE], EXP.bale,
    ),
    metrics: ['service_completion', 'trips', 'capacity_utilization', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(220, 150),
        ...P.tasks(36, 1), ...P.fleet(2, 10, 0.9), ...P.robot(0.8),
        ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '草捆', crop: '牧草', distribution: 'uniform', travel: 'free',
          plots: null, unload: v.refill_point === false ? 0 : 1,
        });
        return wrapFleet('bale-collection', '草捆收集运输',
          `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个草捆 · 单车 ${inst.vehicles.capacity} 捆`,
          seed, inst, v, '草捆散布；容量小导致多趟。',
          RUNNABLE_PATTERNS_REST[2].runnable!.params);
      },
    },
  },
  {
    id: 'mixed-fleet-transport', name: '异构机队集运（大小车混编）',
    environments: ['open_field'], operation: 'material_handling', serviceObject: 'point_set',
    vehicleClasses: ['diff_drive'], planningProblem: 'heterogeneous_coop',
    summary: '大车（大容量/大半径）与小车（小容量/灵活）混编集运，vehicles.fleet 多车型映射。',
    modeling: 'fleet 每类有独立容量/半径/速度；后端按 max(类别半径) 校验放置（构建器已对齐）。',
    refs: ['pyvrp_docs', 'seyyedhasani2017', 'urp_p510'],
    capabilities: cap(
      '多车型 + 点需求 + 容量 + 卸载完整。',
      '全部可编辑。',
      ['yes', 'PyVRP 异构车队原生；OR-Tools 按统一车型近似时注明。'],
      FLEET_CHECK,
      ['no', '执行侧只生成同构内置差速机器人；异构方案启动执行返回 422 heterogeneous_fleet_execution。'], EXP.mixed_fleet,
    ),
    metrics: ['service_completion', 'capacity_utilization', 'makespan'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(240, 160),
        ...P.tasks(40, 60), ...P.fleet(4, 500, 0.9).map(p =>
          p.key === 'vehicles' ? { ...p, min: 2, help: '总台数：大车取一半向上取整，小车取一半向下取整。' }
            : p.key === 'capacity' ? { ...p, label: '大车容量', help: '小车容量为大车的1/3，四舍五入且至少为1；仅容量足够的车型可服务对应需求。' }
              : { ...p, label: '大车速度', help: '小车速度按大车的1.2倍派生。' }), ...P.robot(0.8).map(p => ({ ...p, label: '大车半径', help: '小车半径为大车的一半；放置按大车半径保守校验。' })),
        ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true), ...P.obstacles(0, 0),
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '集货点', crop: '马铃薯', distribution: 'uniform', travel: 'free',
          plots: null, unload: v.refill_point === false ? 0 : 1,
          placeRadius: num(v, 'robot_radius'),
        });
        const cap0 = num(v, 'capacity');
        if (num(v, 'vehicles') < 2) throw new Error('异构机队至少需要2台车（大小车型各至少1台）');
        inst.vehicles = {
          fleet: [
            { name: '大车', count: Math.max(1, Math.round(num(v, 'vehicles') / 2)), capacity: cap0, speed_mps: num(v, 'speed'), robot_radius: num(v, 'robot_radius'), payload: 'load' },
            { name: '小车', count: Math.max(1, Math.floor(num(v, 'vehicles') / 2)), capacity: Math.max(1, Math.round(cap0 / 3)), speed_mps: num(v, 'speed') * 1.2, robot_radius: num(v, 'robot_radius') / 2, payload: 'load' },
          ],
          trips: 'multi', travel: 'free',
        };
        return wrapFleet('mixed-fleet-transport', '异构机队集运',
          `大车 ${inst.vehicles.fleet?.[0]?.count ?? 0} 台 + 小车 ${inst.vehicles.fleet?.[1]?.count ?? 0} 台 · ${inst.tasks.length} 个集货点`,
          seed, inst, v, '大小车混编；放置校验按最大类别半径。',
          RUNNABLE_PATTERNS_REST[3].runnable!.params);
      },
    },
  },
  {
    id: 'dubins-headland-transfer', name: '不可倒车车辆转场（Dubins 曲率约束）',
    environments: ['open_field'], operation: 'transfer', serviceObject: 'point_set',
    vehicleClasses: ['ackermann'], planningProblem: 'multi_visit',
    summary: '最小转弯半径 1.5–10 m 的不可倒车车辆访问作业点；min_turn_radius_m 触发 Dubins 实现。',
    modeling: '先以绕障图距离分配任务/访问顺序，再以 Dubins 航向动态规划或 OMPL 实现曲率约束路径；不声称全局联合最优，无法通过验算时返回无解。',
    refs: ['dubins1957', 'urp_p510'],
    capabilities: cap(
      '点集 + 最小转弯半径完整。',
      '全部可编辑。',
      ['yes', 'min_turn_radius_m 为契约字段；访问顺序按绕障图距离优化，再实现 Dubins 曲率路径，最终按实际路径评分。'],
      '独立复核采样路径长度、三点曲率及足迹；不是连续 SE(2) 可行性的形式证明。',
      ['no', '执行侧为可原地转向差速模型；曲率约束类别启动执行返回 422 curvature_constrained_execution。'], EXP.dubins,
    ),
    metrics: ['total_distance', 'turn_count', 'nonworking_distance'],
    runnable: {
      problem_class: 'fleet_routing',
      params: [
        ...P.size(180, 120),
        ...P.tasks(20, 1), ...P.fleet(2, 1000, 1.0), ...P.robot(0.8),
        ...P.depot('sw'), ...P.objective('total_distance'), ...P.obstacles(1, 10),
        { key: 'turn_radius', label: '最小转弯半径', unit: 'm', type: 'number', default: 4.5, min: 1.5, max: 10, help: 'Dubins 曲率约束（0–10 m）。' },
      ],
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: '作业点', crop: '小麦', distribution: 'uniform', travel: 'free', plots: null,
        });
        const turn = bounded(v, 'turn_radius', 1.5, 10);
        inst.vehicles = {
          fleet: [
            {
              name: '阿克曼作业车', count: Math.round(num(v, 'vehicles')),
              capacity: num(v, 'capacity'), speed_mps: num(v, 'speed'),
              robot_radius: num(v, 'robot_radius'), payload: 'load', min_turn_radius_m: turn,
            },
          ],
          trips: 'multi', travel: 'free',
        };
        return wrapFleet('dubins-headland-transfer', '不可倒车车辆转场',
          `${Math.round(num(v, 'vehicles'))} 台车 · 转弯半径 ${turn} m · ${inst.tasks.length} 个作业点`,
          seed, inst, v, 'min_turn_radius_m 触发 Dubins 实现；曲率验算为契约能力。',
          RUNNABLE_PATTERNS_REST[4].runnable!.params);
      },
    },
    experimentFactors: [
      { name: '转弯半径', range: '1.5–10 m', basis: '契约范围；Dubins 长度随半径增长' },
    ],
  },
  {
    id: 'coverage-tillage', name: '大田耕整全覆盖',
    environments: ['open_field'], operation: 'tillage', serviceObject: 'area',
    vehicleClasses: ['tractor_implement'], planningProblem: 'full_coverage',
    summary: '矩形田全田覆盖：地头内缩、按作业幅宽布行、行排序与转弯连接。',
    modeling: 'coverage 原生语义：coverage_fraction 分母为全田可作业面积（扣除障碍）；working_area_fraction 分母为内缩作业区，interior90 政策要求后者≥90%；地头单列。',
    refs: ['f2c2023', 'bochtis2008', 'oksanen2009', 'galceran2013'],
    capabilities: {
      defined: { level: 'yes', note: '幅宽、地头宽、转弯半径、行向齐备。' },
      editable: { level: 'partial', note: 'coverage 编辑器支持既有字段；模板参数经构建表单生成。' },
      solvable: { level: 'yes', note: '契约求解器为 Fields2Cover（地头→布行→行排序→转弯路径）；实际可用性由现有算法目录检测；基线不约束转弯半径。' },
      checkable: { level: 'yes', note: '独立复算全田 coverage_fraction 和内区 working_area_fraction；内区阈值90%，地头面积单列。' },
      simulatable: { level: 'no', note: 'coverage 问题类不在田间仿真准入范围（CONTRACT §5.1）。' },
      experimented: { level: EXP.cov_tillage[0], note: EXP.cov_tillage[1] },
    },
    metrics: ['coverage_ratio', 'overlap_ratio', 'missed_area', 'headland_area', 'turn_count', 'nonworking_distance'],
    runnable: {
      problem_class: 'coverage',
      params: [
        ...P.size(200, 140),
        { key: 'swath_width', label: '作业幅宽', unit: 'm', type: 'number', default: 3, min: 0.5, max: 12, help: '覆盖面积按 幅宽 × 作业段长度 计算，不用轨迹点数代替。' },
        { key: 'headland_width', label: '地头宽度', unit: 'm', type: 'number', default: 6, min: 0, max: 20, help: '内缩出作业区；地头面积单列统计。' },
        { key: 'turn_radius', label: '转弯半径', unit: 'm', type: 'number', default: 4.5, min: 1, max: 12, help: 'Dubins/Reeds–Shepp 转弯模型。' },
        { key: 'robot_width', label: '机具宽度', unit: 'm', type: 'number', default: 2.2, min: 0.5, max: 8, help: '机具包络。' },
        {
          key: 'route_pattern', label: '覆盖模式', type: 'select', default: 'boustrophedon',
          options: [
            { value: 'boustrophedon', label: '往复（boustrophedon）' },
            { value: 'snake', label: '蛇形' }, { value: 'spiral', label: '螺旋' },
          ],
          help: '行连接模式。',
        },
      ],
      build: (v, seed) => {
        const { W, H, start } = coverageGeometry(v);
        if (!['boustrophedon', 'snake', 'spiral'].includes(str(v, 'route_pattern'))) throw new Error('未知覆盖模式');
        const inst = {
          field: [[0, 0], [W, 0], [W, H], [0, H]],
          headland_width: num(v, 'headland_width'),
          swath_width: num(v, 'swath_width'),
          turn_radius: num(v, 'turn_radius'),
          robot_width: num(v, 'robot_width'),
          start,
          route_pattern: str(v, 'route_pattern'),
          angle_deg: null,
        };
        return wrapCoverage('coverage-tillage', '大田耕整全覆盖',
          `${W}×${H} m · 幅宽 ${num(v, 'swath_width')} m · 地头 ${num(v, 'headland_width')} m`,
          seed, inst, v, 'coverage_fraction 为全田占比；working_area_fraction 为内缩作业区占比，interior90 政策要求后者≥90%。',
          RUNNABLE_PATTERNS_REST[5].runnable!.params);
      },
    },
    experimentFactors: [
      { name: '幅宽', range: '1.5–12 m', basis: '犁/耙/播机具幅宽' },
      { name: '地头宽/转弯半径比', range: '0.5–3', basis: 'bochtis2008：转弯类型与地头空间匹配' },
    ],
  },
  {
    id: 'coverage-obstacle-field', name: '含障碍田块覆盖',
    environments: ['open_field'], operation: 'spraying_blanket', serviceObject: 'area',
    vehicleClasses: ['tractor_implement', 'diff_drive'], planningProblem: 'full_coverage',
    summary: '田内有池塘等障碍：障碍作为内孔，分解后分区覆盖并跨区连接。',
    modeling: 'coverage 原生障碍语义（多边形内孔）；几何分解随障碍变化（zhou2014）。',
    refs: ['zhou2014', 'plessen2019', 'f2c2023'],
    capabilities: {
      defined: { level: 'yes', note: '障碍内孔 + 内缩 + 幅宽。' },
      editable: { level: 'partial', note: 'coverage 编辑器支持障碍多边形。' },
      solvable: { level: 'yes', note: '契约求解器为 Fields2Cover 单元分解处理内孔；实际可用性由现有算法目录检测；基线路径可能穿障，必须通过独立验算。' },
      checkable: { level: 'yes', note: 'coverage_fraction 分母为扣障碍的全田面积；working_area_fraction 为内缩作业区，后者须≥90%。' },
      simulatable: { level: 'no', note: 'coverage 不进仿真。' },
      experimented: { level: EXP.cov_obstacle[0], note: EXP.cov_obstacle[1] },
    },
    metrics: ['coverage_ratio', 'overlap_ratio', 'missed_area', 'headland_area', 'nonworking_distance'],
    runnable: {
      problem_class: 'coverage',
      params: [
        ...P.size(220, 150),
        { key: 'swath_width', label: '作业幅宽', unit: 'm', type: 'number', default: 4, min: 0.5, max: 12, help: '' },
        { key: 'headland_width', label: '地头宽度', unit: 'm', type: 'number', default: 7, min: 0, max: 20, help: '' },
        { key: 'turn_radius', label: '转弯半径', unit: 'm', type: 'number', default: 5, min: 1, max: 12, help: '' },
        { key: 'robot_width', label: '机具宽度', unit: 'm', type: 'number', default: 2.5, min: 0.5, max: 8, help: '' },
        { key: 'pond_r', label: '池塘半径', unit: 'm', type: 'number', default: 18, min: 5, max: 40, help: '内部圆形障碍。' },
      ],
      build: (v, seed) => {
        const { W, H, start } = coverageGeometry(v, true);
        const pond = disc(W * 0.55, H * 0.5, num(v, 'pond_r'), 14);
        const inst = {
          field: [[0, 0], [W, 0], [W, H], [0, H]],
          obstacles: [pond],
          headland_width: num(v, 'headland_width'),
          swath_width: num(v, 'swath_width'),
          turn_radius: num(v, 'turn_radius'),
          robot_width: num(v, 'robot_width'),
          start,
          route_pattern: 'boustrophedon',
          angle_deg: null,
        };
        return wrapCoverage('coverage-obstacle-field', '含障碍田块覆盖',
          `${W}×${H} m · 池塘 r=${num(v, 'pond_r')} m · 幅宽 ${num(v, 'swath_width')} m`,
          seed, inst, v, '障碍为覆盖区内孔；覆盖率分母不含障碍区。',
          RUNNABLE_PATTERNS_REST[6].runnable!.params);
      },
    },
    experimentFactors: [
      { name: '障碍尺寸', range: '5–40 m', basis: 'zhou2014：障碍区拆分子区域分别覆盖' },
    ],
  },
];

RUNNABLE_PATTERNS.push(...RUNNABLE_PATTERNS_REST);
