import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Scenario, Solution } from '../../lab/types';
vi.mock('../../lab/api', async (load) => ({ ...await load<typeof import('../../lab/api')>(), events: { subscribe: vi.fn() } }));
vi.mock('../store', async (load) => {
  const actual = await load<typeof import('../store')>();
  // Server rendering otherwise reads Zustand's initial snapshot, not the test's
  // loaded run. Render the actual panel against an explicit current snapshot.
  return { ...actual, useStudio: Object.assign((select?: (state: ReturnType<typeof actual.useStudio.getState>) => unknown) =>
    select ? select(actual.useStudio.getState()) : actual.useStudio.getState(), actual.useStudio) };
});
import { useStudio } from '../store';
import { ComparePanel } from './PlanPanels';
import { ExperimentPanel } from '../experiment/ExperimentPanel';

const initial = useStudio.getState();
afterEach(() => useStudio.setState(initial, true));

describe('actual comparison panel across problem families', () => {
  it.each(['point_to_point', 'coverage', 'turn_routing', 'euclidean_cvrp'] as const)('renders %s without assuming a fleet instance', (problem) => {
    const instance = problem === 'point_to_point' ? { start: [0, 0, 0], goal: [1, 1, 0] } : { capacity: 3 };
    const r = { run_id: 'run-one', scenario_id: 'scene', problem_class: problem, instance, instance_sha256: 'hash',
      frame: { kind: 'benchmark', units: 'unitless' }, objective: { value: 2, unit: 'unitless' },
      state: 'done', status: 'feasible', check: { valid: true, errors: [] }, map_check: null,
      routes: [{ polyline: [[0, 0], [1, 1]] }], params: {}, metrics: {}, algorithm: { id: 'test', name: '测试算法' },
    } as unknown as Solution;
    useStudio.setState({ scenario: { id: 'scene', problem_class: problem } as Scenario, draft: instance,
      runIds: [r.run_id], runs: { [r.run_id]: r }, focusRun: r.run_id, algorithms: [] });
    expect(renderToStaticMarkup(createElement(ComparePanel))).toContain('可行 · 已验算');
  });
});

describe('actual experiment panel with legacy and abstract fleet fields', () => {
  it.each([
    ['euclidean_cvrp', 'unlimited_identical_vehicles', '绑定的场景与资产版本'],
    ['turn_routing', { asset: { id: 'old-vehicle' } }, '旧绑定未记录忽略字段'],
  ] as const)('renders %s without assuming fleet is a complete asset binding', (problem, fleet, expected) => {
    const r = { run_id: 'legacy-one', scenario_id: 'scene', problem_class: problem,
      instance: { fleet }, instance_sha256: 'hash', state: 'done', status: 'feasible',
      check: { valid: true, errors: [] }, objective: { value: 2, unit: 'unitless' },
      algorithm: { id: 'test', name: '历史算法' }, metrics: {}, params: {},
    } as unknown as Solution;
    useStudio.setState({ scenario: { id: 'scene', problem_class: problem } as Scenario,
      focusRun: r.run_id, runs: { [r.run_id]: r } });
    expect(renderToStaticMarkup(createElement(ExperimentPanel))).toContain(expected);
  });
});

