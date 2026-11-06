import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useAgent } from '../agent/store';
import { useStudioSync } from '../agent/AgentChat';
import { currentRuns } from '../agent/studioSync';
import { STATE_LABEL, type AgentMessage, type AgentSession, type ContractCard, type Pending, type Report, type ToolCall } from '../agent/api';
import type { EngineGap, Spread, TimeWindowRecord } from '../lab/types';
import { routeColor } from '../lab/views/plan/planModel';
import { useFieldSim } from '../studio/ros/live';
import { useStudio } from '../studio/store';
import { fieldTrafficLabel } from '../studio/execution';
import type { FleetInstance } from '../studio/types';
import { Spinner } from '../lab/ui';
import { arrivalLine, lateLine } from './arrivals';
import { GapFacts } from './PhysicsPanel';
import { Composer } from './Composer';
import { Icon, type IconName } from './Icon';
import { Thumb } from './LayerMap';
import { ACTOR, FIELD, SOURCE, TOOL, cx, n0, n1, n2, n3, showValue, stateTone, toolName } from './format';
import { openFrames, openPlan, openRoot, runRecordHref, saveFile, sessionExportHref, sessionLabel, useRun } from './nav';
import { activitySeconds, activityTitle, approvalVerdict, buildTimeline, planSummary, type TimelineItem } from './timeline';
import { Fold, Fresh, Roll, TopBar, useMenu } from './ui';
import s from './desk.module.css';

function Inline({ text }: { text: string }) {
  const runs = useAgent((x) => x.session?.run_ids);
  const nodes: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|run_\d{8}T\d{6}_[0-9a-f]{6})/g;
  let i = 0, m: RegExpExecArray | null;
  const ref = (id: string, key: number) => (runs?.includes(id)
    ? <button key={key} type="button" className={s.ref} onClick={() => openPlan(id)}>{id}</button>
    : <code key={key}>{id}</code>);
  while ((m = re.exec(text))) {
    if (m.index > i) nodes.push(text.slice(i, m.index));
    const t = m[0];
    if (t.startsWith('**')) nodes.push(<strong key={m.index}><Inline text={t.slice(2, -2)} /></strong>);
    else if (t.startsWith('`')) { const x = t.slice(1, -1); nodes.push(/^run_/.test(x) ? ref(x, m.index) : <code key={m.index}>{x}</code>); } else nodes.push(ref(t, m.index));
    i = m.index + t.length;
  }
  if (i < text.length) nodes.push(text.slice(i));
  return <>{nodes}</>;
}
const Prose = ({ text }: { text: string }) => <div className={s.prose}>{text.split(/\n{2,}/).map((p, i) => <p key={i} style={{ '--i': i } as CSSProperties}><Inline text={p.replace(/\n/g, ' ')} /></p>)}</div>;

/* --------------------------------------------------------- tool activity */
function CallDetail({ call }: { call: ToolCall }) {
  const r = (call.result ?? {}) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const go = (label: string, fn: () => void) => <button type="button" className={s.go} onClick={fn}>{label}</button>;
  let body: ReactNode = null;
  if (call.name === 'rule_parse' || call.name === 'update_task_contract') {
    const acc = (r.accepted ?? []) as { key: string; new: unknown; source: keyof typeof SOURCE }[];
    body = <>
      {acc.length ? <dl className={s.kv}>{acc.flatMap((x) => [<dt key={`${x.key}k`}>{FIELD[x.key] ?? x.key}</dt>, <dd key={`${x.key}v`}>{showValue(x.new)}（{SOURCE[x.source] ?? x.source}）</dd>])}</dl> : <p>没有新增字段。</p>}
      {(r.rejected ?? []).length > 0 && <p className={s.err}>拒绝：{(r.rejected as { key: string; reason?: string }[]).map((x) => `${FIELD[x.key] ?? x.key}${x.reason ? `（${x.reason}）` : ''}`).join('、')}</p>}
      {(r.unsupported ?? []).length > 0 && <p className={s.err}>未支持：{(r.unsupported as string[]).join('；')}</p>}
      {go('打开合同', () => openRoot('contract'))}
    </>;
  } else if (call.name === 'build_scenario' && r.summary) {
    body = <>
      <p>{r.summary}</p>
      {r.precheck && <p>总需求 {r.precheck.total_demand}，单趟车队容量 {r.precheck.fleet_capacity_per_trip}，预检{r.precheck.feasible_by_precheck ? '通过' : '未通过'}。</p>}
      {go('打开任务地图', () => openRoot('map'))}
    </>;
  } else if (call.name === 'recommend_algorithms' && r.explanation) {
    body = <><p>{r.explanation}</p>{r.compare && <p>同口径对比：{(r.compare as string[]).join('、')}。</p>}</>;
  } else if ((call.name === 'solve' || call.name === 'compare_runs') && (r.runs || r.groups)) {
    const list = (r.runs ?? (r.groups as { rows: unknown[] }[]).flatMap((g) => g.rows)) as { run_id: string; algorithm: string; diagnosis_label: string; total_distance_m: number | null }[];
    body = <div className={s.mini}>{list.map((x) => <button key={x.run_id} type="button" onClick={() => openPlan(x.run_id)}><span>{x.algorithm}</span><span>{x.diagnosis_label}</span><span className={s.num}>{n1(x.total_distance_m)} m</span></button>)}</div>;
  } else if (call.name === 'verify_run' && r.recheck) {
    const items = r.recheck.items as { id: string; label: string; ok: boolean; detail: string }[];
    body = <div className={s.mini}>{items.map((x) => (
      <button key={x.id} type="button" onClick={() => openFrames([{ t: 'plans' }, { t: 'plan', run: r.run_id }, { t: 'checks', run: r.run_id }, { t: 'check', run: r.run_id, id: x.id }])}>
        <span>{x.label}</span><span>{x.detail.length > 22 ? '' : x.detail}</span><span className={x.ok ? s.okMark : s.warnMark}>{x.ok ? '通过' : '未通过'}</span>
      </button>))}</div>;
  } else if (call.name === 'propose_task_change') {
    body = <>
      <dl className={s.kv}>{((r.changes ?? []) as { key: string; old: unknown; new: unknown }[]).flatMap((x) => [<dt key={`${x.key}k`}>{FIELD[x.key] ?? x.key}</dt>, <dd key={`${x.key}v`}>{showValue(x.old)} → {showValue(x.new)}</dd>])}</dl>
      <p>{String((call.arguments as { reason?: string }).reason ?? '')}</p>
    </>;
  }
  return (
    <div className={s.detail}>
      {call.error && <p className={s.err}>{call.error.message}</p>}
      {body}
      {go('完整记录', () => openFrames([{ t: 'trace' }, { t: 'tool', id: call.id }]))}
    </div>
  );
}

