import { useMemo, useState, type ReactNode } from 'react';
import { useCatalog } from './api';
import { basisOf, fmt } from './model';
import type { Catalog, Implement, Platform, Sourced } from './types';
import s from './field.module.css';

function Value({ c, label, node, unit, digits = 2 }: { c: Catalog; label: string; node?: Sourced | null; unit?: string; digits?: number }) {
  if (!node) return null;
  const b = basisOf(c, node);
  return (
    <tr>
      <th>{label}</th>
      <td className="tnum">{fmt(node.v, digits)}{node.u ?? unit ? ` ${node.u ?? unit}` : ''}{node.r ? <small> {node.r[0]}–{node.r[1]}</small> : null}</td>
      <td><span className={s.basis} data-kind={b.kind} title={b.title}>{b.label}</span></td>
      <td className={s.cellNote}>{node.note}</td>
    </tr>
  );
}

function PlatformDetail({ c, p }: { c: Catalog; p: Platform }) {
  const implementsFor = c.implements.filter((i) => p.hitch.includes(i.hitch) && (!i.for || i.for.includes(p.id)));
  return <>
    <header className={s.detailHead}><h2>{p.name}</h2><span className={s.tier} data-tier={p.tier}>{c.labels.tiers[p.tier]}</span></header>
    <p className={s.detailUse}>{p.use}。{c.labels.platform_classes[p.class]}{p.pivot ? '，可原地转向' : ''}。{p.note ?? ''}</p>
    <table className={s.params}><tbody>
      <Value c={c} label="长度" node={p.length_m} unit="m" />
      <Value c={c} label="宽度" node={p.width_m} unit="m" />
      <Value c={c} label="轮距" node={p.track_m} unit="m" />
      <Value c={c} label="质量" node={p.mass_kg} unit="kg" digits={0} />
      <Value c={c} label="提升能力" node={p.lift_kg} unit="kg" digits={0} />
      <Value c={c} label="载荷" node={p.payload_kg} unit="kg" digits={0} />
      <Value c={c} label="PTO 功率" node={p.pto_kw} unit="kW" />
      <Value c={c} label="最小转弯半径" node={p.min_turn_radius_m} unit="m" />
      <Value c={c} label="作业速度上限" node={p.speed_work_max_kmh} unit="km/h" />
      <Value c={c} label="行驶速度" node={p.speed_transport_kmh} unit="km/h" />
      <Value c={c} label="续航" node={p.endurance_h} unit="h" />
    </tbody></table>
    <h3 className={s.detailSub}>可挂机具</h3>
    <div className={s.chipsStatic}>{implementsFor.map((i) => <span key={i.id}>{i.name}</span>)}{!implementsFor.length && <span>无</span>}</div>
  </>;
}

function ImplementDetail({ c, i }: { c: Catalog; i: Implement }) {
  const carriers = c.platforms.filter((p) => p.hitch.includes(i.hitch) && (!i.for || i.for.includes(p.id)));
  const ops = c.operations.filter((o) => i.operations.includes(o.id));
  return <>
    <header className={s.detailHead}><h2>{i.name}</h2><span className={s.tier} data-tier="planning">{i.category}</span></header>
    <p className={s.detailUse}>{{ '3pt': '三点悬挂', drawbar: '牵引', deck: '平台搭载', integrated: '一体式' }[i.hitch]}。作业：{ops.map((o) => o.name).join('、')}。</p>
    <table className={s.params}><tbody>
      <Value c={c} label="幅宽" node={i.width_m} unit="m" />
      <Value c={c} label="长度" node={i.length_m} unit="m" />
      <Value c={c} label="质量" node={i.mass_kg} unit="kg" digits={0} />
      <Value c={c} label="容量" node={i.tank} digits={0} />
      <Value c={c} label="作业速度" node={i.speed_kmh} unit="km/h" />
      <Value c={c} label="田间效率" node={i.efficiency} />
      <Value c={c} label="转弯半径" node={i.turn_radius_m} unit="m" />
    </tbody></table>
    {i.draft && <p className={s.formula}>牵引阻力 D = F(A + B·S + C·S²)·W·T，A={i.draft.A}，B={i.draft.B}，C={i.draft.C}（{i.draft.unit === 'rows' ? '每行' : '每米'}）<span className={s.basis} data-kind="standard">标准</span></p>}
    {i.pto && <p className={s.formula}>PTO 功率 {i.pto.a} kW + {i.pto.b} kW/m</p>}
    <h3 className={s.detailSub}>可用车辆</h3>
    <div className={s.chipsStatic}>{carriers.map((p) => <span key={p.id}>{p.name}</span>)}</div>
  </>;
}

export function Machines() {
  const res = useCatalog();
  const [kind, setKind] = useState<'platforms' | 'implements'>('platforms');
  const [id, setId] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const c = res.data;
  const items = useMemo(() => {
    if (!c) return [] as { id: string; name: string; group: string; tail?: ReactNode }[];
    const text = q.trim();
    if (kind === 'platforms') return c.platforms.filter((p) => !text || p.name.includes(text) || p.use.includes(text)).map((p) => ({ id: p.id, name: p.name, group: c.labels.platform_classes[p.class], tail: <span className={s.tier} data-tier={p.tier}>{c.labels.tiers[p.tier]}</span> }));
    return c.implements.filter((i) => !text || i.name.includes(text) || i.category.includes(text)).map((i) => ({ id: i.id, name: i.name, group: i.category, tail: <small className="tnum">{i.width_m.v} m</small> }));
  }, [c, kind, q]);
  if (res.error) return <div className={s.page}><p className={s.errorLine}>{res.error.message}</p></div>;
  if (!c) return <div className={s.page}><p className={s.muted}>读取目录…</p></div>;
  const current = id && items.some((x) => x.id === id) ? id : items[0]?.id;
  const groups = [...new Set(items.map((x) => x.group))];
  const p = c.platforms.find((x) => x.id === current);
  const i = c.implements.find((x) => x.id === current);
  return (
    <div className={s.machines}>
      <div className={s.machineList}>
        <div className={s.segment} role="tablist">
          <button type="button" role="tab" aria-selected={kind === 'platforms'} onClick={() => { setKind('platforms'); setId(null); }}>车辆 {c.platforms.length}</button>
          <button type="button" role="tab" aria-selected={kind === 'implements'} onClick={() => { setKind('implements'); setId(null); }}>机具 {c.implements.length}</button>
        </div>
        <input className={s.searchBox} type="search" placeholder="搜索" aria-label="搜索农机" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className={s.machineScroll}>
          {groups.map((g) => (
            <div key={g}>
              <h3>{g}</h3>
              {items.filter((x) => x.group === g).map((x) => (
                <button key={x.id} type="button" className={s.machineRow} aria-current={x.id === current || undefined} onClick={() => setId(x.id)}><span>{x.name}</span>{x.tail}</button>
              ))}
            </div>
          ))}
          {!items.length && <p className={s.muted}>没有匹配的农机。</p>}
        </div>
      </div>
      <div className={s.machineDetail}>
        {kind === 'platforms' && p && <PlatformDetail c={c} p={p} />}
        {kind === 'implements' && i && <ImplementDetail c={c} i={i} />}
      </div>
    </div>
  );
}
