import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Solution } from '../../lab/types';
import { exclusionReason, mapCheckIssue, partitionRuns, verifiedPlan } from '../comparison';
import { MapExclusionNote, verifyText } from './MapExclusionNote';

// Shape copied from a real cvrp map run returned by /lab/api/runs (map_check fields only).
const collision = {
  checked: true, collision_free: false, min_clearance_m: 0.0, robot_radius_m: 0.175, margin_m: 0.05,
  violations: [{ route: 2, segment: 5, point: [-7.775245901639344, 3.8854918032786885], clearance_m: 0.0 }], violation_count: 1,
};
const run = (map_check: unknown, extra: Record<string, unknown> = {}) => ({
  run_id: 'run_abcdef123', scenario_id: 's', problem_class: 'euclidean_cvrp', instance_sha256: 'h',
  frame: { kind: 'map', units: 'm' }, objective: { value: 54.9, unit: 'm' }, state: 'done', status: 'feasible',
  check: { valid: true, errors: [] }, map_check, routes: [{}], params: {}, metrics: {}, algorithm: { id: 'a', name: '算法A' }, ...extra,
}) as unknown as Solution;

describe('map check exclusion reason', () => {
  it('derives a structured map_collision reason with violations and threshold', () => {
    const r = run(collision);
    expect(verifiedPlan(r)).toBe(false);
    const reason = exclusionReason(r)!;
    expect(reason.code).toBe('map_collision');
    expect(reason.map).toEqual({ violations: [{ route: 2, segment: 5, point: [-7.775245901639344, 3.8854918032786885], clearance_m: 0 }],
      total: 1, min_clearance_m: 0, threshold_m: 0.175 + 0.05 });
    expect(partitionRuns([r]).excluded[0].reason.code).toBe('map_collision');
  });
  it('reports unchecked map checks and ignores passing or absent ones', () => {
    expect(mapCheckIssue(run({ ...collision, checked: false, collision_free: null }))?.code).toBe('map_unchecked');
    expect(exclusionReason(run({ ...collision, checked: false, collision_free: null }))?.code).toBe('map_unchecked');
    expect(mapCheckIssue(run({ ...collision, collision_free: true, violations: [] }))).toBeNull();
    expect(mapCheckIssue(run(null))).toBeNull();
    expect(exclusionReason(run(null))).toBeNull();
  });
  it('keeps route-check failures as unchecked rather than blaming the map', () => {
    expect(exclusionReason(run(collision, { check: { valid: false, errors: ['x'] } }))?.code).toBe('unchecked');
  });
  it('uses violation_count when the stored list is truncated and tolerates missing threshold fields', () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ route: 0, segment: i, point: [i, i], clearance_m: 0.1 }));
    const issue = mapCheckIssue(run({ checked: true, collision_free: false, min_clearance_m: null, violations: many, violation_count: 240 }))!;
    expect(issue.map).toMatchObject({ total: 240, min_clearance_m: null, threshold_m: null });
  });
});

describe('MapExclusionNote', () => {
  it('lists violating segments with stable data attributes', () => {
    const html = renderToStaticMarkup(createElement(MapExclusionNote, { run: run(collision) }));
    expect(html).toContain('data-exclusion="map_collision"');
    expect(html).toContain('data-violation-count="1"');
    expect(html).toContain('算法A · run_abcdef123：地图碰撞检查未通过，不计入已验算可行解（最小净空 0.000 m &lt; 阈值 0.225 m）。');
    expect(html).toContain('路线 2 第 5 段 · (-7.775, 3.885) · 净空 0.000 m');
    expect(html).not.toContain('另有');
  });
  it('caps the list at three and states the remainder', () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ route: 1, segment: i, point: [i, -i], clearance_m: 0.1 }));
    const html = renderToStaticMarkup(createElement(MapExclusionNote, { run: run({ ...collision, violations: many, violation_count: 5 }) }));
    expect(html.match(/data-map-violation="true"/g)).toHaveLength(3);
    expect(html).toContain('另有 2 处');
  });
  it('says the map check did not run, and renders nothing for verified runs', () => {
    const html = renderToStaticMarkup(createElement(MapExclusionNote, { run: run({ ...collision, checked: false, collision_free: null, violations: [] }) }));
    expect(html).toContain('data-exclusion="map_unchecked"');
    expect(html).toContain('地图碰撞检查未完成，不计入已验算可行解。');
    expect(renderToStaticMarkup(createElement(MapExclusionNote, { run: run(null) }))).toBe('');
  });
});

describe('verifyText', () => {
  it('agrees with verifiedPlan for map collisions and unchecked maps', () => {
    expect(verifyText(run(collision))).toEqual({ text: '未通过（地图碰撞 1 处）', code: 'map_collision' });
    expect(verifyText(run({ ...collision, checked: false, collision_free: null })).code).toBe('map_unchecked');
    expect(verifyText(run(null))).toEqual({ text: '通过', code: 'pass' });
    expect(verifyText(run(null, { check: { valid: false, errors: ['a', 'b'] } }))).toEqual({ text: '未通过 2 项', code: 'route_fail' });
    expect(verifyText(run(null, { check: null })).code).toBe('unchecked');
  });
});