function Activity({ calls }: { calls: ToolCall[] }) {
  const refused = calls.filter((c) => c.status !== 'ok').length;
  return (
    <Fold summary={<>
      <span className={s.sum}>{activityTitle(calls)}</span>
      <span className={s.discMeta}>{refused > 0 && <b>{refused} 次被拒绝 · </b>}{calls.length} 次调用 · {n1(activitySeconds(calls))} s</span>
    </>}>
      {() => (
        <div className={s.calls}>
          {calls.map((c) => (
            <Fold key={c.id} status={c.status} summary={<>
              <span className={s.sum}>{toolName(c)}</span>
              <span className={s.tag}>{ACTOR[c.actor] ?? c.actor}{c.status === 'refused' ? ' · 被拒绝' : c.status === 'error' ? ' · 失败' : ''}</span>
              <span className={s.discMeta}>{n2(c.duration_s)} s</span>
            </>}>
              {() => <CallDetail call={c} />}
            </Fold>
          ))}
        </div>
      )}
    </Fold>
  );
}

/* -------------------------------------------------------------- documents */
const TIME_FIELDS = new Set(['start_clock', 'task_deadline', 'task_earliest']);

function ContractDoc({ msg, session, latest }: { msg: AgentMessage; session: AgentSession; latest: boolean }) {
  const control = useAgent((x) => x.control);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null);
  const d = msg.data as unknown as { cards: ContractCard[]; unsupported: string[]; revision: number };
  const active = latest && !session.restoration?.read_only && session.state === 'awaiting_confirmation';
  const sources: Record<string, number> = {};
  for (const c of d.cards) sources[c.source] = (sources[c.source] ?? 0) + 1;
  const shown = d.cards.filter((c) => c.source !== 'default');
  return (
    <div className={s.doc} data-need={active || undefined}>
      <header><b>任务合同 · 第 {d.revision} 版</b><span data-tone={active ? 'wait' : undefined}>{active ? '等待确认' : d.revision < session.contract.revision ? '已被新版本替代' : '已确认'}</span></header>
      <p className={s.line}>{shown.map((c) => (c.key === 'simulate' ? (c.value ? '规划后进入仿真' : '不进入仿真') : TIME_FIELDS.has(c.key) ? `${c.label} ${c.text}` : c.text)).join(' · ')}</p>
      {d.unsupported.length > 0 && <p className={s.docWarn}>未支持，不进入求解：{d.unsupported.join('；')}</p>}
      <footer>
        <span className={s.grow}>{Object.entries(sources).map(([k, v]) => `${v} 项${SOURCE[k as keyof typeof SOURCE] ?? k}`).join('，')}</span>
        {active ? <button type="button" className={s.btn} onClick={() => openRoot('contract')}>逐项查看</button> : <button type="button" className={s.go} onClick={() => openRoot('contract')}>逐项查看</button>}
        {active && <button type="button" className={cx(s.btn, s.btnPrimary)} disabled={busy} onClick={() => void control('confirm')}>确认并执行</button>}
      </footer>
    </div>
  );
}

