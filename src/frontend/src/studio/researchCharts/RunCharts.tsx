/** Research figures over the runs loaded in the comparison panel. */
import type { Solution } from '../../lab/types';
import { seedBandData, STATUS_LABEL, STATUS_ORDER, statusCounts, verificationData, type StatusCategory } from './runData';
import p from '../panels/panels.module.css';
import { Axes, Figure, fmtTime, Legend, Marker, NoData, PLOT, scale, stepPath, styleFor } from './SvgChart';

export function SeedBandChart({ runs }: { runs: Solution[] }) {
  const title = '收敛：跨种子中位数与四分位区间（当前比较组）';
  const d = seedBandData(runs);
  if (!d.ok) return <NoData title={title} reason={d.reason} />;
  const all = d.groups.flatMap((g) => g.band);
  const ys = all.flatMap((b) => [b.lo, b.hi, b.median]).filter((v): v is number => v != null);
  if (!ys.length) return <NoData title={title} reason="任何时刻都不到半数运行已有解，中位数未定义" />;
  const x = scale({ label: '', log: true }, all.map((b) => b.t), [PLOT.left, PLOT.right]);
  const y = scale({ label: '' }, ys, [PLOT.bottom, PLOT.top]);
  const objective = runs.find((r) => r.planning?.objective_label)?.planning?.objective_label;
  return (
    <Figure title={title} label={`${d.groups.length} 组收敛中位数与区间`}
      facts={[`样本：${d.groups.map((g) => `${g.label} n = ${g.runs}（种子 ${g.seeds.join('、')}）`).join('；')}`,
        `未计入：已隔离 ${d.excluded.quarantined}，未成功/未验算 ${d.excluded.notSuccessful}，无与目标一致的轨迹 ${d.excluded.noTrace}`,
        '参照：各运行“当前最好目标值”原值（不换算差距）；阴影为 25%–75% 分位，单次运行无区间',
        `单位：${d.unit}；方向：${objective ?? '目标定义未提供'}；不到半数运行有解的时刻不画中位数`]}
      legend={<Legend items={d.groups.map((g, i) => ({ label: g.label, style: styleFor(i), note: `n = ${g.runs}` }))} />}>
      <Axes x={x} y={y} xLabel="时间（对数）" yLabel={`目标值 (${d.unit})`} xFormat={fmtTime} />
      {d.groups.map((g, i) => {
        const st = styleFor(i);
        const segs = g.band.filter((b) => b.lo != null && b.hi != null);
        const area = segs.length > 1 && g.runs > 1
          ? `M${segs.map((b) => `${x.map(b.t).toFixed(2)},${y.map(b.hi!).toFixed(2)}`).join('L')}L${[...segs].reverse().map((b) => `${x.map(b.t).toFixed(2)},${y.map(b.lo!).toFixed(2)}`).join('L')}Z` : '';
        return (
          <g key={g.key} data-series={g.key}>
            {area && <path d={area} fill={st.color} fillOpacity={0.15} stroke={st.color} strokeOpacity={0.4} strokeDasharray="2 2" data-band="" />}
            <path d={stepPath(g.band.map((b) => [b.t, b.median]), x.map, y.map)} fill="none" stroke={st.color} strokeWidth={1.8} strokeDasharray={st.dash || undefined} />
            {g.band.filter((b, k) => b.median != null && k % 6 === 0).map((b) => <Marker key={b.t} kind={st.marker} x={x.map(b.t)} y={y.map(b.median!)} color={st.color} />)}
          </g>
        );
      })}
    </Figure>
  );
}

export function VerificationChart({ runs }: { runs: Solution[] }) {
  const title = '独立验算一致性：求解器报告目标 vs 独立重算';
  const d = verificationData(runs);
  if (!d.ok) return <NoData title={title} reason={d.reason} />;
  const vals = d.points.flatMap((q) => [q.reported, q.recomputed]);
  const x = scale({ label: '' }, vals, [PLOT.left, PLOT.right]);
  const y = scale({ label: '', domain: [x.lo, x.hi] }, vals, [PLOT.bottom, PLOT.top]);
  const algs = [...new Set(d.points.map((q) => q.label))];
  const c = d.counts;
  return (
    <Figure title={title} label={`验算一致性散点，${d.points.length} 次运行`}
      facts={[`样本：n = ${d.points.length}/${c.total}；未计入：已隔离 ${c.quarantined}，未完成 ${c.notDone}，无独立验算值 ${c.noCheck}，无报告值 ${c.noReported}，单位不同 ${c.otherUnit}`,
        `最大相对偏差 ${(d.maxRelDiff * 100).toPrecision(3)}%；空心标记 = 验算未通过；检查器：${[...new Set(d.points.map((q) => q.checker))].join('、')}`,
        '参照：对角线 y = x（完全一致）；数据为 objective.value 与 check.computed_cost 原值',
        `单位：${d.unit}；方向：越贴近对角线越一致`]}
      legend={<Legend items={[...algs.map((a, i) => ({ label: a, style: styleFor(i), note: '验算通过' })), ...(d.points.some((q) => !q.valid) ? [{ label: '验算未通过', style: { ...styleFor(0), color: '#555' }, hollow: true }] : [])]} />}>
      <Axes x={x} y={y} xLabel={`求解器报告目标 (${d.unit})`} yLabel={`独立重算 (${d.unit})`} />
      <line x1={x.map(x.lo)} y1={y.map(x.lo)} x2={x.map(x.hi)} y2={y.map(x.hi)} stroke="#8e8e93" strokeDasharray="4 3" data-diagonal="" />
      {d.points.map((q) => { const st = styleFor(algs.indexOf(q.label)); return <g key={q.run_id} data-point={q.valid ? 'valid' : 'invalid'}><title>{`${q.label} · ${q.run_id} · 报告 ${q.reported} · 重算 ${q.recomputed}`}</title><Marker kind={st.marker} x={x.map(q.reported)} y={y.map(q.recomputed)} color={st.color} hollow={!q.valid} size={4} /></g>; })}
    </Figure>
  );
}

