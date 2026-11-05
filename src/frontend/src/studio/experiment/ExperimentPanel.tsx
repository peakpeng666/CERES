/**
 * Experiment result of the focused solution: plan metrics with explicit denominators, independent check,
 * the matching field simulation (tracking error, arrivals, closest approach) and an export that binds the
 * record to the scenario and to the exact asset versions it used.
 */
import { useEffect, useRef, useState } from 'react';
import { Download, FlaskConical, X } from 'lucide-react';
import { fmt } from '../../lab/api';
import type { Solution } from '../../lab/types';
import { Badge, Button, EmptyState, Notice, Spinner } from '../../lab/ui';
import { studio } from '../api';
import { isFleet, useStudio } from '../store';
import type { FieldSimSummary, FleetInstance, PerVehicle } from '../types';
import { fleetClassFor, fleetClasses, fleetSize } from '../fleet';
import { planModelSchema, researchAcceptanceIssues } from '../execution';
import type { AssetRef } from '../assets/api';
import type { FleetAssetBinding } from '../assets/planBridge';
import { derive, useReplay, type Derived, type SimResult } from './replay';
import { trafficStatus } from './traffic';
import { SessionEvidence } from './SessionEvidence';
import { downloadServerRecord } from './export';
import { requiresCoverageRecheck } from '../comparison';
import p from '../panels/panels.module.css';
import a from '../assets/assets.module.css';

type Run = Solution & { recommendation_at_submission?: Record<string, unknown>; reproducibility?: Record<string, unknown> };
type Tone = 'good' | 'warn' | 'bad' | undefined;

function Cell({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: Tone }) {
  return <div className={a.metricCell} data-tone={tone}><span>{label}</span><b>{value}</b>{note && <small>{note}</small>}</div>;
}

