/** Research figures drawn from the backend Analysis of a study. */
import type { Analysis } from '../types';
import p from '../panels/panels.module.css';
import { anytimeData, cdData, gapEcdfData, gapTimeData, profileData, runtimeEcdfData } from './chartData';
import { Axes, Figure, fmtTick, fmtTime, Legend, Marker, NoData, PLOT, scale, stepPath, styleFor } from './SvgChart';

const pct = (v: number) => `${+(v * 100).toPrecision(3)}%`;

export function ProfileChart({ analysis }: { analysis: Analysis | null }) {
  const title = '性能剖面（Dolan–Moré）';
  const d = profileData(analysis);
  if (!d.ok) return <NoData title={title} reason={d.reason} />;
  const taus = d.series.flatMap((s) => s.points.map(([t]) => t));
  const x = scale({ label: 'τ', domain: [1, Math.max(1.01, ...taus)] }, taus, [PLOT.left, PLOT.right]);
  const y = scale({ label: 'ρ', domain: [0, 1] }, [], [PLOT.bottom, PLOT.top]);
  const unsolved = d.series.filter((s) => !s.reachedAll).map((s) => `${s.label} ${pct(1 - s.finalRho)}`);
  return (
    <Figure title={title} label={`性能剖面，${d.series.length} 个算法，${d.n} 个完整实例`}
      facts={[
        `样本：n = ${d.n} 个所有算法均有运行的实例${d.absent.length ? `；曲线缺失：${d.absent.join('、')}` : ''}`,
        `未达到（τ 最大处仍未覆盖，含失败/超时/已隔离）：${unsolved.length ? unsolved.join('；') : '无'}`,
        '参照：每个实例取所有算法平均成本的最小值，τ = 成本 / 该最小值（无量纲）',
        `方向：ρ(τ) 越高、越靠左上越好${d.note ? `。${d.note}` : ''}`,
      ]}
      legend={<Legend items={d.series.map((s, i) => ({ label: s.label, style: styleFor(i), note: `τmax 处 ρ = ${pct(s.finalRho)}` }))} />}>
      <Axes x={x} y={y} xLabel="τ（成本 / 最好成本）" yLabel="ρ(τ)" yFormat={pct} />
      {d.series.map((s, i) => {
        const st = styleFor(i);
        const every = Math.max(1, Math.floor(s.points.length / 8));
        return (
          <g key={s.algorithm} data-series={s.algorithm}>
            <path d={stepPath(s.points, x.map, y.map)} fill="none" stroke={st.color} strokeWidth={1.8} strokeDasharray={st.dash || undefined} />
            {s.points.filter((_, k) => k % every === 0).map(([t, r]) => <Marker key={t} kind={st.marker} x={x.map(t)} y={y.map(r)} color={st.color} />)}
          </g>
        );
      })}
    </Figure>
  );
}

function EcdfFigure({ title, d, xLabel, log, xFormat, facts }: { title: string; d: ReturnType<typeof gapEcdfData>; xLabel: string; log?: boolean; xFormat?: (v: number) => string; facts: string[] }) {
  if (!d.ok) return <NoData title={title} reason={d.reason} />;
  const xs = d.series.flatMap((s) => s.points.map(([v]) => v));
  if (!xs.length) return <NoData title={title} reason="所有运行都未达到（没有可绘制的数值）" />;
  const x = scale({ label: xLabel, log }, xs, [PLOT.left, PLOT.right]);
  const y = scale({ label: '', domain: [0, 1] }, [], [PLOT.bottom, PLOT.top]);
  return (
    <Figure title={title} label={`${title}，${d.series.length} 个算法`}
      facts={[`样本：${d.series.map((s) => `${s.label} n = ${s.total}，达到 ${s.reached}，未达到 ${s.notReached}（${s.detail}）`).join('；')}`,
        ...(d.excludedQuarantined ? [`已隔离运行 ${d.excludedQuarantined} 次未计入数值`] : []), ...facts]}
      legend={<Legend items={d.series.map((s, i) => ({ label: s.label, style: styleFor(i), note: `未达到 ${s.notReached}/${s.total}` }))} />}>
      <Axes x={x} y={y} xLabel={xLabel} yLabel="占全部运行的比例" yFormat={pct} xFormat={xFormat} />
      {d.series.map((s, i) => {
        const st = styleFor(i);
        const pts: [number, number][] = s.points.length ? [[s.points[0]![0], 0], ...s.points, [x.hi, s.points[s.points.length - 1]![1]]] : [];
        return (
          <g key={s.algorithm} data-series={s.algorithm}>
            <path d={stepPath(pts, x.map, y.map)} fill="none" stroke={st.color} strokeWidth={1.8} strokeDasharray={st.dash || undefined} />
            {s.points.filter((_, k) => k % Math.max(1, Math.floor(s.points.length / 8)) === 0).map(([v, r]) => <Marker key={`${v}-${r}`} kind={st.marker} x={x.map(v)} y={y.map(r)} color={st.color} />)}
          </g>
        );
      })}
    </Figure>
  );
}

