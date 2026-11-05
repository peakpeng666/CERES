import { useEffect, useMemo, useRef, useState } from 'react';
import * as Plot from '@observablehq/plot';
import { BarChart3, Download, FlaskConical, Play, Square } from 'lucide-react';
import { fmt, lab, useLabResource } from '../../lab/api';
import { Badge, Button, EmptyState, Input, Notice, Spinner } from '../../lab/ui';
import { studio, urls } from '../api';
import { useStudio } from '../store';
import type { Analysis, InstanceSpace, Study } from '../types';
import { logTimeAxis } from '../chart';
import s from './panels.module.css';

type ArchiveExperiment = {
  id: string;
  title: string;
  status: string;
  parameters?: { timing_scope?: string; headline?: string };
};

function BaselineArchiveCard() {
  const archives = useLabResource('baseline-archives', async (signal) => {
    const response = await fetch('/api/experiments', { signal, cache: 'no-store', credentials: 'same-origin' });
    const contentType = response.headers.get('content-type') ?? '';
    if (!contentType.includes('application/json')) throw new Error('档案接口未返回 JSON');
    const body = await response.json() as { experiments?: ArchiveExperiment[]; message?: string };
    if (!response.ok) throw new Error(body.message ?? `档案接口 HTTP ${response.status}`);
    return body;
  });
  const rows = (archives.data?.experiments ?? [])
    .filter((item) => item.parameters?.timing_scope)
    .slice()
    .sort((a, b) => (a.id.includes('-53-') ? 0 : 1) - (b.id.includes('-53-') ? 0 : 1) || a.id.localeCompare(b.id));
  return (
    <div className={s.pad} data-testid="baseline-archive">
      <h3>基线档案 · 按计时口径</h3>
      {archives.error && <p className={s.hint}>实验档案暂不可读：{archives.error.message}</p>}
      {!archives.error && archives.loading && !archives.data && <p className={s.hint}>正在读取实验档案…</p>}
      {archives.data && rows.length === 0 && <p className={s.hint}>暂无标注计时口径的基线包。</p>}
      <div className={s.baselineGrid}>
        {rows.map((item) => (
          <article key={item.id} className={s.quote} data-testid="baseline-archive-item" data-timing-scope={item.parameters?.timing_scope}>
            <strong>{item.title}</strong>
            <p>{item.parameters?.headline}</p>
            <p className={s.meta}>{item.parameters?.timing_scope} · {item.status} · {item.id}</p>
          </article>
        ))}
      </div>
      {rows.length > 1 && <p className={s.hint}>5 秒端到端与 1 秒搜索循环分别统计。16 例的两批结果并列显示在 1 秒卡片中，独立种子数按各批计算。</p>}
    </div>
  );
}

/* -------------------------------------------------------------- studies -- */
export function StudiesPanel() {
  const list = useLabResource('studies', () => studio.studies(), { pollMs: 3000 });
  const studyId = useStudio((st) => st.studyId);
  const setStudy = useStudio((st) => st.setStudy);
  const [designing, setDesigning] = useState(!studyId);
  // a study picked elsewhere (analysis list, palette) replaces the designer
  useEffect(() => { if (studyId) setDesigning(false); }, [studyId]);
  return (
    <div className={s.studies}>
    <BaselineArchiveCard />
    <div className={s.split}>
      <div className={s.splitSide}>
        <div className={s.pad} style={{ paddingBottom: 4 }}>
          <Button variant="primary" icon={FlaskConical} onClick={() => { setDesigning(true); setStudy(null); }}>新建课题</Button>
        </div>
        <ul className={s.list}>
          {(list.data?.studies ?? []).map((x) => (
            <li key={x.study_id}>
              <button type="button" className={s.item} aria-current={studyId === x.study_id || undefined} onClick={() => { setStudy(x.study_id); setDesigning(false); }}>
                <span className={s.itemTitle}>{x.title} {x.progress.state === 'done' ? <Badge tone="green">完成</Badge> : <Badge tone="blue">运行中</Badge>}</span>
                <span className={s.itemSub}>{fmt.time(x.created_utc)} · {x.progress.finished}/{x.progress.total} 次运行</span>
                <span className={s.progress}><i style={{ width: `${(100 * x.progress.finished) / Math.max(1, x.progress.total)}%` }} /></span>
              </button>
            </li>
          ))}
          {list.data && !list.data.studies.length && <li className={s.meta} style={{ padding: 10 }}>还没有课题。</li>}
        </ul>
      </div>
      <div className={s.splitMain}>{designing || !studyId ? <StudyDesigner onCreated={(id) => { setStudy(id); setDesigning(false); list.reload(); }} /> : <StudyProgress id={studyId} />}</div>
    </div>
    </div>
  );
}

