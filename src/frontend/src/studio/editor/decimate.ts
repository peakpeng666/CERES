/**
 * Display-only decimation for large scenes. Everything here returns new arrays for drawing; the instance
 * passed to the store, the planner and the solver is never touched (see decimate.test.ts).
 */
import type { XY } from '../../lab/types';
import type { FleetInstance, FleetRoute } from '../types';
import { segDist } from '../agri/geometry';

/** Below this many drawn vertices the scene is shown exactly. */
export const DECIMATE_THRESHOLD = 20000;
/** Allowed on-screen deviation in pixels. */
export const DECIMATE_PX = 0.75;

/** Ramer–Douglas–Peucker, iterative; keeps both endpoints. Returns the input itself when nothing is dropped. */
export function simplifyPolyline(pts: XY[], tol: number): XY[] {
  const n = pts.length;
  if (n <= 2 || !(tol > 0)) return pts;
  const keep = new Uint8Array(n);
  keep[0] = keep[n - 1] = 1;
  const stack: [number, number][] = [[0, n - 1]];
  while (stack.length) {
    const [a, b] = stack.pop()!;
    let best = -1;
    let at = -1;
    for (let i = a + 1; i < b; i++) {
      const d = segDist(pts[i], pts[a], pts[b]);
      if (d > best) { best = d; at = i; }
    }
    if (at >= 0 && best > tol) {
      keep[at] = 1;
      stack.push([a, at], [at, b]);
    }
  }
  const out: XY[] = [];
  for (let i = 0; i < n; i++) if (keep[i]) out.push(pts[i]);
  return out.length === n ? pts : out;
}

/** Closed ring: split at the vertex farthest from the first so both halves keep their shape; never below 3 vertices. */
export function simplifyRing(ring: XY[], tol: number): XY[] {
  if (ring.length <= 8) return ring;
  let far = 1;
  let fd = -1;
  for (let i = 1; i < ring.length; i++) {
    const d = Math.hypot(ring[i][0] - ring[0][0], ring[i][1] - ring[0][1]);
    if (d > fd) { fd = d; far = i; }
  }
  const a = simplifyPolyline(ring.slice(0, far + 1), tol);
  const b = simplifyPolyline([...ring.slice(far), ring[0]], tol);
  const out = [...a, ...b.slice(1, -1)];
  return out.length >= 3 && out.length < ring.length ? out : ring;
}

export function sceneVertexCount(inst: FleetInstance, routes: FleetRoute[] = [], lanes: XY[][] = []): number {
  let n = inst.boundary.length;
  for (const p of inst.plots) n += p.polygon.length;
  for (const o of inst.obstacles) n += o.polygon.length;
  for (const c of inst.corridors ?? []) n += c.polygon.length;
  for (const r of routes) n += r.polyline.length;
  for (const l of lanes) n += l.length;
  return n;
}

export interface DisplayScene {
  inst: FleetInstance;
  routes: FleetRoute[];
  lanes: XY[][] | undefined;
  decimated: boolean;
  vertices: number;
  shownVertices: number;
}

/**
 * Simplified copies for drawing at scale `k` px/m. Polygons whose ids are in `keepExact` (the selection, whose
 * vertex handles index into the real polygon) are passed through untouched, as are tasks and depots.
 */
export function decimateForDisplay(inst: FleetInstance, routes: FleetRoute[], lanes: XY[][] | undefined, k: number, keepExact: Set<string> = new Set(), threshold = DECIMATE_THRESHOLD): DisplayScene {
  const vertices = sceneVertexCount(inst, routes, lanes);
  if (vertices <= threshold || !(k > 0)) return { inst, routes, lanes, decimated: false, vertices, shownVertices: vertices };
  const tol = DECIMATE_PX / k;
  const ring = (key: string, poly: XY[]) => (keepExact.has(key) ? poly : simplifyRing(poly, tol));
  const out: FleetInstance = {
    ...inst,
    boundary: ring('boundary:boundary', inst.boundary),
    plots: inst.plots.map((p) => ({ ...p, polygon: ring(`plot:${p.id}`, p.polygon) })),
    obstacles: inst.obstacles.map((o) => ({ ...o, polygon: ring(`obstacle:${o.id}`, o.polygon) })),
    corridors: inst.corridors?.map((c) => ({ ...c, polygon: ring(`corridor:${c.id}`, c.polygon) })),
  };
  const rs = routes.map((r) => ({ ...r, polyline: simplifyPolyline(r.polyline, tol) }));
  const ls = lanes?.map((l) => simplifyPolyline(l, tol));
  return { inst: out, routes: rs, lanes: ls, decimated: true, vertices, shownVertices: sceneVertexCount(out, rs, ls) };
}
