import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { EDITABLE, FieldEditor, OPTIONAL_LABEL } from '../agent/TaskPanel';
import { currentRuns } from '../agent/studioSync';
import { useAgent } from '../agent/store';
import type { AgentSession, ToolCall } from '../agent/api';
import { routeColor } from '../lab/views/plan/planModel';
import { Spinner } from '../lab/ui';
import { lab } from '../lab/api';
import type { PhysicsRun, Solution, XY } from '../lab/types';
import { GapFacts, PhysicsFacts } from './PhysicsPanel';
import { useStudio } from '../studio/store';
import { studio } from '../studio/api';
import { useLanes } from '../studio/editor/lanes';
import { signature } from '../studio/panels/PlanPanels';
import { PanelHost, PANELS, type PanelId } from '../studio/panelRegistry';
import type { SimResult } from '../studio/experiment/replay';
import type { FieldSimSummary, FleetInstance, FleetRoute } from '../studio/types';
import { ROOTS, encodeStack, type Frame } from './frames';
import { useDesk } from './store';
import { Icon } from './Icon';
import { LayerMap, boxOf, cumulative, type Box, type MapApi, type MapPlan, type MapView } from './LayerMap';
import { ACTOR, FIELD, SOURCE, cx, dateCN, n0, n1, n2, n3, obstacleName, showValue, timeCN, toolName, TOOL } from './format';
import { openFrames, runRecordHref, saveFile, sessionExportHref, useRunCache } from './nav';
import { Facts, Kvs, Row, Rows } from './ui';
import s from './desk.module.css';

type Service = { task: number; position: number; stop?: XY };
const routesOf = (r: Solution) => r.routes as FleetRoute[];
const metric = (r: Solution, k: string) => r.metrics[k] as number | null | undefined;
const verifyCall = (session: AgentSession, id: string): ToolCall | undefined =>
  session.tool_calls.find((c) => c.name === 'verify_run' && (c.arguments as { run_id?: string }).run_id === id && c.result);
interface Recheck { valid: boolean; agrees: boolean; items: { id: string; label: string; ok: boolean; detail: string }[]; checker?: string }
const recheckOf = (c?: ToolCall) => (c?.result as { recheck?: Recheck } | undefined)?.recheck;

interface Ctx {
  session: AgentSession;
  inst: FleetInstance | null;
  lanes: XY[][];
  lanePlots: { id: string; lanes: number; spacing_m: number }[];
  physics: PhysicsRun | null;
  run: (id: string) => Solution | undefined;
  exec: SimResult | null;
}

const push = (f: Frame) => useDesk.getState().push(f);
const planCache = new WeakMap<Solution, MapPlan>();
function planOf(r: Solution | undefined): MapPlan | null {
  if (!r) return null;
  let p = planCache.get(r);
  if (!p) {
    p = { key: r.run_id, routes: routesOf(r).map((x) => ({ name: x.vehicle_name, line: x.polyline, cost: x.cost, services: x.services })) };
    planCache.set(r, p);
  }
  return p;
}
const thin = (trail: number[][], gap = 0.35): XY[] => {
  const out: XY[] = [];
  let last: XY | null = null;
  for (const p of trail) {
    const xy: XY = [p[1], p[2]];
    if (!last || Math.hypot(xy[0] - last[0], xy[1] - last[1]) >= gap) { out.push(xy); last = xy; }
  }
  return out;
};

interface Meta { title: string; big?: boolean; noMap?: boolean; flush?: boolean; focus?: Box | null; view?: MapView }

function meta(f: Frame, c: Ctx): Meta {
  const inst = c.inst;
  switch (f.t) {
    case 'map': return { title: '任务地图', big: true };
    case 'plots': return { title: '地块' };
    case 'plot': {
      const p = inst?.plots.find((x) => x.id === f.id);
      return { title: p?.name ?? f.id, focus: p ? boxOf(p.polygon) : null, view: { plot: f.id } };
    }
    case 'obstacles': return { title: '障碍' };
    case 'obstacle': {
      const o = inst?.obstacles.find((x) => x.id === f.id);
      if (!o) return { title: f.id };
      const b = boxOf(o.polygon); const p = Math.max(b[2] - b[0], b[3] - b[1]) * 0.6;
      return { title: o.name || obstacleName(o.kind), focus: [b[0] - p, b[1] - p, b[2] + p, b[3] + p], view: { obstacle: f.id } };
    }
    case 'tasks': return { title: '任务点' };
    case 'task': {
      const t = inst?.tasks.find((x) => x.id === f.id);
      if (!t || !inst) return { title: String(f.id) };
      const w = boxOf(inst.boundary); const half = Math.max(5, Math.min(w[2] - w[0], w[3] - w[1]) * 0.14);
      return { title: t.label ?? String(t.id), focus: [t.xy[0] - half, t.xy[1] - half, t.xy[0] + half, t.xy[1] + half], view: { stop: { xy: t.xy, label: t.label ?? String(t.id) } } };
    }
    case 'plans': {
      const best = bestRun(c);
      return { title: '方案', view: { plan: planOf(best) } };
    }
    case 'plan': { const r = c.run(f.run); return { title: r?.algorithm.name ?? '方案', view: { plan: planOf(r) } }; }
    case 'vehicle': {
      const r = c.run(f.run); const x = r ? routesOf(r)[f.v] : undefined;
      return { title: x?.vehicle_name ?? '车辆', focus: x ? boxOf(x.polyline) : null, view: { plan: planOf(r), v: f.v } };
    }
    case 'stop': {
      const r = c.run(f.run); const x = r ? routesOf(r)[f.v] : undefined; const t = inst?.tasks.find((q) => q.id === f.task);
      const svc = x?.services.find((q) => q.task === f.task) as Service | undefined;
      const xy = t?.xy ?? svc?.stop ?? (svc && x ? x.polyline[svc.position] : undefined);
      if (!xy || !inst) return { title: t?.label ?? '任务点', view: { plan: planOf(r), v: f.v } };
      const w = boxOf(inst.boundary); const half = Math.max(4, Math.min(w[2] - w[0], w[3] - w[1]) * 0.13);
      const label = t?.label ?? `任务 ${f.task}`;
      return { title: label, focus: [xy[0] - half, xy[1] - half, xy[0] + half, xy[1] + half], view: { plan: planOf(r), v: f.v, stop: { xy, label } } };
    }
    case 'checks': return { title: verifyCall(c.session, f.run) ? '独立复核' : '方案检查', view: { plan: planOf(c.run(f.run)) } };
    case 'check': { const it = recheckOf(verifyCall(c.session, f.run))?.items.find((x) => x.id === f.id); return { title: it?.label ?? '检查项', view: { plan: planOf(c.run(f.run)) } }; }
    case 'params': return { title: '求解参数', view: { plan: planOf(c.run(f.run)) } };
    case 'repro': return { title: '可重现信息', view: { plan: planOf(c.run(f.run)) } };
    case 'raw': return { title: '原始记录', noMap: true };
    case 'exec': case 'execv': {
      const e = c.exec; const r = c.run(f.run);
      const names = e ? e.vehicles.map((v) => v.name) : [];
      const pick = f.t === 'execv' ? names.filter((n) => n === f.name) : names;
      const trails = e ? pick.map((n) => ({ line: thin((e.trails[n] ?? []) as unknown as number[][]), index: names.indexOf(n) })) : [];
      const label = f.t === 'execv' ? e?.vehicles.find((v) => v.name === f.name)?.label ?? '车辆' : '实际执行';
      const focusLine = f.t === 'execv' ? trails[0]?.line : undefined;
      return { title: label, big: f.t === 'exec', focus: focusLine?.length ? boxOf(focusLine) : null, view: { plan: planOf(r), trails } };
    }
    case 'phys': {
      const e = c.physics; const r = c.run(f.run);
      const names = (e?.vehicles ?? []).map((v) => v.name);
      const trails = names.map((n, index) => ({ line: thin((e?.trails?.[n] ?? []) as number[][]), index }));
      return { title: '物理预演', big: true, view: { plan: planOf(r), trails } };
    }
    case 'contract': return { title: '合同', noMap: true };
    case 'field': return { title: c.session.contract_cards.find((x) => x.key === f.key)?.label ?? FIELD[f.key] ?? f.key, noMap: true };
    case 'rev': return { title: `第 ${c.session.contract.history[f.i]?.revision ?? ''} 版`, noMap: true };
    case 'trace': return { title: '记录', noMap: true };
    case 'tool': { const x = c.session.tool_calls.find((q) => q.id === f.id); return { title: x ? toolName(x) : '工具调用', noMap: true }; }
    case 'llm': return { title: `模型调用 ${f.i + 1}`, noMap: true };
    case 'sim': return { title: '仿真', noMap: true };
    case 'panel': return { title: PANELS[f.id]?.title ?? f.id, noMap: true, flush: true };
    default: return { title: '' };
  }
}

