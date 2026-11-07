import { useMemo, useState } from 'react';
import { navigate } from '../app/router';
import { AGRI_PATTERNS, patternStatus } from '../studio/agri/patterns';
import { AgriCatalog } from '../studio/agri/AgriCatalog';
import { useCatalog } from './api';
import { BASE, implementFits, reconcile, saveMission } from './model';
import type { Catalog } from './types';
import s from './field.module.css';

type Cell = 'native' | 'template' | 'approx' | 'proposal' | 'none';
const LABEL: Record<Cell, string> = { native: '作业规划', template: '点任务模板', approx: '近似模板', proposal: '提案', none: '' };

/** What the system can do for one environment and one operation, strongest first. */
export function cell(c: Catalog, env: string, op: string): Cell {
  const e = c.environments.find((x) => x.id === env)!;
  if (e.line_operations.includes(op)) {
    const rig = c.platforms.some((p) => p.tier !== 'display' && e.platforms.includes(p.class) && c.implements.some((i) => implementFits(i, p, op)));
    if (rig) return 'native';
  }
  const patterns = AGRI_PATTERNS.filter((p) => p.operation === op && p.environments.includes(env as never));
  const status = patterns.map(patternStatus);
  if (status.includes('runnable')) return 'template';
  if (status.includes('approx')) return 'approx';
  if (status.includes('proposal')) return 'proposal';
  return 'none';
}

export function Coverage() {
  const res = useCatalog();
  const [templates, setTemplates] = useState(false);
  const c = res.data;
  const stats = useMemo(() => {
    if (!c) return null;
    const out: Record<Cell, number> = { native: 0, template: 0, approx: 0, proposal: 0, none: 0 };
    for (const e of c.environments) for (const o of c.operations) out[cell(c, e.id, o.id)] += 1;
    return out;
  }, [c]);
  if (res.error) return <div className={s.page}><p className={s.errorLine}>{res.error.message}</p></div>;
  if (!c || !stats) return <div className={s.page}><p className={s.muted}>读取目录…</p></div>;
  const ops = c.operations.filter((o) => o.family !== 'support');
  const open = (env: string, op: string) => {
    saveMission(reconcile(c, { ...BASE, environment: env, operation: op, crop: null, overrides: {} }));
    navigate('#/field');
  };
  return (
    <div className={s.page}>
      <div className={s.coverHead}>
        <h2>场景 × 作业</h2>
        <div className={s.keys}>
          {(['native', 'template', 'approx', 'proposal'] as Cell[]).map((k) => <span key={k} data-cell={k}><i />{LABEL[k]} {stats[k]}</span>)}
          <span data-cell="none"><i />未覆盖 {stats.none}</span>
        </div>
      </div>
      <div className={s.matrixWrap}>
        <table className={s.matrix}>
          <thead><tr><th />{c.environments.map((e) => <th key={e.id} title={`${e.name}：${e.spatial}`}><span>{e.short}</span></th>)}</tr></thead>
          <tbody>
            {ops.map((o) => (
              <tr key={o.id}>
                <th>{o.name}</th>
                {c.environments.map((e) => {
                  const k = cell(c, e.id, o.id);
                  return <td key={e.id} data-cell={k}>{k === 'native' ? <button type="button" title={`${e.name} · ${o.name}：打开作业设计`} aria-label={`${e.name} ${o.name}`} onClick={() => open(e.id, o.id)} /> : <i title={k === 'none' ? undefined : `${e.name} · ${o.name}：${LABEL[k]}`} />}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className={s.linkBtn} aria-expanded={templates} onClick={() => setTemplates(!templates)}>{templates ? '收起任务模板' : `任务模板 ${AGRI_PATTERNS.length} 个`}</button>
      {templates && <AgriCatalog />}
    </div>
  );
}
