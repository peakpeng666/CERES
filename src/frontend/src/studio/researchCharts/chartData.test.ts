import { describe, expect, it } from 'vitest';
import { ecdfPoints, gapEcdfData, profileData, runtimeEcdfData } from './chartData';
import type { Analysis } from '../types';
import { analysis } from './testFixtures';

describe('performance profile transform', () => {
  it('maps taus/curves to step series and reports the never-reached share', () => {
    const d = profileData(analysis());
    if (!d.ok) throw new Error(d.reason);
    expect(d.n).toBe(2);
    expect(d.series.map((s) => [s.label, s.reachedAll, s.finalRho])).toEqual([['Alg A', true, 1], ['Alg B', false, 0.5]]);
    expect(d.series[0]!.points).toEqual([[1, 0.5], [1.5, 1], [2, 1]]);
  });
  it('explicit reasons when data is absent; mismatched curves are listed, not zero-filled', () => {
    expect(profileData(null)).toMatchObject({ ok: false });
    expect(profileData(analysis({ empty: true }))).toMatchObject({ ok: false, reason: expect.stringContaining('empty') });
    expect(profileData(analysis({ performance_profile: { taus: [], curves: {} } }))).toMatchObject({ ok: false, reason: expect.stringContaining('taus') });
    const d = profileData(analysis({ performance_profile: { taus: [1, 2], curves: { a: [0.5, 1] } } }));
    expect(d.ok && d.absent).toEqual(['Alg B']);
  });
});

describe('ECDF transforms', () => {
  const base = analysis({
    summary: [{ algorithm: 'a', name: 'Alg A', runs: 4, successes: 3 }, { algorithm: 'b', name: 'Alg B', runs: 2, successes: 0 }] as Analysis['summary'],
    ecdf: { a: [2, 0, 1], b: [] },
    scatter: [
      { algorithm: 'a', time: 0.5, gap: 0, instance: 'i', run_id: 'r1' }, { algorithm: 'a', time: 2, gap: 1, instance: 'i', run_id: 'r2' },
      { algorithm: 'a', time: null, gap: 2, instance: 'i', run_id: 'r3' }, { algorithm: 'a', time: 9, gap: null, instance: 'i', run_id: 'r4' },
      { algorithm: 'b', time: 30, gap: null, instance: 'i', run_id: 'r5' }, { algorithm: 'b', time: 30, gap: null, instance: 'i', run_id: 'q1' },
    ],
  });
  it('ecdfPoints divides by all runs so failures keep the curve below 1', () => {
    expect(ecdfPoints([3, 1, NaN], 4)).toEqual([[1, 0.25], [3, 0.5]]);
    expect(ecdfPoints([1], 0)).toEqual([]);
  });
  it('gap ECDF counts failed/timeout runs as not reaching', () => {
    const d = gapEcdfData(base);
    if (!d.ok) throw new Error(d.reason);
    expect(d.series.map((s) => [s.label, s.total, s.reached, s.notReached])).toEqual([['Alg A', 4, 3, 1], ['Alg B', 2, 0, 2]]);
    expect(d.series[0]!.points).toEqual([[0, 0.25], [1, 0.5], [2, 0.75]]);
  });
  it('runtime ECDF: only successful runs with a time reach; quarantined rows are dropped and counted', () => {
    const d = runtimeEcdfData(base, new Set(['q1']));
    if (!d.ok) throw new Error(d.reason);
    expect(d.excludedQuarantined).toBe(1);
    expect(d.series[0]).toMatchObject({ total: 4, reached: 2, notReached: 2, points: [[0.5, 0.25], [2, 0.5]] });
    expect(d.series[0]!.detail).toContain('用时缺失 1');
    expect(d.series[1]).toMatchObject({ total: 1, reached: 0 });
    expect(runtimeEcdfData(analysis({ scatter: [] }))).toMatchObject({ ok: false });
  });
  it('uses the current backend denominator and excludes runs without a positive reference', () => {
    const counts = { terminal_runs: 4, eligible_terminal_runs: 3, finite_gap_runs: 2,
      eligible_failed_runs: 1, eligible_nonfinite_gap_runs: 0, missing_reference_runs: 1,
      failed_runs: 1, failed_missing_reference_runs: 0 };
    const d = gapEcdfData(analysis({ algorithms: ['a'], summary: base.summary, ecdf: { a: [0, 1] },
      ecdf_metadata: { schema: 'urp.gap-ecdf/1', normalization: 'per_algorithm_eligible_terminal_runs',
        weighting: 'one_terminal_run_one_observation', denominator_field: 'eligible_terminal_runs',
        counts: { a: counts }, totals: counts, note: '' } }));
    expect(d.ok && d.series[0]).toMatchObject({ total: 3, reached: 2, notReached: 1, points: [[0, 1 / 3], [1, 2 / 3]] });
  });
});