function bestRun(c: Ctx): Solution | undefined {
  const list = currentRuns(c.session).map(c.run).filter((r): r is Solution => !!r && r.state === 'done' && !!r.check?.valid);
  return list.sort((a, b) => (metric(a, 'total_distance_m') ?? 1e18) - (metric(b, 'total_distance_m') ?? 1e18))[0];
}

/* --------------------------------------------------------------- layers */
const Empty = ({ children }: { children: ReactNode }) => <p className={s.emptyLine}>{children}</p>;

function MapRoot({ c }: { c: Ctx }) {
  const inst = c.inst;
  if (!inst) return <Empty>确认任务合同后，智能体建立的任务地图显示在这里。</Empty>;
  const b = boxOf(inst.boundary); const v = inst.vehicles;
  const id = c.session.scenario_id!;
  return (
    <div className={s.layer}>
      <h2>任务地图</h2>
      <Facts items={[['场地', `${n0(b[2] - b[0])} × ${n0(b[3] - b[1])}`, 'm'], ['任务点', inst.tasks.length, '个'], ['车辆', v.count ?? v.fleet?.length ?? '—', '台'], v.capacity != null && ['单车容量', v.capacity]]} />
      <Rows>
        <Row title="地块" value={`${inst.plots.length} 块`} onClick={() => push({ t: 'plots' })} />
        <Row title="障碍" value={`${inst.obstacles.length} 处`} onClick={() => push({ t: 'obstacles' })} />
        <Row title="任务点" value={`${inst.tasks.length} 个`} onClick={() => push({ t: 'tasks' })} />
      </Rows>
      <h3>动手</h3>
      <Rows>
        <Row title="编辑地图" sub="地块、障碍、任务点、车库与机耕道" onClick={() => push({ t: 'panel', id: 'map' })} />
        <Row title="车队与约束" sub="检查器：车辆、容量、趟次、通行方式" onClick={() => push({ t: 'panel', id: 'inspector' })} />
        <Row title="手动求解" sub="推荐、选算法、设预算与种子" onClick={() => push({ t: 'panel', id: 'advisor' })} />
        <Row title="全屏规划" sub="离开对话，用完整规划页" onClick={() => { location.hash = `#/plan/${encodeURIComponent(id)}`; }} />
      </Rows>
    </div>
  );
}

function Plots({ c }: { c: Ctx }) {
  const inst = c.inst!;
  return (
    <div className={s.layer}><h2>地块 {inst.plots.length} 块</h2>
      <Rows>{inst.plots.map((p) => {
        const l = c.lanePlots.find((x) => x.id === p.id); const n = inst.tasks.filter((t) => t.plot === p.id).length;
        return <Row key={p.id} title={p.name} sub={[p.crop, l ? `${l.lanes} 条通道` : null].filter(Boolean).join(' · ')} value={`${n} 个任务点`} onClick={() => push({ t: 'plot', id: p.id })} />;
      })}</Rows>
    </div>
  );
}
function Plot({ c, id }: { c: Ctx; id: string }) {
  const inst = c.inst!; const p = inst.plots.find((x) => x.id === id);
  if (!p) return <Empty>这块地已不在当前地图中。</Empty>;
  const b = boxOf(p.polygon); const l = c.lanePlots.find((x) => x.id === id);
  return (
    <div className={s.layer}><h2>{p.name}{p.crop ? ` · ${p.crop}` : ''}</h2>
      <Facts items={[['长宽', `${n1(b[2] - b[0])} × ${n1(b[3] - b[1])}`, 'm'], ['作物行方向', n0(p.rows_deg), '°'], l && ['通道', l.lanes, '条'], l && ['行距', n1(l.spacing_m), 'm']]} />
      <h3>任务点</h3>
      <Rows>{inst.tasks.filter((t) => t.plot === id).map((t) => <Row key={t.id} dense title={t.label} sub={`(${n1(t.xy[0])}, ${n1(t.xy[1])}) m`} value={`需求 ${t.demand}`} onClick={() => push({ t: 'task', id: t.id })} />)}</Rows>
    </div>
  );
}
function Obstacles({ c }: { c: Ctx }) {
  const o = c.inst!.obstacles;
  return <div className={s.layer}><h2>障碍 {o.length} 处</h2>{o.length ? <Rows>{o.map((x) => <Row key={x.id} title={x.name || obstacleName(x.kind)} sub={obstacleName(x.kind)} onClick={() => push({ t: 'obstacle', id: x.id })} />)}</Rows> : <p className={s.txt}>这张地图没有障碍。</p>}</div>;
}
function Obstacle({ c, id }: { c: Ctx; id: string }) {
  const o = c.inst!.obstacles.find((x) => x.id === id);
  if (!o) return <Empty>这处障碍已不在当前地图中。</Empty>;
  const b = boxOf(o.polygon);
  return <div className={s.layer}><h2>{o.name || obstacleName(o.kind)}</h2><Facts items={[['类型', obstacleName(o.kind)], ['外接尺寸', `${n1(b[2] - b[0])} × ${n1(b[3] - b[1])}`, 'm'], ['顶点', o.polygon.length, '个']]} /></div>;
}
function Tasks({ c }: { c: Ctx }) {
  const t = c.inst!.tasks;
  return <div className={s.layer}><h2>任务点 {t.length} 个</h2><Rows>{t.map((x) => <Row key={x.id} dense title={x.label} sub={`(${n1(x.xy[0])}, ${n1(x.xy[1])}) m`} value={`需求 ${x.demand}`} onClick={() => push({ t: 'task', id: x.id })} />)}</Rows></div>;
}
function Task({ c, id }: { c: Ctx; id: number }) {
  const inst = c.inst!; const t = inst.tasks.find((x) => x.id === id);
  if (!t) return <Empty>这个任务点已不在当前地图中。</Empty>;
  const p = inst.plots.find((x) => x.id === t.plot);
  return <div className={s.layer}><h2>{t.label}</h2><Kvs items={[['坐标', `(${n2(t.xy[0])}, ${n2(t.xy[1])}) m`], ['所属地块', p ? `${p.name}${p.crop ? ` · ${p.crop}` : ''}` : '—'], ['需求', t.demand], ['停留', `${t.service_s ?? 0} s`]]} /></div>;
}

