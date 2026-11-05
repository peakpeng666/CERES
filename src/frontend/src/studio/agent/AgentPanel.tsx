import { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, Check, FlaskConical, Map as MapIcon, Plus, Send, ShieldAlert, Square, X } from 'lucide-react';
import { fmt, useLabEvent, useLabResource } from '../../lab/api';
import { Badge, Button, EmptyState, Notice, Spinner } from '../../lab/ui';
import { studio } from '../api';
import { useStudio } from '../store';
import { groundingVerdict, isBusy, preview, resultSummary, runScenarios, segments, STATE_LABEL, TIER_LABEL } from './model';
import { useAgent } from './store';
import type { AgentSession, AgentStep } from './types';
import s from './agent.module.css';

const EXAMPLES = [
  '在 200×120 米的果园里，3 台机器人从西南角车库出发，给 40 个点采样，每台最多装 10 个样本，避开两个池塘和一栋房子。生成地图后比较 PyVRP 和 OR-Tools，各给 3 秒。',
  '列出适用于多车任务的算法，说明来源和最优性保证。',
  '看看最近的运行记录，哪些没有通过独立检查，原因是什么？',
];

type ToolStep = Extract<AgentStep, { kind: 'tool' }>;

function openScenario(id: string) {
  useStudio.getState().setWorkspace('plan');
  location.hash = `#/plan/${encodeURIComponent(id)}`;
}

function openRuns(scenarioId: string, runIds: string[]) {
  const inPlan = useStudio.getState().workspace === 'plan';
  void useStudio.getState().showRuns(scenarioId, runIds);
  if (inPlan) window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: 'compare' }));
}

function openStudy(id: string) {
  const st = useStudio.getState();
  st.setWorkspace('experiments');
  st.setStudy(id);
}

/** Answer text with record ids as buttons; nothing the model writes is rendered as HTML. */
function RichText({ text, runMap }: { text: string; runMap: Record<string, string> }) {
  return (
    <p className={s.text}>
      {segments(text).map((part, i) => {
        if (part.kind === 'text') return <span key={i}>{part.text}</span>;
        const scenario = part.type === 'run' ? runMap[part.text] : undefined;
        if (part.type === 'run' && scenario) {
          return <button key={i} type="button" className={s.idLink} title="在地图和方案对比中查看" onClick={() => openRuns(scenario, [part.text])}>{part.text}</button>;
        }
        if (part.type === 'study') {
          return <button key={i} type="button" className={s.idLink} title="打开课题" onClick={() => openStudy(part.text)}>{part.text}</button>;
        }
        return <code key={i} className={s.idCode}>{part.text}</code>;
      })}
    </p>
  );
}

function Details({ args, result }: { args: unknown; result?: unknown }) {
  const json = (v: unknown) => {
    const text = typeof v === 'string' ? v : JSON.stringify(v, null, 1);
    return text.length > 6000 ? `${text.slice(0, 6000)}\n…` : text;
  };
  return (
    <details className={s.details}>
      <summary>参数与结果</summary>
      <pre>{json(args)}</pre>
      {result !== undefined && <pre>{json(result)}</pre>}
    </details>
  );
}

function ToolCard({ step }: { step: ToolStep }) {
  return (
    <div className={s.tool} data-ok={step.ok || undefined} data-tier={step.tier ?? undefined}>
      <div className={s.toolHead}>
        <span className={s.toolMark}>{step.ok ? <Check size={12} /> : <X size={12} />}</span>
        <strong>{step.label ?? step.name}</strong>
        <code className={s.toolName}>{step.name}</code>
        {step.tier && <Badge tone={step.tier === 'actuate' ? 'orange' : step.tier === 'compute' ? 'blue' : 'gray'}>{TIER_LABEL[step.tier]}</Badge>}
        <span className={s.spacer} />
        <span className={s.meta}>{fmt.sec(step.duration_s)}</span>
      </div>
      <div className={s.toolLine} title={preview(step.arguments, 400)}>{resultSummary(step)}</div>
      <Details args={step.arguments} result={step.ok ? step.result : undefined} />
    </div>
  );
}

