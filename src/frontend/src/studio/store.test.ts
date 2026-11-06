import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Algorithm, Instance, Scenario } from '../lab/types';
import type { FleetInstance } from './types';

const mocks = vi.hoisted(() => ({
  saveScenario: vi.fn(),
  forkScenario: vi.fn(),
  solve: vi.fn(),
  cancelRun: vi.fn(),
  scenario: vi.fn(),
  runs: vi.fn(),
  run: vi.fn(),
  recommend: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock('../app/router', async (importOriginal) => ({ ...await importOriginal<typeof import('../app/router')>(), navigate: mocks.navigate }));
vi.mock('../lab/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lab/api')>();
  return {
    ...actual,
    lab: { ...actual.lab, solve: mocks.solve, cancelRun: mocks.cancelRun, scenario: mocks.scenario, runs: mocks.runs, run: mocks.run, recommend: mocks.recommend },
    events: { subscribe: vi.fn() },
  };
});
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return { ...actual, studio: { ...actual.studio, saveScenario: mocks.saveScenario, forkScenario: mocks.forkScenario } };
});

import { LabApiError } from '../lab/api';
import { flushSave, useStudio } from './store';

const instance: FleetInstance = {
  boundary: [[0, 0], [60, 0], [60, 40], [0, 40]],
  plots: [], obstacles: [], depot: [3, 15],
  tasks: [{ id: 1, xy: [20, 15], demand: 1, label: 'T1' }, { id: 2, xy: [40, 25], demand: 1, label: 'T2' }],
  vehicles: { count: 2, capacity: 4, speed_mps: 1, trips: 'multi', travel: 'free' },
  objective: 'total_distance', robot_radius: 0.5, units: 'm',
};
const scenario = { id: 'scenario-a', origin: 'user', problem_class: 'fleet_routing', instance } as unknown as Scenario;
const algorithm = { id: 'pyvrp.fleet', params: [{ name: 'time_limit_s' }, { name: 'seed' }] } as unknown as Algorithm;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function stored(body: { instance?: unknown }): Scenario {
  return { ...scenario, instance: body.instance } as unknown as Scenario;
}

