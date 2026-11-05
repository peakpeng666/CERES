import { describe, expect, it } from 'vitest';
import type { FleetInstance, FleetRoute } from '../types';
import { ALL_LAYERS, legendEntries, routeDash } from './legend';

const inst: FleetInstance = {
  boundary: [[0, 0], [10, 0], [10, 10], [0, 10]],
  plots: [], obstacles: [{ id: 'O1', kind: 'pond', polygon: [[1, 1], [2, 1], [2, 2]] }], corridors: [],
  depot: [0.5, 0.5], tasks: [{ id: 1, xy: [5, 5], demand: 1, label: 'T1' }],
  vehicles: { count: 1, capacity: 1 }, objective: 'total_distance', robot_radius: 0.3, units: 'm',
};
const route = { vehicle_name: 'V1', color: '#f00', polyline: [[0, 0], [1, 1]] } as unknown as FleetRoute;
const opts = { lanes: false, badTasks: 0, violations: 0, idp: 't', routeIndex: () => 0 };

describe('legend', () => {
  it('lists only what is present and visible', () => {
    const keys = legendEntries(inst, [route], ALL_LAYERS, opts).map((e) => e.key);
    expect(keys).toEqual(['boundary', 'ob-pond', 'route-V1', 'task', 'depot']);
    const off = legendEntries(inst, [route], { ...ALL_LAYERS, obstacles: false, routes: false }, opts).map((e) => e.key);
    expect(off).toEqual(['boundary', 'task', 'depot']);
    expect(legendEntries(inst, [], ALL_LAYERS, { ...opts, badTasks: 1 }).map((e) => e.key)).toContain('taskBad');
  });
  it('gives successive vehicles different dashes', () => {
    expect(new Set([0, 1, 2, 3].map(routeDash)).size).toBe(4);
  });
});
