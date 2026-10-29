import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DockviewReact, themeLight, type DockviewApi, type DockviewReadyEvent } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { Command } from 'cmdk';
import {
  BookOpen,
  Bot,
  Boxes,
  ChevronDown,
  Command as CommandIcon,
  FlaskConical,
  LayoutGrid,
  ListRestart,
  Map as MapIcon,
  MessagesSquare,
  Server,
  Sparkles,
  Upload,
  Wand2,
} from 'lucide-react';
import type { Route } from '../app/router';
import { lab, useGatewayConnected, useLabResource } from '../lab/api';
import { Button, Segmented, Spinner, StatusDot } from '../lab/ui';
import { simPhaseLabel } from '../lab/shell/labels';
import { ViewErrorBoundary } from '../lab/shell/Page';
import { studio } from './api';
import { flushSave, useStudio, type Workspace } from './store';
import { removeDepot } from './fleet';
import { useAssets } from './assets/store';
import { useFieldSim } from './ros/live';
import { ReplayPanel } from './experiment/ReplayPanel';
import type { FleetInstance, ParseResult } from './types';
import { AdvisorPanel, ComparePanel, InspectorPanel, MapPanel, VerifyPanel } from './panels/PlanPanels';
import { ScenarioNavigator } from './panels/LibraryPanels';
import { ConsolePanel, LegacyPlan, RunsPanel, ServicesPanel, SimPanel } from './panels/SystemPanels';
import { AgentChatPanel, ModelChip } from '../agent/AgentChat';
import { useAgent } from '../agent/store';

const lib = () => import('./panels/LibraryPanels');
const research = () => import('./panels/ResearchPanels');
const AlgorithmsPanel = lazy(() => lib().then((m) => ({ default: m.AlgorithmsPanel })));
const CodePanel = lazy(() => lib().then((m) => ({ default: m.CodePanel })));
const CvrplibPanel = lazy(() => lib().then((m) => ({ default: m.CvrplibPanel })));
const FieldsPanel = lazy(() => lib().then((m) => ({ default: m.FieldsPanel })));
const ReferencesPanel = lazy(() => lib().then((m) => ({ default: m.ReferencesPanel })));
const StudiesPanel = lazy(() => research().then((m) => ({ default: m.StudiesPanel })));
const FieldSimPanel = lazy(() => import('./panels/FieldSimPanel').then((m) => ({ default: m.FieldSimPanel })));
const RosView = lazy(() => import('./ros/RosScene').then((m) => ({ default: m.RosView })));
const GazeboView = lazy(() => import('./ros/GazeboView').then((m) => ({ default: m.GazeboView })));
const TelemetryView = lazy(() => import('./ros/Telemetry').then((m) => ({ default: m.TelemetryView })));
const AnalysisPanel = lazy(() => research().then((m) => ({ default: m.AnalysisPanel })));
const AssetLibrary = lazy(() => import('./assets/AssetLibrary').then((m) => ({ default: m.AssetLibrary })));
const AssetPreview = lazy(() => import('./assets/AssetPreview').then((m) => ({ default: m.AssetPreview })));
const AssetInspector = lazy(() => import('./assets/AssetInspector').then((m) => ({ default: m.AssetInspector })));
const AgentTaskPanel = lazy(() => import('../agent/TaskPanel').then((m) => ({ default: m.AgentTaskPanel })));
const ImportWizard = lazy(() => import('./assets/ImportWizard').then((m) => ({ default: m.ImportWizard })));
const MapToTaskMap = lazy(() => import('./assets/MapToTaskMap').then((m) => ({ default: m.MapToTaskMap })));
const ExperimentPanel = lazy(() => import('./experiment/ExperimentPanel').then((m) => ({ default: m.ExperimentPanel })));
const DecisionPanel = lazy(() => import('./experiment/DecisionPanel').then((m) => ({ default: m.DecisionPanel })));
const FiguresPanel = lazy(() => import('./figures/FiguresPanel').then((m) => ({ default: m.FiguresPanel })));
import './dock.css';
import s from './studio.module.css';

