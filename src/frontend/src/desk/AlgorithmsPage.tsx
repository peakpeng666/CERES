import { useEffect, useMemo, useRef, useState } from 'react';
import { navigate, type Route } from '../app/router';
import { lab, useLabResource } from '../lab/api';
import type { Algorithm, AlgorithmParam, AlgorithmStat, ProblemClass, Recommendation, ScenarioSummary, Solution } from '../lab/types';
import { PanelHost } from '../studio/panelRegistry';
import { studio } from '../studio/api';
import { cite } from '../studio/cite';
import { useStudio } from '../studio/store';
import type { Reference } from '../studio/types';
import { n0, n1, n2, n3 } from './format';
import { Icon } from './Icon';
import { useDesk } from './store';
import { SurfaceFrame } from './Surface';
import { Facts, Fold, Kvs, Row, Rows, TopBar } from './ui';
import s from './desk.module.css';

const CLASSES: [ProblemClass, string][] = [['fleet_routing', '多车任务'], ['coverage', '覆盖作业'], ['point_to_point', '运动规划'], ['euclidean_cvrp', 'CVRP'], ['turn_routing', '转弯约束']];
const FAMILY: Record<string, string> = { exact: '精确算法', relaxation: '松弛下界', metaheuristic: '元启发式', heuristic: '构造启发式', baseline: '基线', coverage: '覆盖规划', graph_search: '图搜索', sampling: '采样规划' };
const GUARANTEE: Record<string, string> = { optimal: '保证最优', optimal_for_relaxation: '松弛问题最优', none: '无最优性保证', resolution_optimal: '分辨率最优', asymptotically_optimal: '渐近最优', probabilistically_complete: '概率完备' };
const SELECTOR: Record<string, string> = { available: '已启用', unavailable: '未接入', not_applicable: '不适用于这类问题', out_of_scope: '这张地图超出训练范围', out_of_profile: '这张地图超出已评估的配置' };
const FINAL = new Set(['done', 'error', 'timeout', 'canceled']);
const seconds = (v?: number | null) => (v == null ? '—' : v < 1 ? `${n3(v)} s` : `${n2(v)} s`);
const pct = (v: number) => `${n2(v * 100)}%`;

function useMapsOf(cls: ProblemClass) {
  const res = useLabResource('desk-maps', () => lab.scenarios());
  return useMemo(() => {
    const list = (res.data?.scenarios ?? []).filter((x) => x.problem_class === cls);
    const mine = (x: ScenarioSummary) => (x as ScenarioSummary & { origin?: string }).origin === 'user';
    return [...list.filter(mine).reverse(), ...list.filter((x) => !mine(x))];
  }, [res.data, cls]);
}

/** The map the page reasons about: the one open on the planning surface when it fits, otherwise the newest of this class. */
function useChosenMap(cls: ProblemClass) {
  const maps = useMapsOf(cls);
  const open = useStudio((x) => x.scenarioId);
  const [pick, setPick] = useState<string | null>(null);
  const chosen = maps.find((m) => m.id === pick) ?? maps.find((m) => m.id === open) ?? maps[0] ?? null;
  return { maps, chosen, setPick };
}

function MapPicker({ maps, chosen, setPick }: ReturnType<typeof useChosenMap>) {
  if (!maps.length) return <span className={s.dim}>这一类还没有地图</span>;
  return (
    <select className={s.inlineSelect} aria-label="地图" value={chosen?.id ?? ''} onChange={(e) => setPick(e.target.value)}>
      {maps.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}
    </select>
  );
}

