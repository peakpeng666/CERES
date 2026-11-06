import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import type { Route } from '../app/router';
import { lab, useLabResource } from '../lab/api';
import { useAgent } from '../agent/store';
import { ALWAYS, PANELS, PanelHost, type PanelId } from '../studio/panelRegistry';
import { useStudio } from '../studio/store';
import { Composer } from './Composer';
import { useDesk } from './store';
import { Icon } from './Icon';
import { sessionLabel, useMedia } from './nav';
import { Marker, TopBar, useMenu } from './ui';
import s from './desk.module.css';

export type SurfaceName = 'plan' | 'assets' | 'experiments' | 'sim' | 'library';
type Tab = { id: PanelId; label?: string };
interface SurfaceDef { main: Tab[]; bottom?: Tab[]; pane: Tab[]; more?: Tab[]; pane0: boolean }

/** Every panel of the old workspaces has a home here; the free layout still offers them all side by side. */
export const SURFACES: Record<SurfaceName, SurfaceDef> = {
  plan: {
    main: [{ id: 'map' }], pane0: true,
    pane: [{ id: 'advisor', label: '求解' }, { id: 'compare', label: '方案' }, { id: 'verify' }, { id: 'inspector' }],
    more: [{ id: 'results' }, { id: 'replay' }, { id: 'decision' }, { id: 'figures' }, { id: 'scenarios' }, { id: 'console' }, { id: 'classic' }],
  },
  assets: { main: [{ id: 'assets' }], pane0: true, pane: [{ id: 'assetPreview', label: '预览' }, { id: 'assetInspector', label: '检查器' }] },
  experiments: { main: [{ id: 'studies' }, { id: 'runs' }], pane0: true, pane: [{ id: 'analysis' }, { id: 'figures' }], more: [{ id: 'researchAgent' }, { id: 'results' }, { id: 'code' }] },
  sim: {
    main: [{ id: 'rosview' }, { id: 'gazebo' }], bottom: [{ id: 'telemetry' }, { id: 'console' }], pane0: true,
    pane: [{ id: 'fieldsim' }, { id: 'physics' }, { id: 'sim', label: 'TurtleBot4' }, { id: 'replay', label: '回放' }, { id: 'results', label: '结果' }],
  },
  library: { main: [{ id: 'cvrplib' }, { id: 'fields' }, { id: 'references' }], pane0: false, pane: [{ id: 'code' }] },
};

export const regionKey = (surface: string, region: 'main' | 'bottom' | 'pane') => `${surface}:${region}`;

/** Where a panel lives, for when one panel asks for another. */
export function panelHome(id: PanelId, prefer?: SurfaceName): { surface: SurfaceName; region: 'main' | 'bottom' | 'pane' } | null {
  const order = prefer ? [prefer, ...(Object.keys(SURFACES) as SurfaceName[]).filter((x) => x !== prefer)] : (Object.keys(SURFACES) as SurfaceName[]);
  for (const name of order) {
    const d = SURFACES[name];
    if (d.main.some((t) => t.id === id)) return { surface: name, region: 'main' };
    if (d.bottom?.some((t) => t.id === id)) return { surface: name, region: 'bottom' };
    if (d.pane.some((t) => t.id === id) || d.more?.some((t) => t.id === id)) return { surface: name, region: 'pane' };
  }
  return null;
}

function Region({ surface, region, tabs, more, tail }: { surface: SurfaceName; region: 'main' | 'bottom' | 'pane'; tabs: Tab[]; more?: Tab[]; tail?: ReactNode }) {
  const key = regionKey(surface, region);
  const active = useDesk((d) => d.tabs[key]);
  const setTab = useDesk((d) => d.setTab);
  const all = [...tabs, ...(more ?? [])];
  const current = all.some((t) => t.id === active) ? active! : tabs[0].id;
  const overflow = more?.find((t) => t.id === current);
  const seen = useRef(new Set<PanelId>());
  seen.current.add(current);
  const menu = useMenu();
  const strip = useRef<HTMLDivElement>(null);
  return (
    <>
      {(tabs.length > 1 || tail || more) && (
        <div className={s.tabs} role="tablist" ref={strip}>
          <Marker host={strip} match='[aria-selected="true"]' kind="line" watch={current} />
          {tabs.length > 1 && tabs.map((t) => <button key={t.id} type="button" role="tab" className={s.tab} aria-selected={t.id === current} onClick={() => setTab(key, t.id)}>{t.label ?? PANELS[t.id].title}</button>)}
          {overflow && <button type="button" role="tab" className={s.tab} aria-selected>{overflow.label ?? PANELS[overflow.id].title}</button>}
          {more && <button type="button" className={s.tab} aria-haspopup="menu" onClick={(e) => menu.open(e.currentTarget, more.map((t) => ({ label: t.label ?? PANELS[t.id].title, on: t.id === current, onSelect: () => setTab(key, t.id) })))}>更多</button>}
          {tail}
        </div>
      )}
      {menu.node}
      <div className={s.tabBody}>
        {all.filter((t) => t.id === current || (ALWAYS.has(t.id) && seen.current.has(t.id))).map((t) => (
          <div key={t.id} className={s.tabPanel} hidden={t.id !== current}><PanelHost id={t.id} /></div>
        ))}
      </div>
    </>
  );
}

