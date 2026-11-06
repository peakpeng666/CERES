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

const STATE: Record<string, string> = { queued: '排队中', running: '运行中', succeeded: '完成', failed: '失败', canceled: '已取消', truncated: '到时限' };
const COMPARISON_METHOD = 'https://github.com/peakpeng666/URP/blob/main/setup/README.md#仿真与引擎比较';

export function GapFacts({ gap }: { gap: EngineGap }) {
  if (gap.available === false) return <p className={s.docBad} role="alert">引擎对比不可用：{gap.errors?.join('；') || gap.scope || '无法核验两次执行的条件一致'}</p>;
  const m = gap.metrics;
  const rows = [
    { key: 'total_distance_m', label: '实际里程', unit: 'm', format: n3 },
    { key: 'makespan_s', label: '实际工期', unit: 's', format: n1 },
    { key: 'cross_track_max_m', label: '最大横向偏差', unit: 'm', format: n3 },
  ];
  return <div className={s.comparison}>
    {gap.available !== true && <p className={s.docWarn}>历史对比未核验输入身份，不能据此确认两次执行条件一致。</p>}
    <Facts items={[['最大轨迹差', n3(gap.path_max_m), 'm'], ['平均轨迹差', n3(gap.path_mean_m), 'm']]} />
    <table className={s.comparisonTable} aria-label="Gazebo 与 MuJoCo 仿真指标">
      <thead><tr><th scope="col">仿真指标</th><th scope="col">Gazebo</th><th scope="col">MuJoCo</th></tr></thead>
      <tbody>{rows.map(({ key, label, unit, format }) => <tr key={key}>
        <th scope="row">{label}<small> ({unit})</small></th>
        <td>{format(m[key]?.a)}</td><td>{format(m[key]?.b)}</td>
      </tr>)}</tbody>
    </table>
    <details className={s.physicsDetails}>
      <summary>时钟口径与比较方法</summary>
      <div className={s.physicsDetailBody}>
        <Kvs items={[[gap.time_alignment === 'per_vehicle_departure' ? '各车发车对齐 RMSE' : '同时刻 RMSE', `${n3(gap.time_rmse_m)} m`],
          gap.time_alignment === 'per_vehicle_departure' && ['任务同钟 RMSE', `${n3(gap.mission_clock_rmse_m)} m`]]} />
        <p className={s.physicsNote}>{gap.time_alignment === 'per_vehicle_departure' ? '发车对齐剔除排队差异；任务同钟保留排队。' : '历史记录未提供时钟对齐方式。'} 轨迹差是两个仿真引擎之间的差异。<a className={s.go} href={COMPARISON_METHOD} target="_blank" rel="noreferrer">比较方法</a></p>
      </div>
    </details>
  </div>;
}

function PhysicsIssues({ r }: { r: PhysicsRun }) {
  return <>
    {[...new Set([...r.errors, ...(r.check?.errors ?? [])])].slice(0, 4).map((e) => <p key={e} className={s.docBad} role="alert">{e}</p>)}
    {!!r.time_windows?.late?.length && <p className={s.docBad} role="alert">晚于截止：{lateLine(r.time_windows)}</p>}
  </>;
}

export function PhysicsFacts({ r, showIssues = true }: { r: PhysicsRun; showIssues?: boolean }) {
  const m = r.metrics ?? {};
  return <>
    <Facts items={[['完成', `${m.tasks_completed ?? '—'}/${m.tasks_total ?? '—'}`], ['实际里程', n1(m.total_distance_m), 'm'], ['规划里程', n1(m.planned_total_distance_m), 'm'], ['实际工期', n1(m.makespan_s), 's'], ['横向偏差最大', n3(m.cross_track_max_m), 'm']]} />
    <Kvs items={[r.traffic_mode === 'coordinated' && ['执行方式', `并行 · 路线相遇 ${r.traffic?.meetings ?? '—'} 处 · 等待 ${r.traffic?.holds ?? 0} 次共 ${n1(r.traffic?.held_s ?? 0)} s`],
      r.traffic?.min_gap_m != null && ['最小车距', `${n3(r.traffic.min_gap_m)} m`], !!r.time_windows && ['实测到达', arrivalLine(r.time_windows)], ['运行核验', r.check?.valid ? '通过' : '未通过'], !!r.wall_s && ['用时', `${n1(r.wall_s)} s · ${n1(r.real_time_factor)}× 实时`], !!r.contacts?.length && ['接触', r.contacts.map((c) => c.between.join(' / ')).join('；')]]} />
    {showIssues && <PhysicsIssues r={r} />}
  </>;
}

