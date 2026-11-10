import { useEffect, useRef, type ReactNode } from 'react';
import { navigate, parseHash, patchParams, type Route, type ViewName } from '../app/router';
import { lab, useGatewayConnected } from '../lab/api';
import { useAgent } from '../agent/store';
import { currentRuns } from '../agent/studioSync';
import { STATE_LABEL } from '../agent/api';
import { AssetModals } from '../studio/AssetModals';
import { removeDepot } from '../studio/fleet';
import { useAssets } from '../studio/assets/store';
import { Studio } from '../studio/Studio';
import { flushSave, useStudio, type Workspace } from '../studio/store';
import type { FleetInstance } from '../studio/types';
import type { PanelId } from '../studio/panelRegistry';
import { Conversation } from './Conversation';
import { Icon } from './Icon';
import { NewTask } from './Composer';
import { decodeStack, encodeStack } from './frames';
import { MapsPage } from './MapsPage';
import { FieldPage } from '../field/FieldPage';
import { openFrames, openRoot, sessionLabel, useMedia } from './nav';
import { Overlays, Palette } from './Overlays';
import { ViewErrorBoundary } from '../lab/shell/Page';
import { AlgorithmsPage } from './AlgorithmsPage';
import { SessionPane } from './Pane';
import { Sidebar } from './Sidebar';
import { applyTheme, useDesk } from './store';
import { PageHead, SurfaceFrame, WorkSurface, panelHome, regionKey, type SurfaceName } from './Surface';
import { SettingsPage } from './Settings';
import s from './desk.module.css';

const WORKSPACE_OF: Partial<Record<ViewName, Workspace>> = {
  lab: 'agent', agent: 'agent', maps: 'plan', plan: 'plan', assets: 'assets', experiments: 'experiments', runs: 'experiments', sim: 'sim', library: 'library', archive: 'library', system: 'system',
};
const SURFACE_OF: Partial<Record<ViewName, SurfaceName>> = { plan: 'plan', assets: 'assets', experiments: 'experiments', runs: 'experiments', sim: 'sim', library: 'library', archive: 'library' };
const BPP: ViewName[] = ['overview', 'samples', 'replay', 'risk', 'evidence'];
const PRODUCT = 'CERES 灵畴';

function hrefFor(w: Workspace): string {
  const st = useStudio.getState();
  switch (w) {
    case 'agent': { const id = useAgent.getState().sessionId; return id ? `#/agent/${encodeURIComponent(id)}` : '#/agent'; }
    case 'plan': return st.scenarioId ? `#/plan/${encodeURIComponent(st.scenarioId)}` : '#/maps';
    default: return `#/${w}`;
  }
}