describe('gap vs time and CD layout', () => {
  it('gap-time keeps only runs with a gap and positive time, counting the rest', async () => {
    const { gapTimeData } = await import('./chartData');
    const a = analysis({ algorithms: ['a@2s'], summary: [{ algorithm: 'a@2s', name: 'A' }] as Analysis['summary'], scatter: [
      { algorithm: 'a@2s', time: 1, gap: 0.5, instance: 'i', run_id: 'r1' }, { algorithm: 'a@2s', time: 0, gap: 1, instance: 'i', run_id: 'r2' },
      { algorithm: 'a@2s', time: 3, gap: null, instance: 'i', run_id: 'r3' }, { algorithm: 'a@2s', time: 1, gap: 0, instance: 'i', run_id: 'q' }] });
    const d = gapTimeData(a, new Set(['q']));
    if (!d.ok) throw new Error(d.reason);
    expect(d.points.map((p) => p.run_id)).toEqual(['r1']);
    expect(d.algorithms[0]).toMatchObject({ total: 3, plotted: 1, failed: 1, noTime: 1 });
    expect(d.budgets).toEqual(['2']);
    expect(d.excludedQuarantined).toBe(1);
  });
  it('cdLayout sorts by mean rank, splits sides and finds maximal cliques within CD', async () => {
    const { cdLayout } = await import('./chartData');
    const { items, cliques } = cdLayout({ c: 3.5, a: 1.2, b: 1.9, d: 3.6 }, 1, { a: 'A' });
    expect(items.map((i) => [i.label, i.side, i.row])).toEqual([['A', 'left', 0], ['b', 'left', 1], ['c', 'right', 1], ['d', 'right', 0]]);
    expect(cliques).toEqual([[0, 1], [2, 3]]);
    expect(cdLayout({ a: 1, b: 1.5, c: 2 }, 1).cliques).toEqual([[0, 2]]);
  });
  it('cdData explains a missing Nemenyi result and lists Holm-significant pairs', async () => {
    const { cdData } = await import('./chartData');
    expect(cdData(analysis({ tests: { alpha: .05, k: 2, n: 1, wilcoxon_holm: [], friedman: { error: '实例不足' } } }))).toMatchObject({ ok: false, reason: expect.stringContaining('实例不足') });
    const d = cdData(analysis({ tests: { alpha: .05, k: 2, n: 5, wilcoxon_holm: [{ a: 'a', b: 'b', n: 5, a_better: 5, b_better: 0, ties: 0, p_holm: 0.01, significant: true }],
      nemenyi: { status: 'ok', q_alpha: 1.96, critical_difference: 0.5, mean_ranks: { a: 1, b: 2 } }, friedman: { status: 'ok', testable: true, statistic: 5, p_value: 0.02, significant: true } } }));
    expect(d.ok && d.holmSignificant[0]).toContain('Alg A vs Alg B');
  });
  it('keeps an unverified historical Nemenyi payload out of CD claims', async () => {
    const { cdData } = await import('./chartData');
    expect(cdData(analysis({ tests: { alpha: .05, k: 2, n: 5, wilcoxon_holm: [],
      nemenyi: { q_alpha: 1.96, critical_difference: .5, mean_ranks: { a: 1, b: 2 } } } }))).toMatchObject({ ok: false });
  });
});