function BatchFold({ r, onCancel }: { r: PhysicsRun; onCancel: () => void }) {
  const sum = r.summary;
  const line = sum ? `${sum.passed}/${sum.finished} 通过${sum.makespan_s.n ? ` · 中位 ${n1(sum.makespan_s.median)} s · P90 ${n1(sum.makespan_s.p90)} s` : ''}` : `${r.progress?.runs_done ?? 0}/${r.progress?.runs_total ?? '—'} 次`;
  return (
    <Fold summary={<span className={s.physRow}><b>{r.algorithm ?? r.run_id}</b><small>鲁棒性 · {r.traffic_mode === 'coordinated' ? '并行' : '逐车'} · {r.state === 'succeeded' ? line : `${STATE[r.state] ?? r.state} · ${line}`}</small></span>}>{() => <BatchFacts r={r} onCancel={onCancel} />}</Fold>
  );
}

function RunFold({ r, onRetry, disabled }: { r: PhysicsRun; onRetry?: () => void; disabled?: boolean }) {
  const traffic = r.traffic_mode === 'coordinated' ? '并行' : r.traffic_mode === 'serial' ? '逐车' : '交通方式未知';
  const resultLine = r.comparison?.available === false ? '无法核验同条件' : r.comparison?.path_max_m != null ? `最大差 ${n3(r.comparison.path_max_m)} m` : r.metrics ? `${n1(r.metrics.total_distance_m)} m · ${n1(r.metrics.makespan_s)} s` : '';
  const line = `${traffic}${resultLine ? ` · ${resultLine}` : ''}`;
  const recorded = r.finished_utc || r.created_utc;
  const validTime = recorded && Number.isFinite(Date.parse(recorded));
  const identity = `${validTime ? `${dateCN(recorded)} ${timeCN(recorded)} · ` : ''}记录 ${r.physics_run_id.slice(-8)}`;
  const check = r.check?.valid === true ? '通过' : r.check?.valid === false ? '未通过' : ['queued', 'running'].includes(r.state) ? '未完成' : '未记录';
  return (
    <Fold defaultOpen={!!onRetry || r.comparison?.available === false} summary={<span className={s.physSummary}>
      <span className={s.physRow}><b>{r.algorithm ?? r.run_id}</b><small>{STATE[r.state] ?? r.state}{line ? ` · ${line}` : ''}</small></span>
      <small className={s.physIdentity} title={`MuJoCo: ${r.physics_run_id}${r.session_id ? ` · Gazebo: ${r.session_id}` : ''}`}>{identity}</small>
    </span>}>{() => <div className={s.physicsBody}>
      {r.comparison ? <GapFacts gap={r.comparison} /> : null}
      {r.session_id ? <>
        <p className={r.check?.valid === false ? s.docBad : s.physicsNote}>MuJoCo 核验：{check}{r.time_windows ? ` · ${arrivalLine(r.time_windows)}` : ''}</p>
        <PhysicsIssues r={r} />
        <details className={s.physicsDetails}>
          <summary>MuJoCo 执行详情</summary>
          <div className={s.physicsDetailBody}>
            <PhysicsFacts r={r} showIssues={false} />
            <Kvs items={[[ 'MuJoCo 记录', r.physics_run_id], ['Gazebo 会话', r.session_id]]} />
          </div>
        </details>
      </> : <PhysicsFacts r={r} />}
      {onRetry && <button type="button" className={s.btn} disabled={disabled} onClick={onRetry}>重试对比</button>}
    </div>}</Fold>
  );
}

