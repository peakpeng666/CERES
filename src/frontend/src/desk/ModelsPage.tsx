import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { agentApi, type ConnectedService, type ModelInfo, type ServiceRow, type SessionSummary } from '../agent/api';
import { findModel, useAgent } from '../agent/store';
import { Spinner } from '../lab/ui';
import { dateCN, n0, n1, n2 } from './format';
import { Icon } from './Icon';
import { Facts, Fold, Kvs, Row, Rows } from './ui';
import s from './desk.module.css';

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

const context = (v?: number | null) => (v ? (v >= 1_000_000 ? `${n1(v / 1_000_000)}M` : `${n0(v / 1000)}K`) : null);
const price = (m: ModelInfo) => (m.input_price != null ? `输入 $${n2(m.input_price)} / 输出 $${n2(m.output_price)} 每百万 tokens` : null);
const describe = (m: ModelInfo) => [context(m.context) && `上下文 ${context(m.context)}`, m.reasoning && '推理', m.beta && '测试版', price(m)].filter(Boolean).join(' · ');

function stateOf(x: ServiceRow) {
  if (x.connected === 'env') return `环境变量 ${x.env.find(Boolean) ?? ''}`.trim();
  if (x.connected === 'key') return x.verified === true ? '已验证' : '未验证';
  return null;
}