describe('legacy source-invalidated records in the primary studio', () => {
  it.each([['comparison', ComparePanel], ['experiment', ExperimentPanel]] as const)(
    'suppresses invalidated metrics and traces in the %s panel', (_, Panel) => {
      const r = { run_id: 'invalidated', scenario_id: 'scene', problem_class: 'coverage',
        instance: {}, instance_sha256: 'hash', frame: { kind: 'field', units: 'm' },
        state: 'error', status: null, error: '执行期间来源身份核对失败',
        check: null, map_check: null, objective: { value: null, unit: 'm', lower_bound: 20 },
        routes: [], algorithm: { id: 'test', name: '历史算法' }, params: {},
        process: { returncode: 0, source_identity_verified: false },
        metrics: { coverage_fraction: .934, working_area_fraction: .978, turn_radius_enforced: true },
        trace_contract: { semantic: 'reported_objective', note: 'raw candidate trace' },
        trace: [[.1, 30], [.2, 20]],
      } as unknown as Solution;
      useStudio.setState({ scenario: { id: 'scene', problem_class: 'coverage' } as Scenario,
        draft: {}, runIds: [r.run_id], focusRun: r.run_id, runs: { [r.run_id]: r }, algorithms: [] });
      const markup = renderToStaticMarkup(createElement(Panel));
      expect(markup).not.toContain('93.4%');
      expect(markup).not.toContain('97.8%');
      expect(markup).not.toContain('求解过程中最好解的变化');
      expect(useStudio.getState().runs[r.run_id]).toBe(r);
      expect(r.metrics.coverage_fraction).toBe(.934);
      if (Panel === ComparePanel) {
        const ordinaryFailure = { ...r, process: { ...r.process, source_identity_verified: true } };
        useStudio.setState({ runs: { [r.run_id]: ordinaryFailure } });
        expect(renderToStaticMarkup(createElement(ComparePanel))).toContain('求解过程中最好解的变化');
      }
    },
  );
});

describe('experiment panel integrity states', () => {
  const done = { run_id: 'q-one', scenario_id: 'scene', problem_class: 'coverage', instance: {}, instance_sha256: 'hash',
    frame: { kind: 'field', units: 'm' }, state: 'done', status: 'feasible', check: { valid: true, errors: [] }, map_check: null,
    objective: { value: 123.456, unit: 'm', lower_bound: null }, routes: [{ polyline: [[0, 0], [1, 1]] }],
    algorithm: { id: 'test', name: '测试算法' }, params: {}, metrics: { coverage_fraction: .912, wall_s: 1 } } as unknown as Solution;
  const show = (r: Solution) => {
    useStudio.setState({ scenario: { id: 'scene', problem_class: 'coverage' } as Scenario, draft: {},
      runIds: [r.run_id], focusRun: r.run_id, runs: { [r.run_id]: r }, algorithms: [] });
    return renderToStaticMarkup(createElement(ExperimentPanel));
  };
  it('quarantined: banner with reason, no objective/metrics/check presented as valid, raw kept', () => {
    const r = { ...done, error: '源码哈希与登记不符', process: { source_identity_verified: false } } as Solution;
    const html = show(r);
    expect(html).toContain('已隔离');
    expect(html).toContain('源码哈希与登记不符');
    expect(html).not.toContain('123.46');
    expect(html).not.toContain('91.2%');
    expect(html).not.toContain('全田块覆盖率');
    expect(html).not.toContain('计算时间');
    expect(useStudio.getState().runs[r.run_id]!.objective.value).toBe(123.456);
  });
  it('unknown identity: labelled 身份未核验, values from the record, never marked verified', () => {
    const html = show(done);
    expect(html).toContain('身份未核验');
    expect(html).toContain('123.46');
    expect(html).not.toContain('身份已核验');
  });
});

describe('comparison panel ranking excludes invalid runs but lists them', () => {
  const mk = (id: string, over: Record<string, unknown>) => ({ run_id: id, scenario_id: 'scene', problem_class: 'euclidean_cvrp',
    instance: { capacity: 3 }, instance_sha256: 'hash', frame: { kind: 'benchmark', units: 'unitless' },
    objective: { value: 5, unit: 'unitless', lower_bound: null }, state: 'done', status: 'feasible',
    check: { valid: true, errors: [] }, map_check: null, routes: [{ polyline: [[0, 0], [1, 1]] }], params: {},
    metrics: { cost: 5 }, algorithm: { id: id, name: `算法${id}` }, process: { source_identity_verified: true }, ...over }) as unknown as Solution;
  it('a quarantined lower objective is neither shown nor best; failed runs appear in the separate list', () => {
    const rows = [mk('good-run', {}), mk('quar-run', { objective: { value: 1.234, unit: 'unitless', lower_bound: null }, metrics: { cost: 1.234 },
      process: { source_identity_verified: false }, error: '源码不符' }), mk('fail-run', { state: 'error', error: '依赖缺失', routes: [] })];
    useStudio.setState({ scenario: { id: 'scene', problem_class: 'euclidean_cvrp' } as Scenario, draft: { capacity: 3 },
      runIds: rows.map((r) => r.run_id), runs: Object.fromEntries(rows.map((r) => [r.run_id, r])), focusRun: 'good-run', algorithms: [] });
    const html = renderToStaticMarkup(createElement(ComparePanel));
    expect(html).not.toContain('1.23');
    expect(html).toContain('未参与比较与排名（2）');
    expect(html).toContain('data-exclusion="quarantined"');
    expect(html).toContain('data-exclusion="failed"');
    expect(html).toContain('依赖缺失');
    expect(html).toContain('已隔离 · 不适用');
    expect(html).toContain('选中方案追溯');
    expect(html).toContain('href="#/plan/scene"');
  });
});

