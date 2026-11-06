import { cite } from '../cite';
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { importGeojsonOnce, importScenarioOnce, UncertainImportError, type GeojsonResolution } from '../scenarioWrites';
import { BookOpen, Code2, Copy, Download, FileUp, Sprout } from 'lucide-react';
import { lab, useLabResource } from '../../lab/api';
import { Badge, Button, EmptyState, Input, Notice, Segmented, Select, Spinner } from '../../lab/ui';
import type { ScenarioSummary } from '../../lab/types';
import { studio, urls } from '../api';
import { useStudio } from '../store';
import type { CvrplibInstance, FieldEntry, Reference } from '../types';
import { LibraryBadge } from './PlanPanels';
import s from './panels.module.css';
import { AgriCatalog } from '../agri/AgriCatalog';
import { FOCUS_EVENT, peekCatalogFocus } from '../agri/drafts';

const CodeMirror = lazy(() => import('@uiw/react-codemirror'));

const CLASS_LABEL: Record<string, string> = {
  fleet_routing: '多车任务', turn_routing: '转弯约束路网', euclidean_cvrp: '车辆路径 CVRP', coverage: '覆盖作业', point_to_point: '点到点规划',
};
const FRAME_LABEL = (sc: ScenarioSummary) =>
  sc.frame.kind === 'map' ? `仿真地图 · ${(sc.frame as { world?: string }).world}` : sc.frame.kind === 'field' ? '实地坐标 · 米' : '基准算例';

