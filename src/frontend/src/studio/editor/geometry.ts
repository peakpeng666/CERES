import type { XY } from '../../lab/types';
import type { FleetInstance, FleetObstacle, FleetRoute, ObstacleKind } from '../types';

// 包含与点线距离与农业构建器共用同一实现，避免两套几何判定出现分歧。
import { pointInPolygon as sharedPointInPolygon, segDist } from '../agri/geometry';

export const pointInPolygon = (p: XY, poly: XY[]): boolean => sharedPointInPolygon(p, poly);
export const distToSegment = (p: XY, a: XY, b: XY): number => segDist(p, a, b);

export function distToEdges(p: XY, poly: XY[]): number {
  let best = Infinity;
  for (let i = 0; i < poly.length; i++) best = Math.min(best, distToSegment(p, poly[i], poly[(i + 1) % poly.length]));
  return best;
}

export const distToPolygon = (p: XY, poly: XY[]) => (pointInPolygon(p, poly) ? 0 : distToEdges(p, poly));

export function centroid(poly: XY[]): XY {
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i];
    const [x1, y1] = poly[(i + 1) % poly.length];
    const f = x0 * y1 - x1 * y0;
    a += f;
    cx += (x0 + x1) * f;
    cy += (y0 + y1) * f;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length || 1;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cy / (3 * a)];
}

export function area(poly: XY[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i];
    const [x1, y1] = poly[(i + 1) % poly.length];
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a) / 2;
}

export const translate = (poly: XY[], dx: number, dy: number): XY[] => poly.map(([x, y]) => [round(x + dx), round(y + dy)]);

export const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

export function rect(a: XY, b: XY): XY[] {
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(([x, y]) => [round(x), round(y)] as XY);
}

export function ellipse(a: XY, b: XY, n = 18): XY[] {
  const cx = (a[0] + b[0]) / 2;
  const cy = (a[1] + b[1]) / 2;
  const rx = Math.max(0.5, Math.abs(b[0] - a[0]) / 2);
  const ry = Math.max(0.5, Math.abs(b[1] - a[1]) / 2);
  return Array.from({ length: n }, (_, i) => {
    const t = (2 * Math.PI * i) / n;
    return [round(cx + rx * Math.cos(t)), round(cy + ry * Math.sin(t))] as XY;
  });
}

export const circle = (c: XY, r: number, n = 10) => ellipse([c[0] - r, c[1] - r], [c[0] + r, c[1] + r], n);

/** Shape used when an obstacle of this kind is drawn by dragging from a to b. */
export function obstacleShape(kind: ObstacleKind, a: XY, b: XY): XY[] {
  if (kind === 'pond') return ellipse(a, b, 20);
  if (kind === 'tree') return circle(a, Math.max(0.8, Math.hypot(b[0] - a[0], b[1] - a[1])), 10);
  if (kind === 'rock') return ellipse(a, b, 7);
  return rect(a, b);
}

export const OBSTACLE_STYLE: Record<ObstacleKind, { label: string; fill: string; stroke: string }> = {
  pond: { label: '池塘', fill: 'var(--map-water)', stroke: 'var(--map-water-edge)' },
  building: { label: '建筑', fill: 'var(--map-solid)', stroke: 'var(--map-solid-edge)' },
  greenhouse: { label: '温室', fill: 'var(--map-water)', stroke: 'var(--map-glass-edge)' },
  tree: { label: '树', fill: 'var(--map-tree)', stroke: 'var(--map-tree-edge)' },
  ditch: { label: '水渠', fill: 'var(--map-soil)', stroke: 'var(--map-soil-edge)' },
  rock: { label: '石块', fill: 'var(--map-solid)', stroke: 'var(--map-soil-edge)' },
  other: { label: '障碍', fill: 'var(--map-solid)', stroke: 'var(--map-solid-edge)' },
};

/** Local, instant feedback while editing (the server validator remains the authority). */
export function taskIssues(inst: FleetInstance): Map<number, string> {
  const out = new Map<number, string>();
  const r = inst.robot_radius;
  for (const t of inst.tasks) {
    if (!pointInPolygon(t.xy, inst.boundary)) out.set(t.id, '在场地边界外');
    else if (distToEdges(t.xy, inst.boundary) < r) out.set(t.id, '离边界太近');
    else {
      const o = inst.obstacles.find((ob) => distToPolygon(t.xy, ob.polygon) < r);
      if (o) out.set(t.id, `离${OBSTACLE_STYLE[o.kind].label}太近或在其内部`);
    }
  }
  return out;
}

export function boundsOf(inst: FleetInstance): [number, number, number, number] {
  const xs = inst.boundary.map((p) => p[0]);
  const ys = inst.boundary.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** Position along a polyline after travelling `d` metres. */
export function along(poly: XY[], d: number): { xy: XY; heading: number; done: boolean } {
  let left = d;
  for (let i = 1; i < poly.length; i++) {
    const seg = Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]);
    const heading = Math.atan2(poly[i][1] - poly[i - 1][1], poly[i][0] - poly[i - 1][0]);
    if (left <= seg) {
      const f = seg ? left / seg : 0;
      return { xy: [poly[i - 1][0] + f * (poly[i][0] - poly[i - 1][0]), poly[i - 1][1] + f * (poly[i][1] - poly[i - 1][1])], heading, done: false };
    }
    left -= seg;
  }
  const n = poly.length;
  return { xy: poly[n - 1], heading: n > 1 ? Math.atan2(poly[n - 1][1] - poly[n - 2][1], poly[n - 1][0] - poly[n - 2][0]) : 0, done: true };
}

export const routeLength = (poly: XY[]) => poly.reduce((s, p, i) => (i ? s + Math.hypot(p[0] - poly[i - 1][0], p[1] - poly[i - 1][1]) : 0), 0);

export function nextObstacleId(obs: FleetObstacle[]): string {
  let k = obs.length + 1;
  while (obs.some((o) => o.id === `O${k}`)) k++;
  return `O${k}`;
}

export function vehicleOfTask(routes: FleetRoute[]): Map<number, { color: string; order: number; vehicle: string; trip: number }> {
  const m = new Map<number, { color: string; order: number; vehicle: string; trip: number }>();
  for (const r of routes) {
    let order = 0;
    (r.trips?.length ? r.trips : [r.sequence]).forEach((trip, k) => {
      for (const t of trip) m.set(t, { color: r.color, order: ++order, vehicle: r.vehicle_name, trip: k + 1 });
    });
  }
  return m;
}