export function ExperimentPanel() {
  const st = useStudio();
  const run = (st.focusRun ? st.runs[st.focusRun] : undefined) as Run | undefined;
  const [sims, setSims] = useState<FieldSimSummary[] | null>(null);
  const [sim, setSim] = useState<{ r: SimResult; d: Derived } | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);
  useEffect(() => {
    setSims(null);
    setSim(null);
    setSimLoading(false);
    setSimError(null);
    if (!run || run.problem_class !== 'fleet_routing' || run.state !== 'done') return;
    let alive = true;
    studio.fieldsimResults(run.run_id).then((x) => alive && setSims(x.results)).catch((e) => {
      if (alive) {
        setSims([]);
        setSimError(`仿真列表读取失败：${(e as Error).message}`);
      }
    });
    return () => { alive = false; };
  }, [run?.run_id, run?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  const simsHere = sims?.filter((item) => item.run_id === run?.run_id);
  const latest = simsHere?.[0];
  const simHere = sim?.r.run_id === run?.run_id ? sim : null;
  useEffect(() => {
    if (!latest) return;
    const controller = new AbortController();
    setSimLoading(true);
    setSimError(null);
    studio.fieldsimResult(latest.session_id, { signal: controller.signal }).then((r) => {
      if (!controller.signal.aborted) setSim({ r, d: derive(r) });
    }).catch((e) => {
      if (!controller.signal.aborted) setSimError(`仿真结果读取失败：${(e as Error).message}`);
    }).finally(() => {
      if (!controller.signal.aborted) setSimLoading(false);
    });
    return () => controller.abort();
  }, [latest?.session_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!st.scenario) return <EmptyState icon={FlaskConical} title="未打开场景" />;
  if (!run) return <EmptyState icon={FlaskConical} title="选择一个方案" detail="在「方案对比」中选择方案，查看规划指标、验算和仿真结果，并导出实验记录。" />;
  const fleet = run.problem_class === 'fleet_routing';
  const stateDetail = {
    queued: { text: '方案仍在队列中，尚无最终解。', tone: 'info' as const },
    running: { text: '方案正在计算，尚无最终解。', tone: 'info' as const },
    error: { text: `方案计算错误：${run.error ?? '后端未提供错误明细'}`, tone: 'error' as const },
    timeout: { text: `方案计算超时：${run.error ?? '超过提交预算'}`, tone: 'error' as const },
    canceled: { text: '方案已取消。', tone: 'warn' as const },
  };
  return (
    <div className={p.scroll}>
      <div className={p.form}>
        <div className={p.rowSpace}>
          <h3>{run.algorithm.name} · 实验结果</h3>
          <ExportButton run={run} />
        </div>
        {run.state !== 'done'
          ? <Notice tone={stateDetail[run.state].tone}>{stateDetail[run.state].text} 可导出本次运行记录。</Notice>
          : fleet ? <FleetMetrics run={run} sim={simHere} simLoading={simLoading} hasSims={!!simsHere?.length} simError={simError} /> : <GenericMetrics run={run} />}
        <AssetBinding run={run} />
      </div>
    </div>
  );
}

function FleetMetrics({ run, sim, simLoading, hasSims, simError }: { run: Run; sim: { r: SimResult; d: Derived } | null; simLoading: boolean; hasSims: boolean; simError: string | null }) {
  const inst = run.instance as unknown as FleetInstance;
  const per = (run.metrics.per_vehicle as unknown as PerVehicle[] | undefined) ?? [];
  const classes = fleetClasses(inst);
  const total = inst?.tasks?.length ?? 0;
  const served = per.reduce((x, v) => x + v.tasks, 0);
  const errors = run.check?.errors ?? [];
  const maxLoad = per.length ? Math.max(...per.map((v) => v.load)) : null;
  const overCapacity = per.some((vehicle) => vehicle.load > (fleetClassFor(inst, vehicle.fleet_class)?.capacity ?? Number.POSITIVE_INFINITY));
  const speeds = [...new Set(classes.map((item) => item.speed_mps ?? inst.vehicles.speed_mps ?? 1))];
  const m = sim?.r.metrics;
  const sep = sim?.d.separation;
  const traffic = sim ? trafficStatus(sim.r) : null;
  const completed = m?.tasks_completed ?? null;
  const verified = m?.tasks_verified ?? null;
  const simNumerator = verified ?? completed ?? m?.tasks_reached ?? 0;
  return (
    <>
      <h3>规划（求解器输出，经独立验算）</h3>
      {researchAcceptanceIssues(inst, planModelSchema(run)).map((issue) => <Notice key={issue} tone="warn">{issue}</Notice>)}
      <div className={a.metricGrid}>
        <Cell label="任务完成率（规划）" value={total ? `${fmt.num((100 * served) / total, 1)}%` : '—'} note={`${served} / ${total}，按地图全部任务点统计服务完成情况，不含喷施或采样质量`} tone={served === total ? 'good' : 'bad'} />
        <Cell label="总里程" value={`${fmt.num(run.metrics.total_distance_m as number, 1)} m`} note="各车规划路径长度之和，已独立复算" />
        <Cell label="最晚完成" value={fmt.sec(run.metrics.makespan_s as number)} note={`按类别车速 ${speeds.join(' / ')} m/s 与作业时间的匀速代理；不含交通等待、接驳和真实转向`} />
        <Cell label="趟数 / 返库" value={`${fmt.num(run.metrics.trips as number, 0)} 趟`} note={`单趟最大载重 ${maxLoad ?? '—'}；类别容量 ${classes.map((item, index) => `${item.name ?? `类${index + 1}`}=${item.capacity}`).join('，')}（抽象容量单位）`} tone={overCapacity ? 'bad' : undefined} />
        <Cell label="约束违规" value={run.check ? String(errors.length) : '未验算'} note={run.check ? (errors.length ? errors.slice(0, 2).join('；') : `验算方：${run.check.checker ?? '独立检查器'}`) : ''} tone={run.check ? (errors.length ? 'bad' : 'good') : 'warn'} />
        <Cell label="使用车辆" value={`${fmt.num(run.metrics.vehicles_used as number, 0)} / ${fleetSize(inst)}`} note={`${classes.length} 类；分工均衡度（变异系数）${fmt.num(run.metrics.balance_cv as number, 2)}；家库按表序轮询`} />
        <Cell label="覆盖率" value="不适用" note="多车任务按任务点计完成率；覆盖率只对覆盖作业（分母：田块面积）计算" />
        <Cell label="计算时间" value={fmt.sec(run.metrics.wall_s)} note={`${run.params?.time_limit_s != null ? `预算 ${run.params.time_limit_s} s` : '预算未记录'}${run.params?.seed != null ? ` · 种子 ${run.params.seed}` : ''}；均为提交时冻结值`} />
      </div>
      <h3>仿真执行（Gazebo 田间仿真）</h3>
      {simLoading && <Spinner />}
      {simError && <Notice tone="error">{simError}</Notice>}
      {!simLoading && !hasSims && !simError && <p className={p.hint}>尚无仿真结果。在「方案对比」中点击 Gazebo 执行后，可查看跟踪误差与车间距。</p>}
      {sim && m && (
        <>
          <div className={a.metricGrid}>
            <Cell label={verified != null ? '任务完成率（真值位置与停留）' : completed != null ? '控制器停留完成率' : '控制器到达率'} value={`${fmt.num((100 * simNumerator) / Math.max(1, m.tasks_total), 1)}%`} note={`${simNumerator} / ${m.tasks_total}；控制器到达 ${m.tasks_reached}${completed != null ? `、停留完成 ${completed}` : '；历史记录未区分完成停留'}；不表示作业质量`} tone={sim.r.state === 'canceled' ? 'warn' : simNumerator === m.tasks_total && sim.r.state === 'succeeded' ? 'good' : 'bad'} />
            <Cell label="实际总里程" value={`${fmt.num(m.total_distance_m, 1)} m`} note={`规划 ${fmt.num(m.planned_total_distance_m, 1)} m；实际轨迹独立显示，含接驳与转向`} />
            <Cell label="实际最晚完成" value={fmt.sec(m.makespan_s)} note={`规划 ${fmt.sec(m.planned_makespan_s)}（匀速代理）；实际含交通等待、接驳与转向`} />
            <Cell label="路径跟踪误差" value={`${fmt.num(m.cross_track_mean_m, 3)} m`} note={`最大 ${fmt.num(m.cross_track_max_m, 2)} m；真值位置到跟踪路线的横向距离；URP 路径跟踪`} tone={m.cross_track_max_m > 0.5 ? 'warn' : 'good'} />
            <Cell label="交通与接触" value={traffic!.value} tone={traffic!.tone} note={`${sep ? `记录轨迹最近车距 ${sep.d.toFixed(2)} m（t=${sep.t.toFixed(0)} s）；` : ''}${traffic!.note}`} />
            <Cell label="异常" value={String(sim.d.anomalies.length)} note={sim.d.anomalies.slice(0, 2).map((x) => x.text).join('；') || '无'} tone={sim.d.anomalies.some((x) => x.tone === 'error') ? 'bad' : sim.d.anomalies.length ? 'warn' : 'good'} />
          </div>
          <div className={p.row}>
            <span className={p.meta} style={{ margin: 0 }}>执行：纯追踪 · 控制定位：{sim.r.execution?.localization?.gnss ? '轮式输入与合成位置观测融合估计' : 'Gazebo 真值对照'} · 会话 {sim.r.session_id} · 状态 {sim.r.state} · 清理 {sim.r.session_report?.cleanup_status ?? '未记录'}</span>
            <span className={p.spacer} />
            <Button size="sm" onClick={() => { void useReplay.getState().load(sim.r.session_id); window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: 'replay' })); }}>在地图上回放</Button>
          </div>
          <SessionEvidence result={sim.r} />
        </>
      )}
    </>
  );
}

