import type { InstanceSpace, InstanceSpacePanel } from '../types';
import p from '../panels/panels.module.css';
import { Axes, Figure, Legend, Marker, NoData, PLOT, scale, styleFor } from './SvgChart';

function Panel({ panel }: { panel: InstanceSpacePanel }) {
  const title = `实例空间 · ${panel.feature_schema} · 预算 ${panel.budget_s ?? '未知'} s`;
  if (panel.projection_status !== 'ok') return <NoData title={title} reason={panel.note ?? '本面板至少需要 3 个实例'} />;
  const points = panel.points.filter((pt) => Number.isFinite(pt.x) && Number.isFinite(pt.y));
  const x = scale({ label: '' }, points.map((pt) => pt.x!), [PLOT.left, PLOT.right]);
  const y = scale({ label: '' }, points.map((pt) => pt.y!), [PLOT.bottom, PLOT.top]);
  const reasons: Record<string, string> = { incomplete_candidates: '候选不齐全', single_candidate: '仅单候选', no_feasible_solution: '无可行解', tie: '并列' };
  return <Figure title={title} label={title}
    facts={[
      `比较定义：${panel.comparison_contract.objective_rule ?? '未知'}；单位 ${panel.comparison_contract.objective_unit ?? '未知'}；候选 ${panel.algorithms.join('、')}`,
      `特征：${panel.features?.join('、') ?? '未提供'}；样本 ${points.length} 个比较输入，未指定唯一胜者 ${points.filter((pt) => !pt.best).length} 个`,
      '候选不齐全、单候选、全失败或并列不指定唯一胜者；颜色不是统计显著性。',
    ]}
    legend={<Legend items={[...panel.algorithms.map((a, i) => ({ label: a, style: styleFor(i) })), { label: '未指定唯一胜者', style: { ...styleFor(0), color: '#8e8e93' } }]} />}>
    <Axes x={x} y={y} xLabel={`PC1（${((panel.explained_variance?.[0] ?? 0) * 100).toFixed(0)}%）`} yLabel={`PC2（${((panel.explained_variance?.[1] ?? 0) * 100).toFixed(0)}%）`} />
    {points.map((pt) => {
      const style = pt.best ? styleFor(panel.algorithms.indexOf(pt.best)) : { ...styleFor(0), color: '#8e8e93' };
      return <g key={pt.comparison_key ?? pt.scenario_id} data-comparison-key={pt.comparison_key}>
        <title>{`${pt.title} · ${pt.best ?? reasons[pt.winner_status ?? ''] ?? '未知'} · runs: ${pt.run_ids?.join(', ') ?? '未提供'}`}</title>
        <Marker x={x.map(pt.x!)} y={y.map(pt.y!)} kind={style.marker} color={style.color} hollow={!pt.best} />
      </g>;
    })}
  </Figure>;
}

export function InstanceSpaceCharts({ space }: { space: InstanceSpace }) {
  if (!space.panels) return <NoData title="实例空间" reason="旧版响应缺少比较/特征分面契约；请刷新后端，不绘制混合 winner map" />;
  return <><p className={p.meta}>{space.note} 未进入特征投影的运行：{space.omitted_runs?.length ?? 0}</p>
    {space.panels.length ? space.panels.map((panel) => <Panel key={panel.panel_id} panel={panel} />) : <NoData title="实例空间" reason="没有可用特征面板" />}</>;
}