function PlansRoot({ c, api }: { c: Ctx; api: React.RefObject<MapApi | null> }) {
  void api;
  const ids = currentRuns(c.session);
  const older = c.session.run_ids.filter((x) => !ids.includes(x));
  const list = ids.map(c.run).filter((r): r is Solution => !!r).sort((a, b) => (metric(a, 'total_distance_m') ?? 1e18) - (metric(b, 'total_distance_m') ?? 1e18));
  if (!ids.length && !older.length) return <Empty>确认任务合同后开始求解，方案会出现在这里。</Empty>;
  const max = Math.max(1, ...list.map((r) => metric(r, 'total_distance_m') ?? 0));
  const best = bestRun(c);
  const p = list[0]?.params as { time_limit_s?: number; seed?: number } | undefined;
  return (
    <div className={s.layer}>
      <h2>{list.length} 个方案</h2>
      {p && <p className={s.sub}>每个算法 {p.time_limit_s ?? '—'} s · 种子 {p.seed ?? '—'} · 同一任务地图</p>}
      <Rows>{list.map((r) => {
        const rc = recheckOf(verifyCall(c.session, r.run_id)); const v = c.session.verifications[r.run_id];
        const ok = rc ? rc.valid && rc.agrees : v ? v.valid && v.agrees : r.check?.valid;
        const running = r.state === 'queued' || r.state === 'running';
        return <Row key={r.run_id} title={r.algorithm.name}
          sub={running ? '求解中' : `${ok ? (rc || v ? '独立复核通过' : '检查通过') : '未通过'} · 最晚完成 ${n1(metric(r, 'makespan_s'))} s`}
          value={<><span className={s.barCell}><i style={{ width: `${(100 * (metric(r, 'total_distance_m') ?? 0)) / max}%` }} data-best={best?.run_id === r.run_id || undefined} /></span>{n1(metric(r, 'total_distance_m'))} m</>}
          onClick={() => push({ t: 'plan', run: r.run_id })} />;
      })}</Rows>
      <h3>深入</h3>
      <Rows>
        <Row title="方案对比表" sub="完整指标、取消运行、送入 Gazebo" onClick={() => push({ t: 'panel', id: 'compare' })} />
        <Row title="验算与导出" sub="逐项检查，JSON / CSV / GeoJSON" onClick={() => push({ t: 'panel', id: 'verify' })} />
        <Row title="实验结果" onClick={() => push({ t: 'panel', id: 'results' })} />
        <Row title="决策记录" onClick={() => push({ t: 'panel', id: 'decision' })} />
        <Row title="科研图表" onClick={() => push({ t: 'panel', id: 'figures' })} />
      </Rows>
      {older.length > 0 && <>
        <h3>历史运行</h3>
        <Rows>{older.slice().reverse().slice(0, 24).map((id) => { const r = c.run(id); return <Row key={id} dense title={r?.algorithm.name ?? id} sub="历史任务版本" value={r ? `${n1(metric(r, 'total_distance_m'))} m` : undefined} onClick={() => push({ t: 'plan', run: id })} />; })}</Rows>
      </>}
    </div>
  );
}

function useSimResults(runId: string) {
  const [list, setList] = useState<FieldSimSummary[]>([]);
  useEffect(() => {
    let live = true;
    studio.fieldsimResults(runId).then((r) => { if (live) setList(r.results); }).catch(() => undefined);
    return () => { live = false; };
  }, [runId]);
  return list;
}

function PlanLayer({ c, id, api }: { c: Ctx; id: string; api: React.RefObject<MapApi | null> }) {
  const r = c.run(id);
  const control = useAgent((x) => x.control);
  const sims = useSimResults(id);
  useEffect(() => { useStudio.setState({ focusRun: id, manualFocus: true }); }, [id]);
  if (!r) return <div className={s.layer}><Spinner /></div>;
  const rc = recheckOf(verifyCall(c.session, id));
  const passed = rc?.items.filter((x) => x.ok).length;
  const p = r.params as { time_limit_s?: number; seed?: number };
  const current = currentRuns(c.session).includes(id);
  const simulate = () => { void control('simulate', { run_id: id }); openFrames([{ t: 'sim' }, { t: 'panel', id: 'rosview' }]); };
  return (
    <div className={s.layer}>
      <h2>{r.algorithm.name}</h2>
      <p className={s.subMono}>{id}</p>
      {r.state === 'done' ? <Facts items={[['总里程', n1(metric(r, 'total_distance_m')), 'm'], ['最晚完成', n1(metric(r, 'makespan_s')), 's'], ['车辆', metric(r, 'vehicles_used') ?? '—', '台'], ['趟数', metric(r, 'trips') ?? '—'], ['求解', n2(metric(r, 'wall_s')), 's']]} /> : <p className={s.txt}>状态：{r.state}{r.error ? ` · ${r.error}` : ''}</p>}
      {!current && <p className={s.docWarn}>这是历史任务版本的方案。地图按它当时的冻结输入显示，不代表当前合同。</p>}
      <h3>车辆</h3>
      <Rows>{routesOf(r).map((x, i) => <Row key={i} lead={<span className={s.sw} style={{ background: routeColor(i) }} />} title={x.vehicle_name} sub={`${x.services.length} 个任务点 · ${x.trips?.length ?? 1} 趟 · 载重 ${x.load ?? '—'}`} value={`${n1(x.cost)} m`}
        onClick={() => push({ t: 'vehicle', run: id, v: i })} onEnter={() => api.current?.hover(i)} onLeave={() => api.current?.hover(null)} />)}</Rows>
      <h3>依据</h3>
      <Rows>
        {sims.map((x) => <Row key={x.session_id} title="实际执行" sub={`Gazebo · ${dateCN(x.finished_utc)}`} value={`${x.metrics.tasks_completed ?? '—'}/${x.metrics.tasks_total ?? '—'}`} onClick={() => push({ t: 'exec', run: id, session: x.session_id })} />)}
        <Row title={rc ? '独立复核' : '方案检查'} value={rc ? `${passed}/${rc.items.length} 项通过` : r.check?.valid ? '通过' : '未通过'} onClick={() => push({ t: 'checks', run: id })} />
        <Row title="求解参数" value={`${p.time_limit_s ?? '—'} s · 种子 ${p.seed ?? '—'}`} onClick={() => push({ t: 'params', run: id })} />
        <Row title="可重现信息" value={r.algorithm.origin} onClick={() => push({ t: 'repro', run: id })} />
      </Rows>
      <div className={s.nextRow}>
        {current && r.state === 'done' && <button type="button" className={s.btn} onClick={simulate}>送入 Gazebo 仿真</button>}
        <button type="button" className={s.btn} onClick={() => saveFile(runRecordHref(id), `${id}.json`)}>导出运行记录</button>
      </div>
    </div>
  );
}