function QuestionsDoc({ msg, active }: { msg: AgentMessage; active: boolean }) {
  const send = useAgent((x) => x.send);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null);
  const pending = msg.data as unknown as Pending;
  return (
    <>
      <Prose text={msg.text} />
      {(pending.questions ?? []).map((q, i) => (
        <div key={i} className={s.doc} data-need={active || undefined}>
          <p className={s.line}>{q.question}</p>
          {q.options.length > 0 && <div className={s.options}>{q.options.map((o) => <button key={o} type="button" className={s.btn} disabled={!active || busy} onClick={() => void send(o)}>{o}</button>)}</div>}
        </div>
      ))}
    </>
  );
}

function ApprovalDoc({ msg, next, session, latest }: { msg: AgentMessage; next?: AgentMessage; session: AgentSession; latest: boolean }) {
  const control = useAgent((x) => x.control);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null);
  const d = msg.data as unknown as Pending & { reason?: string; contract_revision?: number };
  const live = latest && !session.restoration?.read_only && session.state === 'awaiting_approval' && !!session.pending;
  const verdict = approvalVerdict(next);
  const runId = (d.call?.arguments as { run_id?: string } | undefined)?.run_id;
  const run = useRun(runId);
  const check = runId ? session.verifications[runId] : undefined;
  return (
    <div className={s.doc} data-need={live || undefined}>
      <header>
        <b>{d.kind === 'tool_call' ? (d.label ?? TOOL[d.call?.name ?? ''] ?? d.call?.name) : '修改任务合同'}</b>
        <span data-tone={live ? 'wait' : undefined}>{live ? '需要批准' : verdict ?? '已处理'}</span>
      </header>
      <p className={s.line}>{d.reason}</p>
      {d.changes && <p className={s.line}>{d.changes.map((x) => `${FIELD[x.key] ?? x.key} ${showValue(x.old)} → ${showValue(x.new)}`).join('；')}</p>}
      {runId && (
        <button type="button" className={s.result} onClick={() => openPlan(runId)}>
          <span>{run?.algorithm.name ?? runId}<small>{check?.valid && check.agrees ? '独立复核通过' : '尚未复核'}</small></span>
          <span className={s.num}>{run ? `${n1(run.metrics.total_distance_m as number)} m` : ''}</span>
          <Icon name="right" size={12} />
        </button>
      )}
      {live && (
        <footer>
          <span className={s.grow}>合同第 {d.contract_revision ?? session.contract.revision} 版</span>
          <button type="button" className={s.btn} disabled={busy} onClick={() => void control('reject')}>拒绝</button>
          <button type="button" className={cx(s.btn, s.btnPrimary)} disabled={busy} onClick={() => { void control('approve'); if (d.call?.name === 'start_simulation') openFrames([{ t: 'sim' }, { t: 'panel', id: 'rosview' }]); }}>批准一次</button>
        </footer>
      )}
    </div>
  );
}

function ReportDoc({ msg, session }: { msg: AgentMessage; session: AgentSession }) {
  const report = msg.data as unknown as Report;
  const scenario = useStudio((x) => x.scenario);
  const e = report.evidence;
  const best = e.best_verified;
  const current = currentRuns(session);
  const stale = best && !current.includes(best.run_id);
  const verified = e.runs.filter((r) => r.independent_recheck).length;
  const bestRun = useRun(best?.run_id);
  const inst = scenario?.id === session.scenario_id && scenario?.problem_class === 'fleet_routing' ? scenario.instance as unknown as FleetInstance : null;
  const thumbRoutes = useMemo(() => bestRun?.routes.map((r) => ({ line: r.polyline })), [bestRun]);
  return (
    <>
      <Prose text={report.narrative || msg.text} />
      <div className={s.doc}>
        {best && (
          <button type="button" className={s.result} data-thumb={(inst && !stale) || undefined} onClick={() => openPlan(best.run_id)}>
            {inst && !stale && <Thumb inst={inst} routes={thumbRoutes} />}
            <span>{best.algorithm}<small>最优 · {best.ranking_basis === 'predicted_makespan_s' ? '按同模型预计工期排序' : '按规划目标排序'} · 独立复核通过 · {best.predicted_makespan_s != null ? `预计实际 ${n1(best.predicted_makespan_s)} s` : `最晚完成 ${n1(best.makespan_s)} s`}{best.time_windows ? ` · ${arrivalLine(best.time_windows)}` : ''}{stale ? ' · 历史任务版本' : ''}</small></span>
            <span className={s.num}><Roll value={best.total_distance_m} format={n1} /> m</span>
            <Icon name="right" size={12} />
          </button>
        )}
        {e.runs.length > 0 && (
          <button type="button" className={s.result} onClick={() => openRoot('plans')}>
            <span>{e.runs.length} 个方案<small>{verified} 个通过复核</small></span>
            <span />
            <Icon name="right" size={12} />
          </button>
        )}
        {e.time_window && e.runs.filter((r) => r.status !== 'feasible' && r.reason).map((r) => <p key={r.run_id} className={s.docBad}>{r.algorithm}：{r.reason}</p>)}
        {e.unsupported.length > 0 && <p className={s.docWarn}>未纳入求解：{e.unsupported.join('；')}</p>}
        {report.unverified_numbers.length > 0 && <p className={s.docWarn}>以下数值未在执行依据中找到，请以表格为准：{report.unverified_numbers.join('、')}</p>}
        <footer>
          <span className={s.grow}>{report.source === 'llm' ? '模型撰写，数值已与执行依据核对' : '模板生成'}</span>
          {best && <button type="button" className={s.go} onClick={() => saveFile(runRecordHref(best.run_id), `${best.run_id}.json`)}>最优方案记录</button>}
          <button type="button" className={s.go} onClick={() => saveFile(sessionExportHref(session.session_id), `${session.session_id}.json`)}>会话实验包</button>
        </footer>
      </div>
    </>
  );
}