describe('comparison panel explains map-check exclusions', () => {
  it('shows the collision note under the batch summary and in the excluded list', () => {
    const r = { run_id: 'run_map_fail1', scenario_id: 'scene', problem_class: 'euclidean_cvrp', instance: { capacity: 8 }, instance_sha256: 'hash',
      frame: { kind: 'map', units: 'm' }, objective: { value: 54.9, unit: 'm' }, state: 'done', status: 'feasible',
      check: { valid: true, errors: [] }, routes: [{ polyline: [[0, 0], [1, 1]] }], params: {}, metrics: {}, algorithm: { id: 'test', name: '测试算法' },
      map_check: { checked: true, collision_free: false, min_clearance_m: 0, robot_radius_m: 0.175, margin_m: 0.05, violation_count: 1,
        violations: [{ route: 0, segment: 0, point: [-7.582459016393442, 3.3599180327868856], clearance_m: 0 }] },
    } as unknown as Solution;
    useStudio.setState({ scenario: { id: 'scene', problem_class: 'euclidean_cvrp' } as Scenario, draft: { capacity: 8 },
      runIds: [r.run_id], runs: { [r.run_id]: r }, focusRun: r.run_id, algorithms: [] });
    const html = renderToStaticMarkup(createElement(ComparePanel));
    expect(html).toContain('本批 1 次运行 · 0 个已验算可行解');
    expect(html).toContain('data-exclusion-summary="map_check"');
    expect(html).toContain('<li data-exclusion="map_collision">');
    expect(html).toContain('路线 0 第 0 段 · (-7.582, 3.360) · 净空 0.000 m');
    expect(html).toContain('data-verify="map_collision"');
    expect(html).toContain('未通过（地图碰撞 1 处）');
    expect(html).not.toMatch(/data-verify="map_collision"><span[^>]*><svg[^]*?<\/svg> 通过/);
  });

  it('shows full run ids so seeded runs sharing a prefix stay distinguishable, and the quarantine banner for the selected run', () => {
    const base = { scenario_id: 'scene', problem_class: 'euclidean_cvrp', instance: { capacity: 8 }, instance_sha256: 'hash',
      frame: { kind: 'map', units: 'm' }, objective: { value: 1, unit: 'm' }, state: 'done', status: 'feasible', map_check: null,
      check: { valid: true, errors: [] }, routes: [{ polyline: [[0, 0], [1, 1]] }], params: {}, metrics: {}, algorithm: { id: 'test', name: '测试算法' } };
    const q = { ...base, run_id: 'run_e2e_quarantined_unverified', state: 'error', status: null, process: { source_identity_verified: false } } as unknown as Solution;
    const l = { ...base, run_id: 'run_e2e_legacy_noidentity' } as unknown as Solution;
    useStudio.setState({ scenario: { id: 'scene', problem_class: 'euclidean_cvrp' } as Scenario, draft: { capacity: 8 },
      runIds: [l.run_id, q.run_id], runs: { [l.run_id]: l, [q.run_id]: q }, focusRun: q.run_id, algorithms: [] });
    const html = renderToStaticMarkup(createElement(ComparePanel));
    expect(html).toContain('run_e2e_quarantined_unverified · ');
    expect(html).toContain('run_e2e_legacy_noidentity · ');
    expect(html).toContain('data-selected-run="run_e2e_quarantined_unverified"');
    expect(html).toContain('已隔离：');
  });
});
