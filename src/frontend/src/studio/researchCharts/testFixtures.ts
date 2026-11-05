/** Shared test fixtures for research chart tests (test-only; not imported by the app). */
import type { Solution } from '../../lab/types';
import type { Analysis } from '../types';

export const analysis = (over: Partial<Analysis> = {}): Analysis => ({
  empty: false, n_runs: 6, n_instances: 2, n_complete_instances: 2, algorithms: ['a', 'b'],
  summary: [{ algorithm: 'a', name: 'Alg A' }, { algorithm: 'b', name: 'Alg B' }] as Analysis['summary'],
  per_instance: [], tests: { alpha: 0.05, k: 2, n: 2, wilcoxon_holm: [] },
  performance_profile: { taus: [1, 1.5, 2], curves: { a: [0.5, 1, 1], b: [0.5, 0.5, 0.5] } },
  ecdf: {}, anytime: { grid: [], median: {} }, scatter: [], reference_note: 'ref', ...over,
}) as Analysis;


export const solved = (over: Record<string, unknown> = {}) => ({
  run_id: 'r', scenario_id: 's', problem_class: 'euclidean_cvrp', instance_sha256: 'h', frame: { kind: 'benchmark', units: 'unitless' },
  state: 'done', status: 'feasible', check: { valid: true, errors: [], computed_cost: 10 }, map_check: null, routes: [{}],
  objective: { value: 10, unit: 'euclidean', lower_bound: null }, metrics: { wall_s: 1 }, params: { seed: 1, time_limit_s: 2 },
  algorithm: { id: 'alg', name: 'Alg', version: '1' }, process: { source_identity_verified: true },
  trace_contract: { semantic: 'reported_objective', note: '' }, trace: [[0.01, 20], [1, 10]], ...over }) as unknown as Solution;