async function microtasks(count = 12) {
  for (let i = 0; i < count; i++) await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('window', {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
  });
  useStudio.setState({
    scenarioId: scenario.id,
    scenario,
    draft: structuredClone(instance) as unknown as Instance,
    past: [], future: [], save: 'dirty', saveError: null, solveError: null,
    algorithms: [algorithm], chosen: [algorithm.id], budget: 3, seed: 42, paramOverrides: {},
    runIds: [], runs: {}, runErrors: {}, focusRun: null, manualFocus: false,
    recommendation: null, recommending: false, busy: null, toast: null,
  });
  mocks.solve.mockResolvedValue({ job_id: 'job-test', runs: [] });
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('serialized draft saving before solve', () => {
  it('finishes the second PUT before submitting the newest draft', async () => {
    const firstResponse = deferred<Scenario>();
    const secondResponse = deferred<Scenario>();
    mocks.saveScenario
      .mockImplementationOnce(() => firstResponse.promise)
      .mockImplementationOnce(() => secondResponse.promise);

    const firstFlush = flushSave();
    useStudio.getState().edit((draft) => ({ ...draft, depot: [10, 15], depots: [[10, 15]] } as unknown as Instance));
    const solving = useStudio.getState().solve();
    await microtasks();
    expect(mocks.saveScenario).toHaveBeenCalledTimes(1);

    firstResponse.resolve(stored({ instance: (useStudio.getState().scenario!.instance as unknown as FleetInstance) }));
    await microtasks();
    expect(mocks.saveScenario).toHaveBeenCalledTimes(2);
    expect(mocks.solve).not.toHaveBeenCalled();
    const secondBody = mocks.saveScenario.mock.calls[1][1];
    expect(secondBody.instance.depot).toEqual([10, 15]);
    expect(secondBody.instance.depots).toEqual([[10, 15]]);

    secondResponse.resolve(stored(secondBody));
    await Promise.all([firstFlush, solving]);
    expect(mocks.solve).toHaveBeenCalledTimes(1);
    expect(mocks.solve.mock.calls[0][2].depot).toEqual([10, 15]);
    expect(mocks.solve.mock.calls[0][2].depots).toEqual([[10, 15]]);
    expect(useStudio.getState().save).toBe('saved');
    expect((useStudio.getState().draft as unknown as FleetInstance).depot).toEqual([10, 15]);
  });

  it('keeps rejected capacity and demand input unchanged and blocks solve after a 422', async () => {
    const hetero = structuredClone(instance);
    hetero.vehicles.fleet = [
      { name: '小型', count: 1, capacity: 1, payload: 'load' },
      { name: '大型', count: 1, capacity: 10, payload: 'load' },
    ];
    hetero.tasks[1].demand = 10;
    useStudio.setState({ draft: hetero as unknown as Instance, save: 'dirty' });
    mocks.saveScenario.mockRejectedValue(new LabApiError('fleet[0] 容量 1 小于最大单点需求 10，该类车无法服务任何任务', 'invalid_instance', 422));

    await useStudio.getState().solve();

    expect(mocks.solve).not.toHaveBeenCalled();
    const current = useStudio.getState();
    expect(current.save).toBe('error');
    expect(current.solveError).toContain('提交被后端拒绝');
    expect(current.solveError).toContain('类别容量 [1, 10]');
    expect(current.solveError).toContain('任务需求 [1, 10]');
    expect((current.draft as unknown as FleetInstance).vehicles.fleet?.map((item) => item.capacity)).toEqual([1, 10]);
    expect((current.draft as unknown as FleetInstance).tasks.map((item) => item.demand)).toEqual([1, 10]);
  });

  it('does not switch away from a draft whose save failed', async () => {
    mocks.saveScenario.mockRejectedValue(new LabApiError('服务暂不可用', 'network', 0));
    await useStudio.getState().open('scenario-b');
    expect(useStudio.getState().scenarioId).toBe('scenario-a');
    expect(useStudio.getState().draft).not.toBeNull();
    expect(mocks.scenario).not.toHaveBeenCalled();
    expect(mocks.navigate).toHaveBeenCalledWith('#/plan/scenario-a', { replace: true });
  });

  it('reloads an updated agent map even when its id is unchanged', async () => {
    useStudio.setState({ save: 'saved' });
    mocks.scenario.mockResolvedValue({ ...scenario, revision: 2, instance: { ...instance, vehicles: { ...instance.vehicles, count: 4 } } });
    mocks.runs.mockResolvedValue({ runs: [] });
    mocks.recommend.mockResolvedValue({ ranking: [], recommended: null, policy: 'rules-v1' });
    expect(await useStudio.getState().open(scenario.id, { reload: true })).toBe(true);
    expect(useStudio.getState().scenario?.revision).toBe(2);
    expect((useStudio.getState().draft as unknown as FleetInstance).vehicles.count).toBe(4);
  });

  it.each(['dirty', 'saving', 'error'] as const)('preserves a %s draft when an agent asks to reload the same map', async (save) => {
    const draft = structuredClone(instance) as unknown as Instance;
    useStudio.setState({ draft, save });
    expect(await useStudio.getState().open(scenario.id, { reload: true })).toBe(false);
    expect(useStudio.getState().draft).toBe(draft);
    expect(useStudio.getState().save).toBe(save);
    expect(mocks.scenario).not.toHaveBeenCalled();
    expect(mocks.saveScenario).not.toHaveBeenCalled();
    expect(useStudio.getState().toast?.text).toContain('未保存');
  });

  it('submits recommended schema parameters and explicit overrides in addition to budget and seed', async () => {
    const full = {
      id: 'f2c.full',
      params: [
        { name: 'time_limit_s' }, { name: 'decomposition' },
        { name: 'swath_objective' }, { name: 'turn_model' },
      ],
    } as unknown as Algorithm;
    useStudio.setState({
      scenario: { ...scenario, problem_class: 'coverage' } as Scenario,
      draft: {} as Instance,
      algorithms: [full], chosen: [full.id], save: 'saved', budget: 3,
      paramOverrides: { 'f2c.full': { turn_model: 'reeds_shepp' } },
      recommendation: {
        ranking: [{ algorithm_id: full.id, suggested_params: {
          time_limit_s: 30, decomposition: 'boustrophedon', swath_objective: 'n_swath', turn_model: 'dubins',
        } }],
      } as never,
    });

    await useStudio.getState().solve();

    expect(mocks.solve.mock.calls[0][1]).toEqual([{
      id: 'f2c.full',
      params: { time_limit_s: 3, decomposition: 'boustrophedon', swath_objective: 'n_swath', turn_model: 'reeds_shepp' },
    }]);
  });

  it('stores the canceled state returned by the server', async () => {
    const canceled = { run_id: 'run_cancel', state: 'canceled', status: null };
    mocks.cancelRun.mockResolvedValue(canceled);

    await useStudio.getState().cancelRun('run_cancel');

    expect(mocks.cancelRun).toHaveBeenCalledWith('run_cancel');
    expect(useStudio.getState().runs.run_cancel).toEqual(canceled);
  });

  it('a late response from an earlier open of the same scene cannot replace a newer revision', async () => {
    const first = deferred<Scenario>(), second = deferred<Scenario>();
    useStudio.setState({ save: 'saved' });
    mocks.scenario.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    mocks.runs.mockResolvedValue({ runs: [] });
    mocks.recommend.mockResolvedValue({ ranking: [], recommended: null, policy: 'rules-v1' });
    const a = useStudio.getState().open('shared');
    const b = useStudio.getState().open('shared');
    expect(useStudio.getState().draft).toBeNull();
    second.resolve({ ...scenario, id: 'shared', revision: 2 });
    await b;
    first.resolve({ ...scenario, id: 'shared', revision: 1 });
    await a;
    expect(useStudio.getState().scenario?.revision).toBe(2);
  });

  it('a late history response cannot replace a newly submitted comparison', async () => {
    const history = deferred<{ runs: unknown[] }>();
    useStudio.setState({ save: 'saved' });
    mocks.scenario.mockResolvedValue({ ...scenario, id: 'next' });
    mocks.runs.mockReturnValueOnce(history.promise);
    mocks.recommend.mockResolvedValue({ ranking: [], recommended: null, policy: 'rules-v1' });
    const opening = useStudio.getState().open('next');
    await microtasks();
    useStudio.setState({ runIds: ['new-comparison'], busy: null });
    history.resolve({ runs: [{ run_id: 'old', job_id: 'past', state: 'done', routes: [] }] });
    await opening;
    expect(useStudio.getState().runIds).toEqual(['new-comparison']);
  });
});


describe('workbench request lifecycle', () => {
  it('submits only once when solve is clicked again during saving', async () => {
    const saved = deferred<Scenario>();
    mocks.saveScenario.mockReturnValueOnce(saved.promise);
    const first = useStudio.getState().solve();
    const second = useStudio.getState().solve();
    saved.resolve(scenario);
    await Promise.all([first, second]);
    expect(mocks.solve).toHaveBeenCalledTimes(1);
  });

  it('a failed old submission cannot overwrite the newly opened scenario', async () => {
    const result = deferred<never>();
    useStudio.setState({ save: 'saved' });
    mocks.solve.mockReturnValueOnce(result.promise);
    const submitting = useStudio.getState().solve(); await microtasks();
    mocks.scenario.mockResolvedValueOnce({ ...scenario, id: 'new-scene' });
    mocks.runs.mockResolvedValueOnce({ runs: [] });
    mocks.recommend.mockResolvedValue({ ranking: [], recommended: null });
    await useStudio.getState().open('new-scene');
    result.reject(new Error('old submission failed')); await submitting;
    expect(useStudio.getState()).toMatchObject({ scenarioId: 'new-scene', solveError: null, busy: null });
  });

  it('keeps polling failures visible, then clears them on recovery', async () => {
    useStudio.setState({ save: 'saved' });
    mocks.solve.mockResolvedValueOnce({ runs: ['run-new'] });
    mocks.run.mockRejectedValueOnce(new Error('gateway offline')).mockResolvedValueOnce({
      run_id: 'run-new', scenario_id: scenario.id, state: 'error', routes: [],
    });
    await useStudio.getState().solve(); await microtasks();
    expect(useStudio.getState().runErrors['run-new']).toContain('gateway offline');
    await vi.advanceTimersByTimeAsync(300);
    expect(useStudio.getState().runErrors).toEqual({});
    expect(useStudio.getState().runs['run-new'].state).toBe('error');
  });

  it('does not apply a poll response after leaving its comparison', async () => {
    const result = deferred<any>();
    useStudio.setState({ save: 'saved' });
    mocks.solve.mockResolvedValueOnce({ runs: ['run-old'] });
    mocks.run.mockReturnValueOnce(result.promise);
    await useStudio.getState().solve();
    useStudio.setState({ runIds: ['run-other'], runs: {} });
    result.resolve({ run_id: 'run-old', scenario_id: scenario.id, state: 'error', routes: [] });
    await microtasks();
    expect(useStudio.getState().runs).toEqual({});
    expect(mocks.run).toHaveBeenCalledTimes(1);
  });
});


it('allows a new scene to submit while an old-scene request is pending without releasing the new lock', async () => {
  const old = deferred<any>(), next = deferred<any>();
  useStudio.setState({ save: 'saved' });
  mocks.solve.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
  const first = useStudio.getState().solve(); await microtasks();
  mocks.scenario.mockResolvedValueOnce({ ...scenario, id: 'new-scene-concurrent' });
  mocks.runs.mockResolvedValueOnce({ runs: [] });
  mocks.recommend.mockResolvedValue({ ranking: [], recommended: null });
  await useStudio.getState().open('new-scene-concurrent');
  useStudio.getState().setChosen([algorithm.id]);
  const second = useStudio.getState().solve(); await microtasks();
  expect(mocks.solve).toHaveBeenCalledTimes(2);
  old.resolve({ runs: [] }); await first;
  await useStudio.getState().solve();
  expect(mocks.solve).toHaveBeenCalledTimes(2);
  next.resolve({ runs: [] }); await second;
  expect(useStudio.getState().scenarioId).toBe('new-scene-concurrent');
});
