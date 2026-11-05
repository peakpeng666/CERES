import { describe, expect, it } from 'vitest';
import type { Algorithm, ProblemClass, ScenarioSummary } from '../lab/types';
import { studyRunUpperBound } from './studyDesign';
const scenario = (id: string, problem_class: ProblemClass, task_count = 1) => ({ id, problem_class, task_count }) as ScenarioSummary;
const algorithm = (id: string, uses_seed: boolean, extra: Partial<Algorithm> = {}) => ({
  id, available: true, problem_classes: ['euclidean_cvrp'], limits: {}, randomness: { uses_seed, note: '' },
  params: [{ name: 'seed', type: 'integer', default: 42 }], ...extra,
}) as Algorithm;
const algorithms = [algorithm('pyvrp.ils', true), algorithm('ortools.gls', false), algorithm('no-seed-baseline', false, { params: [] })];
describe('study seed-only design count', () => {
  it('matches the backend three-algorithm / three-seed fixture: 3 + 1 + 1', () => {
    expect(studyRunUpperBound(['fixture'], [scenario('fixture', 'euclidean_cvrp')], algorithms, 1, 3)).toBe(5);
    expect(studyRunUpperBound(['fixture'], [scenario('fixture', 'euclidean_cvrp')], algorithms, 2, 3)).toBe(10);
  });
  it('filters mismatches, unavailable algorithms and known task limits like the backend', () => {
    const rows = [...algorithms, algorithm('unavailable', true, { available: false }), algorithm('limited', true, { limits: { max_tasks: 1 } })];
    expect(studyRunUpperBound(['fixture', 'field'], [scenario('fixture', 'euclidean_cvrp', 2), scenario('field', 'coverage')], rows, 0, 3)).toBe(5);
  });
  it('keeps unopened CVRPLIB a conservative upper bound without inventing its size', () => {
    expect(studyRunUpperBound(['cvrplib-x-test'], [], algorithms, 1, 3)).toBe(5);
    expect(studyRunUpperBound([], [], algorithms, 1, 3)).toBe(0);
  });
});
