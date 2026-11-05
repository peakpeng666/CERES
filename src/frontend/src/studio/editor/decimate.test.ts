import { describe, expect, it } from 'vitest';
import type { XY } from '../../lab/types';
import type { FleetInstance, FleetRoute } from '../types';
import { planningFingerprint } from '../fleet';
import { decimateForDisplay, simplifyPolyline, simplifyRing } from './decimate';

function deepFreeze<T>(o: T): T {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o);
    for (const v of Object.values(o as object)) deepFreeze(v);
  }
  return o;
}
const circle = (cx: number, cy: number, r: number, n: number): XY[] => Array.from({ length: n }, (_, i) => [cx + r * Math.cos((2 * Math.PI * i) / n), cy + r * Math.sin((2 * Math.PI * i) / n)] as XY);

function bigScene() {
  const boundary = circle(500, 500, 500, 8000);
  const inst: FleetInstance = {
    boundary,
    plots: [{ id: 'A', name: 'A', crop: 'wheat', rows_deg: 15, polygon: circle(300, 300, 100, 6000) }],
    obstacles: Array.from({ length: 20 }, (_, i) => ({ id: `O${i + 1}`, kind: 'pond' as const, polygon: circle(600 + i * 10, 600, 3, 600) })),
    corridors: [],
    depot: [500, 500],
    unload_points: [[510, 510]],
    tasks: Array.from({ length: 300 }, (_, i) => ({ id: i + 1, xy: [400 + (i % 20) * 5, 400 + Math.floor(i / 20) * 5] as XY, demand: 1, label: `T${i + 1}` })),
    vehicles: { count: 3, capacity: 40, speed_mps: 1.2 },
    objective: 'total_distance',
    robot_radius: 0.6,
    units: 'm',
    meta: { margin_m: 1, road_m: 3 },
  };
  const routes = [{ vehicle_name: 'V1', color: '#f00', polyline: Array.from({ length: 20000 }, (_, i) => [i * 0.05, 100 + Math.sin(i / 50)] as XY), sequence: [1, 2], trips: [[1, 2]] }] as unknown as FleetRoute[];
  const lanes = [Array.from({ length: 5000 }, (_, i) => [i * 0.1, 50] as XY)];
  return { inst, routes, lanes };
}

describe('display decimation', () => {
  it('leaves the solver input byte-for-byte unchanged', () => {
    const { inst, routes, lanes } = bigScene();
    const json = JSON.stringify(inst);
    const fp = planningFingerprint(inst);
    const routesJson = JSON.stringify(routes);
    deepFreeze(inst);
    deepFreeze(routes);
    deepFreeze(lanes);
    const shown = decimateForDisplay(inst, routes, lanes, 0.8); // frozen: any mutation would throw
    expect(shown.decimated).toBe(true);
    expect(shown.shownVertices).toBeLessThan(shown.vertices / 4);
    expect(JSON.stringify(inst)).toBe(json);
    expect(planningFingerprint(inst)).toBe(fp);
    expect(JSON.stringify(routes)).toBe(routesJson);
    expect(shown.inst).not.toBe(inst);
    // Non-geometric planning inputs are carried over as the very same values.
    expect(shown.inst.tasks).toBe(inst.tasks);
    expect(shown.inst.vehicles).toBe(inst.vehicles);
    expect(shown.inst.robot_radius).toBe(inst.robot_radius);
    expect(shown.inst.meta).toBe(inst.meta);
  });
  it('keeps selected polygons exact and small scenes untouched', () => {
    const { inst, routes, lanes } = bigScene();
    const shown = decimateForDisplay(inst, routes, lanes, 0.8, new Set(['obstacle:O3']));
    expect(shown.inst.obstacles[2].polygon).toBe(inst.obstacles[2].polygon);
    expect(shown.inst.obstacles[1].polygon.length).toBeLessThan(inst.obstacles[1].polygon.length);
    const small = { ...inst, boundary: circle(0, 0, 10, 40), plots: [], obstacles: [] };
    const r = decimateForDisplay(small, [], undefined, 1);
    expect(r.decimated).toBe(false);
    expect(r.inst).toBe(small);
  });
  it('stays within the pixel tolerance', () => {
    const line = Array.from({ length: 1000 }, (_, i) => [i, Math.sin(i / 30) * 10] as XY);
    const s = simplifyPolyline(line, 0.5);
    expect(s[0]).toBe(line[0]);
    expect(s[s.length - 1]).toBe(line[999]);
    expect(s.length).toBeLessThan(line.length);
    const ring = circle(0, 0, 10, 1000);
    expect(simplifyRing(ring, 0.05).length).toBeGreaterThanOrEqual(3);
    expect(simplifyRing(ring, 0.05).length).toBeLessThan(1000);
  });
});
