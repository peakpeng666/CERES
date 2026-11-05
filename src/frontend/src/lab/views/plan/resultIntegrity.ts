import type { Solution } from '../../types';

// Match the gateway's diagnostic-only allowlist. Unknown future metrics fail
// closed; raw records and logs remain available in the audit details.
const DIAGNOSTIC_METRICS = new Set(['process_wall_s', 'wall_s', 'cpu_s', 'length_unit']);

export function withoutInvalidatedResults(run: Solution | null): Solution | null {
  if (!run || run.process?.source_identity_verified !== false) return run;
  const metrics = { ...run.metrics };
  for (const key of Object.keys(metrics)) {
    if (!DIAGNOSTIC_METRICS.has(key)) metrics[key] = null;
  }
  return {
    ...run,
    status: null, routes: [], check: null, map_check: null,
    objective: { value: null, unit: run.objective.unit, lower_bound: null },
    metrics,
  };
}

export function coveragePercent(run: Solution): number | null {
  const fraction = run.metrics.coverage_fraction;
  if (run.state !== 'done' || run.process?.source_identity_verified === false ||
      typeof fraction !== 'number' || !Number.isFinite(fraction)) return null;
  return fraction * 100;
}

/** Source identity of the solver that produced a run. A missing field is unknown, never "verified". */
export type IdentityState = 'verified' | 'quarantined' | 'unknown';

export function identityState(run: Pick<Solution, 'process'> | null | undefined): IdentityState {
  const v = run?.process?.source_identity_verified;
  return v === true ? 'verified' : v === false ? 'quarantined' : 'unknown';
}

export const IDENTITY_LABEL: Record<IdentityState, string> = {
  verified: '身份已核验',
  quarantined: '已隔离 · 源码身份不符',
  unknown: '身份未核验',
};

/** Banner text for a quarantined run; null for any other state. Reads only recorded fields. */
export function quarantineReason(run: Solution | null | undefined): string | null {
  if (!run || identityState(run) !== 'quarantined') return null;
  const parts = ['求解器源码身份核对未通过，本次路线、目标值、指标与验算均不作为有效结果'];
  if (run.error) parts.push(`记录原因：${run.error}`);
  if (run.process?.evidence) parts.push(`证据：${run.process.evidence}`);
  return `${parts.join('；')}。原始结果与诊断保留在记录中，仅供审计。`;
}
