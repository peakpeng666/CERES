import { useState } from 'react';
import type { BatchSample, PhysicsRun } from '../lab/types';
import { n1, n2, n3 } from './format';
import { stripLayout, stripRange } from './stripLayout';
import { Facts, Fold, Kvs, Row, Rows } from './ui';
import s from './desk.module.css';

const FACTOR: Record<string, string> = { ground_friction: '地面摩擦', payload: '载荷', wheel_radius: '轮径', service_time: '作业时长' };
const ROW_PX = 11;
const percent = (v?: number | null) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : '—');
const friction = (x: BatchSample) => x.ground_friction ?? x.perturbation?.ground_friction;

interface StripPoint { sample: BatchSample; value: number }

/** Every run on one axis, as a dot that can be pointed at or focused. Few runs: a histogram would hide what a dot plot shows. */
function Strip({ title, unit, points, marks, digits, active, onActive }: {
  title: string; unit: string; points: StripPoint[]; marks: { label: string; value: number | null | undefined; side: 'left' | 'right' }[]; digits: (v?: number | null) => string;
  active: number | null; onActive: (index: number | null) => void;
}) {
  if (!points.length) return null;
  const range = stripRange(points.map((p) => p.value));
  const placed = stripLayout(points.map((p) => p.value), range);
  const rows = Math.max(...placed.map((p) => p.row)) + 1;
  const at = (v: number) => `${((v - range.lo) / (range.hi - range.lo)) * 100}%`;
  return (
    <figure className={s.strip}>
      <figcaption>{title}</figcaption>
      <div className={s.stripPlot} style={{ height: 30 + rows * ROW_PX }}>
        {marks.filter((m) => typeof m.value === 'number').map((m) => (
          <span key={m.label} className={s.stripRef} data-side={m.side} style={{ left: at(m.value as number) }}><i>{m.label}</i></span>
        ))}
        {points.map((p, i) => (
          <button key={p.sample.index} type="button" className={s.stripDot} data-bad={p.sample.valid ? undefined : ''} data-on={active === p.sample.index ? '' : undefined}
            style={{ left: `${placed[i].x * 100}%`, bottom: 4 + placed[i].row * ROW_PX }} aria-label={`第 ${p.sample.index + 1} 次，${title} ${digits(p.value)} ${unit}，${p.sample.valid ? '通过' : '未通过'}`}
            onPointerEnter={() => onActive(p.sample.index)} onPointerLeave={() => onActive(null)} onFocus={() => onActive(p.sample.index)} onBlur={() => onActive(null)} />
        ))}
      </div>
      <div className={s.stripEnds}><span>{digits(range.lo)}</span><span>{digits(range.hi)} {unit}</span></div>
    </figure>
  );
}

function reading(x: BatchSample | undefined, labels: Record<string, string>) {
  if (!x) return '指向或聚焦一次运行，这里显示它的数值';
  const parts = [`第 ${x.index + 1} 次`, x.valid ? '通过' : labels[x.reason ?? ''] ?? '未通过'];
  if (x.valid && x.makespan_s != null) parts.push(`工期 ${n1(x.makespan_s)} s`);
  if (x.min_gap_m != null) parts.push(`最小车距 ${n3(x.min_gap_m)} m`);
  const mu = friction(x);
  if (mu != null) parts.push(`地面摩擦 ${n2(mu)}`);
  return parts.join(' · ');
}

