import { create } from 'zustand';
import { navigate } from '../app/router';
import { events, lab, LabApiError } from '../lab/api';
import type { Algorithm, Instance, Recommendation, Scenario, Solution } from '../lab/types';
import { studio } from './api';
import type { FleetInstance, ParseResult, Selection, Tool, ObstacleKind } from './types';
import { normalizeFleetInstance } from './fleet';
import { filterRecommendation, planningBlockReason } from './capabilities';
import { comparable, verifiedPlan } from './comparison';

export type Workspace = 'agent' | 'plan' | 'assets' | 'experiments' | 'sim' | 'library' | 'system';

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';

interface StudioState {
  workspace: Workspace;
  scenarioId: string | null;
  editorSession: string | null;
  scenario: Scenario | null;
  loadError: string | null;
  draft: Instance | null;
  past: Instance[];
  future: Instance[];
  save: SaveState;
  saveError: string | null;
  solveError: string | null;
  tool: Tool;
  obstacleKind: ObstacleKind;
  selection: Selection;
  algorithms: Algorithm[];
  recommendation: Recommendation | null;
  recommending: boolean;
  chosen: string[];
  budget: number;
  seed: number;
  paramOverrides: Record<string, Record<string, number | string | boolean>>;
  runIds: string[];
  runs: Record<string, Solution>;
  focusRun: string | null;
  manualFocus: boolean;
  hiddenVehicles: string[];
  playback: number | null;
  composeText: string;
  parse: ParseResult | null;
  busy: string | null;
  toast: { tone: 'ok' | 'error' | 'info'; text: string } | null;
  cursor: [number, number] | null;
  paletteOpen: boolean;
  codePath: string | null;
  studyId: string | null;

  setWorkspace: (w: Workspace) => void;
  open: (id: string, options?: { reload?: boolean }) => Promise<boolean>;
  edit: (fn: (inst: Instance) => Instance) => void;
  undo: () => void;
  redo: () => void;
  setTool: (t: Tool) => void;
  setObstacleKind: (k: ObstacleKind) => void;
  select: (s: Selection) => void;
  setChosen: (ids: string[]) => void;
  setBudget: (s: number) => void;
  setSeed: (seed: number) => void;
  setAlgorithmParam: (algorithmId: string, name: string, value: number | string | boolean) => void;
  refreshRecommendation: () => Promise<void>;
  solve: (ids?: string[]) => Promise<void>;
  cancelRun: (runId: string) => Promise<void>;
  focus: (runId: string | null) => void;
  toggleVehicle: (name: string) => void;
  setPlayback: (t: number | null) => void;
  setComposeText: (t: string) => void;
  composeFromText: (opts?: { field_id?: string }) => Promise<void>;
  notify: (tone: 'ok' | 'error' | 'info', text: string) => void;
  setCursor: (p: [number, number] | null) => void;
  setPalette: (open: boolean) => void;
  openCode: (path: string | null) => void;
  setStudy: (id: string | null) => void;
  loadAlgorithms: () => Promise<void>;
}

const HISTORY = 120;
let saveTimer: number | undefined;
let recommendTimer: number | undefined;
let draftRevision = 0;
let loadSequence = 0;
let saveInFlight: Promise<void> | null = null;

const message = (e: unknown) => (e instanceof LabApiError || e instanceof Error ? e.message : String(e));

function withFleetContext(text: string, inst: FleetInstance): string {
  const classes = inst.vehicles.fleet ?? [];
  if (classes.length < 2 || !/容量|需求|capacity|demand|服务任何任务/i.test(text)) return text;
  const capacities = classes.map((item) => item.capacity);
  const demands = inst.tasks.map((item) => item.demand);
  return `${text}\n提交被后端拒绝，请核对容量与任务需求。类别容量 [${capacities.join(', ')}]、任务需求 [${demands.join(', ')}] 均保留；未自动抬高容量或改派任务。`;
}

const draftOf = (scenario: Scenario): Instance => scenario.problem_class === 'fleet_routing'
  ? normalizeFleetInstance(scenario.instance as unknown as FleetInstance) as unknown as Instance
  : scenario.instance;