export function EcdfCharts({ analysis, quarantined }: { analysis: Analysis | null; quarantined?: ReadonlySet<string> }) {
  return (
    <>
      <EcdfFigure title="ECDF：相对参照的差距" d={gapEcdfData(analysis)} xLabel="差距 (%)" xFormat={(v) => `${fmtTick(v)}%`}
        facts={[`参照：${analysis?.reference_note ?? '未提供'}`, '单位：%；方向：曲线越靠左上越好；失败/超时视为未达到，曲线不升到 100%']} />
      <EcdfFigure title="ECDF：成功运行的用时" d={runtimeEcdfData(analysis, quarantined)} xLabel="用时（墙钟，对数）" log xFormat={fmtTime}
        facts={['数据：Analysis.scatter 的 time（metrics.wall_s）；仅有差距值的成功运行计为达到', '单位：秒；方向：越靠左上越快；用时受主机条件影响']} />
    </>
  );
}

export function AnytimeChart({ analysis }: { analysis: Analysis | null }) {
  const title = '条件质量曲线：成功且有合法目标遥测的 incumbent 差距中位数';
  const d = anytimeData(analysis);
  const reasonNames: Record<string, string> = { observed_no_solution: '明确观测预算内无解', failed_or_interrupted: '失败或中断',
    successful_without_telemetry: '成功但无遥测', proxy_or_unknown_semantics: '代理或未知轨迹语义', invalid_trace: '不合法轨迹', missing_reference: '缺少正参照' };
  const counts = analysis?.anytime.counts;
  const sampleFacts = counts ? Object.entries(counts).map(([a, c]) => {
    const observed = analysis?.anytime.observed_counts?.[a] ?? [];
    return `${a}：有效轨迹 ${c.valid_trace_runs} / 终态 ${c.terminal_runs}；全终态成功率 ${c.terminal_successes}/${c.terminal_runs}；逐时刻观测分母 ${observed.length ? `${Math.min(...observed)}–${Math.max(...observed)}` : '0'}；排除：${Object.entries(c.reasons).filter(([, n]) => n > 0).map(([reason, n]) => `${reasonNames[reason] ?? reason} ${n}`).join('、') || '无'}`;
  }) : ['轨迹实际分母及排除原因未提供（旧版响应）；不能以全部运行数代替'];
  if (!d.ok) return <><NoData title={title} reason={d.reason} /><div className={p.meta}>{sampleFacts.map((fact) => <div key={fact}>{fact}</div>)}</div></>;
  const pts = d.series.flatMap((s) => s.points);
  const ys = pts.map(([, v]) => v).filter((v): v is number => v != null && Number.isFinite(v));
  if (!ys.length) return <><NoData title={title} reason="未提供可绘制的已观测质量值；不能由空值推断无解比例" /><div className={p.meta}>{sampleFacts.map((fact) => <div key={fact}>{fact}</div>)}</div></>;
  const x = scale({ label: '', log: true }, pts.map(([t]) => t), [PLOT.left, PLOT.right]);
  const y = scale({ label: '' }, ys, [PLOT.bottom, PLOT.top]);
  return (
    <Figure title={title} label={`收敛中位数，${d.series.length} 个算法`}
      facts={[...sampleFacts,
        '区间：数据未提供（Analysis.anytime 只含中位数；跨种子区间见“方案对比”中的比较组图）',
        `参照：${analysis!.reference_note}`, `单位：%；方向：越低越好。${d.note ?? ''}`]}
      legend={<Legend items={d.series.map((s, i) => ({ label: s.label, style: styleFor(i) }))} />}>
      <Axes x={x} y={y} xLabel="时间（对数）" yLabel="差距 (%)" xFormat={fmtTime} />
      {d.series.map((s, i) => {
        const st = styleFor(i);
        return (
          <g key={s.algorithm} data-series={s.algorithm} data-observed-counts={analysis?.anytime.observed_counts?.[s.algorithm]?.join(',')}>
            <path d={stepPath(s.points, x.map, y.map)} fill="none" stroke={st.color} strokeWidth={1.8} strokeDasharray={st.dash || undefined} />
            {s.points.map(([t, v], k) => v != null && <g key={t}><title>{`${s.label} · ${fmtTime(t)} · 观测分母 n=${analysis?.anytime.observed_counts?.[s.algorithm]?.[k] ?? '未知'}`}</title><Marker kind={st.marker} x={x.map(t)} y={y.map(v)} color={st.color} size={k % 6 === 0 ? 3.5 : 1.5} /></g>)}
          </g>
        );
      })}
    </Figure>
  );
}