function VehicleLayer({ c, f }: { c: Ctx; f: Extract<Frame, { t: 'vehicle' }> }) {
  const r = c.run(f.run); const x = r ? routesOf(r)[f.v] : undefined; const inst = c.inst;
  if (!r || !x || !inst) return <Empty>这辆车的路线不在当前记录中。</Empty>;
  const cum = cumulative(x.polyline);
  const byTask = new Map(inst.tasks.map((t) => [t.id, t]));
  const services = x.services as Service[];
  const trips = x.trips?.length ? x.trips : [services.map((q) => q.task)];
  const order = new Map(services.map((q, i) => [q.task, i + 1]));
  const capacity = inst.vehicles.capacity;
  return (
    <div className={s.layer}>
      <h2>{x.vehicle_name}</h2>
      <Facts items={[['里程', n1(x.cost), 'm'], ['用时', n1(x.duration_s), 's'], ['载重', `${x.load ?? '—'}${capacity ? `/${capacity}` : ''}`], ['趟数', trips.length]]} />
      {trips.length === 1 && <h3>访问顺序 · 累计里程</h3>}
      {trips.map((trip, ti) => (
        <div key={ti}>
          {trips.length > 1 && <h3 style={{ marginBottom: 6 }}>第 {ti + 1} 趟 · {trip.length} 个任务点</h3>}
          <Rows>{services.filter((q) => trip.includes(q.task)).map((q) => {
            const t = byTask.get(q.task); const p = inst.plots.find((y) => y.id === t?.plot);
            return <Row key={q.task} dense lead={<span className={s.idx}>{order.get(q.task)}</span>} title={t?.label ?? `任务 ${q.task}`} sub={p ? `${p.name} · ${p.crop}` : undefined} value={`${n1(cum[q.position])} m`} onClick={() => push({ t: 'stop', run: f.run, v: f.v, task: q.task })} />;
          })}</Rows>
        </div>
      ))}
    </div>
  );
}

function StopLayer({ c, f }: { c: Ctx; f: Extract<Frame, { t: 'stop' }> }) {
  const r = c.run(f.run); const x = r ? routesOf(r)[f.v] : undefined; const inst = c.inst;
  if (!x || !inst) return <Empty>这个任务点不在当前记录中。</Empty>;
  const services = x.services as Service[];
  const idx = services.findIndex((q) => q.task === f.task); const svc = services[idx];
  const t = inst.tasks.find((q) => q.id === f.task);
  if (!svc) return <Empty>这个任务点不在这辆车的路线上。</Empty>;
  const at = cumulative(x.polyline)[svc.position]; const sp = inst.vehicles.speed_mps ?? 1;
  const p = inst.plots.find((q) => q.id === t?.plot);
  return (
    <div className={s.layer}>
      <h2>{t?.label ?? `任务 ${f.task}`}</h2>
      <p className={s.sub}>{x.vehicle_name} 第 {idx + 1} 站</p>
      <Facts items={[['累计里程', n1(at), 'm'], ['恒速模型到达', n1(at / sp), 's'], ['需求', t?.demand ?? '—']]} />
      <Kvs items={[['所属地块', p ? `${p.name} · ${p.crop}` : '—'], t && ['任务点', `(${n2(t.xy[0])}, ${n2(t.xy[1])}) m`], ['停车点', svc.stop ? `(${n2(svc.stop[0])}, ${n2(svc.stop[1])}) m` : '该运行未记录'], ['停车偏移', svc.stop && t ? `${n2(Math.hypot(t.xy[0] - svc.stop[0], t.xy[1] - svc.stop[1]))} m` : '该运行未记录'], ['停留', `${t?.service_s ?? 0} s`]]} />
    </div>
  );
}

