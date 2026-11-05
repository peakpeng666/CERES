/**
 * Pure transforms from the existing backend Analysis (and loaded run records) into chart series.
 * Nothing here invents values: a missing field yields { ok: false, reason }, unknown never becomes 0.
 */
import type { Analysis } from '../types';
import { canShowCd } from '../analysisStats';

export type Missing = { ok: false; reason: string };
export const missing = (reason: string): Missing => ({ ok: false, reason });

export function analysisMissing(a: Analysis | null | undefined): Missing | null {
  if (!a) return missing('尚未载入课题分析（GET /studies/{id}/analysis）');
  if (a.empty) return missing('课题还没有完成的运行（Analysis.empty）');
  return null;
}

export const algorithmNames = (a: Analysis): Record<string, string> =>
  Object.fromEntries((a.summary ?? []).map((x) => [x.algorithm, x.name ?? x.algorithm]));

/* ---------------------------------------------------------- performance profile -- */
export interface ProfileSeries { algorithm: string; label: string; points: [number, number][]; reachedAll: boolean; finalRho: number }
export type ProfileData = Missing | { ok: true; series: ProfileSeries[]; n: number; absent: string[]; note: string | null };

/** Analysis.performance_profile → step curves ρ(τ); 1 − ρ(τmax) is the share of instances never solved. */
export function profileData(a: Analysis | null | undefined): ProfileData {
  const none = analysisMissing(a); if (none) return none;
  const pp = a!.performance_profile;
  if (!pp || !Array.isArray(pp.taus) || !pp.taus.length) return missing('performance_profile.taus 为空：没有所有算法都运行过的完整实例');
  const names = algorithmNames(a!);
  const series: ProfileSeries[] = []; const absent: string[] = [];
  for (const alg of a!.algorithms) {
    const curve = pp.curves?.[alg];
    if (!Array.isArray(curve) || curve.length !== pp.taus.length) { absent.push(names[alg] ?? alg); continue; }
    const points = pp.taus.map((t, i) => [t, curve[i]] as [number, number]).filter(([, r]) => typeof r === 'number' && Number.isFinite(r));
    const finalRho = points.length ? points[points.length - 1]![1] : NaN;
    series.push({ algorithm: alg, label: names[alg] ?? alg, points, finalRho, reachedAll: finalRho >= 1 - 1e-12 });
  }
  if (!series.length) return missing('performance_profile.curves 中没有与 taus 等长的曲线');
  return { ok: true, series, n: a!.n_complete_instances, absent, note: (pp as { note?: string }).note ?? null };
}

/* ------------------------------------------------------------------------ ECDF -- */
export interface EcdfSeries { algorithm: string; label: string; points: [number, number][]; total: number; reached: number; notReached: number; detail: string }
export type EcdfData = Missing | { ok: true; series: EcdfSeries[]; excludedQuarantined: number };

/** Step ECDF over all runs: y = (#values ≤ x) / total, so runs that never reach stay as the gap below 1. */
export function ecdfPoints(values: number[], total: number): [number, number][] {
  const v = values.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  return total > 0 ? v.map((x, i) => [x, (i + 1) / total]) : [];
}

/** Analysis.ecdf (gap %, successful runs with a reference) against Analysis.summary runs/successes. */
export function gapEcdfData(a: Analysis | null | undefined): EcdfData {
  const none = analysisMissing(a); if (none) return none;
  if (!a!.ecdf || typeof a!.ecdf !== 'object') return missing('Analysis.ecdf 缺失');
  const names = algorithmNames(a!);
  const series: EcdfSeries[] = [];
  for (const alg of a!.algorithms) {
    const s = a!.summary.find((x) => x.algorithm === alg);
    const gaps = a!.ecdf[alg];
    if (!s || !Array.isArray(gaps) || typeof s.runs !== 'number') continue;
    const counts = a!.ecdf_metadata?.counts[alg];
    const total = counts?.eligible_terminal_runs ?? s.runs;
    const failed = counts?.eligible_failed_runs ?? s.runs - s.successes;
    const noRef = counts?.missing_reference_runs ?? s.successes - gaps.length;
    series.push({ algorithm: alg, label: names[alg] ?? alg, points: ecdfPoints(gaps, total), total, reached: gaps.length,
      notReached: total - gaps.length, detail: `失败/超时/未通过检查/已隔离 ${failed}${noRef > 0 ? `，无正参照 ${noRef}${counts ? '（不计入分母）' : ''}` : ''}` });
  }
  if (!series.length) return missing('Analysis.ecdf 与 summary 没有可对应的算法');
  return { ok: true, series, excludedQuarantined: 0 };
}