const PANELS = {
  agentChat: { title: '智能体', C: AgentChatPanel },
  agentTask: { title: '任务与执行', C: AgentTaskPanel },
  map: { title: '地图', C: MapPanel },
  inspector: { title: '检查器', C: InspectorPanel },
  advisor: { title: '推荐与求解', C: AdvisorPanel },
  compare: { title: '方案对比', C: ComparePanel },
  verify: { title: '验算', C: VerifyPanel },
  results: { title: '实验结果', C: ExperimentPanel },
  decision: { title: '决策记录', C: DecisionPanel },
  replay: { title: '仿真回放', C: ReplayPanel },
  figures: { title: '科研图表', C: FiguresPanel },
  assets: { title: '资产库', C: AssetLibrary },
  assetPreview: { title: '资产预览', C: AssetPreview },
  assetInspector: { title: '资产检查器', C: AssetInspector },
  scenarios: { title: '场景库', C: ScenarioNavigator },
  algorithms: { title: '算法库', C: AlgorithmsPanel },
  cvrplib: { title: 'CVRPLIB', C: CvrplibPanel },
  fields: { title: '真实田块', C: FieldsPanel },
  references: { title: '文献', C: ReferencesPanel },
  code: { title: '源码', C: CodePanel },
  studies: { title: '课题', C: StudiesPanel },
  analysis: { title: '统计分析', C: AnalysisPanel },
  fieldsim: { title: '田间仿真', C: FieldSimPanel },
  rosview: { title: 'ROS 视图', C: RosView },
  gazebo: { title: 'Gazebo 场景', C: GazeboView },
  telemetry: { title: '遥测', C: TelemetryView },
  sim: { title: 'TurtleBot4 仿真', C: SimPanel },
  runs: { title: '运行记录', C: RunsPanel },
  services: { title: '服务', C: ServicesPanel },
  console: { title: '日志', C: ConsolePanel },
  classic: { title: '经典视图', C: ClassicPanel },
} as const;
type PanelId = keyof typeof PANELS;

function ClassicPanel() {
  const id = useStudio((x) => x.scenarioId);
  return id ? <LegacyPlan id={id} /> : null;
}

const COMPONENTS = Object.fromEntries(Object.entries(PANELS).map(([id, p]) => [id, () => (
  <ViewErrorBoundary name={p.title}><Suspense fallback={<div style={{ padding: 14 }}><Spinner /></div>}><p.C /></Suspense></ViewErrorBoundary>
)]));

const WORKSPACES: { value: Workspace; label: string; icon: typeof MapIcon }[] = [
  { value: 'agent', label: '智能体', icon: MessagesSquare },
  { value: 'plan', label: '任务规划', icon: MapIcon },
  { value: 'assets', label: '资产库', icon: Boxes },
  { value: 'experiments', label: '算法实验', icon: FlaskConical },
  { value: 'sim', label: '仿真', icon: Bot },
  { value: 'library', label: '资料', icon: BookOpen },
  { value: 'system', label: '系统', icon: Server },
];

