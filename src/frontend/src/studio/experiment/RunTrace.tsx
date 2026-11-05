/** Traceability of one run: what was solved, by what, with which seed/budget. Unknown stays 未知, never 0. */
import { MapExclusionNote } from '../panels/MapExclusionNote';
import { Copy } from 'lucide-react';
import type { Solution } from '../../lab/types';
import { IDENTITY_LABEL, identityState } from '../../lab/views/plan/resultIntegrity';
import p from '../panels/panels.module.css';
import a from '../assets/assets.module.css';

export const UNKNOWN = '未知';

const known = (v: unknown): v is string | number | boolean => (typeof v === 'number' && Number.isFinite(v)) || (typeof v === 'string' && v !== '') || typeof v === 'boolean';
const show = (v: unknown, unit = '') => (known(v) ? `${String(v)}${unit}` : UNKNOWN);

export interface TraceFields {
  runId: string;
  scenarioId: string;
  scenarioHref: string | null;
  instanceHash: string | null;
  instanceShort: string;
  algorithm: string;
  version: string;
  seed: string;
  budget: string;
  params: string;
  identity: string;
}

export function traceFields(run: Solution): TraceFields {
  const params = run.params ?? {};
  const seedless = run.algorithm?.randomness?.uses_seed === false;
  const rest = Object.entries(params).filter(([k]) => k !== 'seed' && k !== 'time_limit_s');
  const hash = typeof run.instance_sha256 === 'string' && run.instance_sha256 ? run.instance_sha256 : null;
  return {
    runId: show(run.run_id),
    scenarioId: show(run.scenario_id),
    scenarioHref: known(run.scenario_id) ? `#/plan/${encodeURIComponent(run.scenario_id)}` : null,
    instanceHash: hash,
    instanceShort: hash ? hash.slice(0, 12) : UNKNOWN,
    algorithm: show(run.algorithm?.id),
    version: show(run.algorithm?.version),
    seed: known(params.seed) ? String(params.seed) : seedless ? '算法不使用种子' : UNKNOWN,
    budget: known(params.time_limit_s) ? `${params.time_limit_s} s` : known(run.process?.budget_s) ? `${run.process!.budget_s} s（进程预算）` : UNKNOWN,
    params: rest.length ? rest.map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ') : '无其他参数',
    identity: IDENTITY_LABEL[identityState(run)],
  };
}

export function RunTrace({ run, title = '可追溯信息' }: { run: Solution; title?: string }) {
  const t = traceFields(run);
  return (
    <>
      <h3>{title}</h3>
      <div className={a.kv} aria-label="运行追溯">
        <span>运行</span><span className={a.mono}>{t.runId}</span>
        <span>输入场景</span><span>{t.scenarioHref ? <a className={p.link} href={t.scenarioHref}>{t.scenarioId}</a> : t.scenarioId}</span>
        <span>实例哈希</span>
        <span className={a.mono} title={t.instanceHash ?? undefined}>
          {t.instanceShort}
          {t.instanceHash && (
            <button type="button" className={p.link} style={{ border: 0, background: 'none' }} title="复制完整 instance_sha256" aria-label="复制实例哈希"
              onClick={() => void navigator.clipboard?.writeText(t.instanceHash!)}><Copy size={11} /></button>
          )}
        </span>
        <span>算法</span><span className={a.mono}>{t.algorithm} · 版本 {t.version}</span>
        <span>种子</span><span>{t.seed}</span>
        <span>预算</span><span>{t.budget}</span>
        <span>参数</span><span className={a.mono}>{t.params}</span>
        <span>源码身份</span><span>{t.identity}</span>
      </div>
      <MapExclusionNote run={run} label="本运行" />
    </>
  );
}