function Item({ item, session, lastOf }: { item: TimelineItem; session: AgentSession; lastOf: (kind: string) => string | undefined }) {
  if (item.kind === 'activity') return <Activity calls={item.calls} />;
  const { msg, next } = item;
  const latest = lastOf(msg.kind) === msg.id;
  if (msg.role === 'user') return <div className={s.me}>{msg.text}</div>;
  if (msg.role === 'event') return <div className={s.evt} data-kind={msg.kind}><span>{msg.text.replace(/start_simulation/g, TOOL.start_simulation)}</span></div>;
  if (msg.kind === 'confirmation') return <ContractDoc msg={msg} session={session} latest={latest} />;
  if (msg.kind === 'questions') return <QuestionsDoc msg={msg} active={latest && !session.restoration?.read_only && session.state === 'awaiting_user'} />;
  if (msg.kind === 'approval') return <ApprovalDoc msg={msg} next={next} session={session} latest={latest} />;
  if (msg.kind === 'report') return <ReportDoc msg={msg} session={session} />;
  if (msg.kind === 'simulation') return <SimDoc msg={msg} />;
  if (msg.kind === 'physics') return <PhysicsDoc msg={msg} />;
  if (msg.kind === 'robustness') return <RobustnessDoc msg={msg} />;
  return <Prose text={msg.text} />;
}

const SIM_PHASE: Record<string, string> = { starting: '生成田间世界，启动 Gazebo', ready: '场景就绪', running: '各车按方案执行中', finished: '执行完成', failed: '执行失败', stopped: '已停止' };

/** The simulation, live, in the conversation. The approved run keeps its place in the transcript while it executes. */
function LiveSim({ session }: { session: AgentSession }) {
  const { state } = useFieldSim();
  const control = useAgent((x) => x.control);
  const step = session.plan.find((p) => p.kind === 'simulate');
  if (step?.status !== 'running' || !state) return null;
  const done = state.vehicles.reduce((n, v) => n + (v.tasks_done ?? 0), 0);
  const total = state.vehicles.reduce((n, v) => n + (v.tasks_total ?? 0), 0);
  return (
    <div className={s.doc}>
      <header><b>田间仿真</b><span data-tone="wait">{SIM_PHASE[state.phase] ?? state.phase}</span></header>
      {state.vehicles.length > 0 && (
        <div className={s.simRows}>
          {state.vehicles.map((v, i) => (
            <div key={v.name} className={s.simRow}>
              <span className={s.sw} style={{ background: routeColor(i) }} />
              <span>{v.label}</span>
              <span className={s.simBar}><i style={{ width: `${Math.round((v.progress ?? 0) * 100)}%`, background: routeColor(i) }} /></span>
              <span className={s.num}>{v.tasks_done ?? 0}/{v.tasks_total ?? 0}</span>
              <span className={s.num}>{n1(v.distance_m)} m</span>
            </div>
          ))}
        </div>
      )}
      <footer>
        <span className={s.grow}>仿真时钟 {n1(state.sim_time)} s{state.real_time_factor ? ` · ${n1(state.real_time_factor)}× 实时` : ''}{total ? ` · 完成 ${done}/${total}` : ''}</span>
        <button type="button" className={s.go} onClick={() => openFrames([{ t: 'sim' }, { t: 'panel', id: 'rosview' }])}>ROS 视图</button>
        <button type="button" className={s.go} onClick={() => openFrames([{ t: 'sim' }, { t: 'panel', id: 'gazebo' }])}>Gazebo</button>
        <button type="button" className={s.go} onClick={() => openFrames([{ t: 'sim' }, { t: 'panel', id: 'telemetry' }])}>遥测</button>
        <button type="button" className={s.go} onClick={() => void control('cancel')}>停止</button>
      </footer>
    </div>
  );
}

interface SimOutcome { traffic_mode?: 'serial' | 'coordinated'; traffic?: { policy?: string }; time_windows?: TimeWindowRecord; run_id: string; session_id: string; state?: string; phase?: string; check_valid?: boolean | null; errors: string[]; metrics: Record<string, number | null | undefined>; comparison?: EngineGap & { physics_run_id: string } | null }
interface Coordinated { physics_run_id?: string; state: string; check_valid?: boolean | null; errors: string[]; makespan_s?: number | null; min_gap_m?: number | null; holds?: number; time_windows?: TimeWindowRecord }
interface PhysicsRow { run_id: string; physics_run_id?: string; algorithm?: string; state: string; check_valid?: boolean; errors: string[]; wall_s?: number; metrics: Record<string, number | null | undefined>; coordinated?: Coordinated; time_windows?: TimeWindowRecord }

