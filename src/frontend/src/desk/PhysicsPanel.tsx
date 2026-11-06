import { useMemo, useState } from 'react';
import { lab, useLabResource } from '../lab/api';
import type { EngineGap, PhysicsRun } from '../lab/types';
import { studio } from '../studio/api';
import { useStudio } from '../studio/store';
import { arrivalLine, lateLine } from './arrivals';
import { BatchFacts } from './PhysicsBatch';
import { n1, n3, timeCN, dateCN } from './format';
import { Facts, Fold, Kvs, Row, Rows } from './ui';
import s from './desk.module.css';

const STATE: Record<string, string> = { running: '运行中', succeeded: '完成', failed: '失败', canceled: '已取消', truncated: '到时限' };

export function GapFacts({ gap }: { gap: EngineGap }) {
  const m = gap.metrics;
  const pair = (k: string, unit: string, f: (v?: number | null) => string = n1) => `${f(m[k]?.a)} / ${f(m[k]?.b)} ${unit}`;
  return <>
    <Facts items={[['轨迹差 平均', n3(gap.path_mean_m), 'm'], ['轨迹差 最大', n3(gap.path_max_m), 'm'], ['同时刻 RMSE', n3(gap.time_rmse_m), 'm']]} />
    <Kvs items={[['里程 Gazebo / MuJoCo', pair('total_distance_m', 'm', n3)], ['工期', pair('makespan_s', 's')], ['最大横向偏差', pair('cross_track_max_m', 'm', n3)]]} />
  </>;
}

export function PhysicsFacts({ r }: { r: PhysicsRun }) {
  const m = r.metrics ?? {};
  return <>
    <Facts items={[['完成', `${m.tasks_completed ?? '—'}/${m.tasks_total ?? '—'}`], ['实际里程', n1(m.total_distance_m), 'm'], ['规划里程', n1(m.planned_total_distance_m), 'm'], ['实际工期', n1(m.makespan_s), 's'], ['横向偏差最大', n3(m.cross_track_max_m), 'm']]} />
    <Kvs items={[r.traffic_mode === 'coordinated' && ['执行方式', `并行 · 路线相遇 ${r.traffic?.meetings ?? '—'} 处 · 等待 ${r.traffic?.holds ?? 0} 次共 ${n1(r.traffic?.held_s ?? 0)} s`],
      r.traffic?.min_gap_m != null && ['最小车距', `${n3(r.traffic.min_gap_m)} m`], !!r.time_windows && ['实测到达', arrivalLine(r.time_windows)], ['运行核验', r.check?.valid ? '通过' : '未通过'], !!r.wall_s && ['用时', `${n1(r.wall_s)} s · ${n1(r.real_time_factor)}× 实时`], !!r.contacts?.length && ['接触', r.contacts.map((c) => c.between.join(' / ')).join('；')]]} />
    {[...r.errors, ...(r.check?.errors ?? [])].slice(0, 4).map((e) => <p key={e} className={s.docBad}>{e}</p>)}
    {!!r.time_windows?.late?.length && <p className={s.docBad}>晚于截止：{lateLine(r.time_windows)}</p>}
  </>;
}

function BatchFold({ r, onCancel }: { r: PhysicsRun; onCancel: () => void }) {
  const sum = r.summary;
  const line = sum ? `${sum.passed}/${sum.finished} 通过${sum.makespan_s.n ? ` · 中位 ${n1(sum.makespan_s.median)} s · P90 ${n1(sum.makespan_s.p90)} s` : ''}` : `${r.progress?.runs_done ?? 0}/${r.progress?.runs_total ?? '—'} 次`;
  return (
    <Fold summary={<span className={s.physRow}><b>{r.algorithm ?? r.run_id}</b><small>鲁棒性 · {r.traffic_mode === 'coordinated' ? '并行' : '逐车'} · {r.state === 'succeeded' ? line : `${STATE[r.state] ?? r.state} · ${line}`}</small></span>}>{() => <BatchFacts r={r} onCancel={onCancel} />}</Fold>
  );
}

function RunFold({ r }: { r: PhysicsRun }) {
  const line = r.comparison?.path_max_m != null ? `最大差 ${n3(r.comparison.path_max_m)} m` : r.metrics ? `${r.traffic_mode === 'coordinated' ? '并行 · ' : ''}${n1(r.metrics.total_distance_m)} m · ${n1(r.metrics.makespan_s)} s` : '';
  return (
    <Fold summary={<span className={s.physRow}><b>{r.algorithm ?? r.run_id}</b><small>{STATE[r.state] ?? r.state}{line ? ` · ${line}` : ''}</small></span>}>{() => <>
      {r.comparison ? <GapFacts gap={r.comparison} /> : null}
      <PhysicsFacts r={r} />
    </>}</Fold>
  );
}