type Step = { id: PanelId; ref?: PanelId; dir?: 'left' | 'right' | 'above' | 'below' | 'within'; w?: number; h?: number; inactive?: boolean };
const LAYOUTS: Record<Workspace, Step[]> = {
  agent: [
    { id: 'map' },
    { id: 'agentChat', ref: 'map', dir: 'left', w: 430 },
    { id: 'agentTask', ref: 'map', dir: 'right', w: 370 },
    { id: 'compare', ref: 'map', dir: 'below', h: 230 },
    { id: 'verify', ref: 'compare', dir: 'within', inactive: true },
    { id: 'results', ref: 'compare', dir: 'within', inactive: true },
    { id: 'replay', ref: 'compare', dir: 'within', inactive: true },
  ],
  plan: [
    { id: 'map' },
    { id: 'scenarios', ref: 'map', dir: 'left', w: 250 },
    { id: 'advisor', ref: 'map', dir: 'right', w: 360 },
    { id: 'inspector', ref: 'advisor', dir: 'within', inactive: true },
    { id: 'decision', ref: 'advisor', dir: 'within', inactive: true },
    { id: 'compare', ref: 'map', dir: 'below', h: 250 },
    { id: 'verify', ref: 'compare', dir: 'within', inactive: true },
    { id: 'results', ref: 'compare', dir: 'within', inactive: true },
    { id: 'replay', ref: 'compare', dir: 'within', inactive: true },
    { id: 'figures', ref: 'compare', dir: 'within', inactive: true },
    { id: 'console', ref: 'compare', dir: 'within', inactive: true },
  ],
  assets: [
    { id: 'assetPreview' },
    { id: 'assets', ref: 'assetPreview', dir: 'left', w: 380 },
    { id: 'assetInspector', ref: 'assetPreview', dir: 'right', w: 400 },
  ],
  experiments: [
    { id: 'studies' },
    { id: 'analysis', ref: 'studies', dir: 'right', w: 640 },
    { id: 'figures', ref: 'analysis', dir: 'within', inactive: true },
    { id: 'algorithms', ref: 'studies', dir: 'within', inactive: true },
  ],
  sim: [
    { id: 'rosview' },
    { id: 'fieldsim', ref: 'rosview', dir: 'left', w: 390 },
    { id: 'sim', ref: 'fieldsim', dir: 'within', inactive: true },
    { id: 'gazebo', ref: 'rosview', dir: 'within', inactive: true },
    { id: 'telemetry', ref: 'rosview', dir: 'below', h: 230 },
    { id: 'console', ref: 'telemetry', dir: 'within', inactive: true },
  ],
  library: [
    { id: 'cvrplib' },
    { id: 'fields', ref: 'cvrplib', dir: 'within', inactive: true },
    { id: 'references', ref: 'cvrplib', dir: 'within', inactive: true },
    { id: 'algorithms', ref: 'cvrplib', dir: 'right', w: 380 },
    { id: 'code', ref: 'algorithms', dir: 'within', inactive: true },
  ],
  system: [
    { id: 'services' },
    { id: 'runs', ref: 'services', dir: 'within', inactive: true },
    { id: 'console', ref: 'services', dir: 'below', h: 260 },
  ],
};
/** Panels that keep rendering while hidden (map editor state, WebGL contexts, live sockets). */
const ALWAYS = new Set<PanelId>(['map', 'rosview', 'gazebo']);
const LAYOUT_KEY = (w: Workspace) => `workbench.layout.v4.${w}`;

/** The agent layout is sized from the window: side panels shrink, and fold into one tab group on narrow screens. */
function stepsFor(w: Workspace): Step[] {
  if (w !== 'agent') return LAYOUTS[w];
  const width = typeof window === 'undefined' ? 1600 : window.innerWidth;
  const clamp = (v: number, lo: number, hi: number) => Math.round(Math.min(hi, Math.max(lo, v)));
  return LAYOUTS.agent.map((step) => {
    if (step.id === 'agentChat') return { ...step, w: clamp(width * 0.27, 340, 470) };
    if (step.id === 'agentTask') {
      return width < 1180 ? { id: 'agentTask', ref: 'agentChat', dir: 'within', inactive: true } : { ...step, w: clamp(width * 0.22, 300, 400) };
    }
    if (step.id === 'compare') return { ...step, h: clamp((typeof window === 'undefined' ? 900 : window.innerHeight) * 0.26, 180, 280) };
    return step;
  });
}

function buildDefault(api: DockviewApi, w: Workspace) {
  for (const step of stepsFor(w)) {
    api.addPanel({
      id: step.id, component: step.id, title: PANELS[step.id].title, renderer: ALWAYS.has(step.id) ? 'always' : undefined,
      position: step.ref ? { referencePanel: step.ref, direction: step.dir ?? 'within' } : undefined,
      initialWidth: step.w, initialHeight: step.h, inactive: step.inactive,
    });
  }
}

function restore(api: DockviewApi, w: Workspace) {
  api.clear();
  try {
    const saved = localStorage.getItem(LAYOUT_KEY(w));
    if (saved) {
      api.fromJSON(JSON.parse(saved));
      if (api.panels.length) return;
    }
  } catch {
    /* fall back to the default layout */
  }
  api.clear();
  buildDefault(api, w);
}