function GenericMetrics({ run }: { run: Run }) {
  const m = run.metrics;
  const cov = m.coverage_fraction as number | null | undefined;
  const working = m.working_area_fraction as number | null | undefined;
  const turnEnforced = m.turn_radius_enforced as boolean | null | undefined;
  const recheck = requiresCoverageRecheck(run);
  return (
    <div className={a.metricGrid}>
      <Cell label="目标值" value={`${fmt.num(run.objective.value, 2)} ${run.objective.unit}`} note={run.objective.lower_bound != null ? `下界 ${fmt.num(run.objective.lower_bound, 2)}` : ''} />
      <Cell label="验算" value={recheck ? '旧覆盖记录 · 须重求解' : run.check ? (run.check.valid ? '通过' : `${run.check.errors.length} 项未通过`) : '未验算'} tone={recheck ? 'warn' : run.check ? (run.check.valid ? 'good' : 'bad') : 'warn'} note={recheck ? '历史检查记录；须重新求解并通过新版验算后执行' : run.check?.errors.slice(0, 2).join('；')} />
      {cov != null && <Cell label="全田块覆盖率" value={`${fmt.num(cov * 100, 1)}%`} note={`覆盖面积 ${fmt.num(m.coverage_area_m2 as number, 1)} m² / 田块面积 ${fmt.num(m.field_area_m2 as number, 1)} m²`} />}
      {working != null && <Cell label="内缩作业区覆盖率" value={`${fmt.num(working * 100, 1)}%`} note={`覆盖口径：${String(m.coverage_scope ?? '未记录')}；按声明幅宽与作业状态计算`} />}
      {turnEnforced != null && <Cell label="转弯半径约束" value={turnEnforced ? '已约束' : '未约束'} note={turnEnforced ? '按求解器声明的转弯模型与独立采样检查展示' : '该算法允许原地转向或未施加曲率约束'} />}
      <Cell label="计算时间" value={fmt.sec(m.wall_s)} />
    </div>
  );
}

