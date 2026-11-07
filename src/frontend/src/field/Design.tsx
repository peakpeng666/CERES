import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { navigate } from '../app/router';
import { Icon } from '../desk/Icon';
import { useStudio } from '../studio/store';
import { lab, useLabResource } from '../lab/api';
import { studio } from '../studio/api';
import { createMission, useCatalog, usePreview } from './api';
import { BASE, PRESETS, SHAPES, STAGES, basisOf, fmt, hectares, hours, implementFits, lineOperations, loadMission, platformsFor, reconcile, saveMission } from './model';
import { FieldPreview } from './Preview';
import type { Catalog, Check, Mission, Preview, Sourced } from './types';
import s from './field.module.css';

const ALGORITHMS = ['agri.sequential', 'agri.skip', 'agri.split_anneal', 'ortools.field'];

function Section({ n, title, aside, children }: { n: number; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className={s.section}>
      <header><i>{n}</i><h2>{title}</h2>{aside}</header>
      {children}
    </section>
  );
}

function Basis({ c, node }: { c: Catalog; node?: Sourced | { s: string } | null }) {
  if (!node) return null;
  const b = basisOf(c, node);
  return <span className={s.basis} data-kind={b.kind} title={b.title}>{b.label}</span>;
}

function NumberField({ label, value, unit, min, max, step, placeholder, onChange, c, node, hint }: {
  label: string; value: number | null | undefined; unit?: string; min?: number; max?: number; step?: number; placeholder?: string;
  onChange: (v: number | null) => void; c?: Catalog; node?: Sourced | null; hint?: ReactNode;
}) {
  const [text, setText] = useState(value == null ? '' : String(value));
  useEffect(() => { setText(value == null ? '' : String(value)); }, [value]);
  return (
    <label className={s.field}>
      <span>{label}{c && node && <Basis c={c} node={node} />}</span>
      <div className={s.inputBox}>
        <input type="number" inputMode="decimal" value={text} min={min} max={max} step={step ?? 'any'} placeholder={placeholder}
          onChange={(e) => {
            setText(e.target.value);
            const n = e.target.valueAsNumber;
            if (e.target.value === '') onChange(null);
            else if (Number.isFinite(n) && (min === undefined || n >= min) && (max === undefined || n <= max)) onChange(n);
          }} />
        {unit && <em>{unit}</em>}
      </div>
      {hint && <small>{hint}</small>}
    </label>
  );
}

function Choice({ on, disabled, onClick, title, sub, tail, why }: { on: boolean; disabled?: boolean; onClick: () => void; title: ReactNode; sub?: ReactNode; tail?: ReactNode; why?: string }) {
  return (
    <button type="button" className={s.choice} aria-pressed={on} disabled={disabled} onClick={onClick} title={why}>
      <span><b>{title}</b>{(why || sub) && <small>{why ?? sub}</small>}</span>
      {tail}
    </button>
  );
}

function Tier({ c, tier }: { c: Catalog; tier: string }) {
  return <span className={s.tier} data-tier={tier}>{c.labels.tiers[tier]}</span>;
}

function Stat({ label, value, unit, note }: { label: string; value: ReactNode; unit?: string; note?: string }) {
  return <div className={s.stat} title={note}><span>{label}</span><b>{value}{unit && <em>{unit}</em>}</b></div>;
}

const ORDER: Record<Check['level'], number> = { block: 0, warn: 1, info: 2, pass: 3 };
const LEVEL_ICON = { block: 'cross', warn: 'clock', info: 'ring', pass: 'check' } as const;

function Checks({ checks }: { checks: Check[] }) {
  const [all, setAll] = useState(false);
  const sorted = [...checks].sort((a, b) => ORDER[a.level] - ORDER[b.level]);
  const shown = all ? sorted : sorted.filter((x) => x.level !== 'pass');
  const passed = checks.filter((x) => x.level === 'pass').length;
  return (
    <div className={s.checks}>
      <ul>
        {shown.map((x) => (
          <li key={x.id} data-level={x.level}>
            <Icon name={LEVEL_ICON[x.level]} size={13} />
            <div><b>{x.title}</b><p>{x.detail}{x.fix && x.level !== 'pass' ? <em> {x.fix}。</em> : null}</p></div>
          </li>
        ))}
      </ul>
      {passed > 0 && <button type="button" className={s.linkBtn} onClick={() => setAll(!all)}>{all ? '收起通过项' : `${passed} 项通过`}</button>}
    </div>
  );
}

