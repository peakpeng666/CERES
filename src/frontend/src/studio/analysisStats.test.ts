/** Synthetic API payloads only; not experiment results. */
import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnalysisInference } from './panels/ResearchPanels';
import { canShowCd, hasValidFriedman } from './analysisStats';
import type { Analysis } from './types';

const tests = (): Analysis['tests'] => ({
  alpha: 0.05, k: 7, n: 11, sample_unit: 'paired_instance_block',
  mean_ranks: { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7 },
  friedman: { status: 'ok', testable: true, statistic: 66, p_value: 0.0001, significant: true },
  nemenyi: { status: 'ok', q_alpha: 2.9, critical_difference: 2.5, mean_ranks: { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7 } },
  wilcoxon_holm: [],
});
const markup = (t: Analysis['tests']) => renderToStaticMarkup(createElement(AnalysisInference, { tests: t, names: {} }));

describe('inference display gate', () => {
  it('renders CD only after a valid, significant Friedman test', () => {
    const t = tests();
    expect(canShowCd(t)).toBe(true);
    expect(hasValidFriedman(t)).toBe(true);
    expect(markup(t)).toContain('CD = 2.50');
    expect(markup(t)).toContain('<svg');
    expect(markup(t)).toContain('N = 11');
    expect(markup(t)).toContain('k = 7');
  });
  it.each(['all_ties', 'insufficient_asymptotic_sample', 'insufficient_blocks_or_algorithms'])('keeps %s descriptive, even with a stale CD object', (reason) => {
    const t = tests();
    t.friedman = { status: 'not_testable', testable: false, reason, statistic: null, p_value: null, significant: null };
    expect(canShowCd(t)).toBe(false);
    expect(markup(t)).toContain(reason);
    expect(markup(t)).not.toContain('<svg');
    expect(markup(t)).not.toContain('CD =');
  });
  it('does not confuse finite nonsignificance with an untestable result', () => {
    const t = tests();
    t.friedman = { ...t.friedman, p_value: 0.5, significant: false };
    expect(hasValidFriedman(t)).toBe(true);
    expect(canShowCd(t)).toBe(false);
    expect(markup(t)).toContain('未发现显著差异');
    expect(markup(t)).not.toContain('<svg');
    expect(markup(t)).not.toContain('CD =');
  });
  it.each([NaN, Infinity, -Infinity])('rejects nonfinite p or statistic %s', (value) => {
    for (const field of ['p_value', 'statistic'] as const) {
      const t = tests();
      t.friedman = { ...t.friedman, [field]: value };
      expect(canShowCd(t)).toBe(false);
      expect(markup(t)).not.toMatch(/NaN|Infinity|<svg|CD =/);
    }
  });
  it('does not infer validity from legacy presence alone', () => {
    const t = tests();
    t.friedman = { statistic: 20, p_value: 0.001, significant: true };
    expect(canShowCd(t)).toBe(false);
    expect(markup(t)).not.toContain('<svg');
  });
  it.each([NaN, Infinity, 0, -1])('rejects invalid CD %s', (cd) => {
    const t = tests();
    t.nemenyi!.critical_difference = cd;
    expect(canShowCd(t)).toBe(false);
    expect(markup(t)).not.toContain('<svg');
  });
  it('rejects inconsistent significance and nonfinite ranks', () => {
    const t = tests();
    t.friedman!.p_value = 0.1;
    expect(canShowCd(t)).toBe(false);
    expect(hasValidFriedman(t)).toBe(false);
    const badRanks = tests();
    badRanks.nemenyi!.mean_ranks.a = NaN;
    expect(canShowCd(badRanks)).toBe(false);
  });
});
