/**
 * Historical runs of the open scenario beyond the current batch (GET /runs?scenario_id=…, existing API).
 * Failed, quarantined and identity-unknown runs are listed with their status; adding one to the comparison
 * keeps the comparison rules: only same-key verified runs are ranked, quarantined values are never shown.
 */
import { useState } from 'react';
import { History } from 'lucide-react';
import { fmt, lab } from '../../lab/api';
import type { Solution } from '../../lab/types';
import { Badge, Button, Notice, Spinner } from '../../lab/ui';
import { IDENTITY_LABEL, identityState } from '../../lab/views/plan/resultIntegrity';
import { comparable, exclusionReason, resultState } from '../comparison';
import { useStudio } from '../store';
import s from './panels.module.css';

// GET /runs accepts only scenario_id and limit (no offset/cursor/before), so there is no paging.
export const HISTORY_LIMIT = 200;

export function HistoryLimitNotice({ count, limit = HISTORY_LIMIT }: { count: number; limit?: number }) {
  if (count < limit) return null;
  return <Notice tone="warn">只显示最近 {limit} 条：接口不支持分页，更早的运行未列出（可在 #/runs 按场景筛选查看最近 100 条）。</Notice>;
}

export interface HistoryRow { run: Solution; inBatch: boolean; state: string; identity: string; exclusion: string | null; comparableToAnchor: boolean | null }

/** Rows for display: newest first, values never read here, so quarantined numbers cannot leak. */
export function historyRows(runs: Solution[], batch: string[], anchor: Solution | undefined): HistoryRow[] {
  return runs.map((run) => ({
    run, inBatch: batch.includes(run.run_id), state: resultState(run).text, identity: IDENTITY_LABEL[identityState(run)],
    exclusion: exclusionReason(run)?.text ?? null, comparableToAnchor: anchor ? comparable(run, anchor) : null,
  }));
}

/** Adds runs to the comparison batch without touching store actions. */
export function addToComparison(runs: Solution[]) {
  const st = useStudio.getState();
  const ids = runs.map((r) => r.run_id).filter((id) => !st.runIds.includes(id));
  if (!ids.length) return;
  useStudio.setState({ runIds: [...st.runIds, ...ids], runs: { ...st.runs, ...Object.fromEntries(runs.map((r) => [r.run_id, r])) }, manualFocus: true,
    focusRun: st.focusRun ?? ids[0]! });
}

export function HistoryRuns() {
  const scenarioId = useStudio((x) => x.scenarioId);
  const runIds = useStudio((x) => x.runIds);
  const runs = useStudio((x) => x.runs);
  const focusRun = useStudio((x) => x.focusRun);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ scenario: string; runs: Solution[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    if (!scenarioId) return;
    setLoading(true); setError(null);
    try { setData({ scenario: scenarioId, runs: (await lab.runs({ scenario_id: scenarioId, limit: HISTORY_LIMIT })).runs }); }
    catch (e) { setError(`历史运行读取失败：${(e as Error).message}`); }
    finally { setLoading(false); }
  };
  if (!scenarioId) return null;
  const anchor = (focusRun && runs[focusRun]) || (runIds[0] ? runs[runIds[0]] : undefined);
  const rows = data?.scenario === scenarioId ? historyRows(data.runs, runIds, anchor) : [];
  return (
    <div className={s.pad} aria-label="历史运行">
      <Button size="sm" icon={History} aria-expanded={open} onClick={() => { const next = !open; setOpen(next); if (next) void load(); }}>
        {open ? '收起历史运行' : '历史运行（本场景，含失败/已隔离/身份未核验）'}
      </Button>
      {open && (
        <>
          {loading && <Spinner />}
          {error && <Notice tone="error">{error}</Notice>}
          {data?.scenario === scenarioId && (
            <>
              <HistoryLimitNotice count={rows.length} />
              <p className={s.meta}>最近 {rows.length} 次（上限 {HISTORY_LIMIT}）；加入后按同一比较口径分组，只有已验算可行且身份一致的同口径方案参与排名；已隔离运行不显示数值。</p>
              <div className={s.tableWrap} data-history-table>
              <table className={s.table}>
                <thead><tr><th>时间</th><th>算法 · 运行</th><th>状态</th><th>源码身份</th><th>比较资格</th><th /></tr></thead>
                <tbody>
                  {rows.map((h) => (
                    <tr key={h.run.run_id} data-run={h.run.run_id}>
                      <td>{fmt.time((h.run as Solution & { created_utc?: string }).created_utc)}</td>
                      <td>{h.run.algorithm?.name ?? '未知'}<div className={s.meta}>{h.run.run_id}</div></td>
                      <td>{h.state}</td>
                      <td>{identityState(h.run) === 'quarantined' ? <Badge tone="red">{h.identity}</Badge> : identityState(h.run) === 'unknown' ? <Badge tone="gray">{h.identity}</Badge> : h.identity}</td>
                      <td>{h.exclusion ? `不参与排名：${h.exclusion}` : h.comparableToAnchor === false ? '可排名 · 与选中方案口径不同' : '可参与排名'}</td>
                      <td>{h.inBatch ? '已在比较中' : <Button size="sm" onClick={() => addToComparison([h.run])}>加入比较</Button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {!rows.length && <p className={s.hint}>这个场景还没有运行记录。</p>}
            </>
          )}
        </>
      )}
    </div>
  );
}