function Checks({ c, run }: { c: Ctx; run: string }) {
  const r = c.run(run); const rc = recheckOf(verifyCall(c.session, run));
  if (!r) return <div className={s.layer}><Spinner /></div>;
  const items = rc?.items ?? [{ id: 'check', label: '路线、载重、避障与里程检查', ok: !!r.check?.valid, detail: r.check?.errors?.length ? r.check.errors.join('；') : '无违规' }];
  return (
    <div className={s.layer}>
      <h2>{rc ? '独立复核' : '方案检查'}</h2>
      <p className={s.sub}>{rc?.checker ?? r.check?.checker}</p>
      <Rows>{items.map((x) => <Row key={x.id} wrap lead={<Icon name={x.ok ? 'check' : 'cross'} className={x.ok ? s.okMark : s.warnMark} />} title={x.label} sub={x.detail} onClick={() => push({ t: 'check', run, id: x.id })} />)}</Rows>
    </div>
  );
}
function CheckItem({ c, run, id }: { c: Ctx; run: string; id: string }) {
  const r = c.run(run); const rc = recheckOf(verifyCall(c.session, run));
  const it = rc?.items.find((x) => x.id === id);
  const routes = (r?.check as unknown as { routes?: { vehicle: string; load: number; length_m: number; collisions?: number; off_network?: number }[] } | null)?.routes ?? [];
  return (
    <div className={s.layer}>
      <h2>{it?.label ?? '方案检查'}</h2>
      {it && <p className={s.txt}>{it.detail}</p>}
      <Kvs items={[['判定', (it?.ok ?? r?.check?.valid) ? '通过' : '未通过'], ['复算里程', `${n3(r?.check?.computed_cost)} m`], ['检查器', r?.check?.checker ?? '—']]} />
      {routes.length > 0 && <>
        <h3>逐车结果</h3>
        <div className={s.tblWrap}><table className={s.tbl}><thead><tr><th>车辆</th><th>载重</th><th>里程 m</th><th>碰撞段</th><th>离网段</th></tr></thead>
          <tbody>{routes.map((x) => <tr key={x.vehicle}><td>{x.vehicle}</td><td>{x.load}</td><td>{n1(x.length_m)}</td><td>{x.collisions ?? '—'}</td><td>{x.off_network ?? '—'}</td></tr>)}</tbody></table></div>
      </>}
    </div>
  );
}
function Params({ c, run }: { c: Ctx; run: string }) {
  const r = c.run(run);
  if (!r) return <div className={s.layer}><Spinner /></div>;
  const p = r.params as { time_limit_s?: number; seed?: number; ground_policy?: string };
  const raw = (r.raw_result ?? {}) as { trip_assignment?: string; search_scale?: string; distance_model?: string };
  return <div className={s.layer}><h2>求解参数</h2><Kvs items={[['时间预算', `${p.time_limit_s ?? '—'} s`], ['随机种子', p.seed ?? '—'], ['目标', r.planning?.objective_label ?? '—'], ['实际耗时', `${n2(metric(r, 'wall_s'))} s`], ['地面策略', p.ground_policy === 'ignore' ? '忽略地面风险' : p.ground_policy ?? '—'], raw.trip_assignment && ['分趟方式', raw.trip_assignment], raw.search_scale && ['搜索尺度', raw.search_scale], raw.distance_model && ['距离模型', raw.distance_model]]} /></div>;
}
function Repro({ c, run }: { c: Ctx; run: string }) {
  const r = c.run(run);
  if (!r) return <div className={s.layer}><Spinner /></div>;
  const rep = (r as unknown as { reproducibility?: { python?: string; packages?: Record<string, string>; seed_usage?: string; stopping_rule?: string; execution_sha256?: string } }).reproducibility;
  return (
    <div className={s.layer}><h2>可重现信息</h2>
      <Kvs items={[['算法', `${r.algorithm.name} · ${r.algorithm.origin}`], rep?.python && ['Python', rep.python.split(' ')[0]], ...Object.entries(rep?.packages ?? {}).map(([k, v]) => [k, v] as [string, string]), rep?.seed_usage && ['种子用法', rep.seed_usage], rep?.stopping_rule && ['停止规则', rep.stopping_rule], ['输入哈希', <span key="i" className={s.mono}>{r.instance_sha256.slice(0, 24)}…</span>], rep?.execution_sha256 && ['执行哈希', <span key="e" className={s.mono}>{rep.execution_sha256.slice(0, 24)}…</span>]]} />
      <Rows><Row title="原始记录" sub="运行记录 JSON，不含路线折线" onClick={() => push({ t: 'raw', run })} /></Rows>
    </div>
  );
}
function Raw({ c, run }: { c: Ctx; run: string }) {
  const r = c.run(run);
  const text = useMemo(() => {
    if (!r) return '';
    const { routes, instance, scenario_snapshot, reproducibility, recommendation_at_submission, trace, ...rest } = r as unknown as Record<string, unknown>;
    void routes; void instance; void scenario_snapshot; void reproducibility; void recommendation_at_submission; void trace;
    return JSON.stringify(rest, null, 2);
  }, [r]);
  return <div className={s.layer}><h2>原始记录</h2><pre className={s.json}>{text}</pre></div>;
}

function Exec({ c, f }: { c: Ctx; f: Extract<Frame, { t: 'exec' }> }) {
  const e = c.exec;
  if (!e) return <div className={s.layer}><Spinner /></div>;
  const m = e.metrics as unknown as Record<string, number | null | undefined>;
  const check = (e as unknown as { check?: { valid?: boolean; track_tolerance_m?: number; scope?: string } }).check;
  const traffic = (e as unknown as { traffic?: { scope?: string } }).traffic;
  return (
    <div className={s.layer}>
      <h2>实际执行</h2>
      <p className={s.sub}>{e.algorithm ?? ''} · Gazebo Fortress。虚线为规划路线，实线为实测轨迹。</p>
      <Facts items={[['完成', `${m.tasks_completed ?? '—'}/${m.tasks_total ?? '—'}`], ['实际里程', n1(m.total_distance_m), 'm'], ['规划里程', n1(m.planned_total_distance_m), 'm'], ['实际工期', n1(m.makespan_s), 's'], ['横向偏差最大', n3(m.cross_track_max_m), 'm']]} />
      {traffic?.scope && <p className={s.txt}>{traffic.scope}；规划工期 {n1(m.planned_makespan_s)} s 为恒速估计，实际工期含排队与接驳。</p>}
      <h3>车辆</h3>
      <Rows>{e.vehicles.map((x, i) => <Row key={x.name} lead={<span className={s.sw} style={{ background: routeColor(i) }} />} title={x.label} sub={`${x.tasks_done}/${x.tasks_total} 个任务点`} value={`${n1(x.distance_m)} m`} onClick={() => push({ t: 'execv', run: f.run, session: f.session, name: x.name })} />)}</Rows>
      <h3>运行核验</h3>
      <Kvs items={[['判定', check?.valid ? '通过' : '未通过'], check?.track_tolerance_m != null && ['跟踪容差', `${check.track_tolerance_m} m`], check?.scope && ['范围', check.scope]]} />
      <Rows><Row title="回放" sub="按时间轴在地图上回放" onClick={() => push({ t: 'panel', id: 'replay' })} /></Rows>
    </div>
  );
}
function PhysicsLayer({ c }: { c: Ctx }) {
  const e = c.physics;
  if (!e) return <div className={s.layer}><Spinner /></div>;
  return (
    <div className={s.layer}>
      <h2>物理预演</h2>
      <p className={s.sub}>{e.algorithm ?? ''} · MuJoCo {e.engine_version}。虚线为规划路线，实线为物理轨迹。</p>
      {e.comparison && <><h3>与 Gazebo 的差距</h3><GapFacts gap={e.comparison} /></>}
      <PhysicsFacts r={e} />
      {!!e.vehicles?.length && <>
        <h3>车辆</h3>
        <Rows>{e.vehicles.map((x, i) => <Row key={x.name} dense lead={<span className={s.sw} style={{ background: routeColor(i) }} />} title={x.label} sub={`${x.tasks_done}/${x.tasks_total} 个任务点`} value={`${n1(x.distance_m)} m`} />)}</Rows>
      </>}
      {e.check?.scope && <p className={s.txt}>{e.check.scope}</p>}
    </div>
  );
}