/** Physics runs of this machine. MuJoCo needs no ROS and no approval, so any verified plan can run here at once. */
export function PhysicsPanel() {
  const engines = useLabResource('physics-engines', () => lab.physicsEngines());
  const runs = useLabResource('physics-runs', () => lab.physicsRuns(), { reloadOn: 'physics_run' });
  const gazebo = useLabResource('physics-gazebo', () => studio.fieldsimResults(), { pollMs: 5000 });
  const focus = useStudio((x) => x.focusRun);
  const plan = useStudio((x) => (x.focusRun ? x.runs[x.focusRun] : undefined));
  const notify = useStudio((x) => x.notify);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const engine = engines.data?.engines.find((x) => x.id === 'mujoco');
  const available = !!engine?.available && !engines.error;
  const list = runs.data?.runs ?? [];
  const compared = useMemo(() => new Set(list.filter((r) => r.session_id && ((r.comparison && r.comparison.available !== false) || ['queued', 'running'].includes(r.state))).map((r) => r.session_id)), [list]);
  const gaps = list.filter((r) => r.session_id);
  const plans = list.filter((r) => !r.session_id);
  const sessions = (gazebo.data?.results ?? []).filter((x) => x.state === 'succeeded' && !compared.has(x.session_id)).slice(0, 8);
  const start = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try { await call(); } catch (e) {
      const message = (e as Error).message;
      setActionError(message);
      notify('error', message);
    } finally { runs.reload(); setBusy(false); }
  };
  const submit = (body: { run_id?: string; session_id?: string; traffic?: 'serial' | 'coordinated' }) => start(() => lab.physicsSubmit(body));
  const evaluate = (run_id: string, traffic: 'serial' | 'coordinated') => start(() => lab.physicsBatch({ run_id, traffic }));
  return (
    <div className={s.layer}>
      <h2>物理预演</h2>
      <p className={s.sub}>{available ? `MuJoCo ${engine.version} · 方案预演与 Gazebo 记录对比` : engines.loading ? '正在读取物理引擎' : engines.error ? '物理引擎状态读取失败' : engine?.reason ?? '未读取到物理引擎'}</p>
      {([['物理引擎', engines], ['物理运行', runs], ['Gazebo 记录', gazebo]] as const).map(([name, resource]) => resource.error && (
        <p key={name} className={s.docBad} role="alert">{name}读取失败：{resource.error.message} <button type="button" className={s.go} onClick={resource.reload}>重新读取{name}</button></p>
      ))}
      {actionError && <p className={s.docBad} role="alert">{actionError}</p>}
      {focus && plan?.problem_class === 'fleet_routing' && plan.state === 'done' && (
        <div className={s.nextRow}>
          <button type="button" className={s.btn} disabled={busy || !available} onClick={() => void submit({ run_id: focus })}>逐车执行 · {plan.algorithm.name}</button>
          {(plan.routes?.length ?? 0) > 1 && <button type="button" className={s.btn} disabled={busy || !available} onClick={() => void submit({ run_id: focus, traffic: 'coordinated' })}>并行执行</button>}
          <button type="button" className={s.btn} disabled={busy || !available} onClick={() => void evaluate(focus, 'serial')}>鲁棒性 · 逐车</button>
          {(plan.routes?.length ?? 0) > 1 && <button type="button" className={s.btn} disabled={busy || !available} onClick={() => void evaluate(focus, 'coordinated')}>鲁棒性 · 并行</button>}
        </div>
      )}
      {sessions.length > 0 && <>
        <h3>可对比的 Gazebo 记录</h3>
        <Rows>{sessions.map((x) => <Row key={x.session_id} dense title={x.algorithm ?? x.run_id} sub={x.finished_utc ? `${dateCN(x.finished_utc)} ${timeCN(x.finished_utc)}` : x.session_id} value={<button type="button" className={s.go} disabled={busy || !available} onClick={() => void submit({ session_id: x.session_id })}>对比</button>} />)}</Rows>
      </>}
      {gaps.length > 0 && <>
        <h3>引擎对比 · Gazebo 与 MuJoCo</h3>
        <div className={s.folds}>{gaps.map((r) => <RunFold key={r.physics_run_id} r={r}
          disabled={busy || !available || compared.has(r.session_id)}
          onRetry={(!r.comparison || r.comparison.available === false) && !['queued', 'running'].includes(r.state) ? () => void submit({ session_id: r.session_id! }) : undefined} />)}</div>
      </>}
      <h3>方案预演</h3>
      {!plans.length && !runs.loading && !runs.error && <p className={s.dim}>还没有物理运行。</p>}
      <div className={s.folds}>{plans.slice(0, 30).map((r) => (r.kind === 'batch'
        ? <BatchFold key={r.physics_run_id} r={r} onCancel={() => void lab.physicsCancel(r.physics_run_id).catch((e) => notify('error', (e as Error).message))} />
        : <RunFold key={r.physics_run_id} r={r} />))}</div>
    </div>
  );
}