function StudyDesigner({ onCreated }: { onCreated: (id: string) => void }) {
  const scenarios = useLabResource('studio-scenarios-all', () => lab.scenarios());
  const algorithms = useStudio((st) => st.algorithms);
  const current = useStudio((st) => st.scenarioId);
  const notify = useStudio((st) => st.notify);
  const [title, setTitle] = useState('');
  const [picked, setPicked] = useState<string[]>(current ? [current] : []);
  const [algos, setAlgos] = useState<string[]>([]);
  const [budgets, setBudgets] = useState('1, 5');
  const [seeds, setSeeds] = useState(3);
  const [xCount, setXCount] = useState(10);
  const [busy, setBusy] = useState(false);
  const all = scenarios.data?.scenarios ?? [];
  const classes = new Set(all.filter((x) => picked.includes(x.id)).map((x) => x.problem_class));
  const pickedCvrplib = picked.filter((p) => p.startsWith('cvrplib-'));
  if (pickedCvrplib.length) classes.add('euclidean_cvrp');
  const usable = algorithms.filter((a) => a.available && a.problem_classes.some((c) => classes.has(c)));
  const budgetList = budgets.split(/[,，\s]+/).map(Number).filter((v) => v > 0);
  const runs = picked.length * algos.length * Math.max(1, budgetList.length) * seeds;
  async function addXSample() {
    const inst = await studio.cvrplibInstances('X');
    const ok = inst.instances.filter((i) => i.supported && (i.n ?? 0) <= 300);
    const step = Math.max(1, Math.floor(ok.length / xCount));
    const ids = ok.filter((_, i) => i % step === 0).slice(0, xCount).map((i) => i.scenario_id);
    setPicked((p) => Array.from(new Set([...p, ...ids])));
  }
  async function start() {
    setBusy(true);
    try {
      const study = await studio.createStudy({
        title: title || `课题 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
        scenarios: picked, algorithms: algos.map((id) => ({ id })), budgets: budgetList, seeds: Array.from({ length: seeds }, (_, i) => 11 + i * 18),
      });
      notify('ok', `课题已开始：${study.progress.total} 次运行`);
      onCreated(study.study_id);
    } catch (e) {
      notify('error', (e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={s.form}>
      <h3>课题名称</h3>
      <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：X 集 100–300 客户上的求解器比较" />
      <h3>场景（实例）· 已选 {picked.length}</h3>
      <div className={s.row}>
        <span className={s.inputUnit} style={{ maxWidth: 220 }}><Input type="number" min={1} max={100} value={xCount} onChange={(e) => setXCount(Number(e.target.value) || 1)} /><em>个</em></span>
        <Button size="sm" onClick={() => void addXSample()}>均匀抽取 CVRPLIB X 集（≤300 客户）</Button>
        <Button size="sm" variant="plain" onClick={() => setPicked([])}>清空</Button>
      </div>
      <div className={s.chips}>
        {all.filter((x) => x.problem_class !== 'turn_routing' || x.frame.kind === 'map').slice(0, 80).map((x) => (
          <button key={x.id} type="button" className={s.chip} aria-pressed={picked.includes(x.id)}
            onClick={() => setPicked((p) => (p.includes(x.id) ? p.filter((y) => y !== x.id) : [...p, x.id]))}>{x.title}</button>
        ))}
        {pickedCvrplib.filter((id) => !all.some((x) => x.id === id)).map((id) => (
          <button key={id} type="button" className={s.chip} aria-pressed onClick={() => setPicked((p) => p.filter((y) => y !== id))}>{id.replace('cvrplib-', '').toUpperCase()}</button>
        ))}
      </div>
      <h3>算法 · 已选 {algos.length}</h3>
      <div className={s.chips}>
        {usable.map((a) => (
          <button key={a.id} type="button" className={s.chip} aria-pressed={algos.includes(a.id)}
            onClick={() => setAlgos((p) => (p.includes(a.id) ? p.filter((y) => y !== a.id) : [...p, a.id]))}>{a.name}</button>
        ))}
        {!usable.length && <span className={s.meta}>先选择场景。</span>}
      </div>
      <div className={s.grid2}>
        <label className={s.field}><span>时间预算（秒，可多个）</span><Input value={budgets} onChange={(e) => setBudgets(e.target.value)} /></label>
        <label className={s.field}><span>随机种子数（随机算法重复次数）</span><Input type="number" min={1} max={30} value={seeds} onChange={(e) => setSeeds(Number(e.target.value) || 1)} /></label>
      </div>
      <p className={s.hint}>最多约 {runs} 次运行（确定性算法只跑一次）。所有运行进入同一个求解队列，结果逐个出现。</p>
      <div className={s.row}>
        <Button variant="primary" icon={Play} loading={busy} disabled={!picked.length || !algos.length} onClick={() => void start()}>开始课题</Button>
      </div>
    </div>
  );
}

const STATE_LABEL: Record<string, string> = { done: '完成', failed: '失败', running: '运行中', queued: '排队', cancelled: '已停止', skipped: '跳过' };

function StudyProgress({ id }: { id: string }) {
  const res = useLabResource(`study-${id}`, () => studio.study(id), { pollMs: 2500 });
  const scenarios = useLabResource('studio-scenarios-all', () => lab.scenarios());
  const algorithms = useStudio((st) => st.algorithms);
  const study = res.data as Study | null;
  if (!study) return <div className={s.pad}><Spinner /></div>;
  const algName = (a: string) => algorithms.find((x) => x.id === a)?.name ?? a;
  const scName = (sc: string) => scenarios.data?.scenarios.find((x) => x.id === sc)?.title ?? sc.replace(/^cvrplib-x-/, 'X-');
  const others = Object.entries(study.progress.counts).filter(([k, v]) => k !== 'done' && v > 0);
  const algos = Array.from(new Set((study.design ?? []).map((d) => d.algorithm_id)));
  const scs = Array.from(new Set((study.design ?? []).map((d) => d.scenario_id)));
  return (
    <div className={s.form}>
      <div className={s.rowSpace}>
        <h3>{study.title}</h3>
        <div className={s.row}>
          {study.progress.state !== 'done' && <Button size="sm" icon={Square} onClick={() => void studio.cancelStudy(id)}>停止</Button>}
          <Button size="sm" variant="primary" icon={BarChart3} onClick={() => window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: 'analysis' }))}>查看分析</Button>
        </div>
      </div>
      <p className={s.hint}>{study.progress.finished}/{study.progress.total} 完成{others.map(([k, v]) => ` · ${STATE_LABEL[k] ?? k} ${v}`).join('')}</p>
      <div className={s.progress}><i style={{ width: `${(100 * study.progress.finished) / Math.max(1, study.progress.total)}%` }} /></div>
      <h3>运行矩阵（行：场景，列：算法）</h3>
      <div style={{ overflow: 'auto' }}>
        <table className={s.table}>
          <thead><tr><th>场景</th>{algos.map((a) => <th key={a}>{algName(a)}</th>)}</tr></thead>
          <tbody>
            {scs.map((sc) => (
              <tr key={sc}>
                <td title={sc}>{scName(sc)}</td>
                {algos.map((a) => (
                  <td key={a}>
                    <div className={s.matrix} style={{ gridTemplateColumns: 'repeat(10, 10px)' }}>
                      {(study.design ?? []).filter((d) => d.scenario_id === sc && d.algorithm_id === a).map((d) => (
                        <span key={d.run_id} data-s={d.state === 'done' ? 'done' : d.state === 'running' ? 'running' : d.state === 'queued' ? 'queued' : d.state} title={`${STATE_LABEL[d.state ?? ''] ?? d.state ?? ''}${d.objective != null ? ` · ${fmt.num(d.objective, 2)}` : ''}`} />
                      ))}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {study.skipped.length > 0 && <Notice tone="info">跳过：{study.skipped.slice(0, 6).join('；')}{study.skipped.length > 6 ? ' …' : ''}</Notice>}
    </div>
  );
}

/* ------------------------------------------------------------- analysis -- */
function PlotBox({ make, deps, height = 240 }: { make: (w: number) => (SVGSVGElement | HTMLElement) | null; deps: unknown[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.replaceChildren();
    const node = make(Math.max(320, el.clientWidth));
    if (node) el.append(node);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return <div ref={ref} style={{ minHeight: height }} />;
}

const FONT = { fontFamily: 'var(--mac-font)', fontSize: '11px' };
/** One colour per algorithm, the same in every chart of a study. */
const PALETTE = ['var(--route-1)', 'var(--route-2)', 'var(--route-3)', 'var(--route-4)', 'var(--route-5)', 'var(--route-6)', '#a2845e', '#64748b', '#8e8e93', '#b0a890'];

export function AnalysisPanel() {
  const studyId = useStudio((st) => st.studyId);
  const studies = useLabResource('studies-a', () => studio.studies(), { pollMs: 5000 });
  const setStudy = useStudio((st) => st.setStudy);
  const [data, setData] = useState<Analysis | null>(null);
  const [space, setSpace] = useState<InstanceSpace | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!studyId) return;
    let alive = true;
    const load = () => {
      studio.analysis(studyId).then((a) => alive && setData(a)).catch((e) => alive && setErr((e as Error).message));
      studio.instanceSpace(studyId).then((x) => alive && setSpace(x)).catch(() => undefined);
    };
    load();
    const t = window.setInterval(load, 6000);
    return () => { alive = false; window.clearInterval(t); };
  }, [studyId]);
  const algs = data?.algorithms ?? [];
  const names = useMemo(() => Object.fromEntries((data?.summary ?? []).map((x) => [x.algorithm, x.name ?? x.algorithm])), [data]);
  const nm = (a: string | null) => (a == null ? '无可行解' : names[a] ?? a);
  const color = { legend: true, domain: algs.map((a) => names[a] ?? a), range: PALETTE.slice(0, algs.length) };
  const profile = useMemo(() => {
    if (!data) return [];
    return algs.flatMap((a) => data.performance_profile.taus.map((tau, i) => ({ tau, rho: data.performance_profile.curves[a]?.[i] ?? 0, a: names[a] ?? a })));
  }, [data, algs, names]);
  if (!studyId) {
    return (
      <div className={s.pad}>
        <EmptyState icon={BarChart3} title="选择一个课题" detail="选择已运行的课题，查看性能剖面、临界差图、收敛曲线和 LaTeX 表格。" />
        <ul className={s.list}>
          {(studies.data?.studies ?? []).map((x) => (
            <li key={x.study_id}><button type="button" className={s.item} onClick={() => setStudy(x.study_id)}><span className={s.itemTitle}>{x.title}</span><span className={s.itemSub}>{x.progress.finished}/{x.progress.total}</span></button></li>
          ))}
        </ul>
      </div>
    );
  }
  if (err) return <div className={s.pad}><Notice tone="error">{err}</Notice></div>;
  if (!data) return <div className={s.pad}><Spinner /></div>;
  if (data.empty) return <EmptyState title="还没有完成的运行" />;
  const t = data.tests;
  return (
    <div className={s.scroll}>
      <div className={s.form}>
        <div className={s.rowSpace}>
          <h3>统计结果 · {data.n_instances} 个实例 · {data.n_runs} 次运行</h3>
          <div className={s.row}>
            <a className={s.link} href={urls.studyExport(studyId, 'latex')} download><Download size={12} /> LaTeX 表</a>
            <a className={s.link} href={urls.studyExport(studyId, 'csv')} download><Download size={12} /> CSV</a>
            <a className={s.link} href={urls.studyExport(studyId, 'json')} download><Download size={12} /> JSON</a>
          </div>
        </div>
        <table className={s.table}>
          <thead><tr><th>算法</th><th className={s.num}>成功</th><th className={s.num}>平均差距</th><th className={s.num}>中位差距</th><th className={s.num}>平均秩</th><th className={s.num}>最优次数</th><th className={s.num}>平均用时</th></tr></thead>
          <tbody>
            {[...data.summary].sort((a, b) => (a.mean_rank ?? 99) - (b.mean_rank ?? 99)).map((r) => (
              <tr key={r.algorithm}>
                <td><strong>{r.name ?? r.algorithm}</strong></td>
                <td className={s.num}>{r.successes}/{r.runs}</td>
                <td className={s.num}>{r.gap_mean == null ? '—' : `${fmt.num(r.gap_mean, 2)}%`}{r.gap_sd != null ? ` ± ${fmt.num(r.gap_sd, 2)}` : ''}</td>
                <td className={s.num}>{r.gap_median == null ? '—' : `${fmt.num(r.gap_median, 2)}%`}</td>
                <td className={s.num}>{fmt.num(r.mean_rank, 2)}</td>
                <td className={s.num}>{r.wins}</td>
                <td className={s.num}>{fmt.sec(r.time_mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={s.meta}>{data.reference_note}</p>
        {t.friedman?.statistic != null && (
          <p className={s.hint}>
            Friedman χ² = {fmt.num(t.friedman.statistic, 2)}，p = {t.friedman.p_value?.toExponential(2)}（{t.friedman.significant ? '算法间差异显著' : '差异不显著'}）；
            Nemenyi 临界差 CD = {fmt.num(t.nemenyi?.critical_difference, 2)}（α = {t.alpha}，k = {t.k}，N = {t.n}）。
          </p>
        )}
        {t.nemenyi && <CdDiagram ranks={t.nemenyi.mean_ranks} cd={t.nemenyi.critical_difference} names={names} />}
        <div className={s.chart}>
          <h4>性能剖面（Dolan–Moré）：ρ(τ) = 成本不超过最好值 τ 倍的实例比例</h4>
          <PlotBox deps={[profile]} make={(w) => profile.length ? Plot.plot({ width: w, height: 230, style: FONT, x: { label: 'τ', grid: true }, y: { label: 'ρ(τ)', domain: [0, 1], grid: true }, color,
            marks: [Plot.line(profile, { x: 'tau', y: 'rho', stroke: 'a', curve: 'step-after', strokeWidth: 1.8 })] }) : null} />
        </div>
        <div className={s.chart}>
          <h4>相对参照的差距分布（每次运行）</h4>
          <PlotBox deps={[data]} make={(w) => {
            const pts = data.scatter.filter((p) => p.gap != null).map((p) => ({ ...p, algorithm: nm(p.algorithm) }));
            return pts.length ? Plot.plot({ width: w, height: 40 + 34 * algs.length, marginLeft: 170, style: FONT, x: { label: '差距 (%)', grid: true }, y: { label: null },
              marks: [Plot.boxX(pts, { x: 'gap', y: 'algorithm' }), Plot.dot(pts, { x: 'gap', y: 'algorithm', r: 2.5, fill: 'algorithm', fillOpacity: 0.7 })], color: { ...color, legend: false } }) : null;
          }} />
        </div>
        {data.anytime.grid.length > 0 && (
          <div className={s.chart}>
            <h4>随时间收敛：当前最好解差距的中位数</h4>
            <PlotBox deps={[data]} make={(w) => {
              const rows = Object.entries(data.anytime.median).flatMap(([a, ys]) => ys.map((y, i) => ({ a: nm(a), t: data.anytime.grid[i], y })).filter((r) => r.y != null));
              return rows.length ? Plot.plot({ width: w, height: 220, style: FONT, x: logTimeAxis(rows.map((r) => r.t)), y: { label: '差距 (%)', grid: true }, color,
                marks: [Plot.line(rows, { x: 't', y: 'y', stroke: 'a', curve: 'step-after', strokeWidth: 1.8 })] }) : null;
            }} />
          </div>
        )}
        <div className={s.chart}>
          <h4>运行时间 vs 差距</h4>
          <PlotBox deps={[data]} make={(w) => {
            const pts = data.scatter.filter((p) => p.gap != null && p.time != null).map((p) => ({ ...p, algorithm: nm(p.algorithm) }));
            return pts.length ? Plot.plot({ width: w, height: 220, style: FONT, x: logTimeAxis(pts.map((p) => p.time as number), '用时'), y: { label: '差距 (%)', grid: true }, color,
              marks: [Plot.dot(pts, { x: 'time', y: 'gap', fill: 'algorithm', r: 3, fillOpacity: 0.7 })] }) : null;
          }} />
        </div>
        {space && space.points.length >= 3 && (
          <div className={s.chart}>
            <h4>实例空间（{space.method}）：每个实例按最佳算法着色，阴影为该算法的“足迹”</h4>
            <PlotBox deps={[space]} make={(w) => {
              const hulls = Object.entries(space.footprints ?? {}).flatMap(([a, poly]) => poly.map(([x, y], i) => ({ a: nm(a), x, y, i })));
              return Plot.plot({ width: w, height: 300, style: FONT, x: { label: `PC1（${fmt.num((space.explained_variance?.[0] ?? 0) * 100, 0)}%）`, grid: true },
                y: { label: `PC2（${fmt.num((space.explained_variance?.[1] ?? 0) * 100, 0)}%）`, grid: true },
                color: space.points.some((pt) => !pt.best) ? { ...color, domain: [...color.domain, '无可行解'], range: [...color.range, '#8e8e93'] } : color,
                marks: [Plot.line(hulls, { x: 'x', y: 'y', z: 'a', stroke: 'a', fill: 'a', fillOpacity: 0.08 }), Plot.dot(space.points, { x: 'x', y: 'y', fill: (d: { best: string | null }) => nm(d.best), r: 4, tip: true, title: 'title' })] });
            }} />
          </div>
        )}
        <h3>两两比较（Wilcoxon 符号秩 + Holm 校正）</h3>
        <table className={s.table}>
          <thead><tr><th>A</th><th>B</th><th className={s.num}>A 更好</th><th className={s.num}>B 更好</th><th className={s.num}>平</th><th className={s.num}>p（Holm）</th><th /></tr></thead>
          <tbody>
            {t.wilcoxon_holm.map((r) => (
              <tr key={`${r.a}-${r.b}`}>
                <td>{r.a}</td><td>{r.b}</td><td className={s.num}>{r.a_better}</td><td className={s.num}>{r.b_better}</td><td className={s.num}>{r.ties}</td>
                <td className={s.num}>{r.p_holm == null ? '样本不足' : r.p_holm.toExponential(2)}</td>
                <td>{r.significant ? <Badge tone="green">显著</Badge> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CdDiagram({ ranks, cd, names }: { ranks: Record<string, number>; cd: number; names: Record<string, string> }) {
  const items = Object.entries(ranks).sort((a, b) => a[1] - b[1]);
  const k = items.length;
  if (k < 2) return null;
  const W = 640;
  const x = (r: number) => 60 + ((r - 1) / Math.max(1, k - 1)) * (W - 120);
  const groups: [number, number][] = [];
  for (let i = 0; i < k; i++) {
    let j = i;
    while (j + 1 < k && items[j + 1][1] - items[i][1] <= cd) j++;
    if (j > i && !groups.some(([a, b]) => a <= i && b >= j)) groups.push([i, j]);
  }
  const half = Math.ceil(k / 2);
  return (
    <div className={s.chart}>
      <h4>临界差图（Demšar 2006）：粗线连接的算法之间无显著差异</h4>
      <svg width="100%" viewBox={`0 0 ${W} ${70 + 22 * half}`} style={{ font: '11px Inter, PingFang SC, sans-serif' }}>
        <line x1={x(1)} x2={x(k)} y1={30} y2={30} stroke="#333" />
        {Array.from({ length: k }, (_, i) => (
          <g key={i}>
            <line x1={x(i + 1)} x2={x(i + 1)} y1={26} y2={34} stroke="#333" />
            <text x={x(i + 1)} y={20} textAnchor="middle">{i + 1}</text>
          </g>
        ))}
        <line x1={x(1)} x2={x(1) + (cd / Math.max(1, k - 1)) * (W - 120)} y1={8} y2={8} stroke="var(--mac-red)" strokeWidth={2} />
        <text x={x(1) + (cd / Math.max(1, k - 1)) * (W - 120) + 6} y={11} fill="var(--mac-red)">CD = {cd.toFixed(2)}</text>
        {items.map(([a, r], i) => {
          const left = i < half;
          const y = 50 + 22 * (left ? i : k - 1 - i);
          return (
            <g key={a}>
              <polyline points={`${x(r)},30 ${x(r)},${y} ${left ? 40 : W - 40},${y}`} fill="none" stroke="#555" />
              <text x={left ? 36 : W - 36} y={y + 4} textAnchor={left ? 'end' : 'start'}>{names[a] ?? a} ({r.toFixed(2)})</text>
            </g>
          );
        })}
        {groups.map(([i, j], g) => (
          <line key={g} x1={x(items[i][1]) - 3} x2={x(items[j][1]) + 3} y1={38 + 5 * g} y2={38 + 5 * g} stroke="#1d1d1f" strokeWidth={3} strokeLinecap="round" />
        ))}
      </svg>
    </div>
  );
}

