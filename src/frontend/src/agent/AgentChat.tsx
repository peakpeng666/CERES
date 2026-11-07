import { useEffect, useRef, useState } from 'react';
import {
  CircleAlert,
  CircleCheck,
  FileJson,
  MapPinned,
  Pause,
  Play,
  Plus,
  RotateCcw,
  SendHorizontal,
  ShieldCheck,
  Square,
  TriangleAlert,
} from 'lucide-react';
import { Button, Segmented, Spinner } from '../lab/ui';
import { useStudio } from '../studio/store';
import {
  SOURCE_LABEL,
  STATE_LABEL,
  type AgentMessage,
  type AgentSession,
  type ContractCard,
  type Pending,
  type Report,
} from './api';
import { findModel, useAgent } from './store';
import { currentRuns, syncAgentStudio } from './studioSync';
import s from './agent.module.css';

const EXAMPLES = [
  '在 200×120 米的果园里，3 台机器人从西南角车库出发，给 40 个点采样，每台最多装 10 个样本，避开两个池塘和一栋房子',
  '5 公顷麦田分成 6 块，4 辆无人车巡检 60 个巡检点，要求尽快完成，有 3 棵大树和一条水渠',
  '30 亩菜地，两台小车从东北角出发运输，每台载重 20，十二个投放点聚集分布，每个点要在上午九点前送达',
];

/** Model status for the toolbar and the empty state: what will actually answer, stated plainly. */
export function ModelChip({ compact = false }: { compact?: boolean }) {
  const providers = useAgent((x) => x.providers);
  const error = useAgent((x) => x.providersError);
  const session = useAgent((x) => x.session);
  const chosen = useAgent((x) => x.newProvider);
  const mode = useAgent((x) => x.newMode);
  const load = useAgent((x) => x.loadProviders);
  useEffect(() => {
    if (!providers && !error) void load();
  }, [providers, error, load]);
  if (session || mode === 'rule') {
    const provider = session?.provider;
    const label = provider ? provider.label : '规则流程（未调用大模型）';
    return (
      <span className={s.chip} data-tone={provider ? 'ok' : 'rule'} title={provider ? `${provider.kind} · ${provider.model}` : '理解与执行均由确定性规则完成'}>
        <span className={s.dot} />
        {compact ? (provider ? provider.model : '规则流程') : label}
      </span>
    );
  }
  const model = findModel(providers, chosen ?? providers?.selected);
  const text = error ? '模型状态未知' : !providers ? '读取模型状态…' : model ? model.name : '还没有连接模型服务商';
  return (
    <span className={s.chip} data-tone={model ? 'ok' : 'warn'} title={model ? model.ref : '没有连接时新任务使用规则流程，并在会话中注明'}>
      <span className={s.dot} />
      {compact ? (model ? model.name : '规则流程') : text}
    </span>
  );
}

function SourceTag({ source }: { source: ContractCard['source'] }) {
  return <span className={s.source} data-source={source}>{SOURCE_LABEL[source] ?? source}</span>;
}

