/**
 * Multi-selection and batch edits for the field editor. Every function is pure and returns a new
 * instance, so the caller can hand it to `onEdit` and the whole batch becomes one undo step.
 */
import type { XY } from '../../lab/types';
import type { FleetInstance, Selection } from '../types';
import { depotsOf, setDepot } from '../fleet';
import { round } from './geometry';

export type ItemType = 'task' | 'obstacle' | 'plot' | 'corridor' | 'depot' | 'unload';
export const ITEM_TYPES: ItemType[] = ['task', 'obstacle', 'plot', 'corridor', 'depot', 'unload'];
export const ITEM_LABEL: Record<ItemType, string> = {
  task: '任务点', obstacle: '障碍', plot: '田块', corridor: '机耕道', depot: '车库', unload: '补给点',
};

/** `id` is the task id / polygon id, or the list index for depots and unload points. */
export interface ItemRef { type: ItemType; id: string }
export const refKey = (r: ItemRef) => `${r.type}:${r.id}`;
export const sameRef = (a: ItemRef, b: ItemRef) => a.type === b.type && a.id === b.id;

export function fromSelection(sel: Selection): ItemRef | null {
  if (!sel || sel.type === 'boundary') return null;
  if (sel.type === 'depot') return { type: 'depot', id: String(sel.index ?? 0) };
  if (sel.type === 'unload') return { type: 'unload', id: String(sel.index) };
  return { type: sel.type, id: String(sel.id) };
}

export function toSelection(r: ItemRef | null | undefined): Selection {
  if (!r) return null;
  if (r.type === 'task') return { type: 'task', id: Number(r.id) };
  if (r.type === 'depot') return { type: 'depot', index: Number(r.id) };
  if (r.type === 'unload') return { type: 'unload', index: Number(r.id) };
  return { type: r.type, id: r.id };
}

function polyList(inst: FleetInstance, type: 'obstacle' | 'plot' | 'corridor') {
  return type === 'obstacle' ? inst.obstacles : type === 'plot' ? inst.plots : inst.corridors ?? [];
}

/** All points of an item (a single point for tasks, depots and unload points); null when it no longer exists. */
export function itemPoints(inst: FleetInstance, r: ItemRef): XY[] | null {
  if (r.type === 'task') {
    const t = inst.tasks.find((x) => String(x.id) === r.id);
    return t ? [t.xy] : null;
  }
  if (r.type === 'depot') {
    const xy = depotsOf(inst)[Number(r.id)];
    return xy ? [xy] : null;
  }
  if (r.type === 'unload') {
    const xy = inst.unload_points?.[Number(r.id)];
    return xy ? [xy] : null;
  }
  return polyList(inst, r.type).find((x) => x.id === r.id)?.polygon ?? null;
}

export function allItems(inst: FleetInstance, types: ItemType[] = ITEM_TYPES): ItemRef[] {
  const out: ItemRef[] = [];
  for (const type of types) {
    if (type === 'task') inst.tasks.forEach((t) => out.push({ type, id: String(t.id) }));
    else if (type === 'depot') depotsOf(inst).forEach((_, i) => out.push({ type, id: String(i) }));
    else if (type === 'unload') (inst.unload_points ?? []).forEach((_, i) => out.push({ type, id: String(i) }));
    else polyList(inst, type).forEach((o) => out.push({ type, id: o.id }));
  }
  return out;
}

/** Items lying entirely inside the axis-aligned box spanned by a and b (world metres). */
export function itemsInBox(inst: FleetInstance, a: XY, b: XY, types: ItemType[] = ITEM_TYPES): ItemRef[] {
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  const inside = (p: XY) => p[0] >= x0 && p[0] <= x1 && p[1] >= y0 && p[1] <= y1;
  return allItems(inst, types).filter((r) => {
    const pts = itemPoints(inst, r);
    return !!pts && pts.length > 0 && pts.every(inside);
  });
}

export interface Box { x: number; y: number; w: number; h: number }

export function boxOf(pts: XY[]): Box {
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}

function mapPoly(inst: FleetInstance, type: 'obstacle' | 'plot' | 'corridor', id: string, f: (poly: XY[]) => XY[]): FleetInstance {
  if (type === 'obstacle') return { ...inst, obstacles: inst.obstacles.map((o) => (o.id === id ? { ...o, polygon: f(o.polygon) } : o)) };
  if (type === 'plot') return { ...inst, plots: inst.plots.map((o) => (o.id === id ? { ...o, polygon: f(o.polygon) } : o)) };
  return { ...inst, corridors: (inst.corridors ?? []).map((o) => (o.id === id ? { ...o, polygon: f(o.polygon) } : o)) };
}

/** Move a point item to an exact position. */
export function setItemPoint(inst: FleetInstance, r: ItemRef, xy: XY): FleetInstance {
  if (!xy.every(Number.isFinite)) return inst;
  const p: XY = [round(xy[0]), round(xy[1])];
  if (r.type === 'task') return { ...inst, tasks: inst.tasks.map((t) => (String(t.id) === r.id ? { ...t, xy: p } : t)) };
  if (r.type === 'depot') return setDepot(inst, Number(r.id), p);
  if (r.type === 'unload') return { ...inst, unload_points: (inst.unload_points ?? []).map((q, i) => (String(i) === r.id ? p : q)) };
  return inst;
}

/** Fit a polygon item to an exact bounding box (x, y = lower-left corner; w, h > 0 metres), scaling its shape. */
export function setItemBox(inst: FleetInstance, r: ItemRef, box: Box): FleetInstance {
  if (r.type !== 'obstacle' && r.type !== 'plot' && r.type !== 'corridor') return inst;
  if (![box.x, box.y, box.w, box.h].every(Number.isFinite) || box.w <= 0 || box.h <= 0) return inst;
  return mapPoly(inst, r.type, r.id, (poly) => {
    const b = boxOf(poly);
    const sx = b.w > 0 ? box.w / b.w : 1;
    const sy = b.h > 0 ? box.h / b.h : 1;
    return poly.map(([x, y]) => [round(box.x + (x - b.x) * sx), round(box.y + (y - b.y) * sy)] as XY);
  });
}

/** Translate every referenced item by (dx, dy) metres. */
export function translateItems(inst: FleetInstance, refs: ItemRef[], dx: number, dy: number): FleetInstance {
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || (dx === 0 && dy === 0) || !refs.length) return inst;
  let out = inst;
  for (const r of refs) {
    if (r.type === 'obstacle' || r.type === 'plot' || r.type === 'corridor') {
      out = mapPoly(out, r.type, r.id, (poly) => poly.map(([x, y]) => [round(x + dx), round(y + dy)] as XY));
    } else {
      const p = itemPoints(out, r)?.[0];
      if (p) out = setItemPoint(out, r, [p[0] + dx, p[1] + dy]);
    }
  }
  return out;
}

/** Drop references whose items no longer exist (e.g. after undo). */
export function liveRefs(inst: FleetInstance, refs: ItemRef[]): ItemRef[] {
  return refs.filter((r) => itemPoints(inst, r));
}