export const useStudio = create<StudioState>((set, get) => ({
  workspace: 'agent',
  scenarioId: null,
  editorSession: null,
  scenario: null,
  loadError: null,
  draft: null,
  past: [],
  future: [],
  save: 'idle',
  saveError: null,
  solveError: null,
  tool: 'select',
  obstacleKind: 'pond',
  selection: null,
  algorithms: [],
  recommendation: null,
  recommending: false,
  chosen: [],
  budget: 3,
  seed: 42,
  paramOverrides: {},
  runIds: [],
  runs: {},
  focusRun: null,
  manualFocus: false,
  hiddenVehicles: [],
  playback: null,
  composeText: '',
  parse: null,
  busy: null,
  toast: null,
  cursor: null,
  paletteOpen: false,
  codePath: null,
  studyId: null,

  setWorkspace: (workspace) => set({ workspace }),

  async open(id, options) {
    if (get().scenarioId === id && get().scenario && !options?.reload) return true;
    const previous = get();
    if (options?.reload && previous.scenarioId === id && previous.draft && ['dirty', 'saving', 'error'].includes(previous.save)) {
      previous.notify('error', '当前地图有未保存的修改，已保留草稿；请先处理草稿，再重新打开智能体会话以载入更新后的地图。');
      return false;
    }
    if (previous.scenarioId && previous.draft && previous.save === 'error') {
      previous.notify('error', `当前草稿尚未保存，已留在本场景：${previous.saveError ?? '请先重试保存'}`);
      navigate(`#/plan/${encodeURIComponent(previous.scenarioId)}`, { replace: true });
      return false;
    }
    if (previous.scenarioId && previous.draft && ['dirty', 'saving'].includes(previous.save)) {
      await flushSave();
      if (get().save !== 'saved') {
        get().notify('error', `当前草稿尚未保存，已留在本场景：${get().saveError ?? '请重试保存'}`);
        navigate(`#/plan/${encodeURIComponent(previous.scenarioId!)}`, { replace: true });
        return false;
      }
    }
    draftRevision += 1;
    const loadToken = ++loadSequence;
    window.clearTimeout(saveTimer);
    window.clearTimeout(recommendTimer);
    set({ scenarioId: id, scenario: null, draft: null, editorSession: id, loadError: null, busy: '载入场景', selection: null, past: [], future: [], save: 'idle', saveError: null, solveError: null, chosen: [],
          runIds: [], runs: {}, focusRun: null, manualFocus: false, recommendation: null, playback: null, hiddenVehicles: [], paramOverrides: {} });
    try {
      const sc = await lab.scenario(id);
      if (get().scenarioId !== id || loadSequence !== loadToken) return false;
      set({ scenario: sc, draft: draftOf(sc), busy: null });
      void get().refreshRecommendation();
      const recent = await lab.runs({ scenario_id: id, limit: 30 });
      if (get().scenarioId !== id || loadSequence !== loadToken) return false;
      if (get().runIds.length || get().busy === '求解中') return true;
      const runs = Object.fromEntries(recent.runs.map((r) => [r.run_id, r]));
      const latestJob = recent.runs[0]?.job_id;
      const ids = recent.runs.filter((r) => r.job_id === latestJob).map((r) => r.run_id);
      set({ runs, runIds: ids, focusRun: pickBest(ids.map((i) => runs[i])) });
      return true;
    } catch (e) {
      if (get().scenarioId === id && loadSequence === loadToken) set({ loadError: message(e), busy: null });
      return false;
    }
  },

  edit(fn) {
    const { draft, past, scenario } = get();
    if (!draft) return;
    const changed = fn(draft);
    if (changed === draft) return;
    const next = scenario?.problem_class === 'fleet_routing'
      ? normalizeFleetInstance(changed as unknown as FleetInstance) as unknown as Instance
      : changed;
    draftRevision += 1;
    set({ draft: next, past: [...past.slice(-HISTORY), draft], future: [], save: 'dirty', saveError: null });
    scheduleSave();
    scheduleRecommend();
  },

  undo() {
    const { past, draft, future } = get();
    if (!past.length || !draft) return;
    draftRevision += 1;
    set({ draft: past[past.length - 1], past: past.slice(0, -1), future: [draft, ...future], save: 'dirty', saveError: null });
    scheduleSave();
    scheduleRecommend();
  },

  redo() {
    const { past, draft, future } = get();
    if (!future.length || !draft) return;
    draftRevision += 1;
    set({ draft: future[0], future: future.slice(1), past: [...past, draft], save: 'dirty', saveError: null });
    scheduleSave();
    scheduleRecommend();
  },

  setTool: (tool) => set({ tool }),
  setObstacleKind: (obstacleKind) => set({ obstacleKind, tool: 'obstacle' }),
  select: (selection) => set({ selection }),
  setChosen: (chosen) => set({ chosen }),
  setBudget: (budget) => set({ budget }),
  setSeed: (seed) => set({ seed }),
  setAlgorithmParam: (algorithmId, name, value) => set((current) => ({
    paramOverrides: { ...current.paramOverrides, [algorithmId]: { ...current.paramOverrides[algorithmId], [name]: value } },
  })),

  async refreshRecommendation() {
    const { scenario, draft } = get();
    if (!scenario || !draft) return;
    const revision = draftRevision;
    set({ recommending: true });
    try {
      const raw = await lab.recommend(scenario.id, draft);
      if (get().scenario?.id !== scenario.id || draftRevision !== revision) return;
      const rec = scenario.problem_class === 'fleet_routing'
        ? filterRecommendation(raw, normalizeFleetInstance(draft as unknown as FleetInstance))
        : raw;
      const compare = (rec as Recommendation & { compare?: string[] }).compare ?? (rec.recommended ? [rec.recommended] : []);
      set((s) => {
        const kept = s.chosen.filter((id) => rec.ranking.some((r) => r.algorithm_id === id && r.applicable));
        return { recommendation: rec, recommending: false, chosen: kept.length ? kept : compare };
      });
      const hint = rec.ranking.find((r) => r.algorithm_id === rec.recommended)?.suggested_params?.time_limit_s;
      if (typeof hint === 'number' && get().budget === 3) set({ budget: hint });
    } catch (e) {
      if (get().scenario?.id !== scenario.id || draftRevision !== revision) return;
      set({ recommending: false });
      get().notify('error', `推荐失败：${message(e)}`);
    }
  },

  async solve(ids) {
    const { scenario, draft, chosen } = get();
    const list = ids ?? chosen;
    if (!scenario || !draft || !list.length) return;
    set({ solveError: null });
    await flushSave();
    const current = get();
    if (current.save === 'error' || current.save === 'dirty' || current.save === 'saving') {
      const fleet = current.scenario?.problem_class === 'fleet_routing' && current.draft
        ? normalizeFleetInstance(current.draft as unknown as FleetInstance)
        : null;
      const raw = current.save === 'error'
        ? `场景尚未保存，未提交求解：${current.saveError ?? '请重试保存'}`
        : '最新草稿仍在保存，未提交求解；保存完成后可重试';
      const detail = fleet ? withFleetContext(raw, fleet) : raw;
      set({ solveError: detail });
      get().notify('error', detail);
      return;
    }
    if (!current.scenario || !current.draft) return;
    if (current.scenario.problem_class === 'fleet_routing') {
      const fleet = normalizeFleetInstance(current.draft as unknown as FleetInstance);
      const blocked = list.map((id) => planningBlockReason(fleet, id)).find(Boolean);
      if (blocked) {
        const detail = `未提交求解：${blocked}`;
        set({ solveError: detail });
        get().notify('error', detail);
        return;
      }
    }
    const submitted = current.scenario.problem_class === 'fleet_routing'
      ? normalizeFleetInstance(current.draft as unknown as FleetInstance) as unknown as Instance
      : current.draft;
    const scenarioId = current.scenario.id;
    set({ busy: '求解中' });
    try {
      const specs = list.map((id) => {
        const a = current.algorithms.find((x) => x.id === id);
        const suggested = current.recommendation?.ranking.find((r) => r.algorithm_id === id)?.suggested_params;
        const overridden = current.paramOverrides[id] ?? {};
        const params: Record<string, unknown> = {};
        for (const p of a?.params ?? []) {
          if (overridden[p.name] !== undefined) params[p.name] = overridden[p.name];
          else if (suggested?.[p.name] !== undefined) params[p.name] = suggested[p.name];
        }
        if (a?.params.some((p) => p.name === 'time_limit_s')) params.time_limit_s = current.budget;
        if (a?.params.some((p) => p.name === 'seed')) params.seed = current.seed;
        return { id, params };
      });
      const job = await lab.solve(scenarioId, specs, submitted, '工作台对比');
      if (get().scenario?.id !== scenarioId) return;
      set({ runIds: job.runs, focusRun: null, manualFocus: false, busy: null, playback: null });
      for (const id of job.runs) void pollRun(id);
    } catch (e) {
      const detail = current.scenario.problem_class === 'fleet_routing'
        ? withFleetContext(message(e), submitted as unknown as FleetInstance)
        : message(e);
      set({ busy: null, solveError: detail });
      get().notify('error', detail);
    }
  },

  async cancelRun(runId) {
    const canceled = await lab.cancelRun(runId);
    set((current) => ({ runs: { ...current.runs, [runId]: canceled } }));
  },

  focus: (focusRun) => set({ focusRun, manualFocus: true, playback: null }),
  toggleVehicle: (name) => set((s) => ({ hiddenVehicles: s.hiddenVehicles.includes(name) ? s.hiddenVehicles.filter((v) => v !== name) : [...s.hiddenVehicles, name] })),
  setPlayback: (playback) => set({ playback }),
  setComposeText: (composeText) => set({ composeText }),

  async composeFromText(opts) {
    const text = get().composeText.trim();
    if (!text && !opts?.field_id) return;
    set({ busy: '生成地图' });
    try {
      const sc = await studio.compose(text, opts);
      set({ busy: null });
      location.hash = `#/plan/${encodeURIComponent(sc.id)}`;
      get().notify('ok', `已生成：${sc.summary}`);
    } catch (e) {
      set({ busy: null });
      get().notify('error', message(e));
    }
  },

  notify(tone, text) {
    set({ toast: { tone, text } });
    window.setTimeout(() => {
      if (get().toast?.text === text) set({ toast: null });
    }, tone === 'error' ? 6000 : 3200);
  },

  setCursor: (cursor) => set({ cursor }),
  setPalette: (paletteOpen) => set({ paletteOpen }),
  openCode: (codePath) => set({ codePath }),
  setStudy: (studyId) => set({ studyId }),

  async loadAlgorithms() {
    try {
      const { algorithms } = await lab.algorithms();
      set({ algorithms });
    } catch (e) {
      get().notify('error', `算法库读取失败：${message(e)}`);
    }
  },
}));

