/**
 * Simulation replay on the task map: planned path vs ground-truth track, the robot body, task arrivals over time,
 * vehicle filter, timeline and an anomaly list that jumps to where (and when) something went wrong.
 */
import { shownColor } from '../../lab/views/plan/planModel';
import { useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, CircleX, Crosshair, Eye, EyeOff, Pause, Play, RotateCcw, X } from 'lucide-react';
import { fmt, lab, useLabResource } from '../../lab/api';
import type { View } from '../../lab/canvas/view';
import { Badge, Button, EmptyState, Notice, Select, Spinner } from '../../lab/ui';
import { studio } from '../api';
import { useStudio } from '../store';
import { useFieldSim } from '../ros/live';
import { indexAt, metricsAt, poseAt, simBody, taskEvents, useReplay, type Anomaly } from './replay';
import { trafficStatus } from './traffic';
import { displaySegments, gapLimit, replayIndex } from './replayIndex';
import { planningFingerprint } from '../fleet';
import type { FleetInstance } from '../types';
import { SessionEvidence } from './SessionEvidence';
import { RunTrace } from './RunTrace';
import { IntegrityNotice } from '../../lab/views/plan/IntegrityNotice';
import p from '../panels/panels.module.css';
import a from '../assets/assets.module.css';

export const centerMap = (xy: [number, number], span = 24, expectedInput?: string) =>
  window.dispatchEvent(new CustomEvent('studio:center', { detail: { xy, span, expectedInput } }));

