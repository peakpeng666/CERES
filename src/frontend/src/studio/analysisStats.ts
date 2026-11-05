import type { Analysis } from './types';

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** The API's validity decision plus finite numbers, including historical payloads. */
export function hasValidFriedman(tests: Analysis['tests']): boolean {
  const fr = tests.friedman;
  return fr?.status === 'ok' && fr.testable === true
    && finite(tests.alpha) && tests.alpha > 0 && tests.alpha < 1
    && finite(fr.statistic) && fr.statistic >= 0
    && finite(fr.p_value) && fr.p_value >= 0 && fr.p_value <= 1
    && fr.significant === (fr.p_value < tests.alpha);
}

/** Never turn an absent, invalid or non-significant omnibus test into a CD claim. */
export function canShowCd(tests: Analysis['tests']): boolean {
  const fr = tests.friedman;
  const nem = tests.nemenyi;
  return hasValidFriedman(tests) && fr?.significant === true
    && finite(tests.alpha) && tests.alpha > 0 && tests.alpha < 1
    && finite(fr.statistic) && fr.statistic >= 0
    && finite(fr.p_value) && fr.p_value >= 0 && fr.p_value < tests.alpha
    && nem?.status === 'ok' && finite(nem.critical_difference) && nem.critical_difference > 0
    && Object.keys(nem.mean_ranks).length > 0 && Object.values(nem.mean_ranks).every(finite);
}
