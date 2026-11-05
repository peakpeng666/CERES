import type { Solution } from '../../lab/types';
import { mapCheckIssue, type MapViolationSummary } from '../comparison';

export const MAP_VIOLATIONS_SHOWN = 3;

/** Verify-column text that agrees with verifiedPlan: a passing route check is not "通过" when the map check failed or did not run. */
export function verifyText(run: Solution): { text: string; code: 'pass' | 'route_fail' | 'map_collision' | 'map_unchecked' | 'unchecked' } {
  if (!run.check) return { text: '未检查', code: 'unchecked' };
  if (!run.check.valid) return { text: `未通过${run.check.errors.length ? ` ${run.check.errors.length} 项` : ''}`, code: 'route_fail' };
  const issue = mapCheckIssue(run);
  if (issue?.code === 'map_collision') return { text: `未通过（地图碰撞 ${issue.map?.total ?? 0} 处）`, code: 'map_collision' };
  if (issue) return { text: '路线通过 · 地图未检查', code: 'map_unchecked' };
  return { text: '通过', code: 'pass' };
}

const n = (v: number, d: number) => v.toFixed(d);

export function violationText(v: MapViolationSummary): string {
  const at = v.point ? `(${n(v.point[0], 3)}, ${n(v.point[1], 3)})` : '位置未记录';
  return `路线 ${v.route} 第 ${v.segment} 段 · ${at}${v.clearance_m !== null ? ` · 净空 ${n(v.clearance_m, 3)} m` : ''}`;
}

/** Explains why a run with a passing route check is not a verified feasible plan: its map check failed or did not run. */
export function MapExclusionNote({ run, label }: { run: Solution; label?: string }) {
  const issue = mapCheckIssue(run);
  if (!issue || run.check?.valid !== true) return null;
  const who = label ?? `${run.algorithm?.name ?? run.run_id} · ${run.run_id}`;
  if (issue.code === 'map_unchecked' || !issue.map) {
    return <p className="map-exclusion" data-exclusion="map_unchecked" data-run-id={run.run_id}>{who}：地图碰撞检查未完成，不计入已验算可行解。</p>;
  }
  const { violations, total, min_clearance_m, threshold_m } = issue.map;
  const shown = violations.slice(0, MAP_VIOLATIONS_SHOWN);
  const rest = total - shown.length;
  const clearance = min_clearance_m !== null
    ? `最小净空 ${n(min_clearance_m, 3)} m${threshold_m !== null ? ` < 阈值 ${n(threshold_m, 3)} m` : ''}`
    : threshold_m !== null ? `阈值 ${n(threshold_m, 3)} m` : '';
  return (
    <div className="map-exclusion" data-exclusion="map_collision" data-run-id={run.run_id} data-violation-count={total}>
      <span>{who}：地图碰撞检查未通过，不计入已验算可行解{clearance ? `（${clearance}）` : ''}。</span>
      {shown.length > 0 && (
        <ul aria-label="碰撞线段">
          {shown.map((v, i) => <li key={i} data-map-violation="true" data-route={v.route} data-segment={v.segment}>{violationText(v)}</li>)}
        </ul>
      )}
      {rest > 0 && <span data-map-violation-rest={rest}>另有 {rest} 处</span>}
    </div>
  );
}