/** Drawn inside the task-map editor's world group (metres, y up). */
export function ReplayOverlay({ view, instance }: { view: View; instance?: FleetInstance }) {
  const { result, derived, t, show, hidden, focus } = useReplay();
  const scenarioId = useStudio((x) => x.scenarioId);
  const focusRun = useStudio((x) => x.focusRun);
  if (!result || !derived || result.scenario_id !== scenarioId || (focusRun && result.run_id !== focusRun)) return null;
  const recorded = result.execution_plan?.instance;
  if (instance && (!recorded || planningFingerprint(instance) !== planningFingerprint(recorded))) return null;
  const k = view.k || 1;
  const px = (n: number) => n / k;
  const vehicles = result.vehicles.filter((v) => !hidden.includes(v.name));
  const plan = new Map((result.execution_plan?.vehicles ?? []).map((v) => [v.name, v]));
  return (
    <g pointerEvents="none">
      {show.planned && vehicles.map((v) => {
        const path = plan.get(v.name)?.path;
        return path ? <polyline key={`p-${v.name}`} points={path.map((q) => q.join(',')).join(' ')} fill="none" stroke={shownColor(v.color)} strokeWidth={1.3} strokeDasharray="6 4" vectorEffect="non-scaling-stroke" opacity={0.85} /> : null;
      })}
      {show.actual && vehicles.map((v) => {
        const tr = result.trails[v.name] ?? [];
        const i = indexAt(tr, t);
        if (i < 0) return null;
        const pose = poseAt(tr, t, gapLimit(result));
        const segments = displaySegments(replayIndex(result, v.name), t);
        if (pose && segments.length && (segments.at(-1)?.at(-1)?.[0] ?? t) < t) segments[segments.length - 1].push([t, ...pose]);
        return segments.map((segment, j) => <polyline key={`a-${v.name}-${j}`} points={segment.map((q) => `${q[1]},${q[2]}`).join(' ')} fill="none" stroke={shownColor(v.color)} strokeWidth={3.2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" opacity={0.5} />);
      })}
      {show.tasks && result.tasks.filter((tk) => !hidden.includes(tk.vehicle)).map((tk) => {
        const reached = tk.state === 'reached' && tk.t_sim != null && tk.t_sim <= t;
        const hasCompletion = 'serviced_at' in tk || tk.service_complete !== undefined;
        const completed = hasCompletion && tk.service_complete !== false && tk.serviced_at != null && tk.serviced_at <= t;
        const missed = tk.state !== 'reached' && t >= derived.duration - 1e-6;
        const at = tk.stop ?? tk.xy;
        return <circle key={`t-${tk.id}`} cx={at[0]} cy={at[1]} r={px(completed ? 7 : 6)} fill={completed ? 'rgba(48,177,88,0.85)' : reached ? 'rgba(255,159,10,0.7)' : 'none'}
          stroke={missed ? '#ff453a' : completed || reached ? '#fff' : '#8e8e93'} strokeWidth={missed ? 2.4 : 1.4} vectorEffect="non-scaling-stroke">
          <title>{`${tk.label} · ${completed ? '已完成停留' : reached ? (hasCompletion ? '已到点，停留未完成' : '已到点，未记录作业完成') : '尚未到点'}`}</title>
        </circle>;
      })}
      {show.body && vehicles.map((v) => {
        const pose = poseAt(result.trails[v.name] ?? [], t, gapLimit(result));
        if (!pose) return null;
        const deg = (pose[2] * 180) / Math.PI;
        const body = simBody(result);
        const L = body.length_m, W = body.width_m;
        const visible = Math.max(L * k, 8) / k / L; // keep the body at least ~8 px long when zoomed out
        return (
          <g key={`b-${v.name}`} transform={`translate(${pose[0]} ${pose[1]}) rotate(${deg}) scale(${Math.max(1, visible)})`}>
            <rect x={-L / 2} y={-W / 2} width={L} height={W} rx={0.06} fill={shownColor(v.color)} stroke="var(--map-halo)" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
            <polygon points={`${L / 2},0 ${L / 2 - 0.22},${W / 2 - 0.08} ${L / 2 - 0.22},${-W / 2 + 0.08}`} fill="#fff" opacity={0.9} />
          </g>
        );
      })}
      {show.anomalies && derived.anomalies.filter((an) => an.xy && (!an.vehicle || !hidden.includes(an.vehicle))).map((an) => (
        <circle key={an.key} cx={an.xy![0]} cy={an.xy![1]} r={px(focus?.key === an.key ? 16 : 9)} fill="none"
          stroke={an.tone === 'error' ? '#ff453a' : '#ff9f0a'} strokeWidth={focus?.key === an.key ? 3 : 2} strokeDasharray={focus?.key === an.key ? undefined : '3 2'} vectorEffect="non-scaling-stroke" />
      ))}
    </g>
  );
}

function useClock() {
  const playing = useReplay((x) => x.playing);
  const raf = useRef(0);
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    const step = (now: number) => {
      const st = useReplay.getState();
      const end = st.derived?.duration ?? 0;
      const next = Math.min(end, st.t + ((now - last) / 1000) * st.speed);
      last = now;
      useReplay.setState({ t: next, playing: next < end });
      if (next < end) raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [playing]);
}

const clock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

export function ReplayPanel() {
  const rp = useReplay();
  const scenarioId = useStudio((x) => x.scenarioId);
  const runs = useStudio((x) => x.runs);
  const live = useFieldSim().state;
  const list = useLabResource('replay-results', () => studio.fieldsimResults(), { pollMs: 10000 });
  useClock();
  const mine = useMemo(() => (list.data?.results ?? []).filter((r) => r.scenario_id === scenarioId), [list.data, scenarioId]);
  useEffect(() => {
    if (rp.result && rp.result.scenario_id !== scenarioId) rp.clear();
  }, [scenarioId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!scenarioId) return <EmptyState icon={Play} title="未打开任务地图" detail="仿真回放显示在任务地图上：先打开一个多车任务地图。" />;
  const r = rp.result, d = rp.derived;
  const traffic = r ? trafficStatus(r) : null;
  // A finished session stays open (running) until stopped; only an executing one is still missing from the list.
  const liveHere = live?.running && ['starting', 'ready', 'running'].includes(live.phase) && live.scenario_id === scenarioId;
  const focusRun = async (runId: string) => {
    const st = useStudio.getState();
    if (!st.runs[runId]) {
      try {
        const run = await lab.run(runId);
        useStudio.setState((x) => ({ runs: { ...x.runs, [runId]: run } }));
      } catch (e) {
        st.notify('error', `读取方案失败：${(e as Error).message}`);
        return;
      }
    }
    useStudio.getState().focus(runId);
  };
  const jump = (an: Anomaly) => {
    rp.setFocus(an);
    if (an.t != null) rp.setT(an.t);
    if (an.xy) centerMap(an.xy);
  };
  return (
    <div className={p.scroll}>
      <div className={p.form}>
        <div className={p.rowSpace}>
          <h3>仿真回放</h3>
          {r && <Button size="sm" variant="plain" icon={X} onClick={rp.clear}>关闭回放</Button>}
        </div>
        <p className={p.meta}>
          田间仿真的执行方式是<b>路径跟踪</b>：每台车用纯追踪控制器沿规划路线行驶，不调用 Nav2 重规划；回放图层显示 <b>Gazebo 真值</b>，控制器实际使用的定位来源见会话证据。
          TurtleBot4 + Nav2 在「仿真」工作区单独记录，可选 AMCL / 真值定位及几何路径跟踪 / Nav2 逐点重规划；以具体运行的来源为准。
        </p>
        {liveHere && <Notice tone="info">这张任务地图的仿真正在运行（{live?.phase}），结束后会出现在下面的列表里。</Notice>}
        {!r && (
          <>
            <h3>这张任务地图的仿真记录</h3>
            {list.loading && !list.data && <Spinner />}
            {list.error && <Notice tone="error">{list.error.message}</Notice>}
            {!mine.length && list.data && <p className={p.hint}>还没有仿真记录：在「方案对比」里点某个方案的 Gazebo 执行。</p>}
            {mine.slice(0, 20).map((x) => (
              <button key={x.session_id} type="button" className={a.ver} onClick={() => void rp.load(x.session_id)}>
                <b>{x.state === 'succeeded' ? <Badge tone="green">完成</Badge> : x.state === 'canceled' ? <Badge tone="orange">已取消</Badge> : <Badge tone="red">失败</Badge>}</b>
                <span>{x.algorithm ?? x.run_id}<div className={a.hint}>{fmt.time(x.finished_utc)} · 到达 {x.metrics.tasks_reached}/{x.metrics.tasks_total} · 最晚完成 {fmt.sec(x.metrics.makespan_s)}{x.travel === 'rows' ? ' · 沿行间' : ''}</div></span>
                <span />
              </button>
            ))}
            {rp.loading && <Spinner />}
            {rp.error && <Notice tone="error">{rp.error}</Notice>}
          </>
        )}
        {r && d && (
          <>
            <div className={a.kv}>
              <span>方案</span><span>{r.algorithm ?? '—'} <button type="button" className={p.link} style={{ border: 0, background: 'none' }} onClick={() => void focusRun(r.run_id)}>{runs[r.run_id] ? '在地图上对照此方案' : '载入此方案对照'}</button></span>
              <span>结果</span><span>{r.state === 'succeeded' ? '全部车辆完成' : r.state === 'canceled' ? '执行已取消' : '执行失败'} · 到达 {r.metrics.tasks_reached}/{r.metrics.tasks_total} 个任务点{r.metrics.tasks_completed != null ? ` · 完成停留 ${r.metrics.tasks_completed}` : ''}</span>
              <span>仿真</span><span>{r.world} · 记录时间跨度 {fmt.sec(d.duration - d.startTime)}{r.real_time_factor ? ` · 实际倍速 ${fmt.num(r.real_time_factor, 1)}×` : ''}{r.travel === 'rows' ? ' · 作物行为实体' : ''}</span>
              <span>会话</span><span className={a.mono}>{r.session_id} · 清理 {r.session_report?.cleanup_status ?? '未记录'}</span>
            </div>
            {r.provenance_warning && <Notice tone="warn">{r.provenance_warning}</Notice>}
            <IntegrityNotice run={runs[r.run_id]} />
            {runs[r.run_id]
              ? <RunTrace run={runs[r.run_id]!} title="方案追溯" />
              : <p className={p.hint}>方案记录未载入：种子、实例哈希与算法版本暂为未知；点“载入此方案对照”读取 {r.run_id}。</p>}
            <SessionEvidence result={r} />
            <p className={p.meta}>以上物理证据与独立检查是全程终检，不随回放时刻改写；下方时间轴只重放已记录样本。</p>
            <div className={a.timeline}>
              <Button size="sm" title={rp.playing ? '暂停历史回放' : '播放历史记录（不控制仿真）'} icon={rp.playing ? Pause : Play} onClick={() => { if (!rp.playing && rp.t >= d.duration) rp.setT(d.startTime); rp.setPlaying(!rp.playing); }} />
              <input type="range" min={d.startTime} max={d.duration} step={(d.duration - d.startTime) / 600 || 1} value={rp.t} aria-label="回放时间"
                onChange={(e) => { rp.setPlaying(false); rp.setT(Number(e.target.value)); }} />
              <span className={a.mono}>{clock(rp.t)} / {clock(d.duration)}</span>
            </div>
            <div className={p.row}>
              <Select aria-label="回放倍速" value={String(rp.speed)} onChange={(e) => rp.setSpeed(Number(e.target.value))}>
                {[1, 5, 10, 20, 50, 100].map((x) => <option key={x} value={x}>{x}× 仿真时间</option>)}
              </Select>
              <Button size="sm" variant="plain" icon={RotateCcw} onClick={() => { rp.setPlaying(false); rp.setT(d.startTime); }}>回到开始</Button>
            </div>
            <div className={p.row}>
              {([['planned', '规划路线'], ['actual', '实际轨迹'], ['body', '车体足迹'], ['tasks', '任务状态'], ['anomalies', '异常']] as const).map(([key, label]) => (
                <button key={key} type="button" className={a.chip} data-tone={rp.show[key] ? 'blue' : undefined} onClick={() => rp.toggle(key)}>
                  {rp.show[key] ? <Eye size={11} /> : <EyeOff size={11} />}{label}
                </button>
              ))}
            </div>
            <table className={p.table}>
              <thead><tr><th>车辆</th><th className={p.num}>任务（截至时刻）</th><th className={p.num}>里程（实际/规划）</th><th className={p.num}>用时（实际/规划）</th><th className={p.num}>横向偏差 均/最大</th></tr></thead>
              <tbody>
                {r.vehicles.map((v) => {
                  const off = rp.hidden.includes(v.name);
                  const current = metricsAt(r, v.name, rp.t);
                  return (
                    <tr key={v.name} onClick={() => rp.toggleVehicle(v.name)} style={{ opacity: off ? 0.45 : 1 }} title={off ? '点击显示' : '点击隐藏'}>
                      <td><span className={p.swatch} style={{ background: shownColor(v.color) }} /> {v.label} {v.state === 'failed' && <Badge tone="red">受阻</Badge>}</td>
                      <td className={p.num}>{current.tasks_completed != null ? `${current.tasks_completed} 完成 / ` : ''}{current.tasks_reached}/{v.tasks_total} 到点</td>
                      <td className={p.num} title={current.missing_s ? `缺流 ${current.missing_s.toFixed(2)} s，距离仅含可观测连续片段` : undefined}>{!current.distance_complete && current.distance_m != null ? '≥ ' : ''}{fmt.num(current.distance_m, 0)} / {fmt.num(v.planned_length_m, 0)} m</td>
                      <td className={p.num}>{fmt.sec(current.elapsed_s)} / {fmt.sec(v.planned_duration_s)}</td>
                      <td className={p.num}>{fmt.num(current.cross_track_mean_m, 3)} / {fmt.num(current.cross_track_max_m, 2)} m</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className={p.meta}>历史回放 · 仿真源时钟 s。到点与停留事件来自控制器判定，独立真值验收另列。里程、用时、任务和横向偏差截至所选时刻；位置只在 ≤{gapLimit(r)} s 的连续样本间插值。跨缺口不连线，里程显示可观测下界。旧记录缺少完成停留证据时只显示到点。地图输入不同或缺少冻结输入时不叠加历史轨迹。</p>
            <h3>任务事件（截至 {clock(rp.t)}）</h3>
            {!taskEvents(r).some((event) => event.t <= rp.t) && <p className={p.hint}>当前时刻之前还没有到点或完成停留记录。</p>}
            {taskEvents(r).filter((event) => event.t <= rp.t).slice(-10).reverse().map((event) => (
              <button key={event.key} type="button" className={a.anomaly} onClick={() => { rp.setPlaying(false); rp.setT(event.t); centerMap(event.xy); }}>
                <Badge tone={event.kind === 'completed' ? 'green' : 'gray'}>{event.kind === 'completed' ? '完成停留' : '到点'}</Badge>
                <span>{clock(event.t)} · {event.task.label} · {event.task.vehicle}</span>
                <Crosshair size={12} />
              </button>
            ))}
            <h3>异常定位（{d.anomalies.length}）</h3>
            {!d.anomalies.length && <p className={p.hint}>没有发现未到达任务、受阻、停滞、偏离 &gt; 0.3 m 或车体接近的情况。</p>}
            {d.anomalies.map((an) => (
              <button key={an.key} type="button" className={a.anomaly} data-tone={an.tone} onClick={() => jump(an)} disabled={!an.xy && an.t == null}>
                {an.tone === 'error' ? <CircleX size={13} /> : <AlertTriangle size={13} />}
                <span>{an.text}</span>
                {an.xy && <Crosshair size={12} />}
              </button>
            ))}
            {traffic && (
              <p className={p.meta}>
                <Badge tone={traffic.tone === 'good' ? 'green' : traffic.tone === 'bad' ? 'red' : 'orange'}>{traffic.value}</Badge>{' '}
                {d.separation ? `记录轨迹最近相距 ${d.separation.d.toFixed(2)} m（t = ${d.separation.t.toFixed(0)} s）；` : ''}{traffic.note}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