function ExecVehicle({ c, f }: { c: Ctx; f: Extract<Frame, { t: 'execv' }> }) {
  const e = c.exec; const x = e?.vehicles.find((v) => v.name === f.name);
  if (!e || !x) return <div className={s.layer}><Spinner /></div>;
  const tasks = (e.tasks as unknown as { id: number; label: string; vehicle: string; t_sim?: number | null; distance_m?: number | null }[]).filter((t) => t.vehicle === f.name).sort((a, b) => (a.t_sim ?? 0) - (b.t_sim ?? 0));
  return (
    <div className={s.layer}><h2>{x.label}</h2>
      <Facts items={[['实测里程', n1(x.distance_m), 'm'], ['规划路径', n1(x.path_length_m), 'm'], ['完成度', n1((x.progress ?? 0) * 100), '%']]} />
      <h3>到达记录 · 仿真时钟</h3>
      <Rows>{tasks.map((t) => <Row key={t.id} dense title={t.label} sub={`到点距离 ${n2(t.distance_m)} m`} value={`${n1(t.t_sim)} s`} />)}</Rows>
    </div>
  );
}

function Contract({ c }: { c: Ctx }) {
  const session = c.session; const k = session.contract;
  const editContract = useAgent((x) => x.editContract);
  void editContract;
  const pending = useAgent((x) => x.sending || x.pendingAction !== null);
  const [adding, setAdding] = useState('');
  const locked = session.state === 'running' || !!session.restoration?.read_only;
  const present = new Set(session.contract_cards.map((x) => x.key));
  const addable = Object.keys(OPTIONAL_LABEL).filter((x) => !present.has(x));
  return (
    <div className={s.layer}>
      <h2>任务合同</h2>
      <p className={s.sub}>第 {k.revision} 版 · {k.status === 'confirmed' ? '已确认' : '草稿'}</p>
      {k.request_text && <p className={s.quote}>{k.request_text}</p>}
      {k.unsupported.length > 0 && <p className={s.docWarn}>未支持的要求，不进入求解：{k.unsupported.join('；')}</p>}
      {session.missing.length > 0 && <p className={s.docWarn}>仍缺少：{session.missing.map((m) => m.label).join('、')}</p>}
      <Rows>{session.contract_cards.map((x) => <Row key={x.key} dense title={x.label} sub={SOURCE[x.source] ?? x.source} value={x.text} onClick={() => push({ t: 'field', key: x.key })} />)}</Rows>
      {!locked && addable.length > 0 && (
        <div className={s.editRow}>
          <select value={adding} disabled={pending} aria-label="添加字段" onChange={(e) => setAdding(e.target.value)}>
            <option value="">添加约束或执行参数…</option>
            {addable.map((x) => <option key={x} value={x}>{OPTIONAL_LABEL[x]}</option>)}
          </select>
          {adding && <FieldEditor key={adding} card={{ key: adding, value: adding === 'simulate' ? 'false' : '' }} onDone={() => setAdding('')} />}
        </div>
      )}
      {k.history.length > 0 && <>
        <h3>修订</h3>
        <Rows>{k.history.map((x, i) => <Row key={i} dense title={`第 ${x.revision} 版`} sub={x.note} value={`${x.changes.length} 项`} onClick={() => push({ t: 'rev', i })} />)}</Rows>
      </>}
    </div>
  );
}
function Field({ c, k }: { c: Ctx; k: string }) {
  const session = c.session; const x = session.contract_cards.find((q) => q.key === k);
  const [editing, setEditing] = useState(false);
  if (!x) return <Empty>这一项已不在当前合同中。</Empty>;
  const req = session.contract.request_text ?? '';
  const hit = x.evidence && req.includes(x.evidence);
  const hist = session.contract.history.flatMap((v) => v.changes.filter((q) => q.key === k).map((q) => ({ v, q })));
  const locked = session.state === 'running' || !!session.restoration?.read_only;
  return (
    <div className={s.layer}>
      <h2>{x.label}</h2>
      <Facts items={[['取值', x.text], ['来源', SOURCE[x.source] ?? x.source]]} />
      {EDITABLE[k] && !locked && (editing ? <div className={s.editRow}><FieldEditor card={x} onDone={() => setEditing(false)} /></div> : <button type="button" className={s.go} onClick={() => setEditing(true)}>修改这一项</button>)}
      {!EDITABLE[k] && <p className={s.txt}>这一项请在地图上修改。</p>}
      <h3>依据</h3>
      {hit ? <p className={s.quote}>{req.split(x.evidence)[0]}<mark>{x.evidence}</mark>{req.split(x.evidence).slice(1).join(x.evidence)}</p> : <p className={s.quote}>{x.evidence || '没有原文依据'}</p>}
      {hist.length > 0 && <>
        <h3>修订</h3>
        <Rows>{hist.map(({ v, q }, i) => <Row key={i} dense title={`第 ${v.revision} 版`} sub={v.note} value={`${showValue(q.old)} → ${showValue(q.new)}`} />)}</Rows>
      </>}
    </div>
  );
}
function Rev({ c, i }: { c: Ctx; i: number }) {
  const v = c.session.contract.history[i];
  if (!v) return <Empty>没有这一版修订。</Empty>;
  return <div className={s.layer}><h2>第 {v.revision} 版</h2><p className={s.sub}>{v.note}</p><Rows>{v.changes.map((q, n) => <Row key={n} dense title={FIELD[q.key] ?? q.key} sub={SOURCE[(q as { source?: keyof typeof SOURCE }).source ?? 'default']} value={`${showValue(q.old)} → ${showValue(q.new)}`} />)}</Rows></div>;
}

const planned = (names: string[] | undefined) => {
  if (!names?.length) return '回复';
  const n: Record<string, number> = {};
  for (const x of names) n[x] = (n[x] ?? 0) + 1;
  return Object.entries(n).map(([k, v]) => `${TOOL[k] ?? k}${v > 1 ? ` × ${v}` : ''}`).join('、');
};

