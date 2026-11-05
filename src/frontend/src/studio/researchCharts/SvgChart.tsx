/**
 * Small dependency-free SVG chart primitives for research figures. Every series has a colour, a marker
 * shape and a dash pattern, so no figure relies on colour alone; legends are built from the same styles.
 */
import type { ReactNode } from 'react';
import { formatSeconds } from '../chart';
import p from '../panels/panels.module.css';

export const PALETTE = ['#0a84ff', '#ff9f0a', '#30b158', '#bf5af2', '#ff453a', '#14b8c4', '#a2845e', '#5e5ce6', '#ff6482', '#64748b'];
export const MARKERS = ['circle', 'square', 'triangle', 'diamond', 'cross', 'plus', 'tri-down', 'star'] as const;
export type MarkerKind = typeof MARKERS[number];
const DASHES = ['', '6 3', '2 3', '8 3 2 3', '10 4', '4 2 1 2', '1 3', '12 3 3 3'];

export interface SeriesStyle { color: string; marker: MarkerKind; dash: string }
export const styleFor = (i: number): SeriesStyle => ({ color: PALETTE[i % PALETTE.length]!, marker: MARKERS[i % MARKERS.length]!, dash: DASHES[i % DASHES.length]! });

export function Marker({ kind, x, y, color, size = 3.5, hollow = false }: { kind: MarkerKind; x: number; y: number; color: string; size?: number; hollow?: boolean }) {
  const fill = hollow ? 'white' : color;
  const common = { stroke: color, strokeWidth: 1.2, fill };
  const s = size;
  switch (kind) {
    case 'circle': return <circle cx={x} cy={y} r={s} {...common} />;
    case 'square': return <rect x={x - s} y={y - s} width={2 * s} height={2 * s} {...common} />;
    case 'triangle': return <polygon points={`${x},${y - s * 1.2} ${x + s * 1.1},${y + s * 0.8} ${x - s * 1.1},${y + s * 0.8}`} {...common} />;
    case 'tri-down': return <polygon points={`${x},${y + s * 1.2} ${x + s * 1.1},${y - s * 0.8} ${x - s * 1.1},${y - s * 0.8}`} {...common} />;
    case 'diamond': return <polygon points={`${x},${y - s * 1.3} ${x + s},${y} ${x},${y + s * 1.3} ${x - s},${y}`} {...common} />;
    case 'cross': return <path d={`M${x - s},${y - s}L${x + s},${y + s}M${x - s},${y + s}L${x + s},${y - s}`} stroke={color} strokeWidth={1.8} />;
    case 'plus': return <path d={`M${x - s},${y}L${x + s},${y}M${x},${y - s}L${x},${y + s}`} stroke={color} strokeWidth={1.8} />;
    default: return <polygon points={star(x, y, s * 1.3)} {...common} />;
  }
}
const star = (x: number, y: number, r: number) => Array.from({ length: 10 }, (_, i) => {
  const a = -Math.PI / 2 + (i * Math.PI) / 5; const rr = i % 2 ? r * 0.45 : r;
  return `${(x + rr * Math.cos(a)).toFixed(2)},${(y + rr * Math.sin(a)).toFixed(2)}`;
}).join(' ');

export interface Axis { label: string; log?: boolean; domain?: [number, number]; format?: (v: number) => string }

export function scale(axis: Axis, values: number[], range: [number, number]) {
  const finite = values.filter((v) => Number.isFinite(v) && (!axis.log || v > 0));
  let [lo, hi] = axis.domain ?? [Math.min(...finite), Math.max(...finite)];
  if (!finite.length && !axis.domain) [lo, hi] = axis.log ? [1, 10] : [0, 1];
  if (axis.log) { lo = 10 ** Math.floor(Math.log10(lo)); hi = 10 ** Math.ceil(Math.log10(hi)); if (lo === hi) hi = lo * 10; }
  else if (lo === hi) { lo -= 1; hi += 1; }
  const f = axis.log ? Math.log10 : (v: number) => v;
  const [a, b] = [f(lo), f(hi)];
  const map = (v: number) => range[0] + ((f(v) - a) / (b - a)) * (range[1] - range[0]);
  const ticks: number[] = [];
  if (axis.log) for (let k = Math.round(a); k <= Math.round(b); k++) ticks.push(10 ** k);
  else {
    const raw = (hi - lo) / 5; const mag = 10 ** Math.floor(Math.log10(raw)); const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw)!;
    for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) ticks.push(+t.toPrecision(12));
  }
  return { map, ticks, lo, hi };
}