export function openPanel(api: DockviewApi | null, id: PanelId) {
  if (!api) return;
  const existing = api.getPanel(id);
  if (existing) {
    existing.api.setActive();
    return;
  }
  const anchor = api.activePanel ?? api.panels[0];
  api.addPanel({ id, component: id, title: PANELS[id].title, position: anchor ? { referencePanel: anchor.id, direction: 'within' } : undefined });
}

export function Studio({ route }: { route: Route }) {
  const st = useStudio();
  const api = useRef<DockviewApi | null>(null);
  const restoring = useRef(false);

  useEffect(() => {
    void st.loadAlgorithms();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // URL → state
  useEffect(() => {
    if (route.view === 'agent' || route.view === 'lab') {
      if (st.workspace !== 'agent') st.setWorkspace('agent');
      void useAgent.getState().select(route.view === 'agent' ? route.id : null);
    } else if (route.view === 'plan' && route.id) {
      void st.open(route.id);
      if (st.workspace !== 'plan') st.setWorkspace('plan');
    } else if (route.view === 'assets') {
      st.setWorkspace('assets');
      if (route.id) void useAssets.getState().select(route.id);
    } else if (route.view === 'sim') st.setWorkspace('sim');
    else if (route.view === 'runs') st.setWorkspace('system');
    else if (route.view === 'archive') st.setWorkspace('library');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.view, route.id]);

  // Opening 任务规划 with nothing loaded lands on the most recent task map.
  useEffect(() => {
    if (st.workspace !== 'plan' || route.view !== 'lab' || st.scenarioId) return;
    lab.scenarios().then(({ scenarios }) => {
      const mine = scenarios.filter((x) => (x as { origin?: string }).origin === 'user' && x.problem_class === 'fleet_routing');
      const pick = mine[mine.length - 1];
      if (pick && !useStudio.getState().scenarioId) location.hash = `#/plan/${encodeURIComponent(pick.id)}`;
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.view, st.workspace]);

  const onReady = useCallback((e: DockviewReadyEvent) => {
    api.current = e.api;
    restoring.current = true;
    restore(e.api, useStudio.getState().workspace);
    restoring.current = false;
    e.api.onDidLayoutChange(() => {
      if (restoring.current) return;
      try {
        localStorage.setItem(LAYOUT_KEY(useStudio.getState().workspace), JSON.stringify(e.api.toJSON()));
      } catch {
        /* storage full or blocked */
      }
    });
  }, []);

  // workspace → layout
  const lastWs = useRef(st.workspace);
  useEffect(() => {
    if (!api.current || lastWs.current === st.workspace) return;
    lastWs.current = st.workspace;
    restoring.current = true;
    restore(api.current, st.workspace);
    restoring.current = false;
  }, [st.workspace]);

  // open the code panel whenever a source file is requested
  useEffect(() => {
    if (st.codePath) openPanel(api.current, 'code');
  }, [st.codePath]);

  useEffect(() => {
    if (st.studyId && st.workspace === 'experiments') openPanel(api.current, 'analysis');
  }, [st.studyId, st.workspace]);

  useShortcuts(api);

  // A save that failed while the service was down is retried as soon as the connection is back.
  const connected = useGatewayConnected();
  useEffect(() => {
    if (connected && useStudio.getState().save === 'error') {
      useStudio.setState({ save: 'dirty' });
      void flushSave();
    }
  }, [connected]);

  useEffect(() => {
    const onOpen = (e: Event) => openPanel(api.current, (e as CustomEvent<PanelId>).detail);
    window.addEventListener('studio:open-panel', onOpen);
    return () => window.removeEventListener('studio:open-panel', onOpen);
  }, []);

  const resetLayout = () => {
    if (!api.current) return;
    localStorage.removeItem(LAYOUT_KEY(st.workspace));
    restoring.current = true;
    api.current.clear();
    buildDefault(api.current, st.workspace);
    restoring.current = false;
  };

  return (
    <div className={s.studio}>
      <header className={s.toolbar}>
        <div className={s.brand}>
          <span className={s.logo}><Sparkles size={14} /></span>
          <span>农业机器人路径规划</span>
        </div>
        {st.workspace === 'agent' ? <div className={s.compose}><ModelChip /></div> : <ComposeBar />}
        <div className={s.workspaceNav}>
          <Segmented value={st.workspace} onChange={(w) => st.setWorkspace(w)} label="工作区"
            options={WORKSPACES.map((w) => ({ value: w.value, label: w.label, icon: w.icon }))} />
          <Button size="sm" icon={ListRestart} title="恢复当前工作区的默认布局" onClick={resetLayout} />
          <Button size="sm" icon={CommandIcon} title="命令面板 (⌘K)" onClick={() => st.setPalette(true)}>⌘K</Button>
        </div>
      </header>
      <div className={s.dock}>
        <DockviewReact components={COMPONENTS} onReady={onReady} theme={themeLight} className={s.dockview} singleTabMode="default" />
      </div>
      <StatusBar />
      {st.toast && <div className={s.toast} data-tone={st.toast.tone}>{st.toast.text}</div>}
      {st.paletteOpen && <Palette api={api} />}
      <AssetModals />
    </div>
  );
}

/** Import wizard and map → task-map wizard: reachable from every workspace. */
function AssetModals() {
  const importOpen = useAssets((x) => x.importOpen);
  const taskMapFrom = useAssets((x) => x.taskMapFrom);
  return (
    <Suspense fallback={null}>
      {importOpen && <ImportWizard />}
      {taskMapFrom && <MapToTaskMap />}
    </Suspense>
  );
}

/* ----------------------------------------------------------- compose bar -- */
function ComposeBar() {
  const text = useStudio((x) => x.composeText);
  const setText = useStudio((x) => x.setComposeText);
  const compose = useStudio((x) => x.composeFromText);
  const busy = useStudio((x) => x.busy);
  const notify = useStudio((x) => x.notify);
  const [focused, setFocused] = useState(false);
  const [parse, setParse] = useState<ParseResult | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
  const [showExamples, setShowExamples] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    studio.examples().then((r) => setExamples(r.examples)).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!text.trim()) {
      setParse(null);
      return;
    }
    const t = window.setTimeout(() => studio.parse(text).then(setParse).catch(() => setParse(null)), 220);
    return () => window.clearTimeout(t);
  }, [text]);
  const found = new Set((parse?.found ?? []).map((f) => f.key.split('.')[0]));
  async function onFile(f: File) {
    try {
      const obj = JSON.parse(await f.text());
      const sc = obj.type === 'FeatureCollection' || obj.type === 'Feature' ? await studio.importGeojson(obj, f.name.replace(/\.(geo)?json$/i, '')) : await studio.importScenario(obj);
      location.hash = `#/plan/${encodeURIComponent(sc.id)}`;
      notify('ok', `已导入 ${sc.title}`);
    } catch (e) {
      notify('error', `导入失败：${(e as Error).message}`);
    }
  }
  return (
    <div className={s.compose}>
      <div className={s.composeField} data-focus={focused || undefined}>
        <Wand2 size={15} className={s.composeIcon} />
        <input
          value={text}
          placeholder="用一句话描述任务，例如：在 200×120 米的果园里，3 台机器人从西南角出发采样 40 个点，避开两个池塘"
          onChange={(e) => setText(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => { setFocused(false); setShowExamples(false); }, 160)}
          onKeyDown={(e) => { if (e.key === 'Enter') { (e.target as HTMLInputElement).blur(); void compose(); } }}
          aria-label="任务描述"
        />
        <button type="button" className={s.examplesBtn} title="示例" onClick={() => setShowExamples((v) => !v)}><ChevronDown size={14} /></button>
        {(focused || showExamples) && (parse || showExamples || !text) && (
          <div className={s.composePop}>
            {parse && text ? (
              <>
                <div className={s.popHead}>将生成</div>
                <div className={s.popCards}>
                  {parse.cards.map((c) => (
                    <span key={c.key} className={s.popCard} data-default={!found.has(c.key) || undefined}><em>{c.label}</em>{c.value}</span>
                  ))}
                </div>
                <div className={s.popHint}>深色项来自你的描述，浅色项为默认值，生成后都可以在地图和检查器里修改。按回车生成。</div>
              </>
            ) : null}
            {(showExamples || !text) && examples.length > 0 && (
              <>
                <div className={s.popHead}>试试这些描述</div>
                {examples.map((ex) => (
                  <button key={ex} type="button" className={s.example} onMouseDown={(e) => { e.preventDefault(); setText(ex); setShowExamples(false); }}>{ex}</button>
                ))}
              </>
            )}
          </div>
        )}
      </div>
      <Button variant="primary" icon={busy === '生成地图' ? undefined : Sparkles} loading={busy === '生成地图'} disabled={!text.trim()} onClick={() => { setFocused(false); void compose(); }}>生成地图</Button>
      <Button icon={Upload} title="导入 GeoJSON 或场景 JSON" onClick={() => file.current?.click()}>导入</Button>
      <input ref={file} type="file" accept=".json,.geojson,application/json" hidden onChange={(e) => e.target.files?.[0] && void onFile(e.target.files[0])} />
    </div>
  );
}

/* ------------------------------------------------------------ status bar -- */
function StatusBar() {
  const connected = useGatewayConnected();
  const sim = useLabResource('studio-sim', () => lab.simState(), { event: 'sim_state', pollMs: 5000 });
  const field = useFieldSim();
  const fieldLabel = field.error ? '断连' : field.state
    ? ({ idle: '未运行', starting: '启动中', ready: '就绪', running: '执行中', finished: '已完成', failed: '失败', stopped: '已停止' }[field.state.phase] ?? '未知')
    : '未知';
  const cursor = useStudio((x) => x.cursor);
  const scenario = useStudio((x) => x.scenario);
  const runs = useStudio((x) => x.runs);
  const runIds = useStudio((x) => x.runIds);
  const selection = useStudio((x) => x.selection);
  const running = runIds.filter((id) => ['queued', 'running'].includes(runs[id]?.state ?? '')).length;
  const units = scenario?.frame.units === 'm' ? 'm' : '';
  return (
    <footer className={s.status}>
      <span className={s.statusItem}><StatusDot tone={connected ? 'ok' : 'error'} /> {connected ? '已连接' : '服务断开'}</span>
      <span className={s.statusItem}>{running ? <><Spinner /> 求解 {running}</> : '求解空闲'}</span>
      <span className={s.statusItem}><Bot size={12} /> 田间：{fieldLabel} · TB4：{sim.data ? simPhaseLabel(sim.data.phase) : '未知'}</span>
      <span className={s.statusGrow}>{scenario ? `${scenario.title}` : ''}</span>
      {selection && <span className={s.statusItem}>选中：{selection.type === 'task' ? `任务 ${selection.id}` : selection.type === 'obstacle' ? `障碍 ${selection.id}` : selection.type === 'plot' ? `地块 ${selection.id}` : selection.type === 'depot' ? '车库' : '边界'}</span>}
      <span className={s.statusMono}>{cursor ? `x ${cursor[0].toFixed(1)}  y ${cursor[1].toFixed(1)} ${units}` : ''}</span>
      <a className={s.statusLink} href="#/jobs">CVRP 数值任务</a>
      <a className={s.statusLink} href="#/bpp">BPP 历史</a>
    </footer>
  );
}

/* --------------------------------------------------------------- palette -- */
function Palette({ api }: { api: React.RefObject<DockviewApi | null> }) {
  const st = useStudio();
  const scenarios = useLabResource('palette-scenarios', () => lab.scenarios());
  const close = () => st.setPalette(false);
  const run = (fn: () => void) => () => { fn(); close(); };
  const fleetScenarios = useMemo(() => (scenarios.data?.scenarios ?? []).slice(0, 200), [scenarios.data]);
  return (
    <div className={s.paletteBackdrop} onMouseDown={close}>
      <div className={s.palette} onMouseDown={(e) => e.stopPropagation()}>
        <Command label="命令面板" loop>
          <Command.Input autoFocus placeholder="搜索命令、面板或场景…" onKeyDown={(e) => e.key === 'Escape' && close()} />
          <Command.List>
            <Command.Empty>没有匹配项</Command.Empty>
            <Command.Group heading="操作">
              <Command.Item onSelect={run(() => document.querySelector<HTMLInputElement>('input[aria-label="任务描述"]')?.focus())}>输入任务描述</Command.Item>
              <Command.Item onSelect={run(() => { st.setWorkspace('assets'); useAssets.getState().openImport(); })}>导入资产（地图 / 车辆 / 设施）</Command.Item>
              <Command.Item onSelect={run(() => openPanel(api.current, 'replay'))}>回放仿真</Command.Item>
              <Command.Item onSelect={run(() => openPanel(api.current, 'results'))}>查看实验结果并导出</Command.Item>
              <Command.Item onSelect={run(() => openPanel(api.current, 'figures'))}>科研图表（期刊版式 / 论文图包）</Command.Item>
              <Command.Item onSelect={run(() => void st.solve())}>求解并对比推荐的算法</Command.Item>
              <Command.Item onSelect={run(() => window.dispatchEvent(new Event('studio:fit')))}>地图适配全图</Command.Item>
              <Command.Item onSelect={run(st.undo)}>撤销</Command.Item>
              <Command.Item onSelect={run(st.redo)}>重做</Command.Item>
              <Command.Item onSelect={run(() => { st.setWorkspace('experiments'); st.setStudy(null); })}>新建研究课题</Command.Item>
              <Command.Item onSelect={run(() => { location.hash = '#/bpp'; })}>打开 BPP 历史结果</Command.Item>
              <Command.Item onSelect={run(() => { location.hash = '#/jobs'; })}>打开 CVRP 数值任务</Command.Item>
            </Command.Group>
            <Command.Group heading="工作区">
              {WORKSPACES.map((w) => <Command.Item key={w.value} onSelect={run(() => st.setWorkspace(w.value))}>切换到 {w.label}</Command.Item>)}
            </Command.Group>
            <Command.Group heading="面板">
              {(Object.keys(PANELS) as PanelId[]).map((id) => (
                <Command.Item key={id} onSelect={run(() => openPanel(api.current, id))}><LayoutGrid size={13} /> 打开 {PANELS[id].title}</Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="场景">
              {fleetScenarios.map((x) => (
                <Command.Item key={x.id} value={`${x.title} ${x.id}`} onSelect={run(() => { location.hash = `#/plan/${encodeURIComponent(x.id)}`; })}>{x.title}</Command.Item>
              ))}
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ shortcuts -- */
function useShortcuts(api: React.RefObject<DockviewApi | null>) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useStudio.getState();
      const typing = (e.target as HTMLElement)?.closest('input, textarea, select, [contenteditable="true"], .cm-editor');
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        st.setPalette(!st.paletteOpen);
        return;
      }
      if (mod && e.key === 'Enter') {
        e.preventDefault();
        void st.solve();
        return;
      }
      if (typing) return;
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) st.redo();
        else st.undo();
        return;
      }
      if (mod && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void flushSave();
        return;
      }
      if (mod) return;
      const tools: Record<string, Parameters<typeof st.setTool>[0]> = { v: 'select', h: 'pan', t: 'task', o: 'obstacle', p: 'plot', d: 'depot', u: 'unload', c: 'corridor' };
      const k = e.key.toLowerCase();
      if (tools[k]) st.setTool(tools[k]);
      else if (k === 'f') window.dispatchEvent(new Event('studio:fit'));
      else if (k === 'escape') st.select(null);
      else if ((e.key === 'Delete' || e.key === 'Backspace') && st.selection) {
        const sel = st.selection;
        st.edit((inst) => {
          const i = inst as unknown as FleetInstance;
          if (sel.type === 'task') return { ...inst, tasks: i.tasks.filter((t) => t.id !== sel.id) } as typeof inst;
          if (sel.type === 'obstacle') return { ...inst, obstacles: i.obstacles.filter((o) => o.id !== sel.id) } as typeof inst;
          if (sel.type === 'plot') return { ...inst, plots: i.plots.filter((o) => o.id !== sel.id) } as typeof inst;
          if (sel.type === 'corridor') return { ...inst, corridors: (i.corridors ?? []).filter((o) => o.id !== sel.id) } as typeof inst;
          if (sel.type === 'unload') return { ...inst, unload_points: (i.unload_points ?? []).filter((_, index) => index !== sel.index) } as typeof inst;
          if (sel.type === 'depot') return removeDepot(i, sel.index ?? 0) as unknown as typeof inst;
          return inst;
        });
        st.select(null);
      }
      void api;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [api]);
}