/** A batch: how often the plan held, how long it took and how close the vehicles came, over every drawn field. */
export function BatchFacts({ r, onCancel }: { r: PhysicsRun; onCancel?: () => void }) {
  const [active, setActive] = useState<number | null>(null);
  const sum = r.summary, spec = r.batch;
  if (!sum) {
    return <div className={s.nextRow}>
      <p className={s.dim}>已完成 {r.progress?.runs_done ?? 0}/{r.progress?.runs_total ?? spec?.runs ?? '—'} 次{spec ? ` · ${spec.workers} 个进程并行` : ''}</p>
      {onCancel && r.state === 'running' && <button type="button" className={s.btn} onClick={onCancel}>取消</button>}
    </div>;
  }
  const samples = r.samples ?? [];
  const done = samples.filter((x) => x.state === 'succeeded' || x.state === 'failed' || x.state === 'crashed');
  const labels = Object.fromEntries(sum.failures.map((f) => [f.reason, f.label]));
  const time = done.filter((x) => x.valid && x.makespan_s != null).map((sample) => ({ sample, value: sample.makespan_s as number }));
  const gap = done.filter((x) => x.min_gap_m != null).map((sample) => ({ sample, value: sample.min_gap_m as number }));
  const m = sum.makespan_s, g = sum.min_gap_m;
  const legend = done.some((x) => !x.valid);
  return <>
    <Facts items={[['通过', `${sum.passed}/${sum.finished}`], ['工期中位数', n1(m.median), 's'], ['工期 90 分位', n1(m.p90), 's'], ['车距中位数', n2(g.median), 'm'], ['车距最小', n2(g.min), 'm']]} />
    <Kvs items={[
      ['通过率 95% 区间', sum.success_ci95 ? `${percent(sum.success_ci95[0])}–${percent(sum.success_ci95[1])}` : '—'],
      ['执行方式', r.traffic_mode === 'coordinated' ? '并行' : '逐车'],
      !!spec && ['扰动', `强度 ${spec.intensity} · ${spec.factors.map((f) => FACTOR[f] ?? f).join('、')}`],
      r.predicted_makespan_s != null && ['理想条件预计工期', `${n1(r.predicted_makespan_s)} s`],
      sum.order_violations > 0 && ['等待时越过门控', `${sum.order_violations} 次运行`],
      sum.cut_off > 0 && ['未完成', `${sum.cut_off} 次，不计入统计；未完成的多是最慢的运行，分位数按下界读`],
      !!spec && ['种子', spec.seed],
      !!r.wall_s && !!spec && ['用时', `${n1(r.wall_s)} s · ${spec.workers} 个进程并行`],
    ]} />
    {legend && <p className={s.stripLegend}><span><i className={s.keyDot} />通过</span><span><i className={s.keyBad} />未通过</span></p>}
    <Strip title="工期（通过的运行）" unit="s" points={time} digits={n1} active={active} onActive={setActive}
      marks={[{ label: '中位', value: m.median, side: 'left' }, { label: 'P90', value: m.p90, side: 'right' }]} />
    <Strip title="最小车距（含未通过的运行）" unit="m" points={gap} digits={n2} active={active} onActive={setActive}
      marks={[{ label: '中位', value: g.median, side: 'right' }, { label: 'P10', value: g.p10, side: 'left' }]} />
    {(time.length > 0 || gap.length > 0) && <p className={s.stripRead} aria-live="polite">{reading(done.find((x) => x.index === active), labels)}</p>}
    {sum.failures.length > 0 && <>
      <h3>失败原因</h3>
      <Rows>{sum.failures.map((f) => <Row key={f.reason} dense title={f.label} value={`${f.count} 次`} />)}</Rows>
    </>}
    {samples.length > 0 && <Fold summary={<span className={s.physRow}><b>逐次记录</b><small>{samples.length} 次</small></span>}>{() => (
      <table className={s.docTable}>
        <thead><tr><th>次</th><th>结果</th><th>工期</th><th>最小车距</th><th>地面摩擦</th></tr></thead>
        <tbody>{samples.map((x) => (
          <tr key={x.index}>
            <td>{x.index + 1}</td>
            <td>{x.valid ? '通过' : x.state === 'cut_off' || x.state === 'not_run' ? '未完成' : labels[x.reason ?? ''] ?? '未通过'}</td>
            <td>{x.valid ? `${n1(x.makespan_s)} s` : '—'}</td>
            <td>{x.min_gap_m != null ? `${n3(x.min_gap_m)} m` : '—'}</td>
            <td>{friction(x) != null ? n2(friction(x)) : '—'}</td>
          </tr>
        ))}</tbody>
      </table>
    )}</Fold>}
    <p className={s.docNote}>{sum.scope}。</p>
  </>;
}
