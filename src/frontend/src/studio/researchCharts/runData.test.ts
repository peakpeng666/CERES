import { describe, expect, it } from 'vitest';
import { geomGrid, quantile, quantileBand } from './chartData';
import { seedBandData, traceMatches } from './runData';
import { solved } from './testFixtures';

describe('quantile band', () => {
  it('type-7 quantiles; an infinite neighbour makes the quantile infinite', () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([1, 2, 3, 4], 0.25)).toBe(1.75);
    expect(quantile([1, 2, Infinity], 0.75)).toBe(Infinity);
    expect(quantile([], 0.5)).toBeNaN();
  });
  it('median only when more than half have a value; open band edges are null, never 0', () => {
    const band = quantileBand([[[1, 10]], [[1, 20], [3, 12]], [[2, 30]]], [0.5, 1, 2, 3]);
    expect(band[0]).toEqual({ t: 0.5, median: null, lo: null, hi: null });
    expect(band[1]).toEqual({ t: 1, median: 20, lo: 15, hi: null });
    expect(band[2]).toEqual({ t: 2, median: 20, lo: 15, hi: 25 });
    expect(band[3]).toEqual({ t: 3, median: 12, lo: 11, hi: 21 });
  });
  it('geometric grid spans the horizon', () => {
    const g = geomGrid(0.01, 10, 4);
    expect(g[0]).toBeCloseTo(0.01); expect(g[3]).toBeCloseTo(10);
  });
});

describe('seed band from runs', () => {
  it('groups seeds of one algorithm config; excludes quarantined, failed and mismatched traces', () => {
    const runs = [solved({ run_id: 'a' }), solved({ run_id: 'b', params: { seed: 2, time_limit_s: 2 }, trace: [[0.1, 30], [0.5, 10]] }),
      solved({ run_id: 'q', process: { source_identity_verified: false } }), solved({ run_id: 'f', state: 'timeout' }),
      solved({ run_id: 'm', trace: [[0.1, 99]] }), solved({ run_id: 'p', params: { seed: 3, time_limit_s: 2, mode: 'x' } })];
    const d = seedBandData(runs);
    if (!d.ok) throw new Error(d.reason);
    expect(d.excluded).toEqual({ quarantined: 1, notSuccessful: 1, noTrace: 1 });
    expect(d.groups.map((g) => [g.runs, g.seeds])).toEqual([[2, ['1', '2']], [1, ['3']]]);
    expect(traceMatches(solved({ trace_contract: { semantic: 'solver_search_proxy', note: '' } }))).toBe(false);
  });
  it('explicit reason when nothing is usable or units differ', () => {
    expect(seedBandData([solved({ process: { source_identity_verified: false } })])).toMatchObject({ ok: false, reason: expect.stringContaining('已隔离 1') });
    expect(seedBandData([solved(), solved({ objective: { value: 10, unit: 'm', lower_bound: null } })])).toMatchObject({ ok: false, reason: expect.stringContaining('单位') });
  });
});

describe('verification consistency', () => {
  it('pairs objective.value with check.computed_cost; quarantined and missing fields are counted, not plotted', async () => {
    const { verificationData } = await import('./runData');
    const d = verificationData([solved({ run_id: 'a' }), solved({ run_id: 'b', objective: { value: 11, unit: 'euclidean', lower_bound: null }, check: { valid: false, errors: ['x'], computed_cost: 10 } }),
      solved({ run_id: 'q', process: { source_identity_verified: false } }), solved({ run_id: 'n', check: { valid: true, errors: [], computed_cost: null } }),
      solved({ run_id: 't', state: 'timeout' }), solved({ run_id: 'u', objective: { value: 3, unit: 'm', lower_bound: null }, check: { valid: true, errors: [], computed_cost: 3 } })]);
    if (!d.ok) throw new Error(d.reason);
    expect(d.points.map((p) => [p.run_id, p.valid, p.relDiff])).toEqual([['a', true, 0], ['b', false, 0.1]]);
    expect(d.counts).toEqual({ total: 6, quarantined: 1, notDone: 1, noCheck: 1, noReported: 0, otherUnit: 1 });
    expect(verificationData([solved({ check: null })])).toMatchObject({ ok: false, reason: expect.stringContaining('check.computed_cost') });
  });
});

describe('status counts', () => {
  it('one category per run, quarantine and unknown identity separate, totals add up', async () => {
    const { statusCounts, statusCategory } = await import('./runData');
    const runs = [solved(), solved({ process: undefined }), solved({ process: { source_identity_verified: false } }),
      solved({ process: { source_identity_verified: false }, state: 'error' }), solved({ status: 'infeasible' }), solved({ check: { valid: false, errors: [], computed_cost: 1 } }),
      solved({ state: 'timeout', process: undefined }), solved({ state: 'canceled' }), solved({ state: 'error', error: '缺少依赖' }), solved({ state: 'running' })];
    expect(runs.map(statusCategory)).toEqual(['success', 'unverified', 'quarantined', 'quarantined', 'infeasible', 'unchecked', 'timeout', 'canceled', 'error', 'pending']);
    const s = statusCounts(runs);
    expect(s.total).toBe(10);
    expect(Object.values(s.counts).reduce((a, b) => a + b, 0)).toBe(10);
    expect(s.counts.quarantined).toBe(2);
    expect(s.identityUnknown).toBe(2);
  });
});