function Catalog({ algorithms, stats }: { algorithms: Algorithm[]; stats: Record<string, AlgorithmStat> }) {
  const [cls, setCls] = useState<ProblemClass>('fleet_routing');
  const [q, setQ] = useState('');
  const map = useChosenMap(cls);
  const id = map.chosen?.id ?? null;
  const rec = useLabResource<Recommendation | null>(`algo-rec-${id}`, () => (id ? lab.recommend(id) : Promise.resolve(null)));
  const rank = useMemo(() => new Map((rec.data?.ranking ?? []).map((r) => [r.algorithm_id, r])), [rec.data]);
  const rows = algorithms.filter((a) => a.problem_classes.includes(cls) && (!q.trim() || `${a.name} ${a.summary ?? ''} ${a.library ?? ''}`.toLowerCase().includes(q.trim().toLowerCase())));
  const order = (a: Algorithm) => rank.get(a.id)?.rank ?? 99;
  const sorted = [...rows].sort((a, b) => order(a) - order(b));
  const top = rec.data?.recommended ? algorithms.find((a) => a.id === rec.data?.recommended) : null;
  return (
    <div className={s.pageIn}>
      <div className={s.pageTabs} role="tablist">
        {CLASSES.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={k === cls} onClick={() => setCls(k)}>{label}<em>{algorithms.filter((a) => a.problem_classes.includes(k)).length}</em></button>)}
      </div>
      <div className={s.algBar}>
        <span>为这张地图排序</span>
        <MapPicker {...map} />
        <span className={s.grow} />
        <label className={s.search} style={{ padding: '2px 8px' }}><input type="search" value={q} placeholder="搜索算法" aria-label="搜索算法" onChange={(e) => setQ(e.target.value)} /></label>
      </div>
      {top && <p className={s.algLead}>推荐 <a href={`#/algorithms/${encodeURIComponent(top.id)}`}>{top.name}</a>：{rec.data?.explanation.replace(/^推荐[^：]*：/, '')}</p>}
      {rec.data?.learned_selector && <p className={s.dim}>学习型选择器：{SELECTOR[rec.data.learned_selector.status] ?? rec.data.learned_selector.status}{rec.data.learned_selector.reason ? `。${rec.data.learned_selector.reason}` : ''}</p>}
      <Rows>
        {sorted.map((a) => {
          const r = rank.get(a.id);
          return (
            <Row key={a.id} wrap title={<>{a.name}{r?.suitability === '不适用' && <span className={s.tagDim}>不适用</span>}{!a.available && <span className={s.tagDim}>未安装</span>}</>}
              sub={a.available ? `${FAMILY[a.family] ?? a.family} · ${GUARANTEE[a.guarantee] ?? a.guarantee} · ${a.library ?? ''}` : a.unavailable_reason ?? '后端未提供原因'}
              value={stats[a.id]?.runs ? `${stats[a.id].runs} 次` : undefined} valueSub={stats[a.id]?.runs ? seconds(stats[a.id].wall_s.median) : undefined}
              onClick={() => navigate(`#/algorithms/${encodeURIComponent(a.id)}`)} />
          );
        })}
      </Rows>
      {!sorted.length && <p className={s.emptyLine}>没有匹配的算法。</p>}
    </div>
  );
}