export function Resizer({ host }: { host: React.RefObject<HTMLDivElement | null> }) {
  const setPaneW = useDesk((d) => d.setPaneW);
  const onDown = () => {
    const el = host.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    const clamp = (x: number) => Math.round(Math.min(box.width - 380, Math.max(380, box.right - x)));
    el.setAttribute('data-dragging', '');
    const move = (ev: PointerEvent) => el.style.setProperty('--pane-w', `${clamp(ev.clientX)}px`);
    const up = (ev: PointerEvent) => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', up);
      el.removeAttribute('data-dragging');
      setPaneW(clamp(ev.clientX));
    };
    addEventListener('pointermove', move); addEventListener('pointerup', up);
  };
  return <div className={s.resizer} role="separator" aria-orientation="vertical" aria-label="调整面板宽度" onPointerDown={onDown} />;
}

/** Shared frame of every surface. Pane state lives in the desk store so shortcuts and panels can open it. */
export function SurfaceFrame({ name, children, pane }: { name: string; children: ReactNode; pane: ReactNode }) {
  const open = useDesk((d) => !!d.pane[name]);
  const wide = useDesk((d) => d.paneWide);
  const paneW = useDesk((d) => d.paneW);
  const narrow = useMedia('(max-width: 1060px)');
  const host = useRef<HTMLDivElement>(null);
  const style = paneW && !narrow ? ({ '--pane-w': `${paneW}px` } as CSSProperties) : undefined;
  return (
    <div ref={host} className={s.surface} data-pane={open ? 'open' : 'closed'} data-wide={(open && wide) || undefined} style={style}>
      <section className={s.main}>{children}</section>
      <aside className={s.pane} aria-label="详情">{pane}</aside>
      {!narrow && <Resizer host={host} />}
    </div>
  );
}

const PAGE_TABS: Record<string, { title: string; tabs: { label: string; href: string; on: (r: Route) => boolean }[] }> = {
  maps: { title: '地图', tabs: [
    { label: '任务地图', href: '#/maps', on: (r) => r.view === 'maps' },
    { label: '资产', href: '#/assets', on: (r) => r.view === 'assets' },
  ] },
  experiments: { title: '实验', tabs: [
    { label: '课题', href: '#/experiments', on: (r) => r.view === 'experiments' },
    { label: 'CVRP 数值任务', href: '#/jobs', on: (r) => r.view === 'jobs' },
    { label: '运行记录', href: '#/runs', on: (r) => r.view === 'runs' },
  ] },
  library: { title: '资料', tabs: [
    { label: 'CVRPLIB', href: '#/library/cvrplib', on: (r) => r.view === 'library' && (!r.id || r.id === 'cvrplib') },
    { label: '真实田块', href: '#/library/fields', on: (r) => r.view === 'library' && r.id === 'fields' },
    { label: '文献', href: '#/library/references', on: (r) => r.view === 'library' && r.id === 'references' },
    { label: 'BPP 历史', href: '#/bpp', on: (r) => ['overview', 'samples', 'replay', 'risk', 'evidence'].includes(r.view) },
  ] },
};

/** Library pages share one header: a name and the rooms inside it. */
export function PageHead({ page, route, right, surface }: { page: keyof typeof PAGE_TABS; route: Route; right?: ReactNode; surface?: string }) {
  const def = PAGE_TABS[page];
  const strip = useRef<HTMLElement>(null);
  return (
    <>
      <TopBar surface={surface} right={right}><h1 className={s.pageTitle}>{def.title}</h1></TopBar>
      <nav className={s.pageNav} aria-label={def.title} ref={strip}>
        <Marker host={strip} match='[aria-current="page"]' kind="line" watch={route.hash} />
        {def.tabs.map((t) => <a key={t.href} href={t.href} aria-current={t.on(route) ? 'page' : undefined}>{t.label}</a>)}
      </nav>
    </>
  );
}