/** Planned against physically executed, for every verified plan. A row opens the trajectory on the map. */
function PhysicsDoc({ msg }: { msg: AgentMessage }) {
  const rows = (msg.data as unknown as { rows: PhysicsRow[] }).rows;
  const ok = (r: PhysicsRow) => r.state === 'succeeded' && r.check_valid;
  const drift = (a?: number | null, b?: number | null) => (typeof a === 'number' && typeof b === 'number' && b ? `${a >= b ? '+' : ''}${n1(((a - b) / b) * 100)}%` : '');
  const together = rows.some((r) => r.coordinated);
  // Show the two traffic policies separately; the actual Gazebo session records its chosen policy.
  const timed = (r: PhysicsRow) => (r.coordinated?.state === 'succeeded' ? r.coordinated.time_windows : undefined) ?? r.time_windows;
  const clocked = rows.some((r) => timed(r));
  return (
    <div className={s.doc}>
      <header><b>物理预演</b><span>MuJoCo · {rows.filter(ok).length}/{rows.length} 通过</span></header>
      <table className={s.docTable}>
        <thead><tr><th>方案</th><th>实际里程</th><th>{together ? '逐车工期' : '实际工期'}</th>{together && <th>并行工期</th>}<th>最大偏差</th>{clocked && <th>实测到达</th>}</tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.run_id} data-go={r.physics_run_id ? '' : undefined} onClick={() => { if (r.physics_run_id) openFrames([{ t: 'plans' }, { t: 'plan', run: r.run_id }, { t: 'phys', run: r.run_id, id: r.physics_run_id }]); }}>
            <td>{r.algorithm ?? r.run_id}{!ok(r) && <small>{r.errors[0] ?? '未通过'}</small>}</td>
            {ok(r) ? <>
              <td><Roll value={r.metrics.total_distance_m} format={n1} /> m<small>{drift(r.metrics.total_distance_m, r.metrics.planned_total_distance_m)}</small></td>
              <td><Roll value={r.metrics.makespan_s} format={n1} /> s<small>{drift(r.metrics.makespan_s, r.metrics.planned_makespan_s)}</small></td>
            </> : <td colSpan={2}>未完成</td>}
            {together && <td>{r.coordinated?.check_valid ? <><Roll value={r.coordinated.makespan_s} format={n1} /> s<small>车距 ≥ {n3(r.coordinated.min_gap_m)} m</small></> : <small>{r.coordinated?.errors[0] ?? '未通过'}</small>}</td>}
            <td>{ok(r) ? `${n3(r.metrics.cross_track_max_m)} m` : '—'}</td>
            {clocked && <td>{timed(r) ? <>{arrivalLine(timed(r))}{!timed(r)?.all_on_time && <small>{lateLine(timed(r))}</small>}</> : '—'}</td>}
          </tr>
        ))}</tbody>
      </table>
      {together && rows.some((r) => r.time_windows?.late?.length) && <p className={s.docNote}>并行与逐车执行分别判定时间窗。逐车执行时后发车辆要排队，{rows.filter((r) => r.time_windows?.late?.length).map((r) => `${r.algorithm ?? r.run_id} 有 ${r.time_windows?.late?.length} 个任务晚于截止`).join('，')}；Gazebo 结果以会话实际选择的交通方式为准。</p>}
      {rows.some((r) => (r.metrics.makespan_s ?? 0) > 1.4 * (r.metrics.planned_makespan_s ?? Infinity)) && <p className={s.docNote}>工期含当前交通策略下整段任务依次执行的等待；规划工期是各车并行的恒速估计。</p>}
    </div>
  );
}

interface RobustnessRow { run_id: string; physics_run_id?: string; algorithm?: string; state: string; requested?: number; finished?: number; passed?: number; cut_off?: number; makespan_s?: Spread; min_gap_m?: Spread; failures?: { label: string; count: number }[]; errors: string[] }
interface RobustnessDifference { a: string; b: string; both_passed: number; a_faster: number; b_faster: number; only_a_passed: number; only_b_passed: number; median_delta_s: number | null }