function AssetBinding({ run }: { run: Run }) {
  const inst = run.instance as unknown as { asset_refs?: AssetRef[]; fleet?: FleetAssetBinding | string } | undefined;
  const refs = inst?.asset_refs ?? [];
  const binding = inst?.fleet && typeof inst.fleet === 'object' ? inst.fleet : null;
  const ignored = Array.isArray(binding?.ignored) ? binding.ignored : null;
  return (
    <>
      <h3>绑定的场景与资产版本</h3>
      <div className={a.kv}>
        <span>场景</span><span>{run.scenario_id} · 实例 <span className={a.mono}>{run.instance_sha256?.slice(0, 12)}</span></span>
        {refs.map((r) => (
          <span key={`${r.role}-${r.id}-${r.version}`} style={{ display: 'contents' }}>
            <span>{{ map: '地图', vehicle: '车辆', facility: '设施', template: '模板' }[r.role] ?? r.role}</span>
            <span>{r.name ?? r.id} v{r.version} <span className={a.mono}>{r.sha256.slice(0, 12)}</span></span>
          </span>
        ))}
        {!refs.length && <><span>资产</span><span>这个方案的任务地图没有绑定资产（文字生成或手工绘制的地图）</span></>}
        {binding && <><span>未参与规划</span><span>{ignored ? (ignored.length ? ignored.join('；') : '无') : '旧绑定未记录忽略字段'}</span></>}
      </div>
    </>
  );
}

function ExportButton({ run }: { run: Run }) {
  const st = useStudio();
  const controller = useRef<AbortController | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error' | 'canceled'>('idle');
  const [detail, setDetail] = useState('');
  useEffect(() => () => controller.current?.abort(), []);
  const download = async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState('loading');
    setDetail('正在读取完整实验记录…');
    try {
      const result = await downloadServerRecord(run.run_id, { signal: current.signal });
      if (current.signal.aborted) return;
      setState('done');
      setDetail(`${result.envelope.record.schema ?? '服务端记录'} · assembled ${result.envelope.assembled_utc}；SHA-256 完整性校验（无身份签名）`);
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        setState('canceled');
        setDetail('导出已取消');
      } else {
        setState('error');
        setDetail(`服务端完整记录导出失败：${(e as Error).message}。请重试。`);
      }
    } finally {
      if (controller.current === current) controller.current = null;
    }
  };
  return (
    <div>
      <div className={p.row}>
        {!isFleet(st.scenario) && <Badge tone="gray">{run.problem_class}</Badge>}
        <Button size="sm" icon={Download} loading={state === 'loading'} onClick={() => void download()} title="下载服务端完整实验记录，含完整性校验与生成时间">
          {state === 'error' ? '重试导出实验记录' : '导出实验记录'}
        </Button>
        {state === 'loading' && <Button size="sm" variant="plain" icon={X} onClick={() => controller.current?.abort()}>取消</Button>}
      </div>
      {detail && <p className={state === 'error' ? p.warn : p.meta} role={state === 'error' ? 'alert' : undefined}>{detail}</p>}
    </div>
  );
}
