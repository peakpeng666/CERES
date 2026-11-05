import { describe, expect, it } from 'vitest';
import { RUNNABLE_PATTERNS } from './patterns/runnable';
import type { FleetInstance } from '../types';

function build(id: string, patch: Record<string, unknown> = {}) {
  const pattern = RUNNABLE_PATTERNS.find(p => p.id === id)!;
  const values = Object.fromEntries(pattern.runnable!.params.map(p => [p.key, p.default]));
  return pattern.runnable!.build({ ...values, ...patch }, 42);
}

describe('agri backend schema and resource regression', () => {
  it('uses the existing fleet schema without a competing rows field', () => {
    const { instance } = build('orchard-harvest-transport');
    const fleet = instance as unknown as FleetInstance;
    expect(fleet.plots[0].polygon).toHaveLength(4);
    expect(fleet.plots[0].crop).toBe('苹果');
    expect(fleet.tasks[0].id).toBe(1);
    expect(fleet.tasks[0].xy).toHaveLength(2);
    expect(fleet.tasks[0].label).toBe('采摘点1');
    expect(instance).not.toHaveProperty('rows');
  });
  it('changes both canonical row direction and generated task coordinates', () => {
    const a = build('orchard-harvest-transport', { row_angle: 0 }).instance as unknown as FleetInstance;
    const b = build('orchard-harvest-transport', { row_angle: 90 }).instance as unknown as FleetInstance;
    expect(a.plots[0].rows_deg).toBe(0);
    expect(b.plots[0].rows_deg).toBe(90);
    expect(a.tasks).not.toEqual(b.tasks);
  });
  it('rejects insufficient capacity rather than silently increasing it', () => {
    expect(() => build('bale-collection', { capacity: 1, demand_per_task: 100 })).toThrow(/容量/);
  });
  it('never silently creates additional vehicles', () => {
    expect(() => build('mixed-fleet-transport', { vehicles: 1 })).toThrow(/至少/);
    for (const vehicles of [2, 3, 4, 15]) {
      const inst = build('mixed-fleet-transport', { vehicles }).instance as unknown as FleetInstance;
      expect(inst.vehicles.fleet!.reduce((sum, f) => sum + f.count, 0)).toBe(vehicles);
    }
  });
  it('rejects degenerate field interiors and oversized obstacles', () => {
    expect(() => build('multi-field-fleet', { field_w: 40, field_h: 40 })).toThrow(/边距/);
    expect(() => build('coverage-obstacle-field', { field_w: 40, field_h: 40, pond_r: 40 })).toThrow(/池塘/);
    expect(() => build('coverage-tillage', { field_w: 40, field_h: 40, headland_width: 20 })).toThrow(/为空/);
    expect(() => build('coverage-tillage', { field_w: 600, field_h: 600, swath_width: 0.5 })).toThrow(/幅宽/);
  });
  it('rejects nonfinite and fractional integer parameters', () => {
    expect(() => build('bale-collection', { field_w: NaN })).toThrow();
    expect(() => build('bale-collection', { task_count: 4.2 })).toThrow(/整数/);
  });
  it('does not mark an unlinked historical run as verified evidence', () => {
    for (const p of RUNNABLE_PATTERNS) expect(p.capabilities.experimented.level).toBe('no');
  });
});

import { AGRI_PATTERNS } from './patterns';
import { distToPolygon, pointInPolygon } from './geometry';
const templates = AGRI_PATTERNS.filter(p => p.runnable);
const defaults = (p: typeof templates[number]) => Object.fromEntries(p.runnable!.params.map(v => [v.key, v.default]));

