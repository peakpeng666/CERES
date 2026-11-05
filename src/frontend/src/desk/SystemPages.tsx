import { useEffect, useState } from 'react';
import { agentApi, type Capability, type Capabilities, type EvalRun, type HarnessInfo, type ProvidersInfo, type SessionSummary } from '../agent/api';
import { useAgent } from '../agent/store';
import { Spinner } from '../lab/ui';
import { n0, n1, n3 } from './format';
import { Facts, Kvs, Row, Rows } from './ui';
import s from './desk.module.css';

const EFFECT: Record<string, string> = { session: '会话内', compute: '计算', write: '写任务地图', physical: '物理仿真' };

function useLoad<T>(fn: () => Promise<T>): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    fn().then((x) => { if (live) { setData(x); setError(null); } }).catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n]);
  return { data, error, reload: () => setN((x) => x + 1) };
}

function CapabilityRows({ items }: { items: Capability[] }) {
  return <Rows>{items.map((x) => (
    <Row key={x.id} wrap title={x.name} sub={x.status === 'ready' ? x.summary ?? x.detail : x.reason ?? '不可用'}
      value={x.status === 'ready' ? (x.approval ? '需批准' : '可用') : '不可用'} valueSub={x.version ?? undefined} onClick={x.href ? () => { location.hash = x.href!; } : undefined} />
  ))}</Rows>;
}

/** Everything the harness can put to work. An entry states its own status; nothing unconnected reads as available. */
export function CapabilitiesPage() {
  const { data, error } = useLoad<Capabilities>(() => agentApi.capabilities());
  if (error) return <p className={s.docBad}>{error}</p>;
  if (!data) return <Spinner />;
  const ready = data.algorithms.filter((a) => a.status === 'ready').length;
  return (
    <div className={s.pageIn}>
      <p className={s.txt} style={{ paddingTop: 8 }}>智能体和你用的是同一份清单。算法 {ready}/{data.algorithms.length} 个可用，详情在<a className={s.go} href="#/algorithms">算法库</a>。</p>
      <h2>仿真器</h2><CapabilityRows items={data.simulators} />
      <h2>引擎迁移</h2><CapabilityRows items={data.transfers} />
      <h2>学习模型</h2><CapabilityRows items={data.learners} />
    </div>
  );
}

function usageTotals(list: SessionSummary[]) {
  const t = { input: 0, output: 0, calls: 0, latency: 0, sessions: list.length, byModel: new Map<string, { calls: number; input: number; output: number }>() };
  for (const x of list) {
    const u = x.usage;
    if (!u) continue;
    t.input += u.input_tokens; t.output += u.output_tokens; t.calls += u.calls; t.latency += u.latency_s;
    const k = x.provider ?? '规则流程';
    const m = t.byModel.get(k) ?? { calls: 0, input: 0, output: 0 };
    m.calls += u.calls; m.input += u.input_tokens; m.output += u.output_tokens;
    t.byModel.set(k, m);
  }
  return t;
}