export function pickBest(runs: (Solution | undefined)[]): string | null {
  const done = runs.filter((r): r is Solution => !!r && r.state === 'done' && !!r.routes.length);
  const anchor = done.find(verifiedPlan) ?? done[0];
  if (!anchor) return null;
  const group = done.filter((r) => comparable(r, anchor));
  group.sort((a, b) => Number(!verifiedPlan(a)) - Number(!verifiedPlan(b)) || (a.objective.value ?? Infinity) - (b.objective.value ?? Infinity));
  return group[0]?.run_id ?? anchor.run_id;
}

async function pollRun(id: string) {
  for (let i = 0; i < 2400; i++) {
    try {
      const r = await lab.run(id);
      useStudio.setState((s) => ({ runs: { ...s.runs, [id]: r } }));
      if (!['queued', 'running'].includes(r.state)) {
        const s = useStudio.getState();
        if (!s.manualFocus) useStudio.setState({ focusRun: pickBest(s.runIds.map((x) => s.runs[x])) });
        return;
      }
    } catch {
      /* transient: retry */
    }
    await new Promise((r) => setTimeout(r, i < 20 ? 300 : 1000));
  }
}

/** Show a given set of runs (for example those an agent session produced) on the map, comparison and checks. */
export function showRuns(ids: string[]) {
  const s = useStudio.getState();
  if (!ids.length) return;
  const same = ids.length === s.runIds.length && ids.every((id, i) => s.runIds[i] === id);
  if (same) return;
  useStudio.setState({ runIds: ids, focusRun: null, manualFocus: false, playback: null });
  for (const id of ids) void pollRun(id);
}

