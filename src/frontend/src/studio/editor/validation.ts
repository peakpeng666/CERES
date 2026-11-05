/**
 * Local geometry checks for the field editor. They only produce warnings to show the user; nothing here
 * changes the instance (capacity, vehicle count, map and swath width stay exactly as entered). The server
 * validator remains the authority.
 */
import type { XY } from '../../lab/types';
import type { FleetInstance } from '../types';
import { distToPolygon, pointInPolygon, segmentsIntersect, selfIntersectionPoint } from '../agri/geometry';
import { depotsOf } from '../fleet';
import { OBSTACLE_STYLE, area } from './geometry';
import type { ItemRef } from './selection';

export type IssueCode =
  | 'non_finite' | 'self_intersection' | 'degenerate'
  | 'obstacle_out_of_bounds' | 'obstacle_overlap' | 'task_in_obstacle' | 'clearance';

export type IssueTarget = ItemRef | { type: 'boundary'; id: 'boundary' };

export interface GeometryIssue {
  key: string;
  code: IssueCode;
  severity: 'error' | 'warning';
  message: string;
  /** What to select; null for non-spatial inputs (e.g. vehicle parameters). */
  target: IssueTarget | null;
  /** Where to zoom: [minX, minY, maxX, maxY] in metres; null when the coordinates themselves are unusable. */
  bounds: [number, number, number, number] | null;
  at: XY | null;
  /** Non-spatial inputs: the fleet-form field to focus (label text as rendered, nth occurrence). */
  field?: FormField;
}

/** `field` matches the input's data-field attribute in the fleet form; `label` is for messages. */
export interface FormField { field: string; label: string }

export const ISSUE_LABEL: Record<IssueCode, string> = {
  non_finite: '非有限数值',
  self_intersection: '多边形自相交',
  degenerate: '退化多边形',
  obstacle_out_of_bounds: '障碍越界',
  obstacle_overlap: '障碍重叠',
  task_in_obstacle: '任务点在障碍内',
  clearance: '车体净空不足',
};

/** Polygons smaller than this (m²) count as degenerate. */
export const MIN_AREA_M2 = 0.01;

const finite = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
const finiteXY = (p: XY | undefined) => !!p && p.length === 2 && finite(p[0]) && finite(p[1]);

function bbox(pts: XY[]): [number, number, number, number] {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}
const pointBox = (p: XY, r = 2): [number, number, number, number] => [p[0] - r, p[1] - r, p[0] + r, p[1] + r];
const overlapBox = (a: number[], b: number[]) => a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];

function polygonsOverlap(a: XY[], b: XY[]): boolean {
  if (!overlapBox(bbox(a), bbox(b))) return false;
  if (a.some((p) => pointInPolygon(p, b)) || b.some((p) => pointInPolygon(p, a))) return true;
  for (let i = 0; i < a.length; i++) for (let j = 0; j < b.length; j++) {
    if (segmentsIntersect(a[i], a[(i + 1) % a.length], b[j], b[(j + 1) % b.length])) return true;
  }
  return false;
}

/** Largest vehicle body radius in the fleet (class radius, footprint circumradius, or the instance default). */
export function vehicleBodyRadius(inst: FleetInstance): number {
  let r = finite(inst.robot_radius) ? inst.robot_radius : 0;
  for (const c of inst.vehicles?.fleet ?? []) {
    if (finite(c.robot_radius)) r = Math.max(r, c.robot_radius!);
    for (const p of c.footprint?.points ?? []) if (finiteXY(p)) r = Math.max(r, Math.hypot(p[0], p[1]));
  }
  return r;
}