describe('adversarial builder inputs', () => {
  it.each(templates.map(p => [p.id, p] as const))('%s rejects seeds which would alias a different experiment', (_, p) => {
    for (const seed of [-1, 0x100000000, NaN, Infinity, 1.5]) {
      expect(() => p.runnable!.build(defaults(p), seed)).toThrow(/种子/);
    }
  });
  it.each(templates.map(p => [p.id, p] as const))('%s keeps deterministic instances at unsigned seed boundaries', (_, p) => {
    for (const seed of [0, 0xffffffff]) {
      const first = p.runnable!.build(defaults(p), seed), second = p.runnable!.build(defaults(p), seed);
      expect(first.instance).toEqual(second.instance);
      expect(first.provenance.seed).toBe(seed);
    }
  });
  it('keeps multiple requested obstacles disjoint rather than silently merging ponds', () => {
    for (const seed of [0, 7, 42, 2026, 0xffffffff]) {
      const p = templates.find(p => p.id === 'pasture-monitoring')!;
      const inst = p.runnable!.build({ ...defaults(p), obstacle_count: 6 }, seed).instance as unknown as FleetInstance;
      expect(inst.obstacles).toHaveLength(6);
      for (let i = 0; i < inst.obstacles.length; i++) for (let j = i + 1; j < inst.obstacles.length; j++) {
        for (const point of inst.obstacles[i].polygon) {
          expect(pointInPolygon(point, inst.obstacles[j].polygon)).toBe(false);
          expect(distToPolygon(point, inst.obstacles[j].polygon)).toBeGreaterThanOrEqual(2 * inst.robot_radius);
        }
      }
    }
  });
  it('fails explicitly when distinct requested obstacles cannot fit', () => {
    expect(() => build('pasture-monitoring', { field_w: 40, field_h: 40, obstacle_count: 6, obstacle_r: 18 })).toThrow(/障碍/);
  });
  it.each(templates.map(p => [p.id, p] as const))('%s never silently changes requested counts/demands at parameter bounds', (_, p) => {
    const base = defaults(p);
    let successful = 0;
    for (const param of p.runnable!.params) {
      if (param.type !== 'number') continue;
      for (const bound of [param.min, param.max]) {
        if (bound === undefined) continue;
        const values = { ...base, [param.key]: bound }, before = structuredClone(values);
        let built;
        try { built = p.runnable!.build(values, 42); }
        catch (e) { expect(e).toBeInstanceOf(Error); expect((e as Error).message.length).toBeGreaterThan(3); continue; }
        successful++;
        expect(values).toEqual(before);
        expect(built.editable.values).toEqual(values);
        if (built.problem_class === 'fleet_routing') {
          const inst = built.instance as unknown as FleetInstance;
          expect(inst.tasks).toHaveLength(Number(values.task_count));
          expect(inst.tasks.every(t => t.demand === values.demand_per_task)).toBe(true);
          expect(inst.vehicles.fleet?.reduce((sum, c) => sum + c.count, 0) ?? inst.vehicles.count).toBe(values.vehicles);
          expect(Math.max(...(inst.vehicles.fleet?.map(c => c.capacity) ?? [inst.vehicles.capacity!]))).toBe(values.capacity);
          expect(Math.max(...(inst.vehicles.fleet?.map(c => c.robot_radius!) ?? [inst.robot_radius]))).toBe(values.robot_radius);
          expect(inst.tasks.every(t => t.xy.every(Number.isFinite))).toBe(true);
        }
      }
    }
    expect(successful).toBeGreaterThan(0);
  });
});

  it('places greenhouse refill at an exact feasible edge projection missed by the radial lattice', () => {
    const inst = build('greenhouse-harvest-logistics', { field_w: 600, field_h: 600, robot_radius: 2 }).instance as unknown as FleetInstance;
    expect(inst.unload_points).toEqual([[597.5, 300]]);
    expect(distToPolygon(inst.depot, inst.plots[0].polygon)).toBeGreaterThanOrEqual(2.5);
  });
  it('preserves mixed-fleet maximum radius and explicitly derives the smaller footprint', () => {
    for (const radius of [0.3, 0.8, 2]) {
      const inst = build('mixed-fleet-transport', { robot_radius: radius }).instance as unknown as FleetInstance;
      expect(inst.vehicles.fleet!.map(c => c.robot_radius)).toEqual([radius, radius / 2]);
    }
  });