function ApprovalCard({ step, session, onDecide }: { step: Extract<AgentStep, { kind: 'approval' }>; session: AgentSession; onDecide: (approve: boolean) => void }) {
  const live = step.decision === 'pending' && session.pending?.call_id === step.call_id && session.state === 'awaiting_approval';
  const label = { pending: '等待你的决定', approved: '已批准', denied: '已拒绝', canceled: '已取消' }[step.decision];
  return (
    <div className={s.approval} data-live={live || undefined}>
      <div className={s.toolHead}>
        <ShieldAlert size={14} />
        <strong>需要批准：{step.label}</strong>
        <span className={s.spacer} />
        <Badge tone={step.decision === 'approved' ? 'green' : step.decision === 'pending' ? 'orange' : 'gray'}>{label}</Badge>
      </div>
      <p className={s.hint}>这是执行操作，会启动田间仿真（需要本机 ROS 2 / Gazebo）。批准前先确认方案已通过独立检查。</p>
      <code className={s.args}>{preview(step.arguments, 300)}</code>
      {live && (
        <div className={s.row}>
          <Button size="sm" variant="primary" icon={Check} onClick={() => onDecide(true)}>批准执行</Button>
          <Button size="sm" icon={X} onClick={() => onDecide(false)}>拒绝</Button>
        </div>
      )}
    </div>
  );
}

function Evidence({ session, runMap }: { session: AgentSession; runMap: Record<string, string> }) {
  const byScenario: Record<string, string[]> = {};
  for (const run of session.evidence.runs) {
    const sc = runMap[run];
    if (sc) (byScenario[sc] ??= []).push(run);
  }
  const scenarios = session.evidence.scenarios;
  if (!scenarios.length && !Object.keys(byScenario).length && !session.evidence.studies.length && !session.evidence.field_sessions.length) return null;
  return (
    <div className={s.evidence}>
      <span className={s.meta}>本会话的记录</span>
      {scenarios.map((id) => (
        <Button key={id} size="sm" icon={MapIcon} title={id} onClick={() => openScenario(id)}>地图 {id.length > 26 ? `${id.slice(0, 26)}…` : id}</Button>
      ))}
      {Object.entries(byScenario).map(([sc, runs]) => (
        <Button key={sc} size="sm" onClick={() => openRuns(sc, runs.slice(-12))}>对比 {runs.length} 个运行</Button>
      ))}
      {session.evidence.studies.map((id) => (
        <Button key={id} size="sm" icon={FlaskConical} onClick={() => openStudy(id)}>课题 {id}</Button>
      ))}
      {session.evidence.field_sessions.map((id) => (
        <Button key={id} size="sm" icon={Bot} onClick={() => useStudio.getState().setWorkspace('sim')}>仿真 {id}</Button>
      ))}
    </div>
  );
}

function Step({ step, session, runMap, onDecide }: { step: AgentStep; session: AgentSession; runMap: Record<string, string>; onDecide: (approve: boolean) => void }) {
  switch (step.kind) {
    case 'user':
      return <div className={s.user}><p className={s.text}>{step.text}</p></div>;
    case 'assistant':
      return step.text || step.reasoning ? (
        <div className={s.assistant}>
          {step.text && <RichText text={step.text} runMap={runMap} />}
          {step.reasoning && <details className={s.details}><summary>模型的思考过程</summary><pre>{step.reasoning}</pre></details>}
        </div>
      ) : null;
    case 'tool':
      return <ToolCard step={step} />;
    case 'approval':
      return <ApprovalCard step={step} session={session} onDecide={onDecide} />;
    case 'final': {
      const verdict = groundingVerdict(step.grounding);
      return (
        <div className={s.final}>
          <RichText text={step.text} runMap={runMap} />
          <div className={s.row}>
            <Badge tone={verdict.tone} title={verdict.detail}>{verdict.label}</Badge>
            <span className={s.meta}>数字核对是启发式的；结论以运行记录和独立检查为准。</span>
          </div>
          {verdict.tone === 'orange' && <p className={s.hint}>{verdict.detail}</p>}
        </div>
      );
    }
    case 'error':
      return <Notice tone="error">{step.message}</Notice>;
    case 'notice':
      return <Notice tone="info">{step.message}</Notice>;
  }
}