function Summary({ preview, mission }: { preview: Preview; mission: Mission }) {
  const e = preview.estimate!;
  const rig = preview.rig!;
  const res = rig.resource;
  return (
    <div className={s.stats}>
      <Stat label="田块" value={fmt(e.field_ha, 2)} unit="ha" />
      <Stat label="作业行" value={e.lines} unit={`条 · 间距 ${fmt(e.pitch_m, 2)} m`} />
      <Stat label="有效能力" value={fmt(e.effective_ha_h, 2)} unit="ha/h" note="幅宽 × 速度 × 典型田间效率" />
      <Stat label={mission.fleet > 1 ? `${mission.fleet} 台约需` : '约需'} value={hours(e.hours_at_typical_efficiency)} note="按 ASAE D497 典型田间效率估算；求解后以方案为准" />
      {res.kind !== 'none' && <Stat label={res.kind === 'collect' ? '至少卸料' : '至少加注'} value={Math.max(0, (e.fills_min ?? 1) - 1)} unit={`次 · 一箱约 ${fmt(e.rows_per_fill, 1)} 行`} />}
      <Stat label="掉头比 2r/w" value={fmt(e.turn_ratio, 2)} unit={e.skip_rows_needed ? `跳 ${e.skip_rows_needed} 行才不回绕` : '相邻行可直转'} />
    </div>
  );
}