function Trace({ c }: { c: Ctx }) {
  const s0 = c.session; const u = s0.usage;
  const refused = s0.tool_calls.filter((x) => x.status === 'refused').length;
  const all = [
    ...s0.tool_calls.map((x) => ({ at: Date.parse(x.started_utc), tool: x, llm: null as null | (typeof s0.llm_calls)[number], i: 0 })),
    ...s0.llm_calls.map((x, i) => ({ at: Date.parse(x.utc) - (x.latency_s ?? 0) * 1000, tool: null as ToolCall | null, llm: x, i })),
  ].sort((a, b) => a.at - b.at);
  const version = s0.llm_calls[s0.llm_calls.length - 1] as unknown as { prompt_version?: string; runner_version?: string } | undefined;
  return (
    <div className={s.layer}>
      <h2>运行记录</h2>
      <p className={s.sub}>{[s0.provider?.label ?? '规则流程', version?.prompt_version, version?.runner_version].filter(Boolean).join(' · ')}</p>
      <Facts items={[['模型调用', u.calls, '次'], ['输入', n0(u.input_tokens), 'tokens'], ['输出', n0(u.output_tokens), 'tokens'], ['模型耗时', n1(u.latency_s), 's'], ['工具调用', s0.tool_calls.length, '次'], refused > 0 && ['被拒绝', refused, '次']]} />
      <h3>时间线</h3>
      <Rows>{all.map((x, n) => (x.tool
        ? <Row key={n} dense lead={<Icon name={x.tool.status === 'ok' ? 'check' : 'cross'} className={x.tool.status === 'ok' ? s.okMark : s.warnMark} />} title={toolName(x.tool)} sub={`${ACTOR[x.tool.actor] ?? x.tool.actor}${x.tool.status === 'refused' ? ' · 被拒绝' : ''}`} value={`${n2(x.tool.duration_s)} s`} onClick={() => push({ t: 'tool', id: x.tool!.id })} />
        : <Row key={n} dense lead={<span className={s.idx} />} title={<span style={{ color: 'var(--mac-label-3)' }}>模型思考 → {planned(x.llm!.tool_calls)}</span>} sub={`${n0(x.llm!.usage.input_tokens)} → ${n0(x.llm!.usage.output_tokens)} tokens`} value={`${n1(x.llm!.latency_s)} s`} onClick={() => push({ t: 'llm', i: x.i })} />))}</Rows>
      <div className={s.nextRow}><button type="button" className={s.btn} onClick={() => saveFile(sessionExportHref(s0.session_id), `${s0.session_id}.json`)}>导出会话实验包</button></div>
    </div>
  );
}
function Tool({ c, id }: { c: Ctx; id: string }) {
  const x = c.session.tool_calls.find((q) => q.id === id);
  if (!x) return <Empty>没有这次调用。</Empty>;
  return (
    <div className={s.layer}>
      <h2>{toolName(x)}</h2>
      <p className={s.subMono}>{x.name}</p>
      <Kvs items={[['状态', x.status === 'ok' ? '成功' : x.status === 'refused' ? '被拒绝' : '失败'], ['发起', ACTOR[x.actor] ?? x.actor], ['开始', timeCN(x.started_utc)], ['耗时', `${n3(x.duration_s)} s`], x.error && ['原因', x.error.message], x.step && ['步骤', x.step]]} />
      <h3>参数</h3>
      <pre className={s.json}>{JSON.stringify(x.arguments, null, 2)}</pre>
      {x.result && <><h3>结果</h3><pre className={s.json}>{JSON.stringify(x.result, null, 2)}</pre></>}
    </div>
  );
}
function Llm({ c, i }: { c: Ctx; i: number }) {
  const x = c.session.llm_calls[i] as (typeof c.session.llm_calls)[number] & { finish_reason?: string; prompt_version?: string; runner_version?: string };
  if (!x) return <Empty>没有这次调用。</Empty>;
  return (
    <div className={s.layer}>
      <h2>模型调用 {i + 1}</h2>
      <p className={s.sub}>{x.model} · {x.phase}</p>
      <Kvs items={[['时间', timeCN(x.utc)], ['延迟', `${n2(x.latency_s)} s`], ['尝试', x.attempts ?? '—'], ['输入', `${n0(x.usage.input_tokens)} tokens`], ['输出', `${n0(x.usage.output_tokens)} tokens`], ['请求工具', planned(x.tool_calls)], x.finish_reason && ['结束原因', x.finish_reason], x.prompt_version && ['提示版本', x.prompt_version], x.runner_version && ['运行器', x.runner_version], x.error && ['错误', x.error]]} />
    </div>
  );
}
function SimRoot() {
  return (
    <div>
      <div className={s.layer} style={{ paddingBottom: 6 }}>
        <Rows>
          <Row title="ROS 视图" sub="田块、规划路线、实际轨迹、激光与 Nav2 路径" onClick={() => push({ t: 'panel', id: 'rosview' })} />
          <Row title="Gazebo 场景" sub="正在运行的 Gazebo 世界" onClick={() => push({ t: 'panel', id: 'gazebo' })} />
          <Row title="遥测" sub="速度、路线完成度、横向偏差" onClick={() => push({ t: 'panel', id: 'telemetry' })} />
          <Row title="物理预演" sub="MuJoCo 记录与双引擎对比" onClick={() => push({ t: 'panel', id: 'physics' })} />
          <Row title="仿真回放" onClick={() => push({ t: 'panel', id: 'replay' })} />
          <Row title="TurtleBot4 仿真" onClick={() => push({ t: 'panel', id: 'sim' })} />
          <Row title="日志" onClick={() => push({ t: 'panel', id: 'console' })} />
        </Rows>
      </div>
      <PanelHost id="fieldsim" />
    </div>
  );
}

function Body({ f, c, api }: { f: Frame; c: Ctx; api: React.RefObject<MapApi | null> }) {
  switch (f.t) {
    case 'map': return <MapRoot c={c} />;
    case 'plots': return <Plots c={c} />;
    case 'plot': return <Plot c={c} id={f.id} />;
    case 'obstacles': return <Obstacles c={c} />;
    case 'obstacle': return <Obstacle c={c} id={f.id} />;
    case 'tasks': return <Tasks c={c} />;
    case 'task': return <Task c={c} id={f.id} />;
    case 'plans': return <PlansRoot c={c} api={api} />;
    case 'plan': return <PlanLayer c={c} id={f.run} api={api} />;
    case 'vehicle': return <VehicleLayer c={c} f={f} />;
    case 'stop': return <StopLayer c={c} f={f} />;
    case 'checks': return <Checks c={c} run={f.run} />;
    case 'check': return <CheckItem c={c} run={f.run} id={f.id} />;
    case 'params': return <Params c={c} run={f.run} />;
    case 'repro': return <Repro c={c} run={f.run} />;
    case 'raw': return <Raw c={c} run={f.run} />;
    case 'exec': return <Exec c={c} f={f} />;
    case 'execv': return <ExecVehicle c={c} f={f} />;
    case 'phys': return <PhysicsLayer c={c} />;
    case 'contract': return <Contract c={c} />;
    case 'field': return <Field c={c} k={f.key} />;
    case 'rev': return <Rev c={c} i={f.i} />;
    case 'trace': return <Trace c={c} />;
    case 'tool': return <Tool c={c} id={f.id} />;
    case 'llm': return <Llm c={c} i={f.i} />;
    case 'sim': return <SimRoot />;
    case 'panel': return <PanelHost id={f.id} />;
    default: return null;
  }
}

