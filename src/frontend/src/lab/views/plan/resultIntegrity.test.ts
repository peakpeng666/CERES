import { describe, expect, it } from 'vitest';
import type { Solution } from '../../types';
import { coveragePercent, withoutInvalidatedResults } from './resultIntegrity';

function record(overrides: Partial<Solution> = {}): Solution {
  return {
    run_id: 'run_1', job_id: 'job_1', scenario_id: 's', problem_class: 'coverage',
    instance_sha256: 'abc', frame: { kind: 'field', units: 'm' }, cost_unit: 'm',
    algorithm: { id: 'test', name: 'Test', source: 'project', origin: 'test', version: null },
    params: {}, state: 'done', status: 'feasible',
    objective: { value: 7, unit: 'm', lower_bound: 6 },
    routes: [], check: { valid: true, checker: 'test', source: 'project', errors: [], computed_cost: 7 },
    map_check: { checked: true, collision_free: true, min_clearance_m: .5, robot_radius_m: .2, violations: [] },
    metrics: { cost: 7, length: 12, length_unit: 'm', n_routes: 2, n_tasks: 4,
      turns: 3, turn_penalty_total: 2, forbidden_turn_violations: 0,
      wall_s: .25, cpu_s: .2, process_wall_s: .5, coverage_fraction: .93,
      makespan_s: 20, total_distance_m: 15, vehicles_used: 2, future_metric: 100 },
    raw_result: { candidate: 'retain for audit', cost: 7 },
    process: { returncode: 0, source_identity_verified: true },
    log: 'worker diagnostic', error: null, created_utc: '', finished_utc: null, label: null,
    ...overrides,
  };
}

describe('source-invalidated result display', () => {
  it('suppresses legacy residual metrics without mutating raw audit evidence', () => {
    const raw = record({ state: 'error', process: { returncode: 0, source_identity_verified: false } });
    const before = structuredClone(raw);
    const display = withoutInvalidatedResults(raw)!;
    expect(display).not.toBe(raw);
    expect(display.status).toBeNull();
    expect(display.objective).toEqual({ value: null, unit: 'm', lower_bound: null });
    expect(display.check).toBeNull();
    expect(display.map_check).toBeNull();
    for (const key of ['coverage_fraction', 'cost', 'length', 'makespan_s', 'total_distance_m', 'vehicles_used', 'future_metric']) {
      expect(display.metrics[key], key).toBeNull();
    }
    expect(display.metrics.wall_s).toBe(.25);
    expect(display.metrics.cpu_s).toBe(.2);
    expect(display.metrics.process_wall_s).toBe(.5);
    expect(display.metrics.length_unit).toBe('m');
    expect(display.raw_result).toEqual(before.raw_result);
    expect(display.log).toBe(before.log);
    expect(raw).toEqual(before);
    expect(coveragePercent(raw)).toBeNull();
    expect(coveragePercent(display)).toBeNull();
  });

  it('keeps successful runs and ordinary solver-failure diagnostics untouched', () => {
    for (const run of [record(), record({ state: 'error' }), record({ state: 'error', process: undefined })]) {
      expect(withoutInvalidatedResults(run)).toBe(run);
    }
    expect(withoutInvalidatedResults(null)).toBeNull();
    expect(coveragePercent(record())).toBe(93);
    expect(coveragePercent(record({ state: 'error' }))).toBeNull();
  });

  it('never converts missing coverage to a plausible zero percent', () => {
    for (const value of [null, undefined, NaN, Infinity, '0.93']) {
      const run = record();
      if (value === undefined) delete run.metrics.coverage_fraction;
      else run.metrics.coverage_fraction = value;
      expect(coveragePercent(run)).toBeNull();
    }
    const zero = record();
    zero.metrics.coverage_fraction = 0;
    expect(coveragePercent(zero)).toBe(0);
  });
});

describe('three-state source identity', () => {
  const base = { state: 'done', error: undefined, process: undefined } as unknown as Solution;
  it('distinguishes verified, quarantined and missing (unknown, not trusted)', async () => {
    const { identityState, IDENTITY_LABEL } = await import('./resultIntegrity');
    expect(identityState({ ...base, process: { source_identity_verified: true } })).toBe('verified');
    expect(identityState({ ...base, process: { source_identity_verified: false } })).toBe('quarantined');
    expect(identityState(base)).toBe('unknown');
    expect(identityState({ ...base, process: { returncode: 0 } })).toBe('unknown');
    expect(identityState(null)).toBe('unknown');
    expect(IDENTITY_LABEL.unknown).toBe('身份未核验');
  });
  it('explains quarantine from recorded fields only', async () => {
    const { quarantineReason } = await import('./resultIntegrity');
    const q = { ...base, error: '源码哈希不符', process: { source_identity_verified: false, evidence: 'logs/x.json' } } as unknown as Solution;
    expect(quarantineReason(q)).toContain('源码哈希不符');
    expect(quarantineReason(q)).toContain('logs/x.json');
    expect(quarantineReason(q)).toContain('原始结果与诊断保留');
    expect(quarantineReason(base)).toBeNull();
  });
  it('renders a banner for quarantined and a label for unknown done runs', async () => {
    const { createElement } = await import('react');
    const { renderToStaticMarkup } = await import('react-dom/server');
    const { IntegrityNotice } = await import('./IntegrityNotice');
    const html = (run: unknown) => renderToStaticMarkup(createElement(IntegrityNotice, { run: run as Solution }));
    expect(html({ ...base, error: '哈希不符', process: { source_identity_verified: false } })).toContain('已隔离');
    expect(html(base)).toContain('身份未核验');
    expect(html({ ...base, process: { source_identity_verified: true } })).toBe('');
  });
});