/** Address → workbench state, and the panels' `setWorkspace` calls back to an address. */
function useRouteSync(route: Route) {
  useEffect(() => {
    if ((route.view === 'library' && route.id === 'algorithms') || route.view === 'archive') { navigate('#/algorithms', { replace: true }); return; }
    const st = useStudio.getState();
    const w = WORKSPACE_OF[route.view];
    if (w && st.workspace !== w) st.setWorkspace(w);
    if (route.view === 'agent' || route.view === 'lab') void useAgent.getState().select(route.view === 'agent' ? route.id : null);
    else if (route.view === 'plan') {
      if (route.id) void st.open(route.id);
      else if (st.scenarioId) navigate(`#/plan/${encodeURIComponent(st.scenarioId)}`, { replace: true });
      else {
        lab.scenarios().then(({ scenarios }) => {
          const mine = scenarios.filter((x) => (x as { origin?: string }).origin === 'user' && x.problem_class === 'fleet_routing');
          const pick = mine[mine.length - 1];
          navigate(pick ? `#/plan/${encodeURIComponent(pick.id)}` : '#/maps', { replace: true });
        }).catch(() => navigate('#/maps', { replace: true }));
      }
    } else if (route.view === 'assets' && route.id) void useAssets.getState().select(route.id);
    else if (route.view === 'experiments' && route.id) st.setStudy(route.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.view, route.id]);

  useEffect(() => useStudio.subscribe((st, prev) => {
    if (st.workspace === prev.workspace) return;
    queueMicrotask(() => {
      const view = parseHash(location.hash).view;
      if (WORKSPACE_OF[view] !== useStudio.getState().workspace) navigate(hrefFor(useStudio.getState().workspace));
    });
  }), []);
}

const BPP_TABS: [ViewName, string, string][] = [['overview', '结果', '#/bpp'], ['samples', '样本', '#/samples'], ['replay', '回放', '#/replay'], ['risk', '同前缀风险', '#/risk'], ['evidence', '证据', '#/evidence']];

/** One panel asks for another: it opens wherever that panel lives, as a pane layer in a task. */
function openAnywhere(id: PanelId) {
  const route = parseHash(location.hash);
  const d = useDesk.getState();
  if (d.dock) return;
  if (route.view === 'agent' || route.view === 'lab') {
    if (id === 'map') { openRoot('map'); return; }
    const root = d.stack[0] ?? { t: 'plans' as const };
    openFrames([root, { t: 'panel', id }]);
    return;
  }
  const here = SURFACE_OF[route.view];
  const home = panelHome(id, here);
  if (!home) {
    const section = ({ models: 'models', capabilities: 'capabilities', data: 'data', desktop: 'desktop', tools: 'tools', evals: 'evals', services: 'services', console: 'logs' } as Partial<Record<PanelId, string>>)[id];
    if (section) navigate(`#/system/${section}`);
    return;
  }
  d.setTab(regionKey(home.surface, home.region), id);
  if (home.region === 'pane') d.setPane(home.surface, true);
  if (id === 'algorithms') navigate('#/algorithms');
  else if (home.surface === 'library' && home.region === 'main') navigate(`#/library/${id}`);
  else if (id === 'runs') navigate('#/runs');
  else if (home.surface !== here) navigate(hrefFor(home.surface as Workspace));
}

function useGlobal() {
  const connected = useGatewayConnected();
  useEffect(() => { void useStudio.getState().loadAlgorithms(); applyTheme(useDesk.getState().theme); }, []);
  useEffect(() => {
    if (connected && useStudio.getState().save === 'error') {
      useStudio.setState({ save: 'dirty' });
      void flushSave();
    }
  }, [connected]);
  useEffect(() => {
    const on = (e: Event) => openAnywhere((e as CustomEvent<PanelId>).detail);
    addEventListener('studio:open-panel', on);
    return () => removeEventListener('studio:open-panel', on);
  }, []);
  const codePath = useStudio((x) => x.codePath);
  const studyId = useStudio((x) => x.studyId);
  const workspace = useStudio((x) => x.workspace);
  useEffect(() => { if (codePath) openAnywhere('code'); }, [codePath]);
  useEffect(() => { if (studyId && workspace === 'experiments') openAnywhere('analysis'); }, [studyId, workspace]);
}

function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useStudio.getState();
      const d = useDesk.getState();
      const typing = (e.target as HTMLElement)?.closest('input, textarea, select, [contenteditable="true"], .cm-editor');
      const mod = e.metaKey || e.ctrlKey;
      const k = e.key.toLowerCase();
      if (mod && k === 'k') { e.preventDefault(); st.setPalette(!st.paletteOpen); return; }
      if (mod && k === 'b') { e.preventDefault(); d.setSide(!d.sideOpen); return; }
      if (mod && k === 'n') { e.preventDefault(); navigate('#/agent'); return; }
      if (mod && k === 'j') {
        e.preventDefault();
        const view = parseHash(location.hash).view;
        const name = SURFACE_OF[view] ?? 'agent';
        d.setPane(name, !d.pane[name]);
        return;
      }
      const planning = d.dock || parseHash(location.hash).view === 'plan';
      if (mod && e.key === 'Enter' && planning) { e.preventDefault(); void st.solve(); return; }
      if (e.key === 'Escape' && !typing && !st.paletteOpen && !d.overlay) {
        const view = parseHash(location.hash).view;
        if ((view === 'agent' || view === 'lab') && d.pane.agent) {
          if (!d.pop()) d.setPane('agent', false);
          return;
        }
      }
      if (typing) return;
      if (e.key === '?') { d.setOverlay('help'); return; }
      if (!planning) return;
      if (mod && k === 'z') { e.preventDefault(); if (e.shiftKey) st.redo(); else st.undo(); return; }
      if (mod && k === 's') { e.preventDefault(); void flushSave(); return; }
      if (mod) return;
      const tools: Record<string, Parameters<typeof st.setTool>[0]> = { v: 'select', h: 'pan', t: 'task', o: 'obstacle', p: 'plot', d: 'depot', u: 'unload', c: 'corridor' };
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
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);
}