function PlanCrumbs() {
  const scenario = useStudio((x) => x.scenario);
  const session = useAgent((x) => x.session);
  const fromSession = session?.scenario_id && session.scenario_id === scenario?.id;
  return <>
    {fromSession ? <a href={`#/agent/${encodeURIComponent(session.session_id)}`}>{sessionLabel(session)}</a> : <a href="#/maps">地图</a>}
    <i>/</i><b>{scenario?.title ?? '规划'}</b>
  </>;
}

function PlanComposer() {
  const scenario = useStudio((x) => x.scenario);
  const setNewMap = useAgent((x) => x.setNewMap);
  const fleet = scenario?.problem_class === 'fleet_routing';
  useEffect(() => {
    if (!fleet || !scenario) return;
    setNewMap({ id: scenario.id, title: scenario.title });
    return () => setNewMap(null);
  }, [fleet, scenario?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!fleet) return null;
  return <div className={s.dock} style={{ paddingTop: 8 }}><div className={s.dockIn}><Composer session={null} placeholder="让智能体在这张地图上求解、对比或修改" compact /></div></div>;
}

/** Said once at the top of the simulation surface, so nobody has to press start to find out. */
function GazeboGap() {
  const gap = useLabResource('physics-engines', () => lab.physicsEngines()).data?.gazebo_gap;
  if (!gap) return null;
  return <p className={s.simGap}>{gap}{window.desktop && <a href="#/system/desktop">打开设置</a>}</p>;
}

export function WorkSurface({ name, route }: { name: SurfaceName; route: Route }) {
  const def = SURFACES[name];
  const paneSet = useDesk((d) => d.pane[name] !== undefined);
  const setPane = useDesk((d) => d.setPane);
  const paneWide = useDesk((d) => d.paneWide);
  const setPaneWide = useDesk((d) => d.setPaneWide);
  const setDock = useDesk((d) => d.setDock);
  const narrow = useMedia('(max-width: 1060px)');
  useEffect(() => {
    if (!paneSet) setPane(name, def.pane0 && !narrow);
  }, [name, paneSet, setPane, def.pane0, narrow]);
  const free = <button type="button" className={s.iconBtn} title="自由布局：面板可拖动、合并、拆分、浮动" aria-label="自由布局" onClick={() => setDock(true)}><Icon name="grid" /></button>;
  const tail = <>
    <span className={s.grow} />
    <button type="button" className={s.iconBtn} title={paneWide ? '恢复宽度' : '放大面板'} aria-pressed={paneWide} aria-label="放大面板" onClick={() => setPaneWide(!paneWide)}><Icon name="widen" /></button>
    <button type="button" className={s.iconBtn} title="关闭面板" aria-label="关闭面板" onClick={() => setPane(name, false)}><Icon name="close" /></button>
  </>;
  const libraryTab = (['cvrplib', 'fields', 'references'] as PanelId[]).find((x) => x === route.id) ?? 'cvrplib';
  const single: PanelId | null = name === 'library' ? libraryTab : name === 'experiments' ? (route.view === 'runs' ? 'runs' : 'studies') : null;
  const head = name === 'plan' ? <TopBar surface={name} right={free}><PlanCrumbs /></TopBar>
    : name === 'sim' ? <TopBar surface={name} right={free}><h1 className={s.pageTitle}>仿真</h1></TopBar>
      : <PageHead page={name === 'assets' ? 'maps' : name} route={route} surface={name} right={free} />;
  return (
    <SurfaceFrame name={name} pane={<div className={s.paneIn}><Region surface={name} region="pane" tabs={def.pane} more={def.more} tail={tail} /></div>}>
      {head}
      {name === 'sim' && <GazeboGap />}
      <div className={s.regionBox}>
        {single ? <div className={s.tabPanel}><PanelHost key={single} id={single} /></div> : <Region surface={name} region="main" tabs={def.main} />}
      </div>
      {def.bottom && <div className={s.bottomBox}><Region surface={name} region="bottom" tabs={def.bottom} /></div>}
      {name === 'plan' && <PlanComposer />}
    </SurfaceFrame>
  );
}