/** Runtime ECDF from Analysis.scatter: only runs with a gap value (successful, referenced) reach. */
export function runtimeEcdfData(a: Analysis | null | undefined, quarantined: ReadonlySet<string> = new Set()): EcdfData {
  const none = analysisMissing(a); if (none) return none;
  if (!Array.isArray(a!.scatter) || !a!.scatter.length) return missing('Analysis.scatter 为空：没有逐次运行的用时记录');
  const names = algorithmNames(a!);
  const rows = a!.scatter.filter((r) => !quarantined.has(r.run_id));
  const series: EcdfSeries[] = [];
  for (const alg of a!.algorithms) {
    const mine = rows.filter((r) => r.algorithm === alg);
    if (!mine.length) continue;
    const ok = mine.filter((r) => r.gap != null);
    const times = ok.map((r) => r.time).filter((t): t is number => typeof t === 'number' && Number.isFinite(t) && t > 0);
    series.push({ algorithm: alg, label: names[alg] ?? alg, points: ecdfPoints(times, mine.length), total: mine.length, reached: times.length,
      notReached: mine.length - times.length, detail: `未成功 ${mine.length - ok.length}${ok.length - times.length ? `，用时缺失 ${ok.length - times.length}` : ''}` });
  }
  if (!series.length) return missing('Analysis.scatter 中没有与 algorithms 对应的运行');
  return { ok: true, series, excludedQuarantined: a!.scatter.length - rows.length };
}

/* ------------------------------------------------------- convergence quantiles -- */
/** Type-7 quantile of an ascending array that may end in +Infinity (runs without a solution yet). */
export function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN;
  const h = (sorted.length - 1) * q; const lo = Math.floor(h); const hi = Math.ceil(h);
  const a = sorted[lo]!, b = sorted[hi]!;
  if (lo === hi) return a;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return Number.POSITIVE_INFINITY;
  return a + (h - lo) * (b - a);
}

export interface BandPoint { t: number; median: number | null; lo: number | null; hi: number | null }

/**
 * Legacy seed-band convention, distinct from the conditional backend anytime/2 curve.
 * A point is shown only when more than half the selected traces have a value; an infinite
 * quartile (too few runs with a value) leaves that band edge open (null), never 0.
 */
export function quantileBand(traces: [number, number][][], grid: number[]): BandPoint[] {
  return grid.map((t) => {
    const vals = traces.map((tr) => { let v = Number.POSITIVE_INFINITY; for (const [tt, y] of tr) if (tt <= t) v = y; return v; }).sort((a, b) => a - b);
    const finite = vals.filter(Number.isFinite).length;
    if (!vals.length || finite * 2 <= vals.length) return { t, median: null, lo: null, hi: null };
    const fin = (v: number) => (Number.isFinite(v) ? v : null);
    return { t, median: fin(quantile(vals, 0.5)), lo: fin(quantile(vals, 0.25)), hi: fin(quantile(vals, 0.75)) };
  });
}

export function geomGrid(lo: number, hi: number, n = 40): number[] {
  const a = Math.log10(Math.max(lo, 1e-3)), b = Math.log10(Math.max(hi, lo * 10, 1e-2));
  return Array.from({ length: n }, (_, i) => 10 ** (a + ((b - a) * i) / (n - 1)));
}

/** Analysis.anytime → median gap curves; the interval is not part of the backend payload. */
export function anytimeData(a: Analysis | null | undefined) {
  const none = analysisMissing(a); if (none) return none;
  const any = a!.anytime as Analysis['anytime'] & { excluded?: number; note?: string };
  if (!any || !Array.isArray(any.grid) || !any.grid.length) return missing(`Analysis.anytime.grid 为空：没有与目标值一致的求解轨迹${any?.excluded ? `（${any.excluded} 次运行未进入条件质量曲线；详见原因计数）` : ''}`);
  const names = algorithmNames(a!);
  const series = Object.entries(any.median ?? {}).map(([alg, ys]) => ({ algorithm: alg, label: names[alg] ?? alg,
    points: any.grid.map((t, i) => [t, ys[i] ?? null] as [number, number | null]) }));
  if (!series.length) return missing('Analysis.anytime.median 为空');
  return { ok: true as const, series, excluded: any.excluded ?? null, note: any.note ?? null };
}

