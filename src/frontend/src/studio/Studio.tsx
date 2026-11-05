import { useCallback, useEffect, useRef } from 'react';
import { DockviewReact, themeLight, type DockviewApi, type DockviewReadyEvent } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { Bot, Command as CommandIcon, ListRestart, LogOut } from 'lucide-react';
import { lab, useGatewayConnected, useLabResource } from '../lab/api';
import { Button, Spinner, StatusDot } from '../lab/ui';
import { simPhaseLabel } from '../lab/shell/labels';
import { useStudio, type Workspace } from './store';
import { useFieldSim } from './ros/live';
import { ALWAYS, PANELS, PanelHost, type PanelId } from './panelRegistry';
import { ComposeBar } from './ComposeBar';
import { ModelChip } from '../agent/AgentChat';
import './dock.css';
import s from './studio.module.css';

const COMPONENTS = Object.fromEntries((Object.keys(PANELS) as PanelId[]).map((id) => [id, () => <PanelHost id={id} />]));

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

/** The free layout: every panel can be dragged, merged into tabs, split or floated, remembered per workspace. */
export function Studio({ onExit }: { onExit: () => void }) {
  const st = useStudio();
  const api = useRef<DockviewApi | null>(null);
  const restoring = useRef(false);

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
        {st.workspace === 'agent' ? <div className={s.compose}><ModelChip /></div> : <ComposeBar />}
        <div className={s.workspaceNav}>
          <Button size="sm" icon={ListRestart} title="恢复当前工作区的默认布局" onClick={resetLayout} />
          <Button size="sm" icon={CommandIcon} title="命令面板 (⌘K)" onClick={() => st.setPalette(true)}>⌘K</Button>
          <Button size="sm" icon={LogOut} title="回到标准布局" onClick={onExit}>标准布局</Button>
        </div>
      </header>
      <div className={s.dock}>
        <DockviewReact components={COMPONENTS} onReady={onReady} theme={themeLight} className={s.dockview} singleTabMode="default" />
      </div>
      <StatusBar />
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
