import { useState, type ReactElement } from 'react';
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  CircleX,
  Clock,
  Hand,
  LoaderCircle,
} from 'lucide-react';
import { Button } from '../lab/ui';
import { useStudio } from '../studio/store';
import { ACTOR_LABEL, SOURCE_LABEL, type AgentSession, type ContractCard, type PlanStep, type ToolCall } from './api';
import { useAgent } from './store';
import s from './agent.module.css';

/** Editable contract fields: kind, options, unit; mirrors ceres_lab.agent.contract.FIELDS. */
export const EDITABLE: Record<string, { kind: 'int' | 'number' | 'enum' | 'bool' | 'size' | 'text'; unit?: string; options?: Record<string, string> }> = {
  task_count: { kind: 'int', unit: '个' },
  vehicle_count: { kind: 'int', unit: '台' },
  vehicle_capacity: { kind: 'int', unit: '单位' },
  task_demand: { kind: 'int', unit: '单位' },
  vehicle_speed_mps: { kind: 'number', unit: 'm/s' },
  field_size_m: { kind: 'size', unit: 'm' },
  plot_count: { kind: 'int', unit: '块' },
  objective: { kind: 'enum', options: { total_distance: '总里程最短', makespan: '最晚完成时间最短' } },
  trip_mode: { kind: 'enum', options: { multi: '允许多趟往返', single: '每车单趟' } },
  travel_mode: { kind: 'enum', options: { rows: '沿行间与地头', free: '自由通行（只避障）' } },
  task_distribution: { kind: 'enum', options: { uniform: '均匀分布', clustered: '成片聚集', rows: '沿作物行' } },
  depot_location: { kind: 'enum', options: { sw: '西南角', se: '东南角', nw: '西北角', ne: '东北角', center: '中部' } },
  start_clock: { kind: 'text' },
  task_deadline: { kind: 'text' },
  task_earliest: { kind: 'text' },
  time_limit_s: { kind: 'number', unit: 's' },
  compare_count: { kind: 'int', unit: '个' },
  seed: { kind: 'int' },
  simulate: { kind: 'bool' },
};
export const OPTIONAL_LABEL: Record<string, string> = {
  trip_mode: '趟次', travel_mode: '通行方式', time_limit_s: '每个算法的时间预算', compare_count: '对比算法数', seed: '随机种子',
  simulate: '规划后进入仿真执行', vehicle_capacity: '单车容量', vehicle_speed_mps: '车速', objective: '优化目标', task_demand: '单点需求',
  start_clock: '出发时刻', task_deadline: '截止时间', task_earliest: '最早时间',
};

export function ContractDetails({ card }: { card: ContractCard }) {
  if (!card.details?.length) return null;
  return (
    <details>
      <summary>完整作业参数</summary>
      <dl>{card.details.map((d) => <div key={d.label}><dt>{d.label}</dt><dd>{d.text}</dd></div>)}</dl>
    </details>
  );
}

export function FieldEditor({ card, onDone }: { card: Pick<ContractCard, 'key' | 'value'>; onDone: () => void }) {
  const spec = EDITABLE[card.key];
  const editContract = useAgent((x) => x.editContract);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null || x.session?.state === 'running');
  const [value, setValue] = useState<string>(() => {
    if (spec.kind === 'size' && Array.isArray(card.value)) return (card.value as number[]).join(' × ');
    if (card.value !== undefined && card.value !== null && card.value !== '') return String(card.value);
    return spec.kind === 'enum' ? Object.keys(spec.options!)[0] : spec.kind === 'bool' ? 'false' : '';
  });
  const commit = async () => {
    let v: unknown = value;
    if (spec.kind === 'int' || spec.kind === 'number') v = Number(value);
    if (spec.kind === 'bool') v = value === 'true';
    if (spec.kind === 'size') v = value.split(/[×x*,，\s]+/).filter(Boolean).map(Number);
    if (await editContract({ [card.key]: v })) onDone();
  };
  return (
    <span className={s.editor}>
      {spec.kind === 'enum' || spec.kind === 'bool' ? (
        <select value={value} disabled={busy} autoFocus onChange={(e) => setValue(e.target.value)}>
          {spec.kind === 'bool'
            ? [<option key="true" value="true">是</option>, <option key="false" value="false">否</option>]
            : Object.entries(spec.options!).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      ) : (
        <input value={value} disabled={busy} autoFocus onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => {
          if (e.key === 'Enter') void commit();
          if (e.key === 'Escape') onDone();
        }} />
      )}
      {spec.unit && <span className={s.unit}>{spec.unit}</span>}
      <Button size="sm" variant="primary" disabled={busy} onClick={() => void commit()}>保存</Button>
      <Button size="sm" variant="plain" onClick={onDone}>取消</Button>
    </span>
  );
}