events.subscribe((type, data) => {
  if (type !== 'run') return;
  const r = data as Solution;
  const s = useStudio.getState();
  if (s.runIds.includes(r.run_id)) useStudio.setState({ runs: { ...s.runs, [r.run_id]: r } });
});

function scheduleSave() {
  useStudio.setState({ solveError: null });
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => void flushSave(), 700);
}

function scheduleRecommend() {
  window.clearTimeout(recommendTimer);
  recommendTimer = window.setTimeout(() => void useStudio.getState().refreshRecommendation(), 900);
}

/** Persist the draft: user scenarios are updated in place; built-in/library ones are first copied to "我的场景". */
export async function flushSave(): Promise<void> {
  window.clearTimeout(saveTimer);
  while (true) {
    if (saveInFlight) {
      await saveInFlight;
      continue;
    }
    const { scenario, draft, save } = useStudio.getState();
    if (!scenario || !draft || save !== 'dirty') return;
    const revision = draftRevision;
    const originalId = scenario.id;
    const submitted = scenario.problem_class === 'fleet_routing'
      ? normalizeFleetInstance(draft as unknown as FleetInstance) as unknown as Instance
      : draft;
    const operation = (async () => {
      useStudio.setState({ save: 'saving', saveError: null });
      try {
        const origin = (scenario as Scenario & { origin?: string }).origin;
        let stored: Scenario;
        if (origin === 'user') {
          stored = (await studio.saveScenario(originalId, { instance: submitted, base_revision: scenario.revision ?? 0 })) as unknown as Scenario;
        } else {
          stored = await studio.forkScenario(originalId, { instance: submitted });
        }
        const current = useStudio.getState();
        if (current.scenarioId !== originalId && current.scenarioId !== stored.id) return;
        if (origin !== 'user' && current.scenarioId === originalId) {
          useStudio.setState({ scenarioId: stored.id });
          navigate(`#/plan/${encodeURIComponent(stored.id)}`, { replace: true });
          useStudio.getState().notify('info', '已另存为“我的场景”，之后的修改会自动保存');
        }
        if (draftRevision === revision && useStudio.getState().draft === draft) {
          useStudio.setState({ save: 'saved', scenario: stored, draft: draftOf(stored), saveError: null });
        } else {
          useStudio.setState({ save: 'dirty', scenario: stored });
        }
      } catch (e) {
        const current = useStudio.getState();
        if (current.scenarioId !== originalId) return;
        if (draftRevision !== revision) {
          useStudio.setState({ save: 'dirty', saveError: message(e) });
        } else {
          useStudio.setState({ save: 'error', saveError: message(e) });
          if (e instanceof LabApiError && e.status === 0) {
            saveTimer = window.setTimeout(() => {
              if (draftRevision !== revision || useStudio.getState().save !== 'error') return;
              useStudio.setState({ save: 'dirty' });
              void flushSave();
            }, 3000);
          }
        }
      }
    })();
    saveInFlight = operation;
    try {
      await operation;
    } finally {
      if (saveInFlight === operation) saveInFlight = null;
    }
    if (useStudio.getState().save !== 'dirty') return;
  }
}

