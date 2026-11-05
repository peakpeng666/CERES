import { useEffect, useMemo, useState } from 'react';
import type { Route } from '../app/router';
import { lab, useGatewayConnected, useLabResource } from '../lab/api';
import { simPhaseLabel } from '../lab/shell/labels';
import { useAgent } from '../agent/store';
import { agentApi, type SessionSummary } from '../agent/api';
import { useStudio } from '../studio/store';
import { useFieldSim } from '../studio/ros/live';
import { useDesk } from './store';
import { Icon, Mark, type IconName } from './Icon';
import { STATE_SHORT, dayLabel, stateTone } from './format';
import { saveFile, sessionExportHref, sessionLabel, useMedia } from './nav';
import { useMenu } from './ui';
import s from './desk.module.css';

const BPP_VIEWS: Route['view'][] = ['overview', 'samples', 'replay', 'risk', 'evidence'];
const PLACES: { view: Route['view'][]; href: string; icon: IconName; label: string }[] = [
  { view: ['algorithms'], href: '#/algorithms', icon: 'route', label: '算法库' },
  { view: ['sim'], href: '#/sim', icon: 'rover', label: '仿真' },
  { view: ['maps', 'plan', 'assets'], href: '#/maps', icon: 'map', label: '地图' },
  { view: ['experiments', 'runs', 'jobs'], href: '#/experiments', icon: 'flask', label: '实验' },
  { view: ['library', 'archive', ...BPP_VIEWS], href: '#/library', icon: 'book', label: '资料' },
];

function SessionRow({ x, current, onPick }: { x: SessionSummary; current: boolean; onPick: () => void }) {
  const patch = useAgent((a) => a.patchSession);
  const menu = useMenu();
  const tone = stateTone(x.state);
  const short = STATE_SHORT[x.state];
  const open = (anchor: HTMLElement) => menu.open(anchor, [
    { label: x.pinned ? '取消置顶' : '置顶', onSelect: () => void patch(x.session_id, { pinned: !x.pinned }) },
    { label: x.archived ? '取消归档' : '归档', sub: '只是收起，记录全部保留', onSelect: () => void patch(x.session_id, { archived: !x.archived }) },
    { label: '导出实验包', onSelect: () => saveFile(sessionExportHref(x.session_id), `${x.session_id}.json`) },
  ]);
  return (
    <div className={s.sessWrap}>
      <a className={s.sessRow} href={`#/agent/${encodeURIComponent(x.session_id)}`} aria-current={current} title={x.title} onClick={onPick}>
        <span className={s.pip} data-tone={tone} />
        <span className={s.title}>{sessionLabel(x)}</span>
        {short ? <small data-tone={tone}>{short}</small> : x.pinned ? <Icon name="pin" size={12} /> : null}
      </a>
      <button type="button" className={s.sessMore} title="更多" aria-label={`${sessionLabel(x)} 的更多操作`} onClick={(e) => open(e.currentTarget)}><Icon name="dots" size={14} /></button>
      {menu.node}
    </div>
  );
}

/** Backend work that outlives a click: solves, studies, physics runs, simulations. Shown only while something runs. */
function Jobs() {
  const res = useLabResource('agent-jobs', () => agentApi.jobs(), { reloadOn: 'physics_run', pollMs: 3000 });
  const jobs = res.data?.jobs ?? [];
  if (!jobs.length) return null;
  return (
    <div className={s.jobs} aria-label="后台任务">
      <h3>后台 {jobs.length}</h3>
      {jobs.slice(0, 5).map((j) => (
        <a key={`${j.kind}-${j.id}`} href={j.href} className={s.job} title={j.detail ?? j.label}>
          <span>{j.label}</span>
          <small>{j.detail ?? '运行中'}</small>
          {j.progress != null && <i style={{ width: `${Math.round(j.progress * 100)}%` }} />}
        </a>
      ))}
    </div>
  );
}

