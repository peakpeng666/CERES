/** Transforms over loaded run records (Solution) for the comparison panel. Quarantined runs never contribute values. */
import type { Solution } from '../../lab/types';
import { identityState } from '../../lab/views/plan/resultIntegrity';
import { exclusionReason } from '../comparison';
import { stableStringify } from '../fleet';
import { geomGrid, missing, quantileBand, type BandPoint, type Missing } from './chartData';

type Traced = Solution & { trace?: [number, number][] };

/** Same rule as analysis.trace_matches: a declared reported-objective trace that ends at the objective. */
export function traceMatches(run: Solution): boolean {
  const tr = (run as Traced).trace;
  const v = run.objective?.value;
  if (run.trace_contract?.semantic !== 'reported_objective' || !Array.isArray(tr) || !tr.length || typeof v !== 'number' || !Number.isFinite(v)) return false;
  return Math.abs(tr[tr.length - 1]![1] - v) <= 5e-3 * Math.max(1, Math.abs(v));
}

export interface SeedBand { key: string; label: string; runs: number; seeds: string[]; band: BandPoint[] }
export type SeedBandData = Missing | { ok: true; groups: SeedBand[]; unit: string; excluded: { quarantined: number; notSuccessful: number; noTrace: number } };

/** Group comparable runs by algorithm + non-seed params; each group's band spans its seeds. */
export function seedBandData(runs: Solution[]): SeedBandData {
  const excluded = { quarantined: 0, notSuccessful: 0, noTrace: 0 };
  const usable: Traced[] = [];
  for (const r of runs) {
    if (identityState(r) === 'quarantined') excluded.quarantined++;
    else if (exclusionReason(r)) excluded.notSuccessful++;
    else if (!traceMatches(r)) excluded.noTrace++;
    else usable.push(r as Traced);
  }
  if (!usable.length) return missing(`没有可用轨迹：须为已验算可行解且 trace_contract 为 reported_objective、轨迹终值等于目标值（已隔离 ${excluded.quarantined}，未成功 ${excluded.notSuccessful}，无一致轨迹 ${excluded.noTrace}）`);
  const units = new Set(usable.map((r) => r.objective.unit));
  if (units.size > 1) return missing(`目标单位不一致（${[...units].join('、')}），不能画在同一坐标轴`);
  const groups = new Map<string, Traced[]>();
  for (const r of usable) {
    const { seed: _s, ...rest } = r.params ?? {}; void _s;
    const key = `${r.algorithm.id}|${r.algorithm.version ?? ''}|${stableStringify(rest)}`;
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  const ts = usable.flatMap((r) => r.trace!.map(([t]) => t)).filter((t) => t > 0);
  const horizon = Math.max(...usable.map((r) => Math.max(r.trace![r.trace!.length - 1]![0], Number(r.params?.time_limit_s) || 0)));
  const grid = geomGrid(Math.min(...ts, horizon), horizon);
  return {
    ok: true, unit: [...units][0]!, excluded,
    groups: [...groups.entries()].map(([key, rs]) => ({ key, label: `${rs[0]!.algorithm.name}${rs[0]!.algorithm.version ? ` ${rs[0]!.algorithm.version}` : ''}`,
      runs: rs.length, seeds: rs.map((r) => (r.params?.seed == null ? '未知' : String(r.params.seed))), band: quantileBand(rs.map((r) => r.trace!), grid) })),
  };
}

/* -------------------------------------------------- independent verification -- */
export interface VerifyPoint { run_id: string; label: string; reported: number; recomputed: number; relDiff: number; valid: boolean; checker: string }
export type VerifyData = Missing | { ok: true; points: VerifyPoint[]; unit: string; counts: { total: number; quarantined: number; notDone: number; noCheck: number; noReported: number; otherUnit: number }; maxRelDiff: number };

/** objective.value (solver-reported) vs check.computed_cost (independent checker) — recorded fields only. */
export function verificationData(runs: Solution[]): VerifyData {
  const counts = { total: runs.length, quarantined: 0, notDone: 0, noCheck: 0, noReported: 0, otherUnit: 0 };
  const candidates: { r: Solution; reported: number; recomputed: number }[] = [];
  for (const r of runs) {
    if (identityState(r) === 'quarantined') { counts.quarantined++; continue; }
    if (r.state !== 'done') { counts.notDone++; continue; }
    const recomputed = r.check?.computed_cost;
    if (typeof recomputed !== 'number' || !Number.isFinite(recomputed)) { counts.noCheck++; continue; }
    const reported = r.objective?.value;
    if (typeof reported !== 'number' || !Number.isFinite(reported)) { counts.noReported++; continue; }
    candidates.push({ r, reported, recomputed });
  }
  if (!candidates.length) return missing(`没有同时具有求解器目标值（objective.value）与独立验算值（check.computed_cost）的运行（共 ${counts.total}：已隔离 ${counts.quarantined}，未完成 ${counts.notDone}，无验算值 ${counts.noCheck}，无报告值 ${counts.noReported}）`);
  const unit = candidates[0]!.r.objective.unit;
  const points: VerifyPoint[] = [];
  for (const { r, reported, recomputed } of candidates) {
    if (r.objective.unit !== unit) { counts.otherUnit++; continue; }
    points.push({ run_id: r.run_id, label: r.algorithm.name, reported, recomputed, valid: r.check!.valid === true, checker: r.check!.checker ?? '未提供',
      relDiff: Math.abs(reported - recomputed) / Math.max(Math.abs(recomputed), 1e-12) });
  }
  return { ok: true, points, unit, counts, maxRelDiff: Math.max(...points.map((p) => p.relDiff)) };
}

/* ------------------------------------------------------------- status flow -- */
export type StatusCategory = 'success' | 'unverified' | 'quarantined' | 'infeasible' | 'unchecked' | 'timeout' | 'canceled' | 'error' | 'pending';
export const STATUS_LABEL: Record<StatusCategory, string> = {
  success: '成功（已验算 · 身份已核验）', unverified: '有解 · 身份未核验', quarantined: '已隔离（源码身份不符）', infeasible: '不可行 / 无解',
  unchecked: '有解 · 未通过验算或缺依据', timeout: '超时', canceled: '已取消', error: '出错（含依赖缺失）', pending: '排队 / 计算中',
};
export const STATUS_ORDER: StatusCategory[] = ['success', 'unverified', 'quarantined', 'infeasible', 'unchecked', 'timeout', 'canceled', 'error', 'pending'];

/** One category per run, reusing identityState and exclusionReason; unknown identity is its own category. */
export function statusCategory(r: Solution): StatusCategory {
  if (identityState(r) === 'quarantined') return 'quarantined';
  const ex = exclusionReason(r);
  if (!ex) return identityState(r) === 'verified' ? 'success' : 'unverified';
  switch (ex.code) {
    case 'pending': return 'pending';
    case 'timeout': return 'timeout';
    case 'canceled': return 'canceled';
    case 'failed': return 'error';
    case 'infeasible': case 'no_solution': return 'infeasible';
    default: return 'unchecked';
  }
}

export function statusCounts(runs: Solution[]) {
  const counts = Object.fromEntries(STATUS_ORDER.map((c) => [c, 0])) as Record<StatusCategory, number>;
  for (const r of runs) counts[statusCategory(r)]++;
  const identityUnknown = runs.filter((r) => identityState(r) === 'unknown').length;
  return { total: runs.length, counts, identityUnknown, categories: STATUS_ORDER.filter((c) => counts[c] > 0) };
}