function ParamInput({ p, value, onChange }: { p: AlgorithmParam; value: unknown; onChange: (v: unknown) => void }) {
  if (p.type === 'boolean') return <input type="checkbox" aria-label={p.label} checked={value === true} onChange={(e) => onChange(e.target.checked)} />;
  if (p.type === 'select') return <select className={s.inlineSelect} aria-label={p.label} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>{p.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>;
  const numeric = p.type === 'number' || p.type === 'integer';
  return <input className={s.inlineInput} type={numeric ? 'number' : 'text'} min={p.min} max={p.max} step={p.type === 'integer' ? 1 : 'any'} aria-label={p.label} value={String(value ?? '')}
    onChange={(e) => onChange(numeric ? (e.target.value === '' ? null : p.type === 'integer' ? Math.round(Number(e.target.value)) : Number(e.target.value)) : e.target.value)} />;
}

function useTrial() {
  const [ids, setIds] = useState<string[]>([]);
  const [runs, setRuns] = useState<Record<string, Solution>>({});
  useEffect(() => {
    if (!ids.length) return undefined;
    let live = true;
    const tick = async () => {
      const got = await Promise.all(ids.map((id) => lab.run(id).catch(() => null)));
      if (!live) return;
      setRuns(Object.fromEntries(got.filter((r): r is Solution => !!r).map((r) => [r.run_id, r])));
      if (got.some((r) => !r || !FINAL.has(r.state))) timer = window.setTimeout(() => void tick(), 500);
    };
    let timer = window.setTimeout(() => void tick(), 200);
    return () => { live = false; window.clearTimeout(timer); };
  }, [ids]);
  return { ids, runs, start: (next: string[]) => { setRuns({}); setIds(next); } };
}

/** Run this algorithm, and any rivals, on one map with one input. */
function Trial({ a, algorithms }: { a: Algorithm; algorithms: Algorithm[] }) {
  const cls = a.problem_classes[0];
  const map = useChosenMap(cls);
  const notify = useStudio((x) => x.notify);
  const [params, setParams] = useState<Record<string, unknown>>(() => Object.fromEntries(a.params.map((p) => [p.name, p.default])));
  const [rivals, setRivals] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const trial = useTrial();
  const others = algorithms.filter((x) => x.id !== a.id && x.available && x.problem_classes.includes(cls));
  const run = async () => {
    if (!map.chosen) return;
    setBusy(true);
    try {
      const budget = params.time_limit_s;
      const job = await lab.solve(map.chosen.id, [{ id: a.id, params }, ...rivals.map((id) => ({ id, params: budget == null ? {} : { time_limit_s: budget } }))], undefined, `算法库试跑 ${a.name}`);
      trial.start(job.runs);
    } catch (e) {
      notify('error', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const results = useRef<HTMLDivElement>(null);
  useEffect(() => { if (trial.ids.length) results.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [trial.ids]);
  const rows = trial.ids.map((id) => trial.runs[id]).filter(Boolean);
  const ranked = [...rows].sort((x, y) => (x.objective?.value ?? Infinity) - (y.objective?.value ?? Infinity));
  const best = ranked.find((r) => r.state === 'done' && r.check?.valid)?.objective?.value;
  const open = (r: Solution) => {
    useStudio.setState({ focusRun: r.run_id, manualFocus: true });
    navigate(`#/plan/${encodeURIComponent(r.scenario_id)}`);
  };
  return (
    <section className={s.trial}>
      <h3>试跑</h3>
      <div className={s.trialForm}>
        <label><span>地图</span><MapPicker {...map} /></label>
        {a.params.map((p) => <label key={p.name}><span>{p.label}</span><ParamInput p={p} value={params[p.name]} onChange={(v) => setParams({ ...params, [p.name]: v })} /></label>)}
      </div>
      {others.length > 0 && (
        <Fold summary={<span>同台对比{rivals.length ? ` · 已选 ${rivals.length}` : ''}</span>}>{() => (
          <div className={s.checks}>
            {others.map((o) => <label key={o.id}><input type="checkbox" checked={rivals.includes(o.id)} onChange={(e) => setRivals(e.target.checked ? [...rivals, o.id] : rivals.filter((x) => x !== o.id))} /><span>{o.name}</span></label>)}
          </div>
        )}</Fold>
      )}
      <div className={s.trialGo}><button type="button" className={`${s.btn} ${s.btnPrimary}`} disabled={!a.available || busy || !map.chosen} onClick={() => void run()}>{rivals.length ? `运行 ${rivals.length + 1} 个算法` : '运行'}</button></div>
      {trial.ids.length > 0 && (
        <div ref={results}><Rows>
          {ranked.map((r) => {
            const done = r.state === 'done';
            const value = r.objective?.value;
            const gap = done && r.check?.valid && best && value != null && value > best ? ` · +${pct((value - best) / Math.abs(best))}` : '';
            return <Row key={r.run_id} title={r.algorithm.name}
              sub={done ? (r.check?.valid ? `通过检查 · ${seconds(r.metrics?.wall_s as number | undefined)}` : `未通过检查 · ${r.status ?? ''}`) : FINAL.has(r.state) ? r.error ?? r.state : '运行中'}
              value={value != null ? `${n1(value)} ${r.objective?.unit ?? ''}` : undefined} valueSub={gap || undefined} onClick={FINAL.has(r.state) ? () => open(r) : undefined} />;
          })}
          {!rows.length && <p className={s.emptyLine}>已提交。</p>}
        </Rows></div>
      )}
    </section>
  );
}

function Detail({ a, algorithms }: { a: Algorithm; algorithms: Algorithm[] }) {
  const hist = useLabResource(`algo-hist-${a.id}`, () => lab.algorithmHistory(a.id), { reloadOn: 'run' });
  const refs = useLabResource('refs-all', () => studio.references());
  const openCode = useStudio((x) => x.openCode);
  const setPane = useDesk((d) => d.setPane);
  const h = hist.data;
  const duel = h?.head_to_head;
  const byKey = useMemo(() => Object.fromEntries((refs.data?.references ?? []).map((r) => [r.key, r])), [refs.data]);
  const cited = (a.refs ?? []).map((k) => byKey[k]).filter(Boolean) as Reference[];
  const local = a.origin.startsWith('src/');
  return (
    <div className={s.pageIn}>
      <h2 className={s.algName}>{a.name}</h2>
      <p className={s.algSummary}>{a.summary ?? a.description}</p>
      {!a.available && <p className={s.docWarn}>当前环境不可用：{a.unavailable_reason ?? '后端未提供原因'}</p>}
      {h && h.runs > 0 && <Facts items={[['本机运行', n0(h.runs), '次'], ['通过检查', n0(h.valid), `/${h.completed}`], ['中位耗时', seconds(h.wall_s.median)], !!duel && ['同题最好', `${duel.wins}/${duel.instances}`], !!duel && ['与最好差距', pct(duel.mean_gap)]]} />}
      {h && h.runs === 0 && <p className={s.dim}>这台机器上还没有它的运行记录。</p>}
      <div className={s.folds}>
        <Fold defaultOpen summary={<span>适用条件</span>}>{() => (
          <Kvs items={[['问题', a.problem_label ?? a.problem_classes.join('、')], ['方法', FAMILY[a.family] ?? a.family], ['最优性', GUARANTEE[a.guarantee] ?? a.guarantee],
            !!a.limits?.max_tasks && ['规模上限', `${a.limits.max_tasks} 个任务`], ['转弯约束', a.turn_aware ? '进入搜索' : '不进入搜索'], !!a.randomness && ['随机性', a.randomness.note]]} />
        )}</Fold>
        {a.params.length > 0 && <Fold summary={<span>参数 · {a.params.length}</span>}>{() => (
          <Rows>{a.params.map((p) => <Row key={p.name} dense title={p.label} sub={p.name} value={String(p.default ?? '—')} valueSub={p.min != null || p.max != null ? `${p.min ?? '—'} – ${p.max ?? '—'}` : undefined} />)}</Rows>
        )}</Fold>}
        <Fold summary={<span>实现与版本</span>}>{() => <>
          <Kvs items={[['来源', a.library ?? a.source], ['实现', a.origin], !!a.version && !a.origin.includes(a.version) && [local ? '源码 SHA256' : '版本', a.version]]} />
          {local && <button type="button" className={s.btn} style={{ marginTop: 8 }} onClick={() => { openCode(a.origin.split(' ')[0]); setPane('algorithms', true); }}>查看源码</button>}
        </>}</Fold>
        {cited.length > 0 && <Fold summary={<span>文献 · {cited.length}</span>}>{() => (
          <Rows>{cited.map((r) => <Row key={r.key} dense wrap title={cite(r)} sub={r.title} onClick={r.doi || r.url ? () => window.open(r.doi ? `https://doi.org/${r.doi}` : r.url ?? '#', '_blank', 'noopener') : undefined} />)}</Rows>
        )}</Fold>}
        {!!h?.physical && <Fold summary={<span>物理预演 · {h.physical.passed}/{h.physical.runs} 通过</span>}>{() => (
          <Kvs items={[['实际与规划里程之比', h.physical!.distance_ratio != null ? `${n3(h.physical!.distance_ratio)} 倍` : '—'], ['最大横向偏差', h.physical!.cross_track_max_m != null ? `${n3(h.physical!.cross_track_max_m)} m` : '—']]} />
        )}</Fold>}
        {!!h?.maps.length && <Fold summary={<span>本机记录 · {h.maps.length} 张地图</span>}>{() => (
          <Rows>{h.maps.map((m) => <Row key={m.scenario_id} dense title={m.title ?? m.scenario_id} sub={`${m.runs} 次`} value={m.best != null ? `${n1(m.best)} ${m.unit ?? ''}` : '无可行解'} onClick={() => navigate(`#/plan/${encodeURIComponent(m.scenario_id)}`)} />)}</Rows>
        )}</Fold>}
      </div>
      <Trial key={a.id} a={a} algorithms={algorithms} />
    </div>
  );
}

/** The algorithm library. A class, then an algorithm, then its conditions, its record on this machine and a trial on a map. */
export function AlgorithmsPage({ route }: { route: Route }) {
  const algorithms = useStudio((x) => x.algorithms);
  const stats = useLabResource('algo-stats', () => lab.algorithmStats(), { reloadOn: 'run' });
  const setPane = useDesk((d) => d.setPane);
  const scroller = useRef<HTMLDivElement>(null);
  const a = route.id ? algorithms.find((x) => x.id === route.id) : null;
  useEffect(() => { scroller.current?.scrollTo(0, 0); }, [route.id]);
  const close = <button type="button" className={s.iconBtn} title="关闭面板" aria-label="关闭面板" onClick={() => setPane('algorithms', false)}><Icon name="close" /></button>;
  return (
    <SurfaceFrame name="algorithms" pane={<div className={s.paneIn}><div className={s.tabs}><span className={s.grow} />{close}</div><div className={s.tabBody}><PanelHost id="code" /></div></div>}>
      <TopBar>{a ? <><a href="#/algorithms">算法库</a><i>/</i><b>{a.name}</b></> : <h1 className={s.pageTitle}>算法库</h1>}</TopBar>
      <div ref={scroller} className={s.page}>
        {route.id && !a && algorithms.length > 0 ? <div className={s.pageIn}><p className={s.emptyLine}>没有这个算法。</p></div>
          : a ? <Detail key={a.id} a={a} algorithms={algorithms} />
            : <Catalog algorithms={algorithms} stats={stats.data?.stats ?? {}} />}
      </div>
    </SurfaceFrame>
  );
}