/** Physics runs of this machine. MuJoCo needs no ROS and no approval, so any verified plan can run here at once. */
export function PhysicsPanel() {
  const engines = useLabResource('physics-engines', () => lab.physicsEngines());
  const runs = useLabResource('physics-runs', () => lab.physicsRuns(), { reloadOn: 'physics_run' });
  const gazebo = useLabResource('physics-gazebo', () => studio.fieldsimResults());
  const focus = useStudio((x) => x.focusRun);
  const plan = useStudio((x) => (x.focusRun ? x.runs[x.focusRun] : undefined));
  const notify = useStudio((x) => x.notify);
  const [busy, setBusy] = useState(false);
  const engine = engines.data?.engines[0];
  const list = runs.data?.runs ?? [];
  const compared = useMemo(() => new Set(list.filter((r) => r.session_id).map((r) => r.session_id)), [list]);
  const gaps = list.filter((r) => r.session_id);
  const plans = list.filter((r) => !r.session_id);
  const sessions = (gazebo.data?.results ?? []).filter((x) => x.state === 'succeeded' && !compared.has(x.session_id)).slice(0, 8);
  const start = async (call: () => Promise<unknown>) => {
    setBusy(true);
    try { await call(); runs.reload(); } catch (e) { notify('error', (e as Error).message); } finally { setBusy(false); }
  };
  const submit = (body: { run_id?: string; session_id?: string; traffic?: 'serial' | 'coordinated' }) => start(() => lab.physicsSubmit(body));
  const evaluate = (run_id: string, traffic: 'serial' | 'coordinated') => start(() => lab.physicsBatch({ run_id, traffic }));
  return (
    <div className={s.layer}>
      <h2>物理预演</h2>
      <p className={s.sub}>{engine?.available ? `MuJoCo ${engine.version} · 与 Gazebo 同一方案、同一路径跟踪器、同一车辆参数` : engine?.reason ?? '正在读取物理引擎'}</p>
      {focus && plan?.problem_class === 'fleet_routing' && plan.state === 'done' && (
        <div className={s.nextRow}>
          <button type="button" className={s.btn} disabled={busy || !engine?.available} onClick={() => void submit({ run_id: focus })}>逐车执行 · {plan.algorithm.name}</button>
          {(plan.routes?.length ?? 0) > 1 && <button type="button" className={s.btn} disabled={busy || !engine?.available} onClick={() => void submit({ run_id: focus, traffic: 'coordinated' })}>并行执行</button>}
          <button type="button" className={s.btn} disabled={busy || !engine?.available} onClick={() => void evaluate(focus, 'serial')}>鲁棒性 · 逐车</button>
          {(plan.routes?.length ?? 0) > 1 && <button type="button" className={s.btn} disabled={busy || !engine?.available} onClick={() => void evaluate(focus, 'coordinated')}>鲁棒性 · 并行</button>}
        </div>
      )}
      {sessions.length > 0 && <>
        <h3>可对比的 Gazebo 记录</h3>
        <Rows>{sessions.map((x) => <Row key={x.session_id} dense title={x.algorithm ?? x.run_id} sub={x.finished_utc ? `${dateCN(x.finished_utc)} ${timeCN(x.finished_utc)}` : x.session_id} value={<button type="button" className={s.go} disabled={busy} onClick={() => void submit({ session_id: x.session_id })}>对比</button>} />)}</Rows>
      </>}
      {gaps.length > 0 && <>
        <h3>引擎对比 · Gazebo 与 MuJoCo</h3>
        <div className={s.folds}>{gaps.map((r) => <RunFold key={r.physics_run_id} r={r} />)}</div>
      </>}
      <h3>方案预演</h3>
      {!plans.length && !runs.loading && <p className={s.dim}>还没有物理运行。</p>}
      <div className={s.folds}>{plans.slice(0, 30).map((r) => (r.kind === 'batch'
        ? <BatchFold key={r.physics_run_id} r={r} onCancel={() => void lab.physicsCancel(r.physics_run_id).catch((e) => notify('error', (e as Error).message))} />
        : <RunFold key={r.physics_run_id} r={r} />))}</div>
    </div>
  );
}