export const fmtTick = (v: number) => (Math.abs(v) >= 1e4 || (v !== 0 && Math.abs(v) < 1e-2) ? v.toExponential(0) : String(+v.toPrecision(4)));
export const fmtTime = formatSeconds;

/** Figure wrapper: title, the mandatory facts (n, failures, reference, units/direction) and an accessible SVG. */
export function Figure({ title, facts, children, legend, label }: { title: string; facts: string[]; children: ReactNode; legend?: ReactNode; label: string }) {
  return (
    <figure className={p.chart} style={{ margin: 0 }}>
      <h4>{title}</h4>
      {children && <svg role="img" aria-label={label} width="100%" viewBox="0 0 640 260" style={{ font: '11px Inter, PingFang SC, sans-serif' }}>{children}</svg>}
      {legend}
      <figcaption className={p.meta} data-facts="">{facts.map((f) => <div key={f}>{f}</div>)}</figcaption>
    </figure>
  );
}

/** Explicit empty state: names the missing field instead of drawing placeholder data. */
export function NoData({ title, reason }: { title: string; reason: string }) {
  return (
    <figure className={p.chart} style={{ margin: 0 }} data-empty="">
      <h4>{title}</h4>
      <p className={p.hint}>数据未提供：{reason}</p>
    </figure>
  );
}

export interface LegendItem { label: string; style: SeriesStyle; note?: string; hollow?: boolean }
export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul aria-label="图例" style={{ listStyle: 'none', display: 'flex', flexWrap: 'wrap', gap: '4px 14px', margin: '2px 0', padding: 0, font: '11px Inter, PingFang SC, sans-serif' }}>
      {items.map((it) => (
        <li key={it.label} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <svg width="30" height="12" aria-hidden="true">
            <line x1={1} x2={29} y1={6} y2={6} stroke={it.style.color} strokeWidth={1.8} strokeDasharray={it.style.dash || undefined} />
            <Marker kind={it.style.marker} x={15} y={6} color={it.style.color} size={3} hollow={it.hollow} />
          </svg>
          <span>{it.label}{it.note ? `（${it.note}）` : ''}</span>
        </li>
      ))}
    </ul>
  );
}

export const PLOT = { left: 56, right: 624, top: 14, bottom: 222 };

/** Axes with ticks, grid and labels. */
export function Axes({ x, y, xLabel, yLabel, xFormat = fmtTick, yFormat = fmtTick }: {
  x: ReturnType<typeof scale>; y: ReturnType<typeof scale>; xLabel: string; yLabel: string; xFormat?: (v: number) => string; yFormat?: (v: number) => string;
}) {
  return (
    <g>
      {x.ticks.map((t) => <g key={`x${t}`}><line x1={x.map(t)} x2={x.map(t)} y1={PLOT.top} y2={PLOT.bottom} stroke="#e5e5ea" /><text x={x.map(t)} y={PLOT.bottom + 13} textAnchor="middle">{xFormat(t)}</text></g>)}
      {y.ticks.map((t) => <g key={`y${t}`}><line x1={PLOT.left} x2={PLOT.right} y1={y.map(t)} y2={y.map(t)} stroke="#e5e5ea" /><text x={PLOT.left - 4} y={y.map(t) + 3} textAnchor="end">{yFormat(t)}</text></g>)}
      <rect x={PLOT.left} y={PLOT.top} width={PLOT.right - PLOT.left} height={PLOT.bottom - PLOT.top} fill="none" stroke="#8e8e93" />
      <text x={(PLOT.left + PLOT.right) / 2} y={252} textAnchor="middle">{xLabel}</text>
      <text transform={`translate(12 ${(PLOT.top + PLOT.bottom) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
    </g>
  );
}

/** Step path through points; null y breaks the line (never drawn as 0). */
export function stepPath(points: [number, number | null][], x: (v: number) => number, y: (v: number) => number): string {
  let d = ''; let prev: [number, number] | null = null;
  for (const [px, py] of points) {
    if (py == null || !Number.isFinite(py)) { prev = null; continue; }
    const X = x(px), Y = y(py);
    d += prev ? `H${X.toFixed(2)}V${Y.toFixed(2)}` : `M${X.toFixed(2)},${Y.toFixed(2)}`;
    prev = [X, Y];
  }
  return d;
}