/** One service: paste a key, then pick a model. The key leaves this form once and is never read back. */
function ServiceLayer({ id, onBack }: { id: string; onBack: () => void }) {
  const info = useAgent((x) => x.providers);
  const load = useAgent((x) => x.loadProviders);
  const select = useAgent((x) => x.selectModel);
  const [row, setRow] = useState<ConnectedService | null>(null);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const [q, setQ] = useState('');
  const refresh = async () => { setRow(await agentApi.providerModels(id)); await load(); };
  useEffect(() => { void agentApi.providerModels(id).then(setRow).catch((e) => setError((e as Error).message)); }, [id]);
  if (!row) return error ? <p className={s.docBad}>{error}</p> : <Spinner />;
  const connect = async (e: FormEvent) => {
    e.preventDefault();
    const value = key;
    setKey(''); setBusy(true); setError(null); setNote(null);
    try {
      const r = await agentApi.connectProvider(id, value);
      setNote(r.detail); setReplacing(false);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const disconnect = async () => {
    setBusy(true); setError(null); setNote(null);
    try {
      await agentApi.disconnectProvider(id);
      if (row.custom) { await load(); onBack(); return; }
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const models = row.models.filter((m) => !q.trim() || `${m.name} ${m.id}`.toLowerCase().includes(q.trim().toLowerCase()));
  const help = row.key_page ?? row.doc;
  const canType = !row.connected || replacing;
  return (
    <>
      <div className={s.backRow}><button type="button" onClick={onBack}><Icon name="left" size={12} />模型</button><i>/</i><b>{row.name}</b></div>
      {row.terms_note && <p className={s.docWarn}>{row.terms_note}</p>}
      {canType && (
        <form className={s.keyForm} onSubmit={(e) => void connect(e)} autoComplete="off">
          <input type="password" value={key} placeholder="粘贴 API Key" aria-label={`${row.name} 的 API Key`} autoComplete="new-password" spellCheck={false} autoFocus onChange={(e) => setKey(e.target.value)} />
          <button type="submit" className={`${s.btn} ${s.btnPrimary}`} disabled={busy || !key.trim()}>{busy ? '验证中' : '连接'}</button>
        </form>
      )}
      {error && <p className={s.docBad}>{error}</p>}
      {note && <p className={s.okLine}>{note}</p>}
      {canType && <p className={s.noteLine}>{help && <><a href={help} target="_blank" rel="noreferrer">获取 API Key</a>。</>}Key 只保存在这台电脑的用户配置里，界面、日志和导出都读不到它。</p>}
      {row.connected && !replacing && (
        <div className={s.nextRow} style={{ paddingTop: 8 }}>
          <span className={s.state}>{stateOf(row)}{row.verified !== true && row.detail ? ` · ${row.detail}` : ''}</span>
          <span className={s.grow} />
          {row.connected === 'key' && <button type="button" className={s.btn} disabled={busy} onClick={() => setReplacing(true)}>更换 Key</button>}
          {row.connected === 'key' && <button type="button" className={s.btn} disabled={busy} onClick={() => void disconnect()}>断开</button>}
        </div>
      )}
      <h2>模型 · {row.models.length}</h2>
      {row.models.length > 8 && <label className={s.search} style={{ padding: '2px 0' }}><input type="search" value={q} placeholder="搜索模型" aria-label="搜索模型" onChange={(e) => setQ(e.target.value)} /></label>}
      <Rows>
        {models.slice(0, 60).map((m) => (
          <Row key={m.ref} wrap title={m.name} sub={describe(m) || m.id} value={m.ref === info?.selected ? '当前' : undefined}
            onClick={row.connected ? () => void select(m.ref).catch((e) => setError((e as Error).message)) : undefined} />
        ))}
      </Rows>
      {models.length > 60 && <p className={s.noteLine}>还有 {models.length - 60} 个，用搜索缩小范围。</p>}
    </>
  );
}

function CustomLayer({ onBack, onDone }: { onBack: () => void; onDone: (id: string) => void }) {
  const load = useAgent((x) => x.loadProviders);
  const [form, setForm] = useState({ name: '', base_url: '', api_key: '', models: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await agentApi.addCustomProvider(form);
      setForm({ name: '', base_url: '', api_key: '', models: '' });
      await load();
      onDone(r.service);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className={s.backRow}><button type="button" onClick={onBack}><Icon name="left" size={12} />模型</button><i>/</i><b>自定义接口</b></div>
      <p className={s.noteLine}>任何兼容 OpenAI 接口的服务都可以接入，比如公司内部网关。</p>
      <form className={s.fieldStack} onSubmit={(e) => void submit(e)} autoComplete="off">
        <label>名称<input className={s.fieldInput} value={form.name} onChange={set('name')} /></label>
        <label>接口地址<input className={s.fieldInput} value={form.base_url} placeholder="https://example.com/v1" spellCheck={false} onChange={set('base_url')} /></label>
        <label>API Key<input className={s.fieldInput} type="password" value={form.api_key} autoComplete="new-password" spellCheck={false} onChange={set('api_key')} /></label>
        <label>模型名（可选，留空时读取接口的模型列表）<textarea className={s.fieldInput} value={form.models} spellCheck={false} onChange={set('models')} /></label>
        <div><button type="submit" className={`${s.btn} ${s.btnPrimary}`} disabled={busy || !form.name.trim() || !form.base_url.trim() || !form.api_key.trim()}>{busy ? '验证中' : '添加'}</button></div>
      </form>
      {error && <p className={s.docBad}>{error}</p>}
    </>
  );
}

function ServiceRows({ rows, onOpen }: { rows: ServiceRow[]; onOpen: (id: string) => void }) {
  return <Rows>{rows.map((x) => <Row key={x.id} dense title={x.name} sub={`${x.model_count} 个模型`} value={stateOf(x) ?? undefined} onClick={() => onOpen(x.id)} />)}</Rows>;
}

/** Model settings. A service, then its key, then a model: one layer per click, and nothing else to configure. */
export function ModelsPage() {
  const info = useAgent((x) => x.providers);
  const error = useAgent((x) => x.providersError);
  const load = useAgent((x) => x.loadProviders);
  const sessions = useAgent((x) => x.sessions);
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  useEffect(() => { void load(); }, [load]);
  const t = usageTotals(sessions);
  const current = findModel(info, info?.selected);
  const rows = useMemo(() => info?.services ?? [], [info]);
  const featured = rows.filter((x) => info?.featured.includes(x.id) && !x.connected);
  const others = rows.filter((x) => !info?.featured.includes(x.id) && !x.connected);
  const query = q.trim().toLowerCase();
  const hits = query ? rows.filter((x) => `${x.name} ${x.id}`.toLowerCase().includes(query)) : [];
  const refresh = async () => {
    setRefreshing(true); setRefreshNote(null);
    try {
      const r = await agentApi.refreshCatalog();
      setRefreshNote(`已更新，共 ${r.services} 家服务商`);
      await load();
    } catch (e) {
      setRefreshNote((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  };
  if (!info) return error ? <p className={s.emptyLine}>{error}</p> : <div className={s.pageIn}><Spinner /></div>;
  return (
    <div className={s.page} style={{ height: '100%' }}>
      <div className={s.pageIn}>
        {open === 'custom' ? <CustomLayer onBack={() => setOpen(null)} onDone={(id) => setOpen(id)} />
          : open ? <ServiceLayer key={open} id={open} onBack={() => setOpen(null)} />
            : <>
              <h2>当前模型</h2>
              {current
                ? <Rows><Row title={current.name} sub={current.service} onClick={() => setOpen(current.ref.split('/')[0])} /></Rows>
                : <p className={s.noteLine}>选一家服务商，粘贴 API Key，新任务就能用自然语言理解。没有连接时，任务走规则流程。</p>}
              {info.connected.length > 0 && <><h2>已连接</h2><ServiceRows rows={info.connected} onOpen={setOpen} /></>}
              <h2>{info.connected.length ? '连接其他服务商' : '连接服务商'}</h2>
              <label className={s.search} style={{ padding: '2px 0' }}><input type="search" value={q} placeholder={`搜索 ${info.catalog.services} 家服务商`} aria-label="搜索服务商" onChange={(e) => setQ(e.target.value)} /></label>
              {query ? <>
                <ServiceRows rows={hits} onOpen={setOpen} />
                {!hits.length && <p className={s.emptyLine}>没有找到。可以用下面的自定义接口接入。</p>}
              </> : <>
                <ServiceRows rows={featured} onOpen={setOpen} />
                <div className={s.folds}><Fold summary={<span>全部服务商 · {others.length}</span>}>{() => <ServiceRows rows={others} onOpen={setOpen} />}</Fold></div>
              </>}
              <Rows><Row dense title="自定义接口" sub="任何兼容 OpenAI 接口的服务" onClick={() => setOpen('custom')} /></Rows>
              <h2>累计用量 · 最近 {t.sessions} 个任务</h2>
              <div style={{ padding: '6px 0 10px' }}><Facts items={[['模型调用', n0(t.calls), '次'], ['输入', n0(t.input), 'tokens'], ['输出', n0(t.output), 'tokens'], ['模型耗时', n1(t.latency), 's']]} /></div>
              {t.byModel.size > 0 && <Rows>{[...t.byModel.entries()].map(([k, v]) => <Row key={k} dense title={k} sub={`${n0(v.calls)} 次调用`} value={`${n0(v.input)} → ${n0(v.output)}`} />)}</Rows>}
              <h2>模型目录</h2>
              <div style={{ paddingTop: 6 }}><Kvs items={[['来源', 'models.dev'], !!info.catalog.updated_utc && ['更新于', dateCN(info.catalog.updated_utc)], ['提示版本', info.prompt_version]]} /></div>
              <div className={s.nextRow} style={{ paddingTop: 8 }}>
                <button type="button" className={s.btn} disabled={refreshing} onClick={() => void refresh()}>{refreshing ? '更新中' : '更新目录'}</button>
                {refreshNote && <span className={s.state}>{refreshNote}</span>}
              </div>
            </>}
      </div>
    </div>
  );
}