const HATCH: Record<StatusCategory, { color: string; path: string }> = {
  success: { color: '#30b158', path: '' }, unverified: { color: '#8e8e93', path: 'M0,4 L4,0' }, quarantined: { color: '#ff453a', path: 'M0,0 L4,4 M0,4 L4,0' },
  infeasible: { color: '#bf5af2', path: 'M0,2 L4,2' }, unchecked: { color: '#ff9f0a', path: 'M2,0 L2,4' }, timeout: { color: '#a2845e', path: 'M0,0 L4,4' },
  canceled: { color: '#64748b', path: 'M0,2 L4,2 M2,0 L2,4' }, error: { color: '#d70015', path: 'M1,1 h2 v2 h-2 z' }, pending: { color: '#0a84ff', path: 'M0,4 L4,0 M-1,1 L1,-1 M3,5 L5,3' },
};

/** Sankey-like flow from all runs to outcome categories; each band has its own hatch pattern and a text label. */
export function StatusFlowChart({ runs }: { runs: Solution[] }) {
  const title = '运行状态流向';
  if (!runs.length) return <NoData title={title} reason="当前批次没有运行记录" />;
  const s = statusCounts(runs);
  const H = 24 + 26 * s.categories.length; const top = 12; const span = H - 2 * top; const gap = 4;
  const usable = span - gap * (s.categories.length - 1);
  let yl = top, yr = top;
  const bands = s.categories.map((c) => {
    const h = Math.max(2, (s.counts[c] / s.total) * usable);
    const b = { c, l0: yl, l1: yl + (s.counts[c] / s.total) * span, r0: yr, r1: yr + h };
    yl = b.l1; yr = b.r1 + gap; return b;
  });
  return (
    <figure className={p.chart} style={{ margin: 0 }}>
      <h4>{title}</h4>
      <svg role="img" aria-label={`${s.total} 次运行的状态分布：${s.categories.map((c) => `${STATUS_LABEL[c]} ${s.counts[c]}`).join('，')}`} width="100%" viewBox={`0 0 640 ${H}`} style={{ font: '11px Inter, PingFang SC, sans-serif' }}>
        <defs>{STATUS_ORDER.map((c) => <pattern key={c} id={`st-${c}`} width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="4" fill={HATCH[c].color} fillOpacity={0.35} />{HATCH[c].path && <path d={HATCH[c].path} stroke={HATCH[c].color} strokeWidth={1} />}</pattern>)}</defs>
        <rect x={40} y={top} width={18} height={span} fill="#d1d1d6" stroke="#555" />
        <text x={36} y={top + span / 2 + 4} textAnchor="end">全部 {s.total}</text>
        {bands.map((b) => (
          <g key={b.c} data-status={b.c}>
            <path d={`M58,${b.l0} C200,${b.l0} 200,${b.r0} 330,${b.r0} L330,${b.r1} C200,${b.r1} 200,${b.l1} 58,${b.l1} Z`} fill={`url(#st-${b.c})`} stroke={HATCH[b.c].color} strokeOpacity={0.6} />
            <rect x={330} y={b.r0} width={18} height={b.r1 - b.r0} fill={`url(#st-${b.c})`} stroke={HATCH[b.c].color} />
            <text x={356} y={(b.r0 + b.r1) / 2 + 4}>{STATUS_LABEL[b.c]} · {s.counts[b.c]}（{((s.counts[b.c] / s.total) * 100).toFixed(0)}%）</text>
          </g>
        ))}
      </svg>
      <figcaption className={p.meta}>
        <div>样本：n = {s.total} 次运行；身份字段缺失（source_identity_verified 未记录）共 {s.identityUnknown} 次，其中有解的单列为“有解 · 身份未核验”</div>
        <div>只有“成功”类进入排名与比较；已隔离运行不提供任何数值</div>
        <div>参照：状态取自运行记录 state/status/check 与源码身份核对；每类有独立纹理与文字标签</div>
      </figcaption>
    </figure>
  );
}
