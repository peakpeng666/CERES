import { describe, expect, it } from 'vitest';
import type { FleetInstance } from '../types';
import { allItems, fromSelection, itemsInBox, setItemBox, setItemPoint, toSelection, translateItems } from './selection';

const inst = (): FleetInstance => ({
  boundary: [[0, 0], [100, 0], [100, 50], [0, 50]],
  plots: [{ id: 'A', name: 'A 区', polygon: [[10, 10], [30, 10], [30, 30], [10, 30]] }],
  obstacles: [{ id: 'O1', kind: 'rock', polygon: [[60, 10], [64, 10], [64, 14], [60, 14]] }],
  corridors: [],
  depot: [2, 2],
  unload_points: [[90, 40]],
  tasks: [{ id: 1, xy: [15, 15], demand: 1, label: 'T1' }, { id: 2, xy: [70, 40], demand: 1, label: 'T2' }],
  vehicles: { count: 2, capacity: 5 },
  objective: 'total_distance',
  robot_radius: 0.5,
  units: 'm',
});

describe('editor selection', () => {
  it('selects by type and by box', () => {
    expect(allItems(inst(), ['task']).map((r) => r.id)).toEqual(['1', '2']);
    const inBox = itemsInBox(inst(), [5, 5], [35, 35]);
    expect(inBox).toEqual([{ type: 'task', id: '1' }, { type: 'plot', id: 'A' }]);
    expect(itemsInBox(inst(), [5, 5], [20, 20])).toEqual([{ type: 'task', id: '1' }]);
  });
  it('round-trips store selections', () => {
    for (const s of [{ type: 'task', id: 2 }, { type: 'depot', index: 0 }, { type: 'unload', index: 0 }, { type: 'obstacle', id: 'O1' }] as const) {
      expect(toSelection(fromSelection(s))).toEqual(s);
    }
  });
  it('batch-translates without mutating the input', () => {
    const a = inst();
    const before = JSON.stringify(a);
    const b = translateItems(a, [{ type: 'task', id: '1' }, { type: 'obstacle', id: 'O1' }, { type: 'depot', id: '0' }], 1.5, -1);
    expect(JSON.stringify(a)).toBe(before);
    expect(b.tasks[0].xy).toEqual([16.5, 14]);
    expect(b.tasks[1].xy).toEqual([70, 40]);
    expect(b.obstacles[0].polygon[0]).toEqual([61.5, 9]);
    expect(b.depot).toEqual([3.5, 1]);
  });
  it('applies precise numeric input and rejects invalid values', () => {
    const a = inst();
    expect(setItemPoint(a, { type: 'task', id: '2' }, [12.345, 7]).tasks[1].xy).toEqual([12.35, 7]);
    expect(setItemPoint(a, { type: 'task', id: '2' }, [NaN, 7])).toBe(a);
    const r = setItemBox(a, { type: 'obstacle', id: 'O1' }, { x: 50, y: 20, w: 8, h: 2 });
    expect(r.obstacles[0].polygon).toEqual([[50, 20], [58, 20], [58, 22], [50, 22]]);
    expect(setItemBox(a, { type: 'obstacle', id: 'O1' }, { x: 0, y: 0, w: 0, h: 2 })).toBe(a);
    expect(setItemBox(a, { type: 'obstacle', id: 'O1' }, { x: Infinity, y: 0, w: 1, h: 2 })).toBe(a);
  });
});