/** The two best plans side by side under the same random fields. A row opens the distribution. */
function RobustnessDoc({ msg }: { msg: AgentMessage }) {
  const { rows, difference: d } = msg.data as unknown as { rows: RobustnessRow[]; difference: RobustnessDifference | null };
  return (
    <div className={s.doc}>
      <header><b>鲁棒性评估</b><span>MuJoCo · 同一组 {rows.find((r) => r.requested)?.requested ?? '—'} 次扰动</span></header>
      <table className={s.docTable}>
        <thead><tr><th>方案</th><th>通过</th><th>工期中位</th><th>90 分位</th><th>最小车距</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.run_id} data-go={r.physics_run_id ? '' : undefined} onClick={() => { if (r.physics_run_id) openFrames([{ t: 'plans' }, { t: 'plan', run: r.run_id }, { t: 'phys', run: r.run_id, id: r.physics_run_id }]); }}>
            <td>{r.algorithm ?? r.run_id}{!!r.failures?.length && <small>{r.failures.map((f) => `${f.label} ${f.count} 次`).join('、')}</small>}{!r.finished && <small>{r.errors[0] ?? '未完成'}</small>}</td>
            {r.finished ? <>
              <td>{r.passed}/{r.finished}{!!r.cut_off && <small>{r.cut_off} 次到时限</small>}</td>
              <td>{n1(r.makespan_s?.median)} s</td>
              <td>{n1(r.makespan_s?.p90)} s</td>
              <td>{n3(r.min_gap_m?.min)} m</td>
            </> : <td colSpan={4}>—</td>}
          </tr>
        ))}</tbody>
      </table>
      {d && d.both_passed > 0 && <p className={s.docNote}>逐次配对：{d.a} 更快 {d.a_faster} 次，{d.b} 更快 {d.b_faster} 次，工期差中位数 {n1(d.median_delta_s)} s{d.only_a_passed + d.only_b_passed > 0 ? `；只有一方通过 ${d.only_a_passed + d.only_b_passed} 次` : ''}。</p>}
      <p className={s.docNote}>假定参数范围内的仿真敏感度，不是实车可靠性。</p>
    </div>
  );
}

function SimDoc({ msg }: { msg: AgentMessage }) {
  const r = msg.data as unknown as SimOutcome;
  const m = r.metrics;
  const ok = r.state === 'succeeded';
  const gap = (a?: number | null, b?: number | null) => (typeof a === 'number' && typeof b === 'number' && b ? ` · ${a >= b ? '+' : ''}${n1(((a - b) / b) * 100)}%` : '');
  return (
    <div className={s.doc}>
      <header><b>田间仿真</b><span>{ok ? `完成 ${m.tasks_completed}/${m.tasks_total}` : r.state === 'failed' || r.phase === 'failed' ? '执行失败' : r.state === 'canceled' ? '已取消' : '未完成'}{r.check_valid === false ? ' · 运行核验未通过' : ''}</span></header>
      {r.errors.length > 0 && <p className={s.docBad}>{r.errors.join('；')}</p>}
      <p className={s.docNote}>Gazebo · {fieldTrafficLabel(r)}</p>
      {r.time_windows && <p className={r.time_windows.all_on_time ? s.docNote : s.docBad}>实测到达：{arrivalLine(r.time_windows)}{r.time_windows.all_on_time ? '' : `，${lateLine(r.time_windows)}`}</p>}
      {ok && <dl className={s.kv} style={{ fontSize: 13.5 }}>
        <dt>里程</dt><dd>实际 {n1(m.total_distance_m)} m，规划 {n1(m.planned_total_distance_m)} m{gap(m.total_distance_m, m.planned_total_distance_m)}</dd>
        <dt>工期</dt><dd>实际 {n1(m.makespan_s)} s，规划 {n1(m.planned_makespan_s)} s{gap(m.makespan_s, m.planned_makespan_s)}</dd>
        <dt>横向偏差</dt><dd>平均 {n3(m.cross_track_mean_m)} m，最大 {n3(m.cross_track_max_m)} m</dd>
      </dl>}
      {r.comparison && <GapFacts gap={r.comparison} />}
      <footer>
        <span className={s.grow} />
        {ok && <button type="button" className={s.go} onClick={() => openFrames([{ t: 'plans' }, { t: 'plan', run: r.run_id }, { t: 'exec', run: r.run_id, session: r.session_id }])}>仿真轨迹</button>}
        <button type="button" className={s.go} onClick={() => openFrames([{ t: 'sim' }, { t: 'panel', id: 'replay' }])}>回放</button>
      </footer>
    </div>
  );
}

function Running({ session }: { session: AgentSession }) {
  const control = useAgent((x) => x.control);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null);
  const step = session.plan.find((p) => p.status === 'running');
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const since = step?.updated_utc ? Math.max(0, (now - Date.parse(step.updated_utc)) / 1000) : null;
  return (
    <div className={s.running}>
      <span className={s.dots}><i /><i /><i /></span>
      <span className={s.grow}><span className={s.shine}>{step ? step.title : '思考下一步'}</span>{since !== null ? ` · ${n0(since)} s` : ''}</span>
      <button type="button" className={s.go} disabled={busy} onClick={() => void control('pause')}>暂停</button>
      <button type="button" className={s.go} disabled={busy} onClick={() => void control('cancel')}>取消</button>
    </div>
  );
}

function Resumable({ session }: { session: AgentSession }) {
  const control = useAgent((x) => x.control);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null);
  if (session.restoration?.read_only || !['paused', 'failed', 'cancelled', 'interrupted'].includes(session.state)) return null;
  return (
    <div className={s.doc} data-need>
      <header><b>{STATE_LABEL[session.state]}</b></header>
      {session.error && <p className={s.docBad}>{session.error.message}</p>}
      <footer>
        <span className={s.grow} />
        {session.state !== 'paused' && <button type="button" className={s.btn} disabled={busy} onClick={() => void control('retry')}>重试当前阶段</button>}
        <button type="button" className={cx(s.btn, s.btnPrimary)} disabled={busy} onClick={() => void control('resume')}>继续</button>
      </footer>
    </div>
  );
}

