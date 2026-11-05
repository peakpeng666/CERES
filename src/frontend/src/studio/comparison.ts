import type { Scenario, Solution, Instance } from '../lab/types';
import { planningFingerprint, stableStringify } from './fleet';
import type { FleetInstance } from './types';
import { identityState } from '../lab/views/plan/resultIntegrity';

export function inputIdentity(problem: string, instance: Instance | undefined): string {
  return problem === 'fleet_routing' ? planningFingerprint(instance as unknown as FleetInstance | undefined) : stableStringify(instance ?? null);
}

export function currentInput(run: Solution, scenario: Scenario, draft: Instance): boolean {
  return run.scenario_id === scenario.id && !!run.instance && inputIdentity(run.problem_class, run.instance) === inputIdentity(scenario.problem_class, draft);
}

/**
 * A shared axis/green best value requires a common model, units and wall budget.
 * Quarantined runs have no comparison key; unknown-identity runs only share a key with each other.
 */
export function comparisonKey(run: Solution): string | null {
  if (!run.instance_sha256 || !run.objective?.unit || !run.frame) return null;
  const identity = identityState(run);
  if (identity === 'quarantined') return null;
  const model = run.planning?.comparison_key ?? stableStringify({
    problem: run.problem_class, input: run.instance_sha256, frame: run.frame,
    unit: run.objective.unit, objective: run.instance && 'objective' in run.instance ? run.instance.objective : null,
    turn_radius_enforced: run.problem_class === 'coverage' ? run.metrics.turn_radius_enforced ?? null : null,
  });
  return JSON.stringify([model, run.objective.unit, run.params.time_limit_s ?? null, identity === 'unknown' ? 'identity:unknown' : 'identity:verified']);
}