export async function retrySave(): Promise<void> {
  const { scenario, draft } = useStudio.getState();
  if (!scenario || !draft) return;
  useStudio.setState({ save: 'dirty' });
  await flushSave();
}

/** Explicit recovery for a conflicting draft: preserve both server and local work. */
export async function saveDraftCopy(): Promise<void> {
  const { scenario, draft } = useStudio.getState();
  if (!scenario || !draft) return;
  const revision = draftRevision;
  const copy = await studio.forkScenario(scenario.id, { instance: draft, title: `${scenario.title}（草稿副本）` });
  if (useStudio.getState().scenarioId !== scenario.id) return;
  useStudio.setState({ scenarioId: copy.id, scenario: copy, saveError: null,
    ...(draftRevision === revision ? { draft: draftOf(copy), save: 'saved' as const } : { save: 'dirty' as const }) });
  navigate(`#/plan/${encodeURIComponent(copy.id)}`, { replace: true });
  useStudio.getState().notify('info', '已另存草稿副本；服务器上的较新场景保留');
  if (useStudio.getState().save === 'dirty') scheduleSave();
}

export const isFleet = (sc: Scenario | null): boolean => sc?.problem_class === 'fleet_routing';
export const asFleet = (inst: Instance | null) => inst as unknown as FleetInstance;