function ContractSection({ session }: { session: AgentSession }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState('');
  const pending = useAgent((x) => x.sending || x.pendingAction !== null);
  const locked = session.state === 'running' || !!session.restoration?.read_only;
  const c = session.contract;
  const present = new Set(session.contract_cards.map((x) => x.key));
  const addable = Object.keys(OPTIONAL_LABEL).filter((k) => !present.has(k));
  return (
    <section className={s.section}>
      <header>
        <h3>任务合同</h3>
        <span className={s.revision} data-status={c.status}>
          第 {c.revision} 版 · {c.status === 'confirmed' ? '已确认' : '草稿'}
        </span>
      </header>
      {session.contract_cards.length === 0 && <p className={s.muted}>尚未读取到任务要素。</p>}
      <table className={s.contractTable}>
        <tbody>
          {session.contract_cards.map((card) => (
            <tr key={card.key}>
              <th>{card.label}</th>
              <td>
                {editing === card.key ? <FieldEditor card={card} onDone={() => setEditing(null)} /> : (
                  <button type="button" className={s.valueButton} disabled={locked || pending || !EDITABLE[card.key]}
                    title={EDITABLE[card.key] ? '点击修改' : card.key === 'field_mission' ? '请在对话中修改田间任务，再确认' : '请在地图上编辑'} onClick={() => setEditing(card.key)}>{card.text}</button>
                )}
                <ContractDetails card={card} />
              </td>
              <td title={card.evidence ? `依据：${card.evidence}` : undefined}>
                <span className={s.source} data-source={card.source}>{SOURCE_LABEL[card.source]}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {session.missing.length > 0 && <p className={s.warnText}>仍缺少：{session.missing.map((m) => m.label).join('、')}</p>}
      {c.unsupported.length > 0 && <p className={s.warnText}>未支持的要求：{c.unsupported.join('；')}</p>}
      {!locked && addable.length > 0 && (
        <div className={s.addRow}>
          <select value={adding} disabled={pending} onChange={(e) => setAdding(e.target.value)} aria-label="添加字段">
            <option value="">添加约束或执行参数…</option>
            {addable.map((k) => <option key={k} value={k}>{OPTIONAL_LABEL[k]}</option>)}
          </select>
          {adding && <FieldEditor key={adding} card={{ key: adding, value: adding === 'simulate' ? 'false' : '' }} onDone={() => setAdding('')} />}
        </div>
      )}
    </section>
  );
}

const STEP_ICON: Record<PlanStep['status'], ReactElement> = {
  pending: <CircleDashed size={14} />,
  running: <LoaderCircle size={14} className={s.spin} />,
  done: <CircleCheck size={14} />,
  failed: <CircleX size={14} />,
  waiting: <Clock size={14} />,
  skipped: <CircleSlash size={14} />,
  taken_over: <Hand size={14} />,
  cancelled: <CircleSlash size={14} />,
};
const STEP_STATUS: Record<PlanStep['status'], string> = {
  pending: '待执行', running: '执行中', done: '完成', failed: '失败', waiting: '等待', skipped: '跳过', taken_over: '用户接管', cancelled: '已取消',
};

function PlanSection({ session }: { session: AgentSession }) {
  const control = useAgent((x) => x.control);
  const studioScenario = useStudio((x) => x.scenario);
  const studioRuns = useStudio((x) => x.runIds);
  const idle = session.state !== 'running' && !session.restoration?.read_only;
  const userMap = studioScenario
    && (studioScenario.problem_class === 'fleet_routing' || studioScenario.problem_class === 'field_operation')
    && (studioScenario as { origin?: string }).origin === 'user'
    ? studioScenario.id : null;
  return (
    <section className={s.section}>
      <header><h3>执行计划</h3></header>
      <ol className={s.plan}>
        {session.plan.map((step) => (
          <li key={step.id} data-status={step.status}>
            <span className={s.stepIcon}>{STEP_ICON[step.status]}</span>
            <span className={s.stepTitle}>{step.title}</span>
            <span className={s.stepDetail}>{step.detail || STEP_STATUS[step.status]}</span>
            {idle && step.kind === 'scenario' && userMap && userMap !== session.scenario_id && (
              <button type="button" className={s.takeover} title="改用地图编辑器中当前打开的任务地图"
                onClick={() => void control('takeover', { step: 'scenario', scenario_id: userMap })}>使用当前地图</button>
            )}
            {idle && step.kind === 'solve' && session.scenario_id && studioScenario?.id === session.scenario_id
              && studioRuns.some((r) => !session.run_ids.includes(r)) && (
              <button type="button" className={s.takeover} title="把规划工作区中对该地图的求解结果接入本会话"
                onClick={() => void control('takeover', { step: 'solve', run_ids: studioRuns })}>接入当前方案</button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function ToolRow({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={s.toolRow} data-status={call.status}>
      <button type="button" className={s.toolHead} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className={s.toolName}>{call.label ?? call.name}</span>
        <span className={s.actor} data-actor={call.actor}>{ACTOR_LABEL[call.actor] ?? call.actor}</span>
        <span className={s.toolStatus}>{call.status === 'ok' ? '成功' : call.status === 'refused' ? '被拒绝' : '失败'}</span>
        <span className={s.toolTime}>{call.duration_s.toFixed(2)} s</span>
      </button>
      {call.error && <div className={s.toolError}>{call.error.message}</div>}
      {open && (
        <pre className={s.json}>{JSON.stringify({ 参数: call.arguments, 结果: call.result ?? null }, null, 1).slice(0, 6000)}</pre>
      )}
    </li>
  );
}

function UsageSection({ session }: { session: AgentSession }) {
  const u = session.usage;
  return (
    <section className={s.section}>
      <header><h3>模型用量</h3></header>
      {session.provider ? (
        <dl className={s.usage}>
          <div><dt>模型</dt><dd>{session.provider.label}</dd></div>
          <div><dt>调用</dt><dd>{u.calls} 次</dd></div>
          <div><dt>输入 / 输出</dt><dd>{u.input_tokens.toLocaleString()} / {u.output_tokens.toLocaleString()} tokens</dd></div>
          <div><dt>模型耗时</dt><dd>{u.latency_s.toFixed(1)} s</dd></div>
          <div><dt>费用</dt><dd>{u.unpriced_calls
            ? `${u.unpriced_calls} 次未按量计价（订阅额度或未配置单价）${u.currency ? ` · 其余 ${(u.cost ?? 0).toFixed(4)} ${u.currency}` : ''}`
            : u.cost === null ? '—' : `${u.cost.toFixed(4)} ${u.currency ?? ''}`}</dd></div>
        </dl>
      ) : <p className={s.muted}>本会话为规则流程，未调用大模型。</p>}
    </section>
  );
}

export function AgentTaskPanel() {
  const session = useAgent((x) => x.session);
  if (!session) {
    return <div className={s.taskEmpty}>开始一个任务后，这里显示任务合同、执行计划、工具调用记录与模型用量。</div>;
  }
  return (
    <div className={s.task}>
      <ContractSection key={session.session_id} session={session} />
      <PlanSection session={session} />
      <section className={s.section}>
        <header><h3>调用记录</h3><span className={s.revision}>{session.tool_calls.length} 次</span></header>
        {session.tool_calls.length === 0 ? <p className={s.muted}>尚无工具调用。</p> : (
          <ul className={s.tools}>{session.tool_calls.slice().reverse().map((c) => <ToolRow key={c.id} call={c} />)}</ul>
        )}
      </section>
      <UsageSection session={session} />
    </div>
  );
}
