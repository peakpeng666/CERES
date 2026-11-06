import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { EngineGap, PhysicsRun } from '../lab/types';

const mocks = vi.hoisted(() => ({
  resources: {} as Record<string, { data: unknown; error: Error | null; loading: boolean; reload: () => void }>,
}));
vi.mock('../lab/api', () => ({
  lab: {},
  useLabResource: (key: string) => mocks.resources[key],
}));
vi.mock('../studio/store', () => ({
  useStudio: (select: (state: unknown) => unknown) => select({ focusRun: null, runs: {}, notify: vi.fn() }),
}));
vi.mock('../studio/api', () => ({ studio: {} }));
import { GapFacts, PhysicsPanel } from './PhysicsPanel';

const loaded = (data: unknown, error: Error | null = null) => ({ data, error, loading: false, reload: vi.fn() });
const record = (state: string, comparison?: EngineGap): PhysicsRun => ({
  physics_run_id: 'phys_1', run_id: 'run_1', session_id: 'gazebo_1', engine: 'mujoco',
  state, created_utc: '', finished_utc: '', errors: state === 'failed' ? ['worker failed'] : [], comparison,
});
const render = () => renderToStaticMarkup(createElement(PhysicsPanel));

beforeEach(() => {
  mocks.resources['physics-engines'] = loaded({ engines: [{ id: 'mujoco', available: true, version: 'test' }] });
  mocks.resources['physics-runs'] = loaded({ runs: [] });
  mocks.resources['physics-gazebo'] = loaded({ results: [{ session_id: 'gazebo_1', run_id: 'run_1', state: 'succeeded' }] });
});

describe('physics panel failure recovery', () => {
  it('shows each failed read and never calls a failed run read an empty history', () => {
    for (const key of Object.keys(mocks.resources)) mocks.resources[key] = loaded(null, new Error(key + ' failed'));
    const html = render();
    for (const key of Object.keys(mocks.resources)) expect(html).toContain(key + ' failed');
    expect(html).toContain('重新读取物理引擎');
    expect(html).not.toContain('还没有物理运行');
    expect(html).not.toContain('正在读取物理引擎');
  });

  it.each(['failed', 'canceled'])('keeps %s comparisons retryable', (state) => {
    mocks.resources['physics-runs'] = loaded({ runs: [record(state)] });
    const html = render();
    expect(html).toContain('可对比的 Gazebo 记录');
    expect(html).toContain('重试对比');
  });

  it('does not submit a duplicate while the comparison is running', () => {
    mocks.resources['physics-runs'] = loaded({ runs: [record('running')] });
    const html = render();
    expect(html).not.toContain('可对比的 Gazebo 记录');
    expect(html).not.toContain('重试对比');
  });

  it('disables comparison when engine readiness is unavailable or stale after an error', () => {
    mocks.resources['physics-engines'].error = new Error('probe failed');
    expect(render()).toMatch(/<button[^>]*disabled=""[^>]*>对比<\/button>/);
  });

  it('shows unverifiable comparison evidence without rendering a numeric gap', () => {
    const gap = { available: false, errors: ['车辆参数不同'], path_mean_m: 42, metrics: {} } as EngineGap;
    const html = renderToStaticMarkup(createElement(GapFacts, { gap }));
    expect(html).toContain('引擎对比不可用');
    expect(html).toContain('车辆参数不同');
    expect(html).not.toContain('42');
    mocks.resources['physics-runs'] = loaded({ runs: [record('succeeded', gap)] });
    expect(render()).toContain('重试对比');
    expect(render()).not.toContain('最大差 42');
  });

  it('marks legacy comparisons as lacking identity checks and labels both clocks', () => {
    const gap = { metrics: {}, scope: 'recorded scope', time_alignment: 'per_vehicle_departure', mission_clock_rmse_m: 0.2 } as EngineGap;
    const html = renderToStaticMarkup(createElement(GapFacts, { gap }));
    expect(html).toContain('历史对比未核验输入身份');
    expect(html).toContain('各车发车对齐 RMSE');
    expect(html).toContain('任务同钟 RMSE');
  });

  it('attributes each execution metric to an engine and keeps definitions in closed details', () => {
    const gap = { available: true, path_max_m: 0.108, path_mean_m: 0.007, time_alignment: 'per_vehicle_departure',
      run_id: 'run_1', a: { engine: 'gazebo' }, b: { engine: 'mujoco' }, vehicles: [],
      time_rmse_m: 0.028, mission_clock_rmse_m: 0.04, scope: 'A long research model explanation',
      metrics: { total_distance_m: { a: 61.31, b: 61.3 }, makespan_s: { a: 144.6, b: 144.7 }, cross_track_max_m: { a: 0.08, b: 0.108 } },
    } satisfies EngineGap;
    const html = renderToStaticMarkup(createElement(GapFacts, { gap }));
    expect(html).toContain('<th scope="col">Gazebo</th><th scope="col">MuJoCo</th>');
    expect(html).toContain('<td>61.310</td><td>61.300</td>');
    expect(html).toContain('<td>0.080</td><td>0.108</td>');
    expect(html.indexOf('最大横向偏差')).toBeLessThan(html.indexOf('<details'));
    expect(html.indexOf('各车发车对齐 RMSE')).toBeGreaterThan(html.indexOf('<details'));
    expect(html).not.toMatch(/<details[^>]*\bopen[= >]/);
    expect(html).not.toContain('A long research model explanation');
    expect(html).toContain('setup/README.md#仿真与引擎比较');
  });

  it('distinguishes repeated algorithm names by traffic, timestamp and physical record id', () => {
    const comparison = { available: true, path_max_m: 0.108 } as EngineGap;
    mocks.resources['physics-runs'] = loaded({ runs: [
      { ...record('succeeded', comparison), algorithm: 'OR-Tools', physics_run_id: 'phys_serial_111111', traffic_mode: 'serial', finished_utc: '2026-11-06T08:01:02Z' },
      { ...record('succeeded', comparison), algorithm: 'OR-Tools', physics_run_id: 'phys_parallel_222222', traffic_mode: 'coordinated', finished_utc: '2026-11-06T08:03:04Z' },
    ] });
    const html = render();
    expect(html).toContain('逐车 · 最大差 0.108 m');
    expect(html).toContain('并行 · 最大差 0.108 m');
    expect(html).toContain('11 月 6 日');
    expect(html).toContain('记录 l_111111');
    expect(html).toContain('记录 l_222222');
  });

  it('keeps a concrete execution failure visible before optional details', () => {
    mocks.resources['physics-runs'] = loaded({ runs: [record('failed')] });
    const html = render();
    expect(html.indexOf('worker failed')).toBeGreaterThan(-1);
    expect(html.indexOf('worker failed')).toBeLessThan(html.indexOf('<details'));
    expect(html).toContain('MuJoCo 执行详情');
  });
});