export function ModelsPage() {
  const info = useLoad<ProvidersInfo>(() => agentApi.providers());
  const sessions = useAgent((x) => x.sessions);
  const reloadBar = useAgent((x) => x.loadProviders);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const t = usageTotals(sessions);
  const local = info.data?.providers.find((p) => p.local);
  const act = async (action: 'start' | 'stop') => {
    setBusy(true); setNote(null);
    try {
      const r = await agentApi.localModel(action);
      setNote(r.detail);
      info.reload(); void reloadBar();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={s.page} style={{ height: '100%' }}>
      <div className={s.pageIn}>
        <h2>累计用量 · 最近 {t.sessions} 个任务</h2>
        <div style={{ padding: '6px 0 10px' }}><Facts items={[['模型调用', n0(t.calls), '次'], ['输入', n0(t.input), 'tokens'], ['输出', n0(t.output), 'tokens'], ['模型耗时', n1(t.latency), 's']]} /></div>
        {t.byModel.size > 0 && <Rows>{[...t.byModel.entries()].map(([k, v]) => <Row key={k} dense title={k} sub={`${n0(v.calls)} 次调用`} value={`${n0(v.input)} → ${n0(v.output)}`} />)}</Rows>}
        <h2>模型</h2>
        {info.error && <p className={s.docBad}>{info.error}</p>}
        {!info.data && !info.error && <Spinner />}
        <Rows>{info.data?.providers.map((p) => <Row key={p.id} wrap title={p.label} sub={[p.model, p.terms_note].filter(Boolean).join(' · ')} value={p.id === info.data!.default ? '默认' : p.enabled ? '可选' : '已停用'} valueSub={p.status.detail} />)}</Rows>
        {local && (
          <div className={s.nextRow} style={{ paddingTop: 10 }}>
            <button type="button" className={s.btn} disabled={busy} onClick={() => void act('start')}>启动本地模型服务</button>
            <button type="button" className={s.btn} disabled={busy} onClick={() => void act('stop')}>停止</button>
            {note && <span className={s.state}>{note}</span>}
          </div>
        )}
        {info.data && <>
          <h2>配置</h2>
          <div style={{ paddingTop: 6 }}><Kvs items={[['提示版本', info.data.prompt_version], ['提供方配置', <span key="c" className={s.mono}>{info.data.config}</span>], ['密钥', '只放在同目录权限 600 的 credentials.json 或环境变量中']]} /></div>
        </>}
      </div>
    </div>
  );
}

const PHASE: Record<string, string> = { understand: '理解', execute: '执行', report: '报告' };

export function ToolsPage() {
  const h = useLoad<HarnessInfo>(() => agentApi.harness());
  if (h.error) return <p className={s.emptyLine}>{h.error}</p>;
  if (!h.data) return <div className={s.pageIn}><Spinner /></div>;
  const d = h.data;
  return (
    <div className={s.page} style={{ height: '100%' }}>
      <div className={s.pageIn}>
        <h2>运行器</h2>
        <div style={{ padding: '6px 0 10px' }}><Facts items={[['每轮模型调用上限', d.limits.model_calls_per_turn, '次'], ['每轮工具调用上限', d.limits.tool_calls_per_turn, '次'], ['工具', d.tools.length, '个']]} /></div>
        <div style={{ paddingBottom: 6 }}><Kvs items={[['提示版本', d.prompt_version], ['运行器版本', d.runner_version]]} /></div>
        <h2>工具与闸门</h2>
        <Rows>{d.tools.map((t) => (
          <Row key={t.name} wrap title={<>{t.label} <span className={s.mono} style={{ color: 'var(--mac-label-3)' }}>{t.name}</span></>} sub={t.description}
            value={EFFECT[t.effect] ?? t.effect}
            valueSub={[t.phases.map((p) => PHASE[p] ?? p).join('、'), t.needs_approval ? '需批准' : null, t.needs_confirmed ? '需已确认合同' : null].filter(Boolean).join(' · ')} />
        ))}</Rows>
      </div>
    </div>
  );
}

export function EvalsPage() {
  const e = useLoad<{ runs: EvalRun[] }>(() => agentApi.evals());
  const [open, setOpen] = useState<string | null>(null);
  if (e.error) return <p className={s.emptyLine}>{e.error}</p>;
  if (!e.data) return <div className={s.pageIn}><Spinner /></div>;
  const exec = e.data.runs.filter((r) => r.kind === 'execution');
  const nlu = e.data.runs.filter((r) => r.kind === 'understanding');
  return (
    <div className={s.page} style={{ height: '100%' }}>
      <div className={s.pageIn}>
        <h2>执行流程评测</h2>
        <Rows>{exec.map((r) => (
          <div key={r.id}>
            <Row title={r.id} sub={[r.mode, r.provider, r.prompt_version, r.runner_version].filter(Boolean).join(' · ')} value={`${r.passed ?? '—'}/${r.total ?? '—'}`} valueSub="情景通过" onClick={() => setOpen(open === r.id ? null : r.id)} />
            {open === r.id && <div style={{ padding: '0 10px 10px' }}><Rows>{(r.cases ?? []).map((c) => <Row key={c.id} dense title={c.id} sub={c.note ?? undefined} value={c.passed === null ? '未判定' : c.passed ? '通过' : '未通过'} valueSub={c.state ?? undefined} />)}</Rows></div>}
          </div>
        ))}</Rows>
        <h2>任务理解评测</h2>
        <Rows>{nlu.map((r) => <Row key={r.id} title={r.id} sub={[r.mode, r.provider, r.split].filter(Boolean).join(' · ')} value={`F1 ${n3(r.metrics?.f1)}`} valueSub={r.metrics?.exact_match != null ? `完全匹配 ${n3(r.metrics.exact_match)}` : undefined} />)}</Rows>
        <p className={s.emptyLine} style={{ padding: '12px 0 0' }}>记录位于 experiments/agent-evals/。这些是规划层流程评测，不代表真实物理执行通过。</p>
      </div>
    </div>
  );
}
