import { useMemo } from 'react';
import { Badge, Notice } from '../lab/ui';
import type { Instance, Scenario, Solution } from '../lab/types';
import type { FieldInstance } from '../lab/views/plan/planModel';
import { useStudio } from '../studio/store';
import { saveMission } from './model';
import type { Mission } from './types';
import s from '../studio/panels/panels.module.css';

/** A finished plan that has an execution model: fleet plans, and field operations on a rig that can be simulated. */
export function executableRun(run: Solution): boolean {
  if (run.state !== 'done' || !run.routes?.length) return false;
  if (run.problem_class === 'fleet_routing') return true;
  return run.problem_class === 'field_operation' && (run.instance as unknown as FieldInstance | undefined)?.rig?.tier === 'simulation';
}

interface Seg { kind: string; duration_s: number; length_m: number }

function shares(run: Solution) {
  const out: Record<string, number> = { work: 0, turn: 0, transfer: 0, service: 0 };
  for (const r of run.routes as unknown as { segments?: Seg[] }[]) for (const seg of r.segments ?? []) out[seg.kind] = (out[seg.kind] ?? 0) + seg.duration_s;
  return out;
}

const PARTS: { key: string; label: string; color: string }[] = [
  { key: 'work', label: '作业', color: 'var(--mac-accent)' }, { key: 'turn', label: '转弯', color: '#b5622f' },
  { key: 'transfer', label: '转场', color: 'var(--mac-label-4)' }, { key: 'service', label: '补给', color: '#a58a2f' },
];

/** Where the machine's time goes in the selected plan, and what the plan saves against driving the rows in order. */
export function FieldPlanSummary({ runs, focus }: { runs: Solution[]; focus?: Solution }) {
  if (!focus || focus.state !== 'done' || !focus.routes.length) return null;
  const part = shares(focus);
  const total = Object.values(part).reduce((a, b) => a + b, 0);
  const metric = (r: Solution) => r.metrics.nonworking_distance_m as number | undefined;
  const base = runs.find((r) => r.algorithm.id === 'agri.sequential');
  const mine = metric(focus);
  const saved = base && mine != null && metric(base) ? 1 - mine / (metric(base) as number) : null;
  return (
    <div className={s.fieldSummary}>
      <header>
        <b>{focus.algorithm.name}</b>
        {saved != null && focus.algorithm.id !== 'agri.sequential' && <span>{saved >= 0 ? `非作业里程比顺序往复少 ${(saved * 100).toFixed(0)}%` : `非作业里程比顺序往复多 ${(-saved * 100).toFixed(0)}%`}</span>}
      </header>
      <div className={s.fieldBar} role="img" aria-label="机组时间构成">
        {PARTS.map((x) => part[x.key] > 0 && <i key={x.key} style={{ flexGrow: part[x.key], background: x.color }} />)}
      </div>
      <div className={s.fieldLegend}>
        {PARTS.map((x) => part[x.key] > 0 && <span key={x.key}><i style={{ background: x.color }} />{x.label} {(part[x.key] / 60).toFixed(1)} min<small>{(100 * part[x.key] / total).toFixed(0)}%</small></span>)}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className={s.rowSpace}><span className={s.meta} style={{ margin: 0 }}>{label}</span><span className="tnum">{value}</span></div>;
}

/** What this job is, in the user's terms, with the way back to where it was decided. */
export function FieldOperationInspector({ scenario, draft }: { scenario: Scenario; draft: Instance }) {
  const inst = draft as unknown as FieldInstance;
  const focus = useStudio((x) => (x.focusRun ? x.runs[x.focusRun] : undefined));
  const rig = inst.rig;
  const res = rig.resource;
  const breakdown = useMemo(() => {
    const m = focus?.metrics as Record<string, unknown> | undefined;
    return m?.turn_counts as Record<string, number> | undefined;
  }, [focus]);
  const edit = () => {
    if (inst.mission) saveMission(inst.mission as unknown as Mission);
    location.hash = '#/field';
  };
  return (
    <div className={s.scroll}>
      <div className={s.form}>
        <div className={s.rowSpace}><h3>{scenario.title}</h3><Badge tone={rig.tier === 'simulation' ? 'green' : 'gray'}>{rig.tier === 'simulation' ? '可仿真' : '只到规划层'}</Badge></div>
        <p className={s.meta}>{rig.name}</p>
        <Row label="作业行" value={`${inst.lines.length} 条 · 间距 ${inst.pitch_m} m`} />
        <Row label="地头" value={`${inst.headland_m} m`} />
        <Row label="机组" value={`${inst.machines} 台`} />
        <Row label="作业速度" value={`${(rig.speed_work_mps * 3.6).toFixed(1)} km/h`} />
        <Row label="最小转弯半径" value={`${rig.min_turn_radius_m} m`} />
        {res.kind !== 'none' && <Row label={res.name ?? '容量'} value={`${res.capacity} ${res.unit} · ${res.rate_per_ha ?? '—'} ${res.unit}/ha`} />}
        {breakdown && Object.keys(breakdown).length > 0 && <Row label="转弯构成" value={Object.entries(breakdown).map(([k, v]) => `${k === 'pivot' ? '原地' : k} ${v}`).join(' · ')} />}
        {rig.tier !== 'simulation' && <Notice tone="info">这台机组没有执行模型，方案可比较，不能进入物理仿真。</Notice>}
        <button type="button" className={s.link} onClick={edit}>修改作业设计</button>
      </div>
    </div>
  );
}