/** End of a turn: what is now true and the next move, the way a desktop session closes. */
function NextStep({ session }: { session: AgentSession }) {
  const best = session.report?.evidence.best_verified;
  const control = useAgent((x) => x.control);
  const runs = currentRuns(session);
  if (session.restoration?.read_only || session.state !== 'done' || !best || !runs.includes(best.run_id)) return null;
  const verified = session.report!.evidence.runs.filter((r) => r.independent_recheck).length;
  const simulated = session.report!.evidence.simulation_started;
  const evidence = session.report!.evidence;
  const physics = (evidence as { physics?: unknown[] }).physics?.length;
  const ready = evidence.best_executable ?? best;
  const swapped = ready.run_id !== best.run_id;
  const simulate = () => { void control('simulate', { run_id: ready.run_id }); openFrames([{ t: 'sim' }, { t: 'panel', id: 'rosview' }]); };
  return (
    <div className={s.nextBox}>
      <p>{verified} 个方案通过复核，规划最优是 {best.algorithm}，总里程 {n1(best.total_distance_m)} m。{swapped ? `它没有通过物理预演，可执行的最好方案是 ${ready.algorithm}。` : physics && !evidence.best_executable ? '方案都没有通过物理预演。' : ''}{simulated ? '' : evidence.simulator_gap ? '这台电脑还不能运行 Gazebo。' : '还没有进入 Gazebo。'}</p>
      <div className={s.nextRow}>
        {!simulated && !evidence.simulator_gap && <button type="button" className={cx(s.btn, s.btnPrimary)} onClick={simulate}>送入 Gazebo 仿真</button>}
        {!simulated && evidence.simulator_gap && window.desktop && <a className={s.btn} href="#/system/desktop">开启容器后端</a>}
        <button type="button" className={s.btn} onClick={() => openFrames([{ t: 'plans' }, { t: 'panel', id: 'figures' }])}>科研图表</button>
        <button type="button" className={s.btn} onClick={() => saveFile(sessionExportHref(session.session_id), `${session.session_id}.json`)}>导出实验包</button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- plan strip */
const STEP_ICON: Record<string, IconName> = { done: 'check', waiting: 'clock', running: 'clock', failed: 'cross', pending: 'ring', skipped: 'ring', taken_over: 'check', cancelled: 'cross' };

function PlanStrip({ session }: { session: AgentSession }) {
  const control = useAgent((x) => x.control);
  const studioScenario = useStudio((x) => x.scenario);
  const studioRuns = useStudio((x) => x.runIds);
  const p = planSummary(session);
  const idle = session.state !== 'running' && !session.restoration?.read_only;
  const userMap = studioScenario && studioScenario.problem_class === 'fleet_routing' && (studioScenario as { origin?: string }).origin === 'user' ? studioScenario.id : null;
  return (
    <Fold className={s.plan} lead={<i className={s.planBar} style={{ transform: `scaleX(${p.steps.length ? p.done / p.steps.length : 0})` }} />} summary={<>
      <span className={s.sum}>{p.done}/{p.steps.length} · {p.current ? `${p.current.title}${p.waiting ? '，等待你' : ''}` : session.state === 'done' ? '已完成' : STATE_LABEL[session.state]}</span>
      <span className={s.discMeta}>执行计划</span>
    </>}>
      {() => (
        <ol>
          {p.steps.map((step) => (
            <li key={step.id} data-s={step.status}>
              <Icon name={STEP_ICON[step.status] ?? 'ring'} size={14} />
              <span>{step.title}
                {idle && step.kind === 'scenario' && userMap && userMap !== session.scenario_id && <> · <button type="button" className={s.takeover} title="改用地图编辑器中当前打开的任务地图" onClick={() => void control('takeover', { step: 'scenario', scenario_id: userMap })}>使用当前地图</button></>}
                {idle && step.kind === 'solve' && session.scenario_id && studioScenario?.id === session.scenario_id && studioRuns.some((r) => !session.run_ids.includes(r)) && <> · <button type="button" className={s.takeover} title="把规划工作区中对该地图的求解结果接入本会话" onClick={() => void control('takeover', { step: 'solve', run_ids: studioRuns })}>接入当前方案</button></>}
              </span>
              <small>{step.detail}</small>
            </li>
          ))}
        </ol>
      )}
    </Fold>
  );
}

/* ---------------------------------------------------------------- header */
function SessionTitle({ session }: { session: AgentSession }) {
  const patch = useAgent((x) => x.patchSession);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const label = sessionLabel(session);
  const commit = () => {
    setEditing(false);
    const t = text.trim();
    if (t !== label) void patch(session.session_id, { title: t || null });
  };
  if (editing) {
    return <input value={text} autoFocus aria-label="任务名称" onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false); }} />;
  }
  return <b title="双击重命名" onDoubleClick={() => { setText(label); setEditing(true); }}>{label}</b>;
}