/* -------------------------------------------------------- the layer pane */
export function SessionPane() {
  const session = useAgent((x) => x.session);
  const stack = useDesk((d) => d.stack);
  const dir = useDesk((d) => d.dir);
  const setStack = useDesk((d) => d.setStack);
  const pop = useDesk((d) => d.pop);
  const setPane = useDesk((d) => d.setPane);
  const paneWide = useDesk((d) => d.paneWide);
  const setPaneWide = useDesk((d) => d.setPaneWide);
  const scenario = useStudio((x) => x.scenario);
  const draft = useStudio((x) => x.draft);
  const studioRuns = useStudio((x) => x.runs);
  const cached = useRunCache((x) => x.runs);
  const want = useRunCache((x) => x.want);
  const [exec, setExec] = useState<SimResult | null>(null);
  const [physics, setPhysics] = useState<PhysicsRun | null>(null);
  const api = useRef<MapApi | null>(null);

  const matches = !!session?.scenario_id && scenario?.id === session.scenario_id && scenario.problem_class === 'fleet_routing' && !!draft;
  const baseInst = matches ? (draft as unknown as FleetInstance) : null;
  const laneInfo = useLanes(baseInst, !!baseInst && baseInst.vehicles.travel === 'rows');
  const top = stack[stack.length - 1];
  const wideFrame = top?.t === 'panel' && WIDE.has(top.id);
  useEffect(() => { setPaneWide(wideFrame); }, [wideFrame, setPaneWide]);
  const topRun = top && 'run' in top ? top.run : null;
  const run = useMemo(() => (id: string) => studioRuns[id] ?? cached[id], [studioRuns, cached]);
  useEffect(() => { for (const f of stack) if ('run' in f && !run(f.run)) want(f.run); }, [stack, run, want]);
  useEffect(() => {
    if (session) for (const id of currentRuns(session)) if (!run(id)) want(id);
  }, [session, run, want]);
  const execId = top && (top.t === 'exec' || top.t === 'execv') ? top.session : null;
  useEffect(() => {
    if (!execId) { setExec(null); return; }
    let live = true;
    studio.fieldsimResult(execId).then((r) => { if (live) setExec(r); }).catch(() => undefined);
    return () => { live = false; };
  }, [execId]);

  const physicsId = top?.t === 'phys' ? top.id : null;
  useEffect(() => {
    if (!physicsId) { setPhysics(null); return; }
    let live = true;
    lab.physicsRun(physicsId).then((r) => { if (live) setPhysics(r); }).catch(() => undefined);
    return () => { live = false; };
  }, [physicsId]);

  if (!session) return <div className={s.paneIn}><Empty>描述任务后，地图、方案、合同与运行记录在这里逐层展开。</Empty></div>;

  // A historical run keeps the input it was solved on; the current draft is not drawn under it.
  const frozen = topRun ? (run(topRun)?.instance as unknown as FleetInstance | undefined) : undefined;
  const sameAsDraft = !!frozen && !!baseInst && signature(frozen) === signature(baseInst);
  const inst = frozen && !sameAsDraft ? frozen : baseInst;
  const lanes = inst === baseInst ? laneInfo?.lanes ?? [] : [];
  const c: Ctx = { session, inst, lanes, lanePlots: laneInfo?.plots ?? [], run, exec, physics };
  const metas = stack.map((f) => meta(f, c));
  const m = metas[metas.length - 1] ?? { title: '' };
  const root = stack[0]?.t;
  const showMap = !!inst && !m.noMap && !!top;
  const sceneKey = inst ? `${session.scenario_id}:${signature(inst).length}:${inst === baseInst ? 'draft' : topRun}` : '';
  return (
    <div className={s.paneIn}>
      <div className={s.tabs} role="tablist">
        {ROOTS.filter((r) => r.t !== 'map' || session.scenario_id).map((r) => (
          <button key={r.t} type="button" role="tab" className={s.tab} aria-selected={root === r.t}
            onClick={() => (root === r.t ? stack.length > 1 && setStack([stack[0]], 'pop') : openFrames([{ t: r.t } as Frame]))}>{r.label}</button>
        ))}
        <span className={s.grow} />
        <button type="button" className={s.iconBtn} title={paneWide ? '恢复宽度' : '放大面板'} aria-pressed={paneWide} aria-label="放大面板" onClick={() => setPaneWide(!paneWide)}><Icon name="widen" /></button>
        <button type="button" className={s.iconBtn} title="关闭面板 Esc" aria-label="关闭面板" onClick={() => setPane('agent', false)}><Icon name="close" /></button>
      </div>
      {stack.length > 1 && (
        <div className={s.crumbs}>
          <button type="button" className={cx(s.iconBtn, s.back)} title="上一层 Esc" aria-label="上一层" onClick={() => pop()}><Icon name="left" /></button>
          {metas.map((x, i) => (i === metas.length - 1 ? <b key={i}>{x.title}</b>
            : <span key={i} style={{ display: 'contents' }}><button type="button" className={s.c} onClick={() => setStack(stack.slice(0, i + 1), 'pop')}>{x.title}</button><i>/</i></span>))}
        </div>
      )}
      {showMap && (
        <div className={s.mapBox} data-big={m.big || undefined}>
          <LayerMap sceneKey={sceneKey} inst={inst!} lanes={lanes} view={m.view ?? EMPTY} focus={m.focus ?? null} apiRef={api}
            onRoute={(v) => { if (top?.t === 'plan') push({ t: 'vehicle', run: top.run, v }); }}
            onTask={(task) => {
              if (top?.t === 'plan') {
                const r = run(top.run); const v = r ? routesOf(r).findIndex((x) => x.services.some((q) => q.task === task)) : -1;
                if (v >= 0) setStack([...stack, { t: 'vehicle', run: top.run, v }, { t: 'stop', run: top.run, v, task }]);
              } else if (top?.t === 'vehicle' || top?.t === 'stop') {
                const r = run(top.run);
                if (r && routesOf(r)[top.v].services.some((q) => q.task === task)) setStack([...(top.t === 'stop' ? stack.slice(0, -1) : stack), { t: 'stop', run: top.run, v: top.v, task }]);
              } else push({ t: 'task', id: task });
            }} />
        </div>
      )}
      <div className={m.flush ? s.bodyFlush : s.body}>
        {top && <div key={encodeStack(stack)} className={cx(dir === 'pop' ? s.pop : s.push)} style={m.flush ? { height: '100%' } : undefined}><Body f={top} c={c} api={api} /></div>}
      </div>
    </div>
  );
}
const EMPTY: MapView = {};
/** Tools that need room open the pane wide; the conversation stays on screen beside them. */
const WIDE = new Set<PanelId>(['map', 'compare', 'figures', 'results', 'replay', 'rosview', 'gazebo', 'telemetry']);