/* ------------------------------------------------------------- gap vs time -- */
export interface GapTimePoint { algorithm: string; label: string; time: number; gap: number; run_id: string; instance: string }
export type GapTimeData = Missing | { ok: true; points: GapTimePoint[]; algorithms: { algorithm: string; label: string; total: number; plotted: number; failed: number; noTime: number }[]; budgets: string[]; excludedQuarantined: number };

export function gapTimeData(a: Analysis | null | undefined, quarantined: ReadonlySet<string> = new Set()): GapTimeData {
  const none = analysisMissing(a); if (none) return none;
  if (!Array.isArray(a!.scatter) || !a!.scatter.length) return missing('Analysis.scatter 为空');
  const names = algorithmNames(a!);
  const rows = a!.scatter.filter((r) => !quarantined.has(r.run_id));
  const points: GapTimePoint[] = []; const algorithms = [];
  for (const alg of a!.algorithms) {
    const mine = rows.filter((r) => r.algorithm === alg);
    if (!mine.length) continue;
    const withGap = mine.filter((r) => r.gap != null && Number.isFinite(r.gap));
    const ok = withGap.filter((r) => typeof r.time === 'number' && Number.isFinite(r.time) && r.time > 0);
    ok.forEach((r) => points.push({ algorithm: alg, label: names[alg] ?? alg, time: r.time!, gap: r.gap!, run_id: r.run_id, instance: r.instance }));
    algorithms.push({ algorithm: alg, label: names[alg] ?? alg, total: mine.length, plotted: ok.length, failed: mine.length - withGap.length, noTime: withGap.length - ok.length });
  }
  if (!points.length) return missing('Analysis.scatter 中没有同时具有差距与正用时的运行');
  const budgets = [...new Set(a!.algorithms.map((x) => /@([\d.]+)s$/.exec(x)?.[1]).filter((x): x is string => !!x))];
  return { ok: true, points, algorithms, budgets, excludedQuarantined: a!.scatter.length - rows.length };
}

/* ---------------------------------------------------- critical difference layout -- */
export interface CdItem { algorithm: string; label: string; rank: number; side: 'left' | 'right'; row: number }
export type CdData = Missing | { ok: true; k: number; cd: number; n: number; alpha: number; items: CdItem[]; cliques: [number, number][];
  friedman: Analysis['tests']['friedman']; holmSignificant: string[] };

/** Demšar-style layout: ascending mean ranks, best half labelled left; cliques = maximal runs within CD. */
export function cdLayout(ranks: Record<string, number>, cd: number, names: Record<string, string> = {}): { items: CdItem[]; cliques: [number, number][] } {
  const sorted = Object.entries(ranks).filter(([, r]) => Number.isFinite(r)).sort((a, b) => a[1] - b[1]);
  const k = sorted.length; const half = Math.ceil(k / 2);
  const items = sorted.map(([algorithm, rank], i) => ({ algorithm, label: names[algorithm] ?? algorithm, rank,
    side: (i < half ? 'left' : 'right') as 'left' | 'right', row: i < half ? i : k - 1 - i }));
  const cliques: [number, number][] = [];
  for (let i = 0; i < k; i++) {
    let j = i;
    while (j + 1 < k && sorted[j + 1]![1] - sorted[i]![1] <= cd) j++;
    if (j > i && !cliques.some(([a, b]) => a <= i && b >= j)) cliques.push([i, j]);
  }
  return { items, cliques };
}

export function cdData(a: Analysis | null | undefined): CdData {
  const none = analysisMissing(a); if (none) return none;
  const t = a!.tests;
  if (!t) return missing('Analysis.tests 缺失');
  if (!t.nemenyi) return missing(`tests.nemenyi 缺失${t.friedman?.error ? `：${t.friedman.error}` : '（需要至少 2 个算法与多个完整实例）'}`);
  if (!canShowCd(t)) return missing(t.posthoc_reason ?? 'Friedman / Nemenyi 未通过有效性与显著性检查');
  const names = algorithmNames(a!);
  const { items, cliques } = cdLayout(t.nemenyi.mean_ranks, t.nemenyi.critical_difference, names);
  if (items.length < 2) return missing('tests.nemenyi.mean_ranks 少于 2 个算法');
  return { ok: true, k: items.length, cd: t.nemenyi.critical_difference, n: t.n, alpha: t.alpha, items, cliques, friedman: t.friedman,
    holmSignificant: (t.wilcoxon_holm ?? []).filter((r) => r.significant).map((r) => `${names[r.a] ?? r.a} vs ${names[r.b] ?? r.b}（p_Holm = ${r.p_holm?.toExponential(2) ?? '未提供'}）`) };
}