function SessionMenu({ session }: { session: AgentSession }) {
  const patch = useAgent((x) => x.patchSession);
  const menu = useMenu();
  return (
    <>
      <button type="button" className={s.iconBtn} title="更多" aria-label="更多操作" onClick={(e) => menu.open(e.currentTarget, [
        { label: session.pinned ? '取消置顶' : '置顶', onSelect: () => void patch(session.session_id, { pinned: !session.pinned }) },
        { label: session.archived ? '取消归档' : '归档', sub: '只是收起，记录全部保留', onSelect: () => void patch(session.session_id, { archived: !session.archived }) },
        { label: '-' },
        { label: '导出会话实验包', sub: '合同、调用记录、地图与全部运行，附 SHA256', onSelect: () => saveFile(sessionExportHref(session.session_id), `${session.session_id}.json`) },
        session.scenario_id ? { label: '在规划工作台打开', onSelect: () => { location.hash = `#/plan/${encodeURIComponent(session.scenario_id!)}`; } } : { label: '在规划工作台打开', disabled: true, sub: '还没有任务地图' },
      ])}><Icon name="dots" /></button>
      {menu.node}
    </>
  );
}

/* ---------------------------------------------------------- conversation */
/** What arrived after the task was opened. Those items animate in; what was already there does not. */
function useFresh(id: string | null, keys: string[]) {
  const seen = useRef({ id: null as string | null, known: new Set<string>(), fresh: new Set<string>() });
  const st = seen.current;
  if (st.id !== id) seen.current = { id, known: new Set(keys), fresh: new Set() };
  for (const k of keys) {
    if (!seen.current.known.has(k)) { seen.current.known.add(k); seen.current.fresh.add(k); }
  }
  return seen.current.fresh;
}

function useStick(ref: React.RefObject<HTMLDivElement | null>, key: unknown, reset: unknown) {
  const pinned = useRef(true);
  const [away, setAway] = useState(false);
  useEffect(() => { pinned.current = true; setAway(false); }, [reset]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [ref, key]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const on = () => {
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      pinned.current = near; setAway(!near);
    };
    el.addEventListener('scroll', on, { passive: true });
    return () => el.removeEventListener('scroll', on);
  }, [ref]);
  return { away, jump: () => { const el = ref.current; if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); } };
}

export function Conversation() {
  const session = useAgent((x) => x.session);
  const sessionId = useAgent((x) => x.sessionId);
  const loadError = useAgent((x) => x.loadError);
  const actionError = useAgent((x) => x.actionError);
  useStudioSync(session);
  const scroll = useRef<HTMLDivElement>(null);
  const timeline = useMemo(() => (session ? buildTimeline(session) : []), [session]);
  const stick = useStick(scroll, `${timeline.length}/${session?.state}`, sessionId);
  const fresh = useFresh(session?.session_id ?? null, timeline.map((x) => x.key));
  const lastOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of session?.messages ?? []) map.set(m.kind, m.id);
    return (kind: string) => map.get(kind);
  }, [session]);
  if (!session) {
    return <>
      <TopBar surface="agent"><b>载入任务</b></TopBar>
      <div className={s.scroll}><div className={s.col}>{loadError ? <p className={s.docBad}>{loadError}</p> : <div className={s.running}><Spinner /> 正在载入</div>}</div></div>
    </>;
  }
  const u = session.usage;
  return (
    <>
      <TopBar surface="agent" right={<><span className={s.state} data-tone={stateTone(session.state)}>{STATE_LABEL[session.state]}</span><SessionMenu session={session} /></>}>
        <SessionTitle session={session} />
      </TopBar>
      <div className={s.scroll} ref={scroll}>
        <div className={s.col} key={session.session_id}>
          {timeline.map((item) => (
            <div key={item.key} className={s.item} data-enter={fresh.has(item.key) || undefined} data-me={item.kind !== 'activity' && item.msg.role === 'user' ? '' : undefined}>
              <Fresh.Provider value={fresh.has(item.key)}><Item item={item} session={session} lastOf={lastOf} /></Fresh.Provider>
            </div>
          ))}
          {!session.restoration?.read_only && <LiveSim session={session} />}
          {session.state === 'running' && !session.restoration?.read_only && <Running session={session} />}
          <Resumable session={session} />
          <NextStep session={session} />
        </div>
      </div>
      {stick.away && <button type="button" className={s.jump} onClick={stick.jump}>回到最新</button>}
      <div className={s.dock}>
        <div className={s.dockIn}>
          {actionError && <p className={s.actionError}>{actionError}</p>}
          <PlanStrip session={session} />
          <Composer session={session} />
          {session.restoration?.read_only && <p className={s.noteLine}>{session.restoration.note}</p>}
          <p className={s.useLine}><button type="button" onClick={() => openRoot('trace')}>
            {session.provider ? `${u.calls} 次模型调用 · ${n0(u.input_tokens)} → ${n0(u.output_tokens)} tokens · ${n1(u.latency_s)} s` : `规则流程 · 未调用大模型 · ${session.tool_calls.length} 次工具调用`}
          </button></p>
        </div>
      </div>
    </>
  );
}
