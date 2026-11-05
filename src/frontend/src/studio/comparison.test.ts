import { describe, expect, it } from 'vitest';
import type { Solution } from '../lab/types';
import { comparable, resultState, verifiedPlan } from './comparison';

const run = () => ({
  run_id: 'one', problem_class: 'euclidean_cvrp', instance_sha256: 'complete-hash',
  frame: { kind: 'benchmark', units: 'unitless' }, instance: { objective: 'sum_unrounded_euclidean_distance' },
  state: 'done', status: 'feasible', routes: [{}], check: { valid: true, errors: [] }, map_check: null,
  objective: { value: 10, unit: 'euclidean' }, metrics: {}, params: { time_limit_s: 2 },
}) as unknown as Solution;

describe('scientific plan comparison', () => {
  it('requires identical units, inputs, distance rules, frames and budgets', () => {
    expect(comparable(run(), run())).toBe(true);
    for (const mutation of [
      { objective: { value: 10, unit: 'm' } }, { instance_sha256: 'another-hash' },
      { instance: { objective: 'sum_rounded_euclidean_distance' } },
      { frame: { kind: 'field', units: 'm' } }, { params: { time_limit_s: 3 } },
    ]) expect(comparable(run(), { ...run(), ...mutation } as Solution)).toBe(false);
  });
  it('never treats a failed map check or no-solution result as a verified plan', () => {
    expect(verifiedPlan(run())).toBe(true);
    expect(verifiedPlan({ ...run(), map_check: { checked: true, collision_free: false } as Solution['map_check'] })).toBe(false);
    expect(resultState({ ...run(), status: 'no_solution' }).text).toBe('未找到可行解');
    expect(verifiedPlan({ ...run(), check: null })).toBe(false);
    expect(comparable({ ...run(), instance_sha256: '' }, run())).toBe(false);
  });
  it('retains old full coverage records without ranking or executing their truncated geometry', () => {
    const old = { ...run(), problem_class: 'coverage', algorithm: { id: 'f2c.full' } } as Solution;
    expect(verifiedPlan(old)).toBe(false);
    expect(resultState(old).text).toContain('须重求解');
    expect(verifiedPlan({ ...old, raw_result: { adapter_contract: 'urp.f2c-full/2' } })).toBe(true);
  });
});

describe('comparison grouping with integrity and outcome', () => {
  const verified = (over: Partial<Solution> = {}) => ({ ...run(), process: { source_identity_verified: true }, ...over }) as Solution;
  it('quarantined runs have no comparison key and are never comparable', async () => {
    const { comparisonKey } = await import('./comparison');
    const q = verified({ process: { source_identity_verified: false } });
    expect(comparisonKey(q)).toBeNull();
    expect(comparable(q, q)).toBe(false);
  });
  it('unknown identity is grouped apart from verified identity', () => {
    expect(comparable(verified(), verified())).toBe(true);
    expect(comparable(run(), run())).toBe(true);
    expect(comparable(verified(), run())).toBe(false);
  });
  it('partitions: only verified feasible runs are ranked; the rest are listed with a status', async () => {
    const { partitionRuns } = await import('./comparison');
    const runs = [
      verified({ run_id: 'ok' }),
      verified({ run_id: 'ok2', objective: { value: 9, unit: 'euclidean', lower_bound: null } }),
      verified({ run_id: 'q', process: { source_identity_verified: false } }),
      verified({ run_id: 'fail', state: 'error', error: '缺少依赖 pyvrp' }),
      verified({ run_id: 'to', state: 'timeout' }),
      verified({ run_id: 'cx', state: 'canceled' }),
      verified({ run_id: 'inf', status: 'infeasible', check: null }),
      verified({ run_id: 'chk', check: { valid: false, errors: ['x'] } as Solution['check'] }),
      verified({ run_id: 'run', state: 'running' }),
      run(),
      verified({ run_id: 'other-input', instance_sha256: 'different' }),
      verified({ run_id: 'other-obj', instance: { objective: 'makespan' } as unknown as Solution['instance'] }),
    ];
    const { groups, excluded } = partitionRuns(runs);
    expect(Object.fromEntries(excluded.map((e) => [e.run.run_id, e.reason.code]))).toEqual({
      q: 'quarantined', fail: 'failed', to: 'timeout', cx: 'canceled', inf: 'infeasible', chk: 'unchecked', run: 'pending',
    });
    expect(excluded.find((e) => e.run.run_id === 'fail')!.reason.text).toContain('缺少依赖');
    expect(groups.map((g) => [g.identity, g.runs.map((r) => r.run_id)])).toEqual([
      ['verified', ['ok', 'ok2']], ['unknown', ['one']], ['verified', ['other-input']], ['verified', ['other-obj']],
    ]);
  });
});
