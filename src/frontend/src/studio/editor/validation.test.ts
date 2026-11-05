import { describe, expect, it } from 'vitest';
import type { FleetInstance } from '../types';
import { selfIntersects } from '../agri/geometry';
import { validateGeometry, vehicleBodyRadius } from './validation';

const base = (): FleetInstance => ({
  boundary: [[0, 0], [100, 0], [100, 60], [0, 60]],
  plots: [{ id: 'A', name: 'A', polygon: [[10, 10], [40, 10], [40, 40], [10, 40]] }],
  obstacles: [{ id: 'O1', kind: 'rock', polygon: [[60, 10], [70, 10], [70, 20], [60, 20]] }],
  corridors: [],
  depot: [5, 5],
  tasks: [{ id: 1, xy: [20, 20], demand: 1, label: 'T1' }],
  vehicles: { count: 2, capacity: 4 },
  objective: 'total_distance',
  robot_radius: 0.5,
  units: 'm',
});
const codes = (i: FleetInstance) => validateGeometry(i).map((x) => x.code);

describe('validateGeometry', () => {
  it('reports nothing for a clean scene', () => {
    expect(validateGeometry(base())).toEqual([]);
  });
  it('detects a self-intersecting (bow-tie) polygon and points at it', () => {
    const i = base();
    i.plots[0].polygon = [[10, 10], [40, 40], [40, 10], [10, 40]];
    const [issue] = validateGeometry(i);
    expect(issue.code).toBe('self_intersection');
    expect(issue.target).toEqual({ type: 'plot', id: 'A' });
    expect(issue.at).toEqual([25, 25]);
    expect(selfIntersects([[0, 0], [1, 0], [1, 1], [0, 1]])).toBe(false);
  });
  it('detects degenerate area', () => {
    const i = base();
    i.obstacles[0].polygon = [[60, 10], [70, 10], [80, 10]];
    expect(codes(i)).toEqual(['degenerate']);
    i.obstacles[0].polygon = [[60, 10], [70, 10]];
    expect(codes(i)).toEqual(['degenerate']);
  });
  it('detects obstacles out of bounds and overlapping', () => {
    const i = base();
    i.obstacles.push({ id: 'O2', kind: 'pond', polygon: [[65, 15], [75, 15], [75, 25], [65, 25]] });
    i.obstacles.push({ id: 'O3', kind: 'tree', polygon: [[95, 50], [110, 50], [110, 55], [95, 55]] });
    const issues = validateGeometry(i);
    expect(issues.map((x) => x.code).sort()).toEqual(['obstacle_out_of_bounds', 'obstacle_overlap']);
    expect(issues.find((x) => x.code === 'obstacle_out_of_bounds')!.target).toEqual({ type: 'obstacle', id: 'O3' });
    expect(issues.find((x) => x.code === 'obstacle_overlap')!.bounds).toEqual([60, 10, 75, 25]);
  });
  it('detects a task inside an obstacle and vehicle clearance', () => {
    const i = base();
    i.tasks.push({ id: 2, xy: [65, 15], demand: 1, label: 'T2' });
    i.tasks.push({ id: 3, xy: [71, 15], demand: 1, label: 'T3' });
    i.vehicles = { fleet: [{ count: 1, capacity: 3, payload: 'load', robot_radius: 1.5 }] };
    expect(vehicleBodyRadius(i)).toBe(1.5);
    const issues = validateGeometry(i);
    expect(issues.map((x) => [x.code, x.target?.id])).toEqual([['task_in_obstacle', '2'], ['clearance', '3']]);
    // Clearance uses the largest body: with the default 0.5 m only, T3 (1 m away) is fine.
    i.vehicles = { count: 1, capacity: 3 };
    expect(validateGeometry(i).map((x) => x.code)).toEqual(['task_in_obstacle']);
  });
  it('detects non-finite numbers without crashing', () => {
    const i = base();
    i.tasks[0].xy = [NaN, 3];
    i.obstacles[0].polygon[1] = [Infinity, 10];
    i.vehicles.capacity = NaN;
    const issues = validateGeometry(i);
    expect(issues.filter((x) => x.code === 'non_finite').length).toBe(3);
    expect(issues.find((x) => x.target?.type === 'obstacle')!.bounds).not.toBeNull();
  });
  it('never modifies the instance (warn only)', () => {
    const i = base();
    i.tasks.push({ id: 2, xy: [65, 15], demand: 1, label: 'T2' });
    i.vehicles.capacity = NaN;
    const before = JSON.stringify(i);
    validateGeometry(i);
    expect(JSON.stringify(i)).toBe(before);
    expect(Number.isNaN(i.vehicles.capacity)).toBe(true);
  });
});

describe('non-finite vehicle parameters point at their form field', () => {
  it('maps flat and per-class parameters to labels; task demand to the task', () => {
    const i = base();
    i.vehicles.speed_mps = Infinity;
    i.tasks[0].demand = NaN;
    const issues = validateGeometry(i);
    expect(issues.find((x) => x.message.startsWith('vehicles.speed_mps'))!.field).toEqual({ field: 'vehicles.speed_mps', label: '车速' });
    expect(issues.find((x) => x.message.includes('需求'))!.target).toEqual({ type: 'task', id: '1' });
    i.vehicles = { fleet: [{ count: 1, capacity: 2, payload: 'load' }, { count: NaN, capacity: 2, payload: 'load', speed_mps: NaN }] };
    i.tasks[0].demand = 1;
    const f = validateGeometry(i).map((x) => x.field);
    expect(f).toEqual([{ field: 'fleet.1.count', label: '数量' }, { field: 'fleet.1.speed_mps', label: '车速' }]);
  });
});