export function Design() {
  const res = useCatalog();
  const c = res.data;
  const [mission, setMission] = useState<Mission | null>(null);
  useEffect(() => {
    if (!c || mission) return;
    setMission(reconcile(c, loadMission() ?? BASE));
  }, [c, mission]);
  useEffect(() => { if (mission) saveMission(mission); }, [mission]);
  const { preview, error, busy } = usePreview(mission);
  const fields = useLabResource('field-design-real', () => studio.fields('nl'));
  const real = fields.data?.fields ?? [];
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const env = c?.environments.find((x) => x.id === mission?.environment);
  const crop = c?.crops.find((x) => x.id === mission?.crop) ?? null;
  const op = c?.operations.find((x) => x.id === mission?.operation);
  const platform = c?.platforms.find((x) => x.id === mission?.platform);
  const implement = c?.implements.find((x) => x.id === mission?.implement);
  const operations = useMemo(() => (c && env ? lineOperations(c, env) : []), [c, env]);

  if (res.error) return <div className={s.page}><p className={s.errorLine}>目录读取失败：{res.error.message}</p><button type="button" className={s.linkBtn} onClick={res.reload}>重试</button></div>;
  if (!c || !mission || !env || !op || !platform || !implement) return <div className={s.page}><p className={s.muted}>读取目录…</p></div>;

  const patch = (next: Partial<Mission>) => setMission(reconcile(c, { ...mission, ...next }));
  const override = (key: keyof Mission['overrides'], value: number | null) => setMission({ ...mission, overrides: { ...mission.overrides, [key]: value } });
  const field = (next: Partial<Mission['field']>) => setMission({ ...mission, field: { ...mission.field, ...next } });
  const list = platformsFor(c, env, mission.operation);
  const fits = c.implements.filter((i) => implementFits(i, platform, mission.operation));
  const blocked = !preview || preview.blocked || !!error;
  const firstBlock = preview?.checks.find((x) => x.level === 'block');
  const res0 = preview?.rig?.resource;

  async function create() {
    if (!mission) return;
    setCreating(true); setCreateError(null);
    try {
      const sc = await createMission(mission);
      navigate(`#/plan/${encodeURIComponent(sc.id)}`);
      const st = useStudio.getState();
      await st.open(sc.id);
      const { algorithms: available } = await lab.algorithms();
      const ids = ALGORITHMS.filter((id) => available.some((a) => a.id === id && a.available));
      st.setChosen(ids);
      await st.solve(ids);
    } catch (e) {
      setCreateError((e as Error).message);
    } finally { setCreating(false); }
  }

  return (
    <div className={s.design}>
      <div className={s.form}>
        <div className={s.presets} role="group" aria-label="常用作业">
          {PRESETS.map((p) => (
            <button key={p.id} type="button" onClick={() => setMission(reconcile(c, { ...BASE, ...p.mission, overrides: { ...p.mission.overrides } }))}>
              <b>{p.label}</b><small>{p.hint}</small>
            </button>
          ))}
        </div>

        <Section n={1} title="场景">
          <div className={s.chips} role="group" aria-label="生产环境">
            {c.environments.filter((x) => x.line_operations.length).map((x) => (
              <button key={x.id} type="button" aria-pressed={x.id === env.id} title={x.spatial} onClick={() => patch({ environment: x.id, crop: null })}>{x.name}</button>
            ))}
          </div>
          <div className={s.grid2}>
            <label className={s.field}>
              <span>作物</span>
              <select value={mission.crop ?? ''} onChange={(e) => setMission({ ...mission, crop: e.target.value || null })}>
                {op.stage === 'pre_crop' && <option value="">无作物（裸地）</option>}
                {c.crops.filter((x) => env.default_crops.includes(x.id) || x.id === mission.crop).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
              {crop?.row_spacing_m && <small>行距 {crop.row_spacing_m.v} m<Basis c={c} node={crop.row_spacing_m} /></small>}
            </label>
            <div className={s.field}>
              <span>田块形状</span>
              <div className={s.shapes} role="group" aria-label="田块形状">
                {SHAPES.map((x) => (
                  <button key={x.id} type="button" aria-pressed={!mission.field.field_id && mission.field.shape === x.id} title={x.label} aria-label={x.label} onClick={() => setMission({ ...mission, angle_deg: 0, field: { ...mission.field, shape: x.id, field_id: undefined } })}>
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"><path d={x.d} /></svg>
                  </button>
                ))}
                <button type="button" className={s.realBtn} aria-pressed={!!mission.field.field_id} onClick={() => { if (!mission.field.field_id && real[0]) setMission({ ...mission, angle_deg: null, field: { ...mission.field, field_id: real[0].id } }); }} disabled={!real.length} title={real.length ? 'Fields2Benchmark 荷兰开发集' : '先在 资料 → 真实田块 下载'}>真实</button>
              </div>
            </div>
          </div>
          {mission.field.field_id ? (
            <div className={s.grid2}>
              <label className={s.field}>
                <span>真实田块</span>
                <select value={mission.field.field_id} onChange={(e) => field({ field_id: e.target.value })}>
                  {real.map((f) => <option key={f.id} value={f.id}>{f.id.replace('nl_field_', '荷兰 ')} · {f.area_ha} ha</option>)}
                </select>
              </label>
              <NumberField label="作业方向" unit="°" min={-180} max={180} placeholder="沿长边" value={mission.angle_deg} onChange={(v) => setMission({ ...mission, angle_deg: v })} />
            </div>
          ) : (
            <div className={s.grid3}>
              <NumberField label="长" unit="m" min={5} max={5000} value={mission.field.width_m} onChange={(v) => v && field({ width_m: v })} />
              <NumberField label="宽" unit="m" min={5} max={5000} value={mission.field.height_m} onChange={(v) => v && field({ height_m: v })} />
              <NumberField label="作业方向" unit="°" min={-90} max={90} value={mission.angle_deg} onChange={(v) => setMission({ ...mission, angle_deg: v ?? 0 })} hint={`约 ${fmt(hectares(mission.field.width_m, mission.field.height_m), 2)} ha`} />
            </div>
          )}
        </Section>

        <Section n={2} title="作业">
          {STAGES.map((stage) => {
            const items = operations.filter((o) => o.stage === stage.id);
            if (!items.length) return null;
            return (
              <div className={s.opGroup} key={stage.id}>
                <h3>{stage.label}</h3>
                <div className={s.chips} role="group" aria-label={stage.label}>
                  {items.map((o) => <button key={o.id} type="button" aria-pressed={o.id === op.id} onClick={() => patch({ operation: o.id })}>{o.name}</button>)}
                </div>
              </div>
            );
          })}
          <p className={s.aside}>采样、巡检等按点作业在<a href="#/maps">任务地图</a>里规划。</p>
          {op.resource === 'consume' && (
            <div className={s.grid3}>
              <NumberField label={`${op.resource_name}用量`} unit={`${res0?.unit ?? op.unit}/ha`} min={0.1} value={mission.overrides.rate_per_ha} onChange={(v) => override('rate_per_ha', v)} />
            </div>
          )}
        </Section>

        <Section n={3} title="机组" aside={<a className={s.headLink} href="#/field/machines">全部农机</a>}>
          <div className={s.pick}>
            <h3>车辆</h3>
            <div className={s.choices}>
              {list.filter((x) => x.ok).map(({ platform: p }) => (
                <Choice key={p.id} on={p.id === platform.id} onClick={() => patch({ platform: p.id })} title={p.name} sub={p.use} tail={<Tier c={c} tier={p.tier} />} />
              ))}
            </div>
            <details className={s.others}>
              <summary>{list.filter((x) => !x.ok).length} 台不适用</summary>
              <div className={s.choices}>
                {list.filter((x) => !x.ok).map(({ platform: p, why }) => <Choice key={p.id} on={false} disabled onClick={() => undefined} title={p.name} why={why} />)}
              </div>
            </details>
          </div>
          <div className={s.pick}>
            <h3>机具</h3>
            <div className={s.choices}>
              {fits.map((i) => (
                <Choice key={i.id} on={i.id === implement.id} onClick={() => setMission({ ...mission, implement: i.id, overrides: { ...mission.overrides, work_width_m: null, capacity: null } })}
                  title={i.name} sub={`${i.category} · ${i.width_m.v} m${i.tank ? ` · ${i.tank.v} ${i.tank.u}` : ''}`} />
              ))}
            </div>
          </div>
          <div className={s.grid3}>
            <NumberField c={c} node={implement.width_m} label="作业幅宽" unit="m" min={.2} max={60} placeholder={String(implement.width_m.v)} value={mission.overrides.work_width_m} onChange={(v) => override('work_width_m', v)}
              hint={implement.width_m.r ? `${implement.width_m.r[0]}–${implement.width_m.r[1]} m` : undefined} />
            {implement.tank && <NumberField c={c} node={implement.tank} label={op.resource === 'collect' ? '粮箱/料箱' : '药箱/料箱'} unit={implement.tank.u} min={1} placeholder={String(implement.tank.v)} value={mission.overrides.capacity} onChange={(v) => override('capacity', v)}
              hint={implement.tank.r ? `${implement.tank.r[0]}–${implement.tank.r[1]}` : undefined} />}
            <NumberField label="机组数" min={1} max={12} step={1} value={mission.fleet} onChange={(v) => v && setMission({ ...mission, fleet: Math.round(v) })} />
          </div>
        </Section>

        <Section n={4} title="规划">
          <div className={s.grid3}>
            <NumberField label="地头宽度" unit="m" min={0} max={100} placeholder={preview?.recommended_headland_m != null ? String(preview.recommended_headland_m) : undefined} value={mission.headland_m}
              onChange={(v) => setMission({ ...mission, headland_m: v })} hint={preview?.recommended_headland_m != null ? `建议 ${preview.recommended_headland_m} m` : undefined} />
            <label className={s.field}>
              <span>优化目标</span>
              <select value={mission.objective ?? (mission.fleet > 1 ? 'makespan' : 'nonworking')} onChange={(e) => setMission({ ...mission, objective: e.target.value as Mission['objective'] })}>
                <option value="nonworking">非作业里程最短</option>
                <option value="makespan">最早完成</option>
              </select>
            </label>
            <label className={s.field}>
              <span>补给站</span>
              <select value={mission.station} onChange={(e) => setMission({ ...mission, station: e.target.value })}>
                {[['sw', '西南角'], ['se', '东南角'], ['ne', '东北角'], ['nw', '西北角'], ['s', '南侧中点'], ['n', '北侧中点']].map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select>
            </label>
          </div>
        </Section>
      </div>

      <aside className={s.side} aria-label="作业预览">
        <div className={s.previewBox} data-busy={busy || undefined}>
          {preview?.field ? <FieldPreview preview={preview} /> : <p className={s.muted}>{error ?? '生成预览…'}</p>}
          {preview?.rig && <div className={s.legend}><span data-k="line">作业行</span><span data-k="head">地头 {fmt(preview.headland_m, 1)} m</span><span data-k="station">补给站</span></div>}
        </div>
        {error && <p className={s.errorLine}>{error}</p>}
        {preview?.estimate && <Summary preview={preview} mission={mission} />}
        {preview && <Checks checks={preview.checks} />}
        <div className={s.go}>
          {createError && <p className={s.errorLine}>{createError}</p>}
          {firstBlock && <p className={s.blockLine}>{firstBlock.title}：{firstBlock.fix ?? firstBlock.detail}</p>}
          <button type="button" className={s.primary} disabled={blocked || creating} onClick={() => void create()}>{creating ? '正在生成…' : '生成并求解'}</button>
        </div>
      </aside>
    </div>
  );
}