function ConfirmationCard({ msg, session, latest }: { msg: AgentMessage; session: AgentSession; latest: boolean }) {
  const control = useAgent((x) => x.control);
  const pending = useAgent((x) => x.sending || x.pendingAction !== null);
  const data = msg.data as { cards: ContractCard[]; unsupported: string[]; revision: number };
  const active = latest && !session.restoration?.read_only && session.state === 'awaiting_confirmation';
  return (
    <div className={s.card}>
      <div className={s.cardHead}>任务合同 · 第 {data.revision} 版</div>
      <table className={s.contractTable}>
        <tbody>
          {data.cards.map((c) => (
            <tr key={c.key} data-default={c.source === 'default' || undefined}>
              <th>{c.label}</th>
              <td>{c.text}</td>
              <td title={c.evidence ? `依据：${c.evidence}` : undefined}><SourceTag source={c.source} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.unsupported.length > 0 && (
        <div className={s.warn}>
          <TriangleAlert size={13} />
          <span>以下要求当前无法表达，不会进入求解：{data.unsupported.join('；')}</span>
        </div>
      )}
      {active && (
        <div className={s.cardActions}>
          <Button variant="primary" size="sm" icon={CircleCheck} disabled={pending} onClick={() => void control('confirm')}>确认并执行</Button>
          <span className={s.hint}>也可在右侧「任务合同」中修改后再确认</span>
        </div>
      )}
    </div>
  );
}

function QuestionsCard({ pending, active }: { pending: Pending; active: boolean }) {
  const send = useAgent((x) => x.send);
  const busy = useAgent((x) => x.sending || x.pendingAction !== null);
  return (
    <div className={s.card}>
      {(pending.questions ?? []).map((q, i) => (
        <div key={i} className={s.question}>
          <div>{q.question}</div>
          {q.options.length > 0 && (
            <div className={s.options}>
              {q.options.map((o) => (
                <button key={o} type="button" className={s.option} disabled={!active || busy} onClick={() => void send(o)}>{o}</button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function ApprovalCard({ msg, session, latest }: { msg: AgentMessage; session: AgentSession; latest: boolean }) {
  const control = useAgent((x) => x.control);
  const pending = useAgent((x) => x.sending || x.pendingAction !== null);
  const active = latest && !session.restoration?.read_only && session.state === 'awaiting_approval';
  return (
    <div className={s.card} data-tone="approval">
      <div className={s.cardHead}><ShieldCheck size={13} /> 需要你的批准</div>
      <div className={s.cardText}>{msg.text}</div>
      {active && (
        <div className={s.cardActions}>
          <Button variant="primary" size="sm" disabled={pending} onClick={() => void control('approve')}>批准</Button>
          <Button size="sm" disabled={pending} onClick={() => void control('reject')}>拒绝</Button>
        </div>
      )}
    </div>
  );
}

const fmt = (v: number | null | undefined, digits = 1) => (typeof v === 'number' ? v.toFixed(digits) : '—');

function ReportCard({ report, session }: { report: Report; session: AgentSession }) {
  const e = report.evidence;
  const best = e.best_verified;
  const canShow = best && currentRuns(session).includes(best.run_id);
  return (
    <div className={s.card} data-tone="report">
      <div className={s.cardHead}>
        结果报告
        <span className={s.sourceNote}>{report.source === 'llm' ? '模型撰写 · 数值已与依据核对' : '模板生成'}</span>
      </div>
      <div className={s.cardText}>{report.narrative}</div>
      {report.unverified_numbers.length > 0 && (
        <div className={s.warn}>
          <TriangleAlert size={13} />
          <span>以下数值未在执行依据中找到，请以下表为准：{report.unverified_numbers.join('、')}</span>
        </div>
      )}
      {best && (
        <div className={s.best}>
          <span className={s.bestBadge}><CircleCheck size={12} /> 独立复核通过</span>
          <span className={s.bestName}>{best.algorithm}</span>
          <span className={s.metric}><em>总里程</em>{fmt(best.total_distance_m)} m</span>
          <span className={s.metric}><em>最晚完成</em>{fmt(best.makespan_s)} s</span>
        </div>
      )}
      {e.runs.length > 0 && (
        <table className={s.runTable}>
          <thead>
            <tr><th>方案</th><th>状态</th><th>里程 m</th><th>工期 s</th><th>耗时 s</th></tr>
          </thead>
          <tbody>
            {e.runs.map((r) => (
              <tr key={r.run_id} data-best={best?.run_id === r.run_id || undefined}>
                <td title={r.run_id}>{r.algorithm}</td>
                <td>{r.independent_recheck === true ? '复核通过' : r.diagnosis_label}</td>
                <td>{fmt(r.total_distance_m)}</td>
                <td>{fmt(r.makespan_s)}</td>
                <td>{fmt(r.wall_s, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {e.unsupported.length > 0 && <div className={s.muted}>未纳入求解的要求：{e.unsupported.join('；')}</div>}
      {best && (
        <div className={s.cardActions}>
          <Button size="sm" icon={MapPinned} disabled={!canShow} title={canShow ? undefined : '该方案属于历史任务版本，可下载记录查看'} onClick={() => { useStudio.setState({ focusRun: best.run_id, manualFocus: true }); window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: 'map' })); }}>在地图上查看</Button>
          <a className={s.linkButton} href={`/lab/api/runs/${encodeURIComponent(best.run_id)}/record`} download={`${best.run_id}.json`}><FileJson size={13} /> 最优方案记录</a>
        </div>
      )}
      <div className={s.cardActions}>
        <a className={s.linkButton} href={`/lab/api/agent/sessions/${encodeURIComponent(session.session_id)}/export`} download={`${session.session_id}.json`}
          title="任务合同、计划、工具与模型调用记录、任务地图及全部运行记录，附 SHA256 完整性校验"><FileJson size={13} /> 导出会话实验包</a>
      </div>
    </div>
  );
}

function Message({ msg, session, latest }: { msg: AgentMessage; session: AgentSession; latest: boolean }) {
  if (msg.role === 'user') return <div className={s.user}>{msg.text}</div>;
  if (msg.role === 'event') {
    return (
      <div className={s.event} data-kind={msg.kind}>
        {msg.kind === 'error' ? <CircleAlert size={12} /> : null}
        <span>{msg.text}</span>
      </div>
    );
  }
  if (msg.kind === 'confirmation') return <ConfirmationCard msg={msg} session={session} latest={latest} />;
  if (msg.kind === 'questions') {
    return (
      <div className={s.assistant}>
        <div>{msg.text}</div>
        <QuestionsCard pending={msg.data as unknown as Pending} active={latest && !session.restoration?.read_only && session.state === 'awaiting_user'} />
      </div>
    );
  }
  if (msg.kind === 'approval') return <ApprovalCard msg={msg} session={session} latest={latest} />;
  if (msg.kind === 'report') return <ReportCard report={msg.data as unknown as Report} session={session} />;
  return <div className={s.assistant}>{msg.text}</div>;
}

function RunningLine({ session }: { session: AgentSession }) {
  const control = useAgent((x) => x.control);
  const pending = useAgent((x) => x.sending || x.pendingAction !== null);
  const step = session.plan.find((p) => p.status === 'running');
  const last = session.tool_calls[session.tool_calls.length - 1];
  return (
    <div className={s.running}>
      <Spinner />
      <span className={s.runningText}>{step ? `${step.title}…` : '思考下一步…'}{last && !step ? ` 上一步：${last.label ?? last.name}` : ''}</span>
      <Button size="sm" icon={Pause} disabled={pending} onClick={() => void control('pause')}>暂停</Button>
      <Button size="sm" icon={Square} disabled={pending} onClick={() => void control('cancel')}>取消</Button>
    </div>
  );
}

function Resumable({ session }: { session: AgentSession }) {
  const control = useAgent((x) => x.control);
  const pending = useAgent((x) => x.sending || x.pendingAction !== null);
  if (session.restoration?.read_only || !['paused', 'failed', 'cancelled', 'interrupted'].includes(session.state)) return null;
  return (
    <div className={s.resume}>
      <span>{STATE_LABEL[session.state]}{session.error ? `：${session.error.message}` : ''}</span>
      <Button size="sm" icon={Play} disabled={pending} onClick={() => void control('resume')}>继续</Button>
      {session.state !== 'paused' && <Button size="sm" icon={RotateCcw} disabled={pending} onClick={() => void control('retry')}>重试当前阶段</Button>}
    </div>
  );
}

function Composer({ session }: { session: AgentSession | null }) {
  const [text, setText] = useState('');
  const send = useAgent((x) => x.send);
  const sending = useAgent((x) => x.sending);
  const sessionId = useAgent((x) => x.sessionId);
  const pending = useAgent((x) => x.pendingAction !== null);
  const busy = session?.state === 'running' || Boolean(sessionId && !session) || pending;
  const placeholder = !session
    ? '描述作业任务，例如场地大小、车辆数、任务点数、容量与目标'
    : session.state === 'awaiting_user' ? '回答上面的问题，或补充其他要求'
      : session.state === 'awaiting_confirmation' ? '输入“确认”开始执行，或说明要修改的地方'
        : session.state === 'awaiting_approval' ? '输入“批准”或“拒绝”，也可提出新的要求'
          : '继续修改要求，例如：改成 4 台车，只允许单趟往返';
  const submit = () => {
    const t = text.trim();
    if (!t || busy || sending) return;
    void send(t).then((sent) => { if (sent) setText(''); });
  };
  return (
    <div className={s.composer}>
      <textarea
        value={text}
        rows={2}
        placeholder={placeholder}
        disabled={busy || sending}
        aria-label="给智能体的消息"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <Button variant="primary" icon={SendHorizontal} loading={sending} disabled={!text.trim() || busy} onClick={submit} title="发送（Enter）" />
    </div>
  );
}

/** Model for new sessions: everything on the connected services; the default is the one chosen in settings. */
function ProviderPicker() {
  const providers = useAgent((x) => x.providers);
  const chosen = useAgent((x) => x.newProvider);
  const setChosen = useAgent((x) => x.setNewProvider);
  const mode = useAgent((x) => x.newMode);
  const models = (providers?.connected ?? []).flatMap((c) => c.models.map((m) => ({ ...m, label: `${m.name} · ${c.name}` })));
  if (mode === 'rule' || models.length < 2) return null;
  const value = chosen ?? providers?.selected ?? models[0].ref;
  return (
    <select className={s.providerPicker} value={value} aria-label="新任务使用的模型" onChange={(e) => setChosen(e.target.value)}>
      {models.map((m) => <option key={m.ref} value={m.ref}>{m.label}{m.ref === providers?.selected ? '（默认）' : ''}</option>)}
    </select>
  );
}

function Welcome() {
  const start = useAgent((x) => x.start);
  const sending = useAgent((x) => x.sending);
  const mode = useAgent((x) => x.newMode);
  const setMode = useAgent((x) => x.setNewMode);
  return (
    <div className={s.welcome}>
      <h2>农业作业任务智能体</h2>
      <p>描述作业需求，确认任务合同后，智能体建图、对比算法并写出报告。</p>
      <ol className={s.flow}>
        <li>理解需求</li><li>确认合同</li><li>建图与推荐</li><li>求解对比</li><li>独立复核</li><li>报告与导出</li>
      </ol>
      <div className={s.modeRow}>
        <span>新任务使用</span>
        <Segmented value={mode} onChange={setMode} label="会话模式" options={[
          { value: 'auto', label: '自动', title: '模型可用时由模型理解与执行，否则使用规则流程' },
          { value: 'rule', label: '规则流程', title: '不调用大模型，作为对照基线' },
        ]} />
        <ProviderPicker />
        <ModelChip />
      </div>
      <div className={s.examples}>
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button" className={s.exampleCard} disabled={sending} onClick={() => void start(ex)}>{ex}</button>
        ))}
      </div>
    </div>
  );
}

/** Keeps the shared map, comparison and verification panels on the agent's scenario and latest runs. */
export function useStudioSync(session: AgentSession | null) {
  const scenarioId = session?.scenario_id ?? null;
  const key = session ? currentRuns(session).join(',') : '';
  const buildKey = `${session?.session_id ?? ''}/${scenarioId ?? ''}/${session?.built_revision ?? ''}/${session?.scenario_instance_sha256 ?? ''}`;
  const synced = useRef<string | null>(null);
  useEffect(() => {
    if (!session || !scenarioId) return;
    let active = true;
    const reload = synced.current !== buildKey && useStudio.getState().scenarioId === scenarioId;
    void syncAgentStudio(session, () => active, reload).then((ready) => {
      if (ready && active) synced.current = buildKey;
    });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildKey, key]);
}

export function AgentChatPanel() {
  const session = useAgent((x) => x.session);
  const sessions = useAgent((x) => x.sessions);
  const sessionId = useAgent((x) => x.sessionId);
  const loadError = useAgent((x) => x.loadError);
  const actionError = useAgent((x) => x.actionError);
  const loadSessions = useAgent((x) => x.loadSessions);
  const stream = useRef<HTMLDivElement>(null);
  useStudioSync(session);
  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);
  const count = session?.messages.length ?? 0;
  useEffect(() => {
    stream.current?.scrollTo({ top: stream.current.scrollHeight, behavior: 'smooth' });
  }, [count, session?.state]);
  const lastIndex = (kind: string) => {
    if (!session) return -1;
    for (let i = session.messages.length - 1; i >= 0; i--) if (session.messages[i].kind === kind) return i;
    return -1;
  };
  return (
    <div className={s.chat}>
      <div className={s.chatHead}>
        <select
          className={s.picker}
          value={sessionId ?? ''}
          aria-label="选择会话"
          onChange={(e) => { location.hash = e.target.value ? `#/agent/${encodeURIComponent(e.target.value)}` : '#/agent'; }}
        >
          <option value="">新任务</option>
          {sessions.map((x) => (
            <option key={x.session_id} value={x.session_id}>{x.title} · {STATE_LABEL[x.state]}</option>
          ))}
        </select>
        <Button size="sm" icon={Plus} title="新任务" onClick={() => { location.hash = '#/agent'; }} />
        {session && <span className={s.state} data-state={session.state}>{STATE_LABEL[session.state]}</span>}
      </div>
      <div className={s.stream} ref={stream}>
        {!sessionId && <Welcome />}
        {sessionId && !session && !loadError && <div className={s.loading}><Spinner /> 载入会话…</div>}
        {loadError && <div className={s.event} data-kind="error"><CircleAlert size={12} /> {loadError}</div>}
        {session?.messages.map((m, i) => (
          <Message key={m.id} msg={m} session={session} latest={i === lastIndex(m.kind)} />
        ))}
        {session?.state === 'running' && !session.restoration?.read_only && <RunningLine session={session} />}
        {session && <Resumable session={session} />}
      </div>
      {actionError && <div className={s.actionError}><CircleAlert size={12} /> {actionError}</div>}
      {session?.restoration?.read_only ? <p className={s.event}>{session.restoration.note}</p> : <Composer key={sessionId ?? 'new'} session={session} />}
    </div>
  );
}