export function AgentPanel() {
  const status = useLabResource('agent-status', () => studio.agentStatus());
  const sessions = useLabResource('agent-sessions', () => studio.agentSessions(), { pollMs: 15_000 });
  const sessionId = useAgent((x) => x.sessionId);
  const select = useAgent((x) => x.select);
  const pendingAsk = useAgent((x) => x.pendingAsk);
  const [loadedSession, setSession] = useState<AgentSession | null>(null);
  const session = loadedSession?.session_id === sessionId ? loadedSession : null;
  const loadSequence = useRef(0);
  const sendInFlight = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const timeline = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async (id: string) => {
    const sequence = ++loadSequence.current;
    try {
      const next = await studio.agentSession(id);
      if (useAgent.getState().sessionId === id && sequence === loadSequence.current) {
        setSession(next);
        setError(null);
      }
    } catch (e) {
      if (useAgent.getState().sessionId !== id || sequence !== loadSequence.current) return;
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    setError(null);
    if (sessionId) void load(sessionId);
    else setSession(null);
  }, [sessionId, load]);

  useLabEvent('agent', (data) => {
    const id = (data as { session_id?: string } | null)?.session_id;
    if (id && id === useAgent.getState().sessionId) void load(id);
  });

  // A long turn can outlive a dropped event stream; a slow poll keeps the timeline honest.
  const busy = isBusy(session?.state);
  useEffect(() => {
    if (!sessionId || !busy) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await load(sessionId);
      if (active) timer = setTimeout(poll, 2000);
    };
    timer = setTimeout(poll, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [sessionId, busy, load]);

  // Follow new steps while the reader is at the bottom; scrolling up to read stops following.
  const pinned = useRef(true);
  const follow = useCallback(() => {
    const t = timeline.current;
    if (t && pinned.current) t.scrollTop = t.scrollHeight;
  }, []);
  useEffect(() => {
    pinned.current = true;
  }, [sessionId]);
  useEffect(() => follow(), [session?.steps.length, session?.state, follow]);
  useEffect(() => {
    // A panel restored in a background tab mounts and loads while hidden; follow once it is shown.
    const t = timeline.current;
    if (!t || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(follow);
    observer.observe(t);
    return () => observer.disconnect();
  }, [follow]);

  const send = useCallback(async (message: string, fresh = false) => {
    const msg = message.trim();
    if (!msg || sendInFlight.current) return;
    sendInFlight.current = true;
    const current = useAgent.getState().sessionId;
    setSending(true);
    setError(null);
    try {
      const next = !fresh && current ? await studio.agentSend(current, msg) : await studio.agentStart(msg);
      sessions.reload();
      if (useAgent.getState().sessionId !== current) return;
      ++loadSequence.current;
      useAgent.getState().select(next.session_id);
      pinned.current = true;
      setSession(next);
      setText('');
    } catch (e) {
      if (useAgent.getState().sessionId === current) setError((e as Error).message);
    } finally {
      sendInFlight.current = false;
      setSending(false);
    }
    // sessions.reload is a new function every render; the request itself does not depend on it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // "交给智能体" in the compose bar hands over its text once.
  useEffect(() => {
    if (pendingAsk === null || sending || sendInFlight.current) return;
    const handed = useAgent.getState().takeAsk();
    if (handed) void send(handed, true);
  }, [pendingAsk, send, sending]);

  const decide = async (approve: boolean) => {
    if (!session?.pending) return;
    try {
      const next = await studio.agentDecide(session.session_id, session.pending.call_id, approve);
      if (useAgent.getState().sessionId === session.session_id) { ++loadSequence.current; setSession(next); }
    } catch (e) {
      if (useAgent.getState().sessionId === session.session_id) setError((e as Error).message);
    }
  };

  const cancel = async () => {
    if (!session) return;
    try {
      const next = await studio.agentCancel(session.session_id);
      if (useAgent.getState().sessionId === session.session_id) { ++loadSequence.current; setSession(next); }
    } catch (e) {
      if (useAgent.getState().sessionId === session.session_id) setError((e as Error).message);
    }
  };

  const configured = status.data?.configured ?? false;
  const model = status.data?.model;
  const runMap = session ? runScenarios(session.steps) : {};
  const list = sessions.data?.sessions ?? [];

  return (
    <div className={s.agent}>
      <header className={s.head}>
        <Bot size={15} />
        <strong>科研智能体</strong>
        {model ? (
          <span className={s.model} title={`系统提示 ${status.data?.prompt.id} · ${status.data?.prompt.sha256.slice(0, 12)}`}>{model.model} · {model.endpoint ?? '本机'}</span>
        ) : (
          status.data && <Badge tone="orange">未配置模型</Badge>
        )}
        <span className={s.spacer} />
        <select className={s.picker} aria-label="选择会话" value={sessionId ?? ''} onChange={(e) => select(e.target.value || null)}>
          <option value="">新会话</option>
          {list.map((x) => (
            <option key={x.session_id} value={x.session_id}>{`${x.title || x.session_id} · ${STATE_LABEL[x.state]} · ${fmt.time(x.updated_utc)}`}</option>
          ))}
        </select>
        <Button size="sm" icon={Plus} title="开始新会话" onClick={() => { select(null); setSession(null); input.current?.focus(); }} />
      </header>

      {status.error && <Notice tone="error">{status.error.message}</Notice>}
      {sessions.error && <Notice tone="error">会话列表读取失败：{sessions.error.message}</Notice>}
      {status.data && !configured && (
        <div className={s.pad}>
          <Notice tone="warn">
            {status.data.reason}
            <code className={s.setup}>export URP_LLM_API_KEY=… ; export URP_LLM_MODEL=glm-5.3 ; bash setup/run.sh lab</code>
          </Notice>
        </div>
      )}

      <div className={s.timeline} ref={timeline}
        onScroll={(e) => { const t = e.currentTarget; pinned.current = t.scrollHeight - t.scrollTop - t.clientHeight < 48; }}>
        {sessionId && !session && !error && <Spinner label="正在读取会话" />}
        {!sessionId && !session && (
          <EmptyState
            icon={Bot}
            title="让智能体完成一项研究任务"
            detail="它通过工作台工具生成任务地图、取得推荐、提交求解、读取独立检查结果并比较方案。答复里的数字来自工具结果；启动仿真前会请你批准。"
            action={
              <div className={s.examples}>
                {EXAMPLES.map((ex) => (
                  <button key={ex} type="button" className={s.example} onClick={() => { setText(ex); input.current?.focus(); }}>{ex}</button>
                ))}
              </div>
            }
          />
        )}
        {session?.steps.map((step) => <Step key={step.i} step={step} session={session} runMap={runMap} onDecide={(a) => void decide(a)} />)}
        {session?.state === 'running' && <div className={s.working}><Spinner label="智能体正在工作" /> 正在调用模型与工具…</div>}
        {session && session.state !== 'running' && <Evidence session={session} runMap={runMap} />}
      </div>

      <footer className={s.composer}>
        {error && <Notice tone="error">{error}{sessionId && <Button size="sm" onClick={() => void load(sessionId)}>重新读取会话</Button>}</Notice>}
        <textarea
          ref={input}
          id="agent-input"
          aria-label="给智能体的消息"
          className={s.input}
          rows={3}
          value={text}
          disabled={!configured || busy || (!!sessionId && !session)}
          placeholder={busy ? '这一轮还在进行，可以停止后再提问' : '描述任务或提出问题，例如：比较这块田上 PyVRP 与 OR-Tools 的方案'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return;
            // ⌘/Ctrl+Enter is also the workbench's global "solve"; sending a message must not start one.
            e.preventDefault();
            e.stopPropagation();
            if (!sending) void send(text);
          }}
        />
        <div className={s.row}>
          {session && <span className={s.meta}>{STATE_LABEL[session.state]} · 模型 {session.usage.model_calls} 次 · 工具 {session.usage.tool_calls} 次 · {session.usage.total_tokens} tokens</span>}
          <span className={s.spacer} />
          {session?.state === 'running' && <Button size="sm" icon={Square} onClick={() => void cancel()}>停止</Button>}
          <Button size="sm" variant="primary" icon={Send} loading={sending} disabled={!configured || busy || (!!sessionId && !session) || !text.trim()} title="发送（⌘/Ctrl + Enter）" onClick={() => void send(text)}>发送</Button>
        </div>
      </footer>
    </div>
  );
}