function Status() {
  const connected = useGatewayConnected();
  const field = useFieldSim();
  const sim = useLabResource('desk-sim', () => lab.simState(), { event: 'sim_state', pollMs: 5000 });
  const runs = useStudio((x) => x.runs);
  const runIds = useStudio((x) => x.runIds);
  const providers = useAgent((x) => x.providers);
  const load = useAgent((x) => x.loadProviders);
  useEffect(() => { void load(); const t = setInterval(() => void load(), 30_000); return () => clearInterval(t); }, [load]);
  const solving = runIds.filter((id) => ['queued', 'running'].includes(runs[id]?.state ?? '')).length;
  const fieldPhase = field.state?.phase;
  const fieldBusy = fieldPhase === 'starting' || fieldPhase === 'running' || fieldPhase === 'ready';
  const fieldText = fieldBusy ? ({ starting: '田间仿真启动中', ready: '田间仿真就绪', running: '田间仿真执行中' } as Record<string, string>)[fieldPhase!] : null;
  const robotBusy = sim.data && !['idle', 'stopped', undefined].includes(sim.data.phase as string);
  const def = providers?.providers.find((p) => p.id === providers.default);
  const modelReady = def && def.enabled && (def.local ? def.status.reachable && def.status.model_present : def.has_credential);
  const line = !connected ? '服务未连接' : solving ? `正在求解 ${solving} 个` : fieldText ?? (robotBusy ? `TB4 ${simPhaseLabel(sim.data!.phase)}` : '服务已连接');
  return (
    <a href="#/system/models" className={s.status} title={def?.status.detail}>
      <span><i className={s.lamp} data-on={!connected ? 'bad' : solving || fieldBusy ? 'busy' : 'ok'} />{line}</span>
      <span><i className={s.lamp} data-on={modelReady ? 'ok' : 'warn'} />{modelReady ? def!.model : '模型未就绪，使用规则流程'}</span>
    </a>
  );
}

export function Sidebar({ route }: { route: Route }) {
  const sessions = useAgent((x) => x.sessions);
  const sessionId = useAgent((x) => x.sessionId);
  const loadSessions = useAgent((x) => x.loadSessions);
  const setPalette = useStudio((x) => x.setPalette);
  const setSide = useDesk((d) => d.setSide);
  const narrow = useMedia('(max-width: 1060px)');
  const [showArchived, setShowArchived] = useState(false);
  useEffect(() => { void loadSessions(); }, [loadSessions]);
  const onPick = () => { if (narrow) setSide(false); };
  const inChat = route.view === 'agent' || route.view === 'lab';
  const { pinned, groups, archived } = useMemo(() => {
    const live = sessions.filter((x) => !x.archived);
    const g = new Map<string, SessionSummary[]>();
    for (const x of live.filter((y) => !y.pinned)) {
      const k = dayLabel(x.created_utc);
      g.set(k, [...(g.get(k) ?? []), x]);
    }
    return { pinned: live.filter((x) => x.pinned), groups: [...g.entries()], archived: sessions.filter((x) => x.archived) };
  }, [sessions]);
  return (
    <aside className={s.side} aria-label="任务">
      <div className={s.sideIn}>
        <div className={s.brand}>
          <Mark />
          <b>农业机器人路径规划</b>
          <button type="button" className={s.iconBtn} title="收起任务栏 ⌘B" aria-label="收起任务栏" onClick={() => setSide(false)}><Icon name="side" /></button>
        </div>
        <nav className={s.nav} aria-label="工作区">
          <a className={`${s.navRow} ${s.navNew}`} href="#/agent" aria-current={inChat && !sessionId ? 'page' : undefined} onClick={onPick}><Icon name="plus" /><span>新任务</span><kbd>⌘N</kbd></a>
          <button type="button" className={s.navRow} onClick={() => setPalette(true)}><Icon name="search" /><span>搜索</span><kbd>⌘K</kbd></button>
          {PLACES.map((p) => (
            <a key={p.href} className={s.navRow} href={p.href} aria-current={p.view.includes(route.view) ? 'page' : undefined} onClick={onPick}><Icon name={p.icon} /><span>{p.label}</span></a>
          ))}
        </nav>
        <div className={s.sess}>
          {pinned.length > 0 && <div><h3>置顶</h3>{pinned.map((x) => <SessionRow key={x.session_id} x={x} current={inChat && sessionId === x.session_id} onPick={onPick} />)}</div>}
          {groups.map(([k, list]) => <div key={k}><h3>{k}</h3>{list.map((x) => <SessionRow key={x.session_id} x={x} current={inChat && sessionId === x.session_id} onPick={onPick} />)}</div>)}
          {!pinned.length && !groups.length && <h3>还没有任务</h3>}
          {archived.length > 0 && <button type="button" className={s.more} onClick={() => setShowArchived(!showArchived)}>已归档 {archived.length}</button>}
          {showArchived && archived.map((x) => <SessionRow key={x.session_id} x={x} current={inChat && sessionId === x.session_id} onPick={onPick} />)}
        </div>
        <Jobs />
        <div className={s.foot}>
          <Status />
          <a className={s.iconBtn} href="#/system" title="设置" aria-label="设置" aria-current={route.view === 'system' ? 'page' : undefined} onClick={onPick}><Icon name="gear" /></a>
        </div>
      </div>
    </aside>
  );
}