export interface MapViolationSummary { route: number; segment: number; point: [number, number] | null; clearance_m: number | null }
export interface MapCollisionDetail { violations: MapViolationSummary[]; total: number; min_clearance_m: number | null; threshold_m: number | null }
export interface Exclusion {
  code: 'quarantined' | 'pending' | 'timeout' | 'canceled' | 'failed' | 'legacy' | 'infeasible' | 'no_solution' | 'missing_basis' | 'map_collision' | 'map_unchecked' | 'unchecked';
  text: string;
  map?: MapCollisionDetail;
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Map-check status from the run's own map_check record; null when the run carries no map check or it passed. */
export function mapCheckIssue(run: Solution): Exclusion | null {
  const mc = run.map_check;
  if (!mc) return null;
  if (mc.checked && mc.collision_free === true) return null;
  if (!mc.checked || mc.collision_free !== false) return { code: 'map_unchecked', text: '地图碰撞检查未完成 · 不计入已验算可行解' };
  const list = Array.isArray(mc.violations) ? mc.violations : [];
  const violations = list.map((v) => ({
    route: v.route, segment: v.segment,
    point: Array.isArray(v.point) && finite(v.point[0]) && finite(v.point[1]) ? [v.point[0], v.point[1]] as [number, number] : null,
    clearance_m: finite(v.clearance_m) ? v.clearance_m : null,
  }));
  const total = finite(mc.violation_count) ? Math.max(mc.violation_count, violations.length) : violations.length;
  const threshold = finite(mc.robot_radius_m) ? mc.robot_radius_m + (finite(mc.margin_m) ? mc.margin_m : 0) : null;
  return { code: 'map_collision', text: `地图碰撞检查未通过（${total} 处）· 不计入已验算可行解`,
    map: { violations, total, min_clearance_m: finite(mc.min_clearance_m) ? mc.min_clearance_m : null, threshold_m: threshold } };
}

/** Why a run may not be ranked or compared; null when it is a verified feasible plan with a full basis. */
export function exclusionReason(run: Solution): Exclusion | null {
  if (identityState(run) === 'quarantined') return { code: 'quarantined', text: '源码身份不符 · 已隔离' };
  if (run.state === 'queued' || run.state === 'running') return { code: 'pending', text: '尚未完成' };
  if (run.state === 'timeout') return { code: 'timeout', text: `超时${run.error ? `：${run.error}` : ''}` };
  if (run.state === 'canceled') return { code: 'canceled', text: '已取消' };
  if (run.state !== 'done') return { code: 'failed', text: `出错${run.error ? `：${run.error}` : '（含依赖缺失）'}` };
  if (requiresCoverageRecheck(run)) return { code: 'legacy', text: '旧覆盖记录 · 须重求解' };
  if (run.status === 'infeasible') return { code: 'infeasible', text: '不可行' };
  if (run.status === 'no_solution') return { code: 'no_solution', text: '未找到可行解' };
  if (!run.instance_sha256 || !run.objective?.unit || !run.frame) return { code: 'missing_basis', text: '缺少比较依据（实例哈希/单位/坐标系）' };
  if (run.check?.valid === true) {
    const map = mapCheckIssue(run);
    if (map) return map;
  }
  if (!verifiedPlan(run)) return { code: 'unchecked', text: '未通过独立验算' };
  return null;
}

export interface ComparisonGroup { key: string; identity: 'verified' | 'unknown'; runs: Solution[] }

/** Rankable runs grouped by comparison key; everything else stays listed with its reason. */
export function partitionRuns(runs: Solution[]): { groups: ComparisonGroup[]; excluded: { run: Solution; reason: Exclusion }[] } {
  const groups = new Map<string, ComparisonGroup>();
  const excluded: { run: Solution; reason: Exclusion }[] = [];
  for (const run of runs) {
    const reason = exclusionReason(run);
    const key = reason ? null : comparisonKey(run);
    if (reason || !key) {
      excluded.push({ run, reason: reason ?? { code: 'missing_basis', text: '缺少比较依据' } });
      continue;
    }
    const group = groups.get(key) ?? { key, identity: identityState(run) === 'verified' ? 'verified' as const : 'unknown' as const, runs: [] };
    group.runs.push(run);
    groups.set(key, group);
  }
  return { groups: [...groups.values()], excluded };
}

export function comparable(a: Solution, b: Solution): boolean {
  const key = comparisonKey(a);
  return key !== null && key === comparisonKey(b);
}

export function verifiedPlan(run: Solution): boolean {
  return !requiresCoverageRecheck(run) && run.state === 'done' && ['optimal', 'feasible'].includes(run.status ?? '') && run.check?.valid === true
    && (!run.map_check || (run.map_check.checked && run.map_check.collision_free === true))
    && typeof run.objective.value === 'number' && Number.isFinite(run.objective.value);
}

export function requiresCoverageRecheck(run: Solution): boolean {
  return run.algorithm?.id === 'f2c.full' && run.raw_result?.adapter_contract !== 'urp.f2c-full/2';
}

export function resultState(run: Solution): { text: string; title?: string } {
  if (run.state === 'queued') return { text: '排队中' };
  if (run.state === 'running') return { text: '计算中' };
  if (run.state === 'timeout') return { text: '超时', title: run.error ?? '预算内未返回最终解' };
  if (run.state === 'canceled') return { text: '已取消', title: run.error ?? undefined };
  if (run.state !== 'done') return { text: '出错', title: run.error ?? undefined };
  if (requiresCoverageRecheck(run)) return { text: '旧覆盖记录 · 须重求解', title: '旧全流程适配漏掉末段；原始记录保留，不计入新版可行解比较或执行' };
  if (run.status === 'infeasible') return { text: '不可行（求解器报告）', title: run.error ?? run.check?.errors[0] };
  if (run.status === 'no_solution') return { text: '未找到可行解', title: run.error ?? run.check?.errors[0] };
  if (!verifiedPlan(run)) return { text: run.check?.valid === false || run.map_check?.collision_free === false ? '检查未通过' : '有解 · 待检查' };
  return { text: run.status === 'optimal' ? '最优（求解器报告）' : '可行 · 已验算' };
}