export function GapTimeChart({ analysis, quarantined }: { analysis: Analysis | null; quarantined?: ReadonlySet<string> }) {
  const title = '差距 vs 用时（每次成功运行）';
  const d = gapTimeData(analysis, quarantined);
  if (!d.ok) return <NoData title={title} reason={d.reason} />;
  const x = scale({ label: '', log: true }, d.points.map((p) => p.time), [PLOT.left, PLOT.right]);
  const y = scale({ label: '' }, d.points.map((p) => p.gap), [PLOT.bottom, PLOT.top]);
  const idx = new Map(d.algorithms.map((a, i) => [a.algorithm, i]));
  return (
    <Figure title={title} label={`差距与用时散点，${d.points.length} 个点`}
      facts={[`样本：${d.algorithms.map((a) => `${a.label} 绘制 ${a.plotted}/${a.total}（失败/无差距 ${a.failed}，用时缺失 ${a.noTime}）`).join('；')}`,
        ...(d.excludedQuarantined ? [`已隔离运行 ${d.excludedQuarantined} 次未绘制`] : []),
        `参照：${analysis!.reference_note}`,
        `预算：${d.budgets.length ? d.budgets.map((b) => `${b} s`).join('、') : '数据未提供（Analysis.scatter 不含 time_limit_s）'}；主机：数据未提供（Analysis 不记录主机），用时受主机条件影响`,
        '单位：差距 %，用时为墙钟秒（对数轴）；方向：越靠左下越好']}
      legend={<Legend items={d.algorithms.map((a, i) => ({ label: a.label, style: styleFor(i), note: `n = ${a.plotted}` }))} />}>
      <Axes x={x} y={y} xLabel="用时（墙钟，对数）" yLabel="差距 (%)" xFormat={fmtTime} />
      {d.points.map((p) => { const st = styleFor(idx.get(p.algorithm) ?? 0); return <g key={p.run_id} data-point={p.algorithm}><title>{`${p.label} · ${p.run_id} · ${p.gap.toFixed(2)}% · ${fmtTime(p.time)}`}</title><Marker kind={st.marker} x={x.map(p.time)} y={y.map(p.gap)} color={st.color} /></g>; })}
    </Figure>
  );
}

export function CdChart({ analysis }: { analysis: Analysis | null }) {
  const title = '临界差图（Demšar 2006，Nemenyi）';
  const d = cdData(analysis);
  if (!d.ok) return <NoData title={title} reason={d.reason} />;
  const W = 640, k = d.k, half = Math.ceil(k / 2);
  const x = (r: number) => 60 + ((r - 1) / Math.max(1, k - 1)) * (W - 120);
  const H = 70 + 22 * half + 6 * d.cliques.length;
  const f = d.friedman;
  return (
    <figure className={p.chart} style={{ margin: 0 }}>
      <h4>{title}</h4>
      <svg role="img" aria-label={`临界差图，${k} 个算法，CD = ${d.cd.toFixed(2)}`} width="100%" viewBox={`0 0 ${W} ${H}`} style={{ font: '11px Inter, PingFang SC, sans-serif' }}>
        <line x1={x(1)} x2={x(k)} y1={30} y2={30} stroke="#333" />
        {Array.from({ length: k }, (_, i) => <g key={i}><line x1={x(i + 1)} x2={x(i + 1)} y1={26} y2={34} stroke="#333" /><text x={x(i + 1)} y={20} textAnchor="middle">{i + 1}</text></g>)}
        <line x1={x(1)} x2={x(1 + d.cd)} y1={8} y2={8} stroke="#1d1d1f" strokeWidth={2} />
        <text x={x(1 + d.cd) + 6} y={11}>CD = {d.cd.toFixed(2)}</text>
        {d.items.map((it) => {
          const y = 50 + 22 * it.row; const left = it.side === 'left';
          return <g key={it.algorithm} data-cd={it.algorithm}><polyline points={`${x(it.rank)},30 ${x(it.rank)},${y} ${left ? 40 : W - 40},${y}`} fill="none" stroke="#555" />
            <text x={left ? 36 : W - 36} y={y + 4} textAnchor={left ? 'end' : 'start'}>{it.label}（{it.rank.toFixed(2)}）</text></g>;
        })}
        {d.cliques.map(([i, j], g) => <line key={g} data-clique="" x1={x(d.items[i]!.rank) - 3} x2={x(d.items[j]!.rank) + 3} y1={38 + 5 * g} y2={38 + 5 * g} stroke="#1d1d1f" strokeWidth={3} strokeLinecap="round" />)}
      </svg>
      <figcaption className={p.meta}>
        <div>样本：N = {d.n} 个完整实例，k = {k} 个算法，α = {d.alpha}；平均秩越小越好（1 = 最好），粗横线连接的算法之间差异不显著（|Δ秩| ≤ CD）</div>
        <div>Friedman：{f?.statistic != null ? `χ² = ${f.statistic.toFixed(2)}，p = ${f.p_value?.toExponential(2) ?? '未提供'}（${f.significant ? '显著' : '不显著，CD 仅供参考'}）` : `数据未提供${f?.error ? `：${f.error}` : ''}`}</div>
        <div>Wilcoxon–Holm 显著对：{d.holmSignificant.length ? d.holmSignificant.join('；') : '无'}</div>
        <div>参照：秩按每个实例的平均目标值计算，失败运行按最差处理（{analysis!.reference_note}）</div>
      </figcaption>
    </figure>
  );
}