/** The task surface: conversation, with the layer pane. The pane position lives in the address. */
function AgentSurface({ route }: { route: Route }) {
  const sessionId = useAgent((x) => x.sessionId);
  const session = useAgent((x) => x.session);
  const stack = useDesk((d) => d.stack);
  const setStack = useDesk((d) => d.setStack);
  const setPane = useDesk((d) => d.setPane);
  const narrow = useMedia('(max-width: 1180px)');
  const seen = useRef<string | null>(null);

  useEffect(() => {
    if (!session || seen.current === session.session_id) return;
    seen.current = session.session_id;
    const fromAddress = decodeStack(route.params.get('p'));
    if (fromAddress) { setStack(fromAddress); setPane('agent', true); return; }
    setStack(currentRuns(session).length ? [{ t: 'plans' }] : [{ t: 'contract' }]);
    const d = useDesk.getState();
    if (d.pane.agent === undefined) setPane('agent', !narrow);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id]);

  useEffect(() => {
    // The address must already name this session; a new task sets state before the hash catches up.
    if (route.view !== 'agent' || route.id !== sessionId || !seen.current || seen.current !== sessionId) return;
    const value = stack.length ? encodeStack(stack) : null;
    if ((route.params.get('p') ?? null) !== value) patchParams({ p: value });
  }, [stack, route.view, route.id, route.params, sessionId]);

  // Tab title carries the session state, so a waiting task is visible from another window.
  useEffect(() => {
    if (!session) { document.title = PRODUCT; return; }
    const waiting = ['awaiting_approval', 'awaiting_confirmation', 'awaiting_user'].includes(session.state);
    document.title = `${waiting ? `（${STATE_LABEL[session.state]}）` : ''}${sessionLabel(session)} · ${PRODUCT}`;
    return () => { document.title = PRODUCT; };
  }, [session]);

  if (!sessionId) return <section className={s.main} style={{ flex: 1 }}><NewTaskBar float /><NewTask /></section>;
  return <SurfaceFrame name="agent" pane={<SessionPane />}><Conversation /></SurfaceFrame>;
}

/** The page's own top bar. With the sidebar open it is empty, and still the place to move the window by. */
function NewTaskBar({ float }: { float?: boolean }) {
  const sideOpen = useDesk((d) => d.sideOpen);
  const setSide = useDesk((d) => d.setSide);
  const narrow = useMedia('(max-width: 1060px)');
  return (
    <header className={float ? `${s.bar} ${s.barFloat}` : s.bar}>
      {(!sideOpen || narrow) && <button type="button" className={s.iconBtn} aria-label="打开任务栏" onClick={() => setSide(true)}><Icon name="side" /></button>}
    </header>
  );
}

export function Desk({ route, children }: { route: Route; children?: ReactNode }) {
  const sideOpen = useDesk((d) => d.sideOpen);
  const setSide = useDesk((d) => d.setSide);
  const dock = useDesk((d) => d.dock);
  const setDock = useDesk((d) => d.setDock);
  const palette = useStudio((x) => x.paletteOpen);
  const toast = useStudio((x) => x.toast);
  useRouteSync(route);
  useGlobal();
  useShortcuts();
  const surface = SURFACE_OF[route.view];
  const isChat = route.view === 'agent' || route.view === 'lab';
  const bpp = BPP.includes(route.view);
  const inDock = dock && (isChat || !!surface);
  return (
    <div className={s.desk} data-side={sideOpen ? 'open' : 'closed'}>
      <Sidebar route={route} />
      <div className={s.stage}>
        <ViewErrorBoundary key={surface ?? route.view} name="此页面">
        {inDock ? <div className={s.dockHost}><Studio onExit={() => setDock(false)} /></div>
          : isChat ? <AgentSurface route={route} />
            : route.view === 'maps' ? <MapsPage route={route} />
              : route.view === 'field' ? <FieldPage route={route} />
              : route.view === 'algorithms' ? <AlgorithmsPage route={route} />
              : route.view === 'system' ? <SettingsPage route={route} />
                : surface ? <WorkSurface key={surface} name={surface} route={route} />
                  : <div className={s.main} style={{ flex: 1 }}>
                    {route.view === 'jobs' && <PageHead page="experiments" route={route} />}
                    {bpp && <PageHead page="library" route={route} />}
                    {route.view === 'notfound' && <NewTaskBar />}
                    {bpp && <nav className={s.subNav} aria-label="BPP 历史结果">{BPP_TABS.map(([view, label, href]) => <a key={view} href={href} aria-current={route.view === view ? 'page' : undefined}>{label}</a>)}</nav>}
                    <div className={s.stagePage}>{children}</div>
                  </div>}
        </ViewErrorBoundary>
      </div>
      <div className={s.scrim} onClick={() => setSide(false)} />
      {toast && <div className={s.toast} data-tone={toast.tone}>{toast.text}</div>}
      {palette && <Palette />}
      <Overlays />
      <AssetModals />
    </div>
  );
}
