import type { ReactNode } from 'react';
import type { FleetInstance, FleetRoute, ObstacleKind } from '../types';
import { depotsOf } from '../fleet';
import { OBSTACLE_STYLE } from './geometry';
import s from './editor.module.css';

export type LayerKey = 'grid' | 'background' | 'plots' | 'lanes' | 'corridors' | 'obstacles' | 'routes' | 'tasks' | 'depots' | 'labels' | 'issues';
export const LAYERS: { key: LayerKey; label: string }[] = [
  { key: 'grid', label: '网格' },
  { key: 'background', label: '底图' },
  { key: 'plots', label: '田块' },
  { key: 'lanes', label: '行间通道' },
  { key: 'corridors', label: '机耕道' },
  { key: 'obstacles', label: '障碍' },
  { key: 'routes', label: '路线' },
  { key: 'tasks', label: '任务点' },
  { key: 'depots', label: '车库/补给点' },
  { key: 'labels', label: '标注' },
  { key: 'issues', label: '几何问题' },
];
export type Layers = Record<LayerKey, boolean>;
export const ALL_LAYERS: Layers = Object.fromEntries(LAYERS.map((l) => [l.key, true])) as Layers;

/** Line dash per vehicle so routes differ by more than colour. */
export const ROUTE_DASH = ['', '9 4', '2 4', '12 4 2 4', '5 5'];
export const routeDash = (i: number) => ROUTE_DASH[i % ROUTE_DASH.length] || undefined;

/** Per-kind hatch so obstacle kinds are distinguishable without colour. `u` = pattern tile size in user units. */
export function KindPattern({ id, kind, u }: { id: string; kind: ObstacleKind; u: number }) {
  const st = OBSTACLE_STYLE[kind];
  const sw = u / 10;
  const line = { stroke: st.stroke, strokeWidth: sw, fill: 'none' };
  let body: ReactNode = null;
  if (kind === 'pond') body = <path d={`M0 ${u * 0.5} Q ${u * 0.25} ${u * 0.25} ${u * 0.5} ${u * 0.5} T ${u} ${u * 0.5}`} {...line} />;
  else if (kind === 'building') body = <path d={`M0 0 L${u} ${u} M${u} 0 L0 ${u}`} {...line} />;
  else if (kind === 'greenhouse') body = <path d={`M0 ${u / 2} H${u} M${u / 2} 0 V${u}`} {...line} />;
  else if (kind === 'tree') body = <circle cx={u / 2} cy={u / 2} r={u * 0.18} fill={st.stroke} />;
  else if (kind === 'ditch') body = <path d={`M0 ${u} L${u} 0`} {...line} />;
  else if (kind === 'rock') body = <path d={`M${u * 0.3} ${u * 0.3} l${u * 0.4} ${u * 0.4} M${u * 0.7} ${u * 0.3} l${-u * 0.4} ${u * 0.4}`} {...line} />;
  return (
    <pattern id={id} width={u} height={u} patternUnits="userSpaceOnUse">
      <rect width={u} height={u} fill={st.fill} />
      {body}
    </pattern>
  );
}

const SW = 26;
function Swatch({ children }: { children: ReactNode }) {
  return <svg width={SW} height={14} viewBox={`0 0 ${SW} 14`} aria-hidden="true">{children}</svg>;
}

export interface LegendEntry { key: string; label: string; swatch: ReactNode }