export function validateGeometry(inst: FleetInstance): GeometryIssue[] {
  const out: GeometryIssue[] = [];
  const push = (i: Omit<GeometryIssue, 'key'>) => out.push({ ...i, key: `${i.code}:${i.target ? `${i.target.type}:${i.target.id}` : 'none'}:${out.length}` });

  const polys: { target: IssueTarget; name: string; poly: XY[] }[] = [
    { target: { type: 'boundary', id: 'boundary' }, name: '场地边界', poly: inst.boundary ?? [] },
    ...(inst.plots ?? []).map((p) => ({ target: { type: 'plot', id: p.id } as IssueTarget, name: `田块 ${p.name ?? p.id}`, poly: p.polygon })),
    ...(inst.obstacles ?? []).map((o) => ({ target: { type: 'obstacle', id: o.id } as IssueTarget, name: `${OBSTACLE_STYLE[o.kind]?.label ?? '障碍'} ${o.id}`, poly: o.polygon })),
    ...(inst.corridors ?? []).map((c) => ({ target: { type: 'corridor', id: c.id } as IssueTarget, name: `机耕道 ${c.name ?? c.id}`, poly: c.polygon })),
  ];
  const usable = new Set<string>();
  for (const { target, name, poly } of polys) {
    const good = (poly ?? []).filter(finiteXY);
    if (!poly || good.length !== poly.length) {
      push({ code: 'non_finite', severity: 'error', message: `${name} 含非有限坐标（NaN/∞）`, target, bounds: good.length ? bbox(good) : null, at: good[0] ?? null });
      continue;
    }
    const b = bbox(poly);
    const x = selfIntersectionPoint(poly);
    if (x) {
      push({ code: 'self_intersection', severity: 'error', message: `${name} 边自相交（第 ${x.i + 1} 与第 ${x.j + 1} 条边）`, target, bounds: b, at: x.at });
      continue;
    }
    if (poly.length < 3 || area(poly) < MIN_AREA_M2) {
      push({ code: 'degenerate', severity: 'error', message: `${name} 面积约为 0（${poly.length} 个顶点，${area(poly).toFixed(3)} m²）`, target, bounds: poly.length ? b : null, at: poly[0] ?? null });
      continue;
    }
    usable.add(`${target.type}:${target.id}`);
  }

  const boundaryOk = usable.has('boundary:boundary');
  const obstacles = (inst.obstacles ?? []).filter((o) => usable.has(`obstacle:${o.id}`));
  if (boundaryOk) for (const o of obstacles) {
    const outside = o.polygon.filter((p) => !pointInPolygon(p, inst.boundary)).length;
    if (outside) push({
      code: 'obstacle_out_of_bounds', severity: 'warning',
      message: `障碍 ${o.id} ${outside === o.polygon.length ? '完全' : '部分'}位于场地边界外`,
      target: { type: 'obstacle', id: o.id }, bounds: bbox(o.polygon), at: o.polygon.find((p) => !pointInPolygon(p, inst.boundary)) ?? null,
    });
  }
  for (let i = 0; i < obstacles.length; i++) for (let j = i + 1; j < obstacles.length; j++) {
    const a = obstacles[i], b = obstacles[j];
    if (polygonsOverlap(a.polygon, b.polygon)) {
      const [ax0, ay0, ax1, ay1] = bbox(a.polygon);
      const [bx0, by0, bx1, by1] = bbox(b.polygon);
      push({ code: 'obstacle_overlap', severity: 'warning', message: `障碍 ${a.id} 与 ${b.id} 重叠`, target: { type: 'obstacle', id: a.id }, bounds: [Math.min(ax0, bx0), Math.min(ay0, by0), Math.max(ax1, bx1), Math.max(ay1, by1)], at: null });
    }
  }

  const R = vehicleBodyRadius(inst);
  const points: { target: ItemRef; name: string; xy: XY; task: boolean }[] = [
    ...(inst.tasks ?? []).map((t) => ({ target: { type: 'task', id: String(t.id) } as ItemRef, name: `任务点 ${t.label ?? t.id}`, xy: t.xy, task: true })),
    ...depotsOf(inst).map((xy, i) => ({ target: { type: 'depot', id: String(i) } as ItemRef, name: `车库 D${i + 1}`, xy, task: false })),
    ...(inst.unload_points ?? []).map((xy, i) => ({ target: { type: 'unload', id: String(i) } as ItemRef, name: `补给点 U${i + 1}`, xy, task: false })),
  ];
  for (const { target, name, xy, task } of points) {
    if (!finiteXY(xy)) {
      push({ code: 'non_finite', severity: 'error', message: `${name} 坐标非有限数值`, target, bounds: null, at: null });
      continue;
    }
    const inside = obstacles.find((o) => pointInPolygon(xy, o.polygon));
    if (inside) {
      if (task) push({ code: 'task_in_obstacle', severity: 'error', message: `${name} 位于障碍 ${inside.id} 内`, target, bounds: pointBox(xy), at: xy });
      else push({ code: 'clearance', severity: 'error', message: `${name} 位于障碍 ${inside.id} 内`, target, bounds: pointBox(xy), at: xy });
      continue;
    }
    if (R <= 0) continue;
    const dB = boundaryOk ? distToPolygon(xy, inst.boundary) : Infinity;
    const near = obstacles.map((o) => ({ o, d: distToPolygon(xy, o.polygon) })).filter((x) => x.d < R).sort((a, b) => a.d - b.d)[0];
    if (boundaryOk && pointInPolygon(xy, inst.boundary) && dB < R) {
      push({ code: 'clearance', severity: 'warning', message: `${name} 距边界 ${dB.toFixed(2)} m，小于车体半径 ${R.toFixed(2)} m`, target, bounds: pointBox(xy, R + 1), at: xy });
    } else if (near) {
      push({ code: 'clearance', severity: 'warning', message: `${name} 距障碍 ${near.o.id} ${near.d.toFixed(2)} m，小于车体半径 ${R.toFixed(2)} m`, target, bounds: pointBox(xy, R + 1), at: xy });
    }
  }

  const v = inst.vehicles ?? {};
  const classMode = !!v.fleet?.length;
  type Num = [string, unknown, FormField | null, ItemRef | null];
  const nums: Num[] = [
    ['robot_radius', inst.robot_radius, classMode ? { field: 'fleet.0.robot_radius', label: '规划半径' } : { field: 'robot_radius', label: '车体半径' }, null],
    ['vehicles.count', v.count, classMode ? null : { field: 'vehicles.count', label: '车辆数' }, null],
    ['vehicles.capacity', v.capacity, classMode ? null : { field: 'vehicles.capacity', label: '单车容量' }, null],
    ['vehicles.speed_mps', v.speed_mps, classMode ? null : { field: 'vehicles.speed_mps', label: '车速' }, null],
    ...(v.fleet ?? []).flatMap((c, i) => [
      [`fleet[${i}].count`, c.count, { field: `fleet.${i}.count`, label: '数量' }, null],
      [`fleet[${i}].capacity`, c.capacity, { field: `fleet.${i}.capacity`, label: '容量（抽象单位）' }, null],
      [`fleet[${i}].speed_mps`, c.speed_mps, { field: `fleet.${i}.speed_mps`, label: '车速' }, null],
      [`fleet[${i}].robot_radius`, c.robot_radius, { field: `fleet.${i}.robot_radius`, label: '规划半径' }, null],
    ] as Num[]),
    ...(inst.tasks ?? []).map((t) => [`任务 ${t.label ?? t.id} 需求`, t.demand, null, { type: 'task', id: String(t.id) }] as Num),
  ];
  for (const [name, val, field, target] of nums) {
    if (val !== undefined && val !== null && !finite(val)) {
      const task = target ? inst.tasks.find((t) => String(t.id) === target.id) : undefined;
      const xy = task && finiteXY(task.xy) ? task.xy : null;
      push({ code: 'non_finite', severity: 'error', message: `${name} 不是有限数值（${String(val)}）`, target, bounds: xy ? pointBox(xy) : null, at: xy, ...(field ? { field } : {}) });
    }
  }
  return out;
}
