import type { Algorithm, ScenarioSummary } from '../lab/types';

/** Same seed-only expansion as StudyManager.plan; unopened CVRPLIB sizes remain an upper bound. */
export function studyRunUpperBound(
  picked: string[], scenarios: ScenarioSummary[], algorithms: Algorithm[], budgetCount: number, seedCount: number,
): number {
  let cells = 0;
  for (const id of picked) {
    const scenario = scenarios.find((s) => s.id === id);
    const problemClass = scenario?.problem_class ?? (id.startsWith('cvrplib-') ? 'euclidean_cvrp' : null);
    for (const algorithm of algorithms) {
      if (!algorithm.available || (problemClass && !algorithm.problem_classes.includes(problemClass))) continue;
      const limitedTasks = scenario && (problemClass === 'turn_routing' || problemClass === 'euclidean_cvrp') ? scenario.task_count : 0;
      if (algorithm.limits.max_tasks && limitedTasks > algorithm.limits.max_tasks) continue;
      cells += algorithm.randomness?.uses_seed === true ? seedCount : 1;
    }
  }
  return cells * Math.max(1, budgetCount);
}
