/**
 * 模式目录与状态判定。
 * 状态优先级：proposal > approx > runnable。
 * 注意：带 approximation 的可运行模板（如茶园鲜叶集运）状态为 approx——
 * 可提交但必须显式声明近似点。
 */
import type { AgriTaskPattern } from '../taxonomy';
import { RUNNABLE_PATTERNS } from './runnable';
import { APPROX_PATTERNS } from './approx';
import { PROPOSAL_PATTERNS } from './proposals';
import { ROW_WEEDING } from './rowWeeding';

/** 以区域覆盖代理整行服务的近似模式；与点访问代理分开列出，便于各自守住代理口径。 */
export const COVERAGE_APPROX_PATTERNS: AgriTaskPattern[] = [ROW_WEEDING];

export const AGRI_PATTERNS: AgriTaskPattern[] = [
  ...RUNNABLE_PATTERNS, ...APPROX_PATTERNS, ...COVERAGE_APPROX_PATTERNS, ...PROPOSAL_PATTERNS,
];

export const APPROX_IDS: ReadonlySet<string> = new Set([
  ...APPROX_PATTERNS.map((p) => p.id),
  ...COVERAGE_APPROX_PATTERNS.map((p) => p.id),
  ...RUNNABLE_PATTERNS.filter((p) => p.approximation).map((p) => p.id),
]);

export const PROPOSAL_IDS: ReadonlySet<string> = new Set(PROPOSAL_PATTERNS.map((p) => p.id));

export type PatternStatus = 'runnable' | 'approx' | 'proposal';

export function patternStatus(p: AgriTaskPattern): PatternStatus {
  if (PROPOSAL_IDS.has(p.id)) return 'proposal';
  if (APPROX_IDS.has(p.id)) return 'approx';
  return 'runnable';
}

export function getPattern(id: string): AgriTaskPattern | undefined {
  return AGRI_PATTERNS.find((p) => p.id === id);
}
