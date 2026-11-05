import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
vi.mock('../../lab/api', async (load) => ({ ...await load<typeof import('../../lab/api')>(), events: { subscribe: vi.fn() } }));
vi.mock('../store', async (load) => {
  const actual = await load<typeof import('../store')>();
  // Server rendering otherwise reads Zustand's initial snapshot, not the test's
  // loaded run. Render the actual panel against an explicit current snapshot.
  return { ...actual, useStudio: Object.assign((select?: (state: ReturnType<typeof actual.useStudio.getState>) => unknown) =>
    select ? select(actual.useStudio.getState()) : actual.useStudio.getState(), actual.useStudio) };
});
import { useStudio } from '../store';
import { addToComparison, historyRows } from './HistoryRuns';
import { ComparePanel } from './PlanPanels';
import { solved } from '../researchCharts/testFixtures';

const initial = useStudio.getState();
afterEach(() => useStudio.setState(initial, true));

const quarantined = solved({ run_id: 'run_e2e_quarantined_unverified', objective: { value: 1.234, unit: 'euclidean', lower_bound: null },
  process: { source_identity_verified: false }, error: '源码身份不符' });
const legacy = solved({ run_id: 'run_e2e_legacy_noidentity', process: undefined });
const current = solved({ run_id: 'current' });

describe('historical runs entry', () => {
  it('lists status, identity and exclusion for failed, quarantined and identity-unknown runs', () => {
    const rows = historyRows([quarantined, legacy, solved({ run_id: 'to', state: 'timeout' }), current], ['current'], current);
    expect(rows.map((r) => [r.run.run_id, r.identity, r.exclusion, r.inBatch, r.comparableToAnchor])).toEqual([
      ['run_e2e_quarantined_unverified', '已隔离 · 源码身份不符', '源码身份不符 · 已隔离', false, false],
      ['run_e2e_legacy_noidentity', '身份未核验', null, false, false],
      ['to', '身份已核验', '超时', false, true],
      ['current', '身份已核验', null, true, true],
    ]);
  });
  it('adding to comparison keeps rules: quarantined listed as excluded with no value, unknown identity grouped apart', () => {
    useStudio.setState({ scenario: { id: 's', problem_class: 'euclidean_cvrp' } as never, scenarioId: 's', draft: {} as never,
      runIds: ['current'], runs: { current }, focusRun: 'current', algorithms: [] });
    addToComparison([quarantined, legacy, current]);
    const st = useStudio.getState();
    expect(st.runIds).toEqual(['current', 'run_e2e_quarantined_unverified', 'run_e2e_legacy_noidentity']);
    const html = renderToStaticMarkup(createElement(ComparePanel));
    expect(html).toContain('历史运行（本场景');
    expect(html).toContain('data-exclusion="quarantined"');
    expect(html).toContain('身份未核验');
    expect(html).toContain('含身份未核验组');
    expect(html).not.toContain('1.23');
  });
  it('the entry is present even when the current batch is empty', () => {
    useStudio.setState({ scenarioId: 's', scenario: { id: 's', problem_class: 'euclidean_cvrp' } as never, runIds: [], runs: {} });
    expect(renderToStaticMarkup(createElement(ComparePanel))).toContain('历史运行（本场景');
  });
});

describe('history limit notice', () => {
  it('appears exactly when the result count reaches the limit', async () => {
    const { HistoryLimitNotice, HISTORY_LIMIT } = await import('./HistoryRuns');
    expect(HISTORY_LIMIT).toBe(200);
    expect(renderToStaticMarkup(createElement(HistoryLimitNotice, { count: 200 }))).toContain('只显示最近 200 条');
    expect(renderToStaticMarkup(createElement(HistoryLimitNotice, { count: 199 }))).toBe('');
  });
});