/* ------------------------------------------------------------ scenarios -- */
export function ScenarioNavigator() {
  const [catalog, setCatalog] = useState(() => Boolean(peekCatalogFocus()));
  useEffect(() => { const on = () => setCatalog(true); window.addEventListener(FOCUS_EVENT, on); return () => window.removeEventListener(FOCUS_EVENT, on); }, []);
  const res = useLabResource('studio-scenarios', () => lab.scenarios(), { pollMs: 8000 });
  const current = useStudio((st) => st.scenarioId);
  const notify = useStudio((st) => st.notify);
  const [q, setQ] = useState('');
  const list = (res.data?.scenarios ?? []).filter((sc) => !q || `${sc.title} ${sc.summary} ${sc.id}`.toLowerCase().includes(q.toLowerCase()));
  const groups: [string, ScenarioSummary[]][] = [
    ['我的任务地图', list.filter((x) => (x as { origin?: string }).origin === 'user' && x.problem_class === 'fleet_routing')],
    ['我的其他场景', list.filter((x) => (x as { origin?: string }).origin === 'user' && x.problem_class !== 'fleet_routing')],
    ['仿真地图', list.filter((x) => x.frame.kind === 'map' && (x as { origin?: string }).origin !== 'user')],
    ['资源库（最近打开）', list.filter((x) => ['cvrplib', 'fields2benchmark'].includes((x as { origin?: string }).origin ?? ''))],
    ['基准算例', list.filter((x) => x.frame.kind === 'benchmark' && (x as { origin?: string }).origin === 'builtin')],
  ];
  const [uncertain, setUncertain] = useState<{ error: UncertainImportError; geojson: unknown; title: string } | null>(null);
  async function finishGeojson(geojson: unknown, title: string, resolution?: GeojsonResolution) {
    try {
      const sc = await importGeojsonOnce(geojson, title, resolution);
      setUncertain(null);
      location.hash = `#/plan/${encodeURIComponent(sc.id)}`;
      res.reload();
      notify('ok', resolution && 'adopt' in resolution ? `已打开已保存的 ${sc.title}` : `已导入 ${sc.title}`);
    } catch (e) {
      if (e instanceof UncertainImportError) setUncertain({ error: e, geojson, title });
      else notify('error', `导入失败：${(e as Error).message}`);
    }
  }
  async function importFile(file: File) {
    try {
      const obj = JSON.parse(await file.text());
      if (obj.type === 'FeatureCollection' || obj.type === 'Feature') return await finishGeojson(obj, file.name.replace(/\.(geo)?json$/i, ''));
      const sc = await importScenarioOnce(obj);
      location.hash = `#/plan/${encodeURIComponent(sc.id)}`;
      res.reload();
      notify('ok', `已导入 ${sc.title}`);
    } catch (e) {
      notify('error', `导入失败：${(e as Error).message}`);
    }
  }
  return (
    <div className={s.scroll}>
      <div className={s.pad}><Button size="sm" onClick={() => setCatalog(!catalog)} aria-expanded={catalog}>{catalog ? '返回已有场景' : '打开农业任务目录'}</Button></div>
      <div hidden={!catalog}><AgriCatalog visible={catalog} /></div>
      <div hidden={catalog}>
      <div className={s.search}>
        <Input placeholder="搜索场景" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜索场景" />
      </div>
      <div className={s.pad} style={{ paddingTop: 4, paddingBottom: 0 }}>
        <label className={s.link} style={{ cursor: 'pointer' }}>
          <FileUp size={13} /> 导入 GeoJSON / 场景 JSON
          <input type="file" accept=".json,.geojson,application/json" hidden onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
        </label>
      </div>
      {uncertain && (
        <div className={s.pad} aria-label="导入结果待确认">
          <Notice tone="warn">{uncertain.error.message}</Notice>
          {uncertain.error.candidates.map((c) => (
            <Button key={c.id} size="sm" onClick={() => void finishGeojson(uncertain.geojson, uncertain.title, { adopt: c.id })}>这是刚才的导入：{c.title}（{c.id}）</Button>
          ))}
          <Button size="sm" variant="plain" onClick={() => void finishGeojson(uncertain.geojson, uncertain.title, { reimport: true })}>确认未保存，重新导入</Button>
        </div>
      )}
      {res.loading && !res.data && <div className={s.pad}><Spinner /></div>}
      {groups.map(([title, items]) => items.length > 0 && (
        <div key={title}>
          <div className={s.listHead}>{title} · {items.length}</div>
          <ul className={s.list}>
            {items.slice(0, title === '基准算例' ? 60 : 200).map((sc) => (
              <li key={sc.id}>
                <button type="button" className={s.item} aria-current={current === sc.id || undefined}
                  onClick={() => { location.hash = `#/plan/${encodeURIComponent(sc.id)}`; }}>
                  <span className={s.itemTitle}>{sc.title}</span>
                  <span className={s.itemSub}>{CLASS_LABEL[sc.problem_class] ?? sc.problem_class} · {FRAME_LABEL(sc)}{sc.task_count ? ` · ${sc.task_count} 个任务` : ''}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- algorithms -- */
export function AlgorithmsPanel() {
  const algorithms = useStudio((st) => st.algorithms);
  const openCode = useStudio((st) => st.openCode);
  const [cls, setCls] = useState('fleet_routing');
  const refs = useLabResource('refs-all', () => studio.references());
  const byKey = useMemo(() => Object.fromEntries((refs.data?.references ?? []).map((r) => [r.key, r])), [refs.data]);
  const list = algorithms.filter((a) => a.problem_classes.includes(cls as never));
  return (
    <div className={s.scroll}>
      <div className={s.search}>
        <Select value={cls} onChange={(e) => setCls(e.target.value)} aria-label="问题类型">
          {Object.entries(CLASS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>
      {list.map((a) => (
        <div key={a.id} className={s.algCard}>
          <h5>{a.name} <LibraryBadge library={a.library} />{!a.available && <Badge tone="red">不可用</Badge>}</h5>
          <p>{a.summary ?? a.description}</p>
          <p className={s.meta} style={{ overflowWrap: 'anywhere' }}>
            实现：{a.origin}
            {a.version && !a.origin.includes(a.version) ? ` · ${a.origin.startsWith('src/') ? '源码 SHA256' : '版本'} ${a.version}` : ''}
          </p>
          {!a.available && <p className={s.meta}>不可用原因：{a.unavailable_reason ?? '后端未提供原因'}</p>}
          {a.randomness && <p className={s.meta}>{a.randomness.note}</p>}
          <p className={s.meta}>
            {{ exact: '精确算法', relaxation: '松弛下界', metaheuristic: '元启发式', heuristic: '构造启发式', baseline: '基线', coverage: '覆盖规划', graph_search: '图搜索', sampling: '采样规划' }[a.family] ?? a.family}
            {' · '}{{ optimal: '保证最优', optimal_for_relaxation: '松弛问题最优', none: '无最优性保证', resolution_optimal: '分辨率最优', asymptotically_optimal: '渐近最优', probabilistically_complete: '概率完备' }[a.guarantee] ?? a.guarantee}
            {a.limits?.max_tasks ? ` · ≤ ${a.limits.max_tasks} 个任务` : ''}
          </p>
          {(a.refs ?? []).length > 0 && (
            <p className={s.meta}>
              {(a.refs ?? []).map((k) => byKey[k]).filter(Boolean).map((r: Reference) => (
                <a key={r.key} className={s.link} href={r.doi ? `https://doi.org/${r.doi}` : r.url ?? '#'} target="_blank" rel="noreferrer" style={{ marginRight: 10 }}>
                  {cite(r)}
                </a>
              ))}
            </p>
          )}
          {a.origin.startsWith('src/') && (
            <Button size="sm" variant="plain" icon={Code2} onClick={() => openCode(a.origin.split(' ')[0])}>查看源码</Button>
          )}
        </div>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------- cvrplib -- */
export function CvrplibPanel() {
  const sets = useLabResource('cvrplib-sets', () => studio.cvrplibSets());
  const [set, setSet] = useState('X');
  const [q, setQ] = useState('');
  const inst = useLabResource(`cvrplib-${set}-${q}`, () => studio.cvrplibInstances(set, q));
  const notify = useStudio((st) => st.notify);
  const [opening, setOpening] = useState<string | null>(null);
  const meta = sets.data?.sets.find((x) => x.id === set);
  async function open(i: CvrplibInstance) {
    setOpening(i.name);
    try {
      await lab.scenario(i.scenario_id);
      location.hash = `#/plan/${encodeURIComponent(i.scenario_id)}`;
    } catch (e) {
      notify('error', `${i.name}：${(e as Error).message}`);
    } finally {
      setOpening(null);
    }
  }
  return (
    <div className={s.split}>
      <div className={s.splitSide}>
        <div className={s.listHead}>CVRPLIB · {sets.data?.sets.reduce((a, x) => a + x.count, 0) ?? '…'} 个算例</div>
        <ul className={s.list}>
          {(sets.data?.sets ?? []).map((x) => (
            <li key={x.id}>
              <button type="button" className={s.item} aria-current={x.id === set || undefined} onClick={() => setSet(x.id)}>
                <span className={s.itemTitle}>{x.id} <span className={s.itemSub}>{x.count}</span>{!x.supported && <Badge>VRPTW</Badge>}</span>
                <span className={s.itemSub}>{x.cite} · {x.note}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className={s.splitMain}>
        <div className={s.search}>
          <Input placeholder={`在 ${set} 集中搜索`} value={q} onChange={(e) => setQ(e.target.value)} />
          {meta && <p className={s.meta}>{meta.citation} · {meta.note}{meta.link ? ' · ' : ''}{meta.link && <a className={s.link} href={meta.link} target="_blank" rel="noreferrer">原文</a>}</p>}
        </div>
        <table className={s.table}>
          <thead><tr><th>算例</th><th className={s.num}>客户</th><th className={s.num}>车辆下限</th><th className={s.num}>容量</th><th className={s.num}>最优已知解</th><th /></tr></thead>
          <tbody>
            {(inst.data?.instances ?? []).map((i) => (
              <tr key={i.name} onClick={() => i.supported && void open(i)} style={{ opacity: i.supported ? 1 : 0.5 }}>
                <td><strong>{i.name}</strong> {i.cached && <Badge tone="gray">已下载</Badge>}</td>
                <td className={s.num}>{i.n ?? '—'}</td>
                <td className={s.num}>{i.k ?? '—'}</td>
                <td className={s.num}>{i.q ?? '—'}</td>
                <td className={s.num}>{i.bks?.toLocaleString('zh-CN') ?? '—'}{i.optimal ? ' ✓' : ''}</td>
                <td>{opening === i.name ? <Spinner /> : i.supported ? <span className={s.link}>打开</span> : <span className={s.meta}>暂不支持</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={s.meta} style={{ padding: '0 12px 12px' }}>✓ 表示 BKS 已被证明最优。打开时从 CVRPLIB 下载原始 .vrp/.sol 并缓存在本机；求解结果自动给出与 BKS 的差距。</p>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- fields -- */
export function FieldsPanel() {
  const [country, setCountry] = useState('');
  const res = useLabResource(`fields-${country}`, () => studio.fields(country || undefined));
  const compose = useStudio((st) => st.composeFromText);
  const text = useStudio((st) => st.composeText);
  const setText = useStudio((st) => st.setComposeText);
  const notify = useStudio((st) => st.notify);
  const [busy, setBusy] = useState(false);
  const status = res.data?.status;
  if (status && !status.downloaded) {
    return (
      <EmptyState icon={Sprout} title="真实田块库尚未下载" detail="Fields2Benchmark：荷兰、爱沙尼亚、立陶宛的 350 块真实农田（CC-BY-4.0）。"
        action={<Button variant="primary" loading={busy} onClick={async () => { setBusy(true); try { await studio.fetchFields(false); res.reload(); } catch (e) { notify('error', (e as Error).message); } finally { setBusy(false); } }}>下载田块（约 0.2 MB）</Button>} />
    );
  }
  return (
    <div className={s.scroll}>
      <div className={s.search}>
        <Segmented value={country} onChange={setCountry} options={[{ value: '', label: '全部' }, { value: 'nl', label: '荷兰' }, { value: 'ee', label: '爱沙尼亚' }, { value: 'lt', label: '立陶宛' }]} />
        <p className={s.meta}>Fields2Benchmark（Mier 等, 2024）· {status?.fields ?? 0} 块田{status && !status.images ? ' · 卫星影像未下载（显示边界轮廓）' : ''}。点“生成任务”会用这块田的真实边界和上方的任务描述生成多车任务地图。</p>
        {!text && <Notice tone="info">可先在上方输入例如“3 台机器人采样 25 个点”，再选择田块。</Notice>}
      </div>
      <div className={s.gallery}>
        {(res.data?.fields ?? []).slice(0, 240).map((f: FieldEntry) => (
          <div key={f.id} className={s.tile}>
            <img src={f.image ? urls.fieldImage(f.id) : urls.fieldOutline(f.id)} alt={f.id} loading="lazy" />
            <strong>{f.country_name} {f.id.split('_').pop()}</strong>
            <span>{f.area_ha} ha · 凸性 {f.convexity.toFixed(2)}</span>
            <div className={s.row}>
              <Button size="sm" onClick={() => { location.hash = `#/plan/${encodeURIComponent(f.scenario_id)}`; }}>覆盖规划</Button>
              <Button size="sm" variant="primary" onClick={() => { if (!text) setText('3 台机器人采样 25 个点'); void compose({ field_id: f.id }); }}>生成任务</Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- references -- */
export function ReferencesPanel() {
  const [topic, setTopic] = useState('');
  const [q, setQ] = useState('');
  const res = useLabResource(`refs-${topic}-${q}`, () => studio.references(topic || undefined, q || undefined));
  const notify = useStudio((st) => st.notify);
  const topics = res.data?.topics ?? {};
  return (
    <div className={s.scroll}>
      <div className={s.search}>
        <Input placeholder="搜索标题、作者、期刊" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className={s.chips} style={{ marginTop: 8 }}>
          <button type="button" className={s.chip} aria-pressed={!topic} onClick={() => setTopic('')}>全部</button>
          {Object.entries(topics).map(([k, v]) => (
            <button key={k} type="button" className={s.chip} aria-pressed={topic === k} onClick={() => setTopic(k)}>{v}</button>
          ))}
        </div>
        <div className={s.row} style={{ marginTop: 8 }}>
          <a className={s.link} href={urls.referencesBib((res.data?.references ?? []).map((r) => r.key))} download><Download size={12} /> 导出当前列表 BibTeX</a>
        </div>
      </div>
      {(res.data?.references ?? []).map((r) => (
        <div key={r.key} className={s.ref}>
          <h5>{r.title}</h5>
          <p>{r.authors.slice(0, 6).join('; ')}{r.authors.length > 6 ? ' 等' : ''}</p>
          <p>{r.venue}{r.year ? ` · ${r.year}` : ''}</p>
          <div className={s.row}>
            {r.doi && <a className={s.link} href={`https://doi.org/${r.doi}`} target="_blank" rel="noreferrer">doi:{r.doi}</a>}
            <button type="button" className={s.link} style={{ border: 0, background: 'none', cursor: 'pointer' }}
              onClick={async () => { const t = await fetch(urls.referencesBib([r.key])).then((x) => x.text()); await navigator.clipboard.writeText(t); notify('ok', `已复制 ${r.key} 的 BibTeX`); }}>
              <Copy size={12} /> BibTeX
            </button>
            {r.tags.map((t) => <Badge key={t}>{topics[t] ?? t}</Badge>)}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------- code -- */
export function CodePanel() {
  const path = useStudio((st) => st.codePath);
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lang, setLang] = useState<unknown[]>([]);
  useEffect(() => {
    if (!path) return;
    setText(null);
    setErr(null);
    studio.source(path).then(async (r) => {
      setText(r.text);
      if (r.language === 'py') setLang([(await import('@codemirror/lang-python')).python()]);
      else if (r.language === 'json') setLang([(await import('@codemirror/lang-json')).json()]);
      else setLang([]);
    }).catch((e) => setErr((e as Error).message));
  }, [path]);
  if (!path) return <EmptyState icon={BookOpen} title="源码" detail="在算法库中点“查看源码”打开算法实现。" />;
  if (err) return <div className={s.pad}><Notice tone="error">{err}</Notice></div>;
  return (
    <div className={s.code}>
      <div className={s.listHead}>{path}</div>
      <Suspense fallback={<Spinner />}>
        {text !== null && <CodeMirror value={text} editable={false} extensions={lang as never[]} basicSetup={{ foldGutter: true, highlightActiveLine: false }} />}
      </Suspense>
    </div>
  );
}