/** Legend rows for exactly what the map is drawing now (data present and layer on). */
export function legendEntries(inst: FleetInstance, routes: FleetRoute[], layers: Layers, opts: { lanes: boolean; badTasks: number; violations: number; geoIssues?: number; idp: string; routeIndex: (r: FleetRoute) => number }): LegendEntry[] {
  const out: LegendEntry[] = [];
  out.push({ key: 'boundary', label: '场地边界', swatch: <Swatch><rect x={1} y={1} width={SW - 2} height={12} fill="#f6f3e9" stroke="#8b7d5c" strokeWidth={2} /></Swatch> });
  if (layers.plots && inst.plots.length) out.push({ key: 'plot', label: `田块（斜线为作物行向）×${inst.plots.length}`, swatch: <Swatch><rect x={1} y={1} width={SW - 2} height={12} fill="rgba(126,190,102,0.22)" stroke="#6aa05a" /><path d="M4 12 L10 2 M10 12 L16 2 M16 12 L22 2" stroke="#548e40" /></Swatch> });
  if (layers.lanes && opts.lanes) out.push({ key: 'lanes', label: '行间通道（虚线）', swatch: <Swatch><line x1={1} x2={SW - 1} y1={7} y2={7} stroke="#96762e" strokeDasharray="5 3" /></Swatch> });
  if (layers.corridors && inst.corridors?.length) out.push({ key: 'corridor', label: `机耕道（虚边）×${inst.corridors.length}`, swatch: <Swatch><rect x={1} y={1} width={SW - 2} height={12} fill="rgba(194,157,74,0.28)" stroke="#9a7b3c" strokeDasharray="7 3" /></Swatch> });
  if (layers.obstacles) {
    const kinds = [...new Set(inst.obstacles.map((o) => o.kind))];
    for (const k of kinds) {
      const n = inst.obstacles.filter((o) => o.kind === k).length;
      out.push({ key: `ob-${k}`, label: `${OBSTACLE_STYLE[k].label} ×${n}`, swatch: <Swatch><defs><KindPattern id={`${opts.idp}-lg-${k}`} kind={k} u={7} /></defs><rect x={1} y={1} width={SW - 2} height={12} fill={`url(#${opts.idp}-lg-${k})`} stroke={OBSTACLE_STYLE[k].stroke} /></Swatch> });
    }
  }
  if (layers.routes) for (const r of routes) {
    out.push({ key: `route-${r.vehicle_name}`, label: `路线 ${r.vehicle_name}（箭头=方向）`, swatch: <Swatch><line x1={1} x2={SW - 1} y1={7} y2={7} stroke={r.color} strokeWidth={2.6} strokeDasharray={routeDash(opts.routeIndex(r))} /><path d="M15 3.5 L22 7 L15 10.5 Z" fill={r.color} /></Swatch> });
  }
  if (layers.tasks && inst.tasks.length) {
    out.push({ key: 'task', label: `任务点（数字=服务顺序）×${inst.tasks.length}`, swatch: <Swatch><circle cx={13} cy={7} r={5} fill="#fff" stroke="#1d1d1f" strokeWidth={2} /></Swatch> });
    if (opts.badTasks) out.push({ key: 'taskBad', label: `不可达任务点（虚圈 + !）×${opts.badTasks}`, swatch: <Swatch><circle cx={13} cy={7} r={5} fill="#ffe5e3" stroke="#e5352b" strokeWidth={2} strokeDasharray="2 2" /><text x={20} y={11} fontSize={10} fill="#e5352b">!</text></Swatch> });
  }
  if (layers.depots) {
    out.push({ key: 'depot', label: `车库（方块）×${depotsOf(inst).length}`, swatch: <Swatch><rect x={7} y={1} width={12} height={12} rx={3} fill="#1d1d1f" /></Swatch> });
    if (inst.unload_points?.length) out.push({ key: 'unload', label: `补给/卸载点（菱形）×${inst.unload_points.length}`, swatch: <Swatch><path d="M13 1 L19 7 L13 13 L7 7 Z" fill="#555" /></Swatch> });
  }
  if (layers.issues && opts.violations) out.push({ key: 'viol', label: `求解器报告的违规 ×${opts.violations}`, swatch: <Swatch><circle cx={13} cy={7} r={5.5} fill="rgba(229,53,43,0.18)" stroke="#e5352b" strokeWidth={2} /></Swatch> });
  if (layers.issues && opts.geoIssues) out.push({ key: 'geo', label: `几何问题（三角 ×=错误 !=警告）×${opts.geoIssues}`, swatch: <Swatch><path d="M13 1 L20 13 L6 13 Z" fill="#f0b93a" stroke="#1d1d1f" /><text x={13} y={12} fontSize={8} textAnchor="middle" fill="#fff">!</text></Swatch> });
  return out;
}

export function LayerLegendPanel({ layers, onToggle, entries }: { layers: Layers; onToggle: (k: LayerKey) => void; entries: LegendEntry[] }) {
  return (
    <>
    <details className={s.panel} open aria-label="图层">
      <summary className={s.panelSummary}>图层</summary>
      <div className={s.layerGrid}>
        {LAYERS.map((l) => (
          <label key={l.key}><input type="checkbox" checked={layers[l.key]} onChange={() => onToggle(l.key)} /> {l.label}</label>
        ))}
      </div>
    </details>
    <details className={s.panel} open aria-label="图例">
      <summary className={s.panelSummary}>图例</summary>
      <ul className={s.legendList}>
        {entries.map((e) => <li key={e.key}>{e.swatch}<span>{e.label}</span></li>)}
      </ul>
    </details>
    </>
  );
}

/** Origin cross and axis directions (world is y-up). */
export function OriginMark({ at }: { at: [number, number] }) {
  return (
    <g className={s.origin} transform={`translate(${at[0]} ${at[1]})`} pointerEvents="none">
      <line x1={-8} x2={8} y1={0} y2={0} />
      <line x1={0} x2={0} y1={-8} y2={8} />
      <circle r={3} />
      <text x={6} y={14}>原点 (0, 0)</text>
    </g>
  );
}

export function AxisCompass({ x, y }: { x: number; y: number }) {
  return (
    <g className={s.axes} transform={`translate(${x} ${y})`} pointerEvents="none" aria-label="坐标轴方向">
      <line x1={0} y1={0} x2={30} y2={0} />
      <path d="M30 -4 L38 0 L30 4 Z" />
      <text x={40} y={4}>+X (m)</text>
      <line x1={0} y1={0} x2={0} y2={-30} />
      <path d="M-4 -30 L0 -38 L4 -30 Z" />
      <text x={-10} y={-42}>+Y (m)</text>
    </g>
  );
}
