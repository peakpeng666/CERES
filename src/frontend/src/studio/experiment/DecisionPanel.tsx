/**
 * Decision record of a solve: the state the recommender saw, its candidates and recommendation, the state of the
 * learned selector, what was actually run, and how each choice turned out. Read from the advice snapshot that every
 * run stores at submission (recommendation_at_submission), so it describes what really happened.
 */
import { useMemo } from 'react';
import { Check, Scale, X } from 'lucide-react';
import { fmt } from '../../lab/api';
import type { Recommendation, Solution } from '../../lab/types';
import { Badge, EmptyState } from '../../lab/ui';
import { useStudio } from '../store';
import p from '../panels/panels.module.css';
import a from '../assets/assets.module.css';

type Advice = Recommendation & { compare?: string[] };
type WithAdvice = Solution & { recommendation_at_submission?: { advice?: Advice; submitted_algorithm?: string; note?: string } };

const SELECTOR: Record<string, { text: string; tone: 'green' | 'gray' | 'orange' }> = {
  available: { text: '已接入', tone: 'green' }, unavailable: { text: '未接入', tone: 'orange' },
  not_applicable: { text: '不适用于此问题类型', tone: 'gray' }, out_of_scope: { text: '超出训练范围', tone: 'orange' },
  out_of_profile: { text: '预算未匹配评估条件，使用规则', tone: 'orange' },
};

export function DecisionPanel() {
  const st = useStudio();
  const runs = st.runIds.map((id) => st.runs[id]).filter(Boolean) as WithAdvice[];
  const snap = runs.find((r) => r.recommendation_at_submission?.advice)?.recommendation_at_submission;
  const advice: Advice | null = snap?.advice ?? st.recommendation;
  const fromRuns = !!snap?.advice;
  const algos = useMemo(() => Object.fromEntries(st.algorithms.map((x) => [x.id, x])), [st.algorithms]);
  if (!st.scenario) return <EmptyState icon={Scale} title="未打开场景" />;
  if (!advice) return <EmptyState icon={Scale} title="还没有推荐" detail="打开任务地图后，推荐器会根据地图特征给出候选算法。" />;
  const done = runs.filter((r) => r.state === 'done' && r.routes.length && r.objective.value != null);
  const valid = done.filter((r) => r.check?.valid);
  const best = valid.length ? Math.min(...valid.map((r) => r.objective.value as number)) : null;
  const recRun = runs.find((r) => r.algorithm.id === advice.recommended);
  const regret = recRun && best != null && recRun.check?.valid && recRun.objective.value != null ? ((recRun.objective.value - best) / Math.max(1e-9, best)) * 100 : null;
  const sel = advice.learned_selector;
  const selLabel = SELECTOR[sel?.status ?? 'unavailable'] ?? SELECTOR.unavailable;
  return (
    <div className={p.scroll}>
      <div className={p.form}>
        <div className={p.rowSpace}>
          <h3>决策记录</h3>
          <Badge tone={fromRuns ? 'blue' : 'gray'}>{fromRuns ? '提交时快照' : '当前地图（尚未求解）'}</Badge>
        </div>
        <p className={p.meta}>{fromRuns ? (snap?.note ?? '') : '当前显示实时推荐；求解后显示提交时保存的推荐快照。'}</p>

        <h3>① 输入特征</h3>
        <div className={p.features}>
          {advice.features.map((f) => (
            <div key={f.key} className={p.feature}><span>{f.name}</span><strong>{f.value ?? '—'}{f.unit ? ` ${f.unit}` : ''}</strong></div>
          ))}
        </div>

        <h3>② 候选与排序</h3>
        <table className={p.table}>
          <thead><tr><th>#</th><th>算法</th><th className={p.num}>得分</th><th>判断</th><th>理由</th></tr></thead>
          <tbody>
            {advice.ranking.map((r) => (
              <tr key={r.algorithm_id} style={{ cursor: 'default', opacity: r.applicable ? 1 : 0.55 }}>
                <td>{r.rank}</td>
                <td>{algos[r.algorithm_id]?.name ?? r.algorithm_id}</td>
                <td className={p.num}>{fmt.num((r as { score?: number }).score, 2)}</td>
                <td>{r.suitability}</td>
                <td style={{ whiteSpace: 'normal', minWidth: 180 }}>{r.reasons.join('；')}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3>③ 推荐</h3>
        <p className={p.hint}>
          {advice.recommended ? <>推荐 <b>{algos[advice.recommended]?.name ?? advice.recommended}</b>。</> : '没有适用的算法。'}
          策略 <span className={p.mono}>{advice.policy}</span>{advice.policy === 'rules-v2' ? '（特征规则）' : advice.policy === 'coverage-order-v1' ? '（经验静态尝试顺序，开发侧拟合）' : '（学习型选择器）'}。
        </p>
        <div className={a.kv}>
          <span>{sel?.prediction?.kind === 'static' ? '经验尝试顺序' : '学习型选择器'}</span><span><span className={a.chip} data-tone={selLabel.tone === 'gray' ? undefined : selLabel.tone}>{selLabel.text}</span> <span className={p.mono}>{sel?.id}</span></span>
          {sel?.reason && <><span>原因</span><span>{sel.reason}</span></>}
          {sel?.scope && <><span>适用范围</span><span>{sel.scope}</span></>}
          {sel?.evaluation && <><span>离线评估</span><span>测试 {sel.evaluation.test_n} 例，平均改进 {fmt.num(sel.evaluation.mean_improvement_pct, 2)}%，有害 {sel.evaluation.harmful_instances} 例（对照单一最佳 {sel.evaluation.sbs}）</span></>}
          {sel?.status === 'not_applicable' && <><span>说明</span><span>学习型选择器适用于欧氏 CVRP；此问题使用规则推荐。</span></>}
        </div>
        {sel?.prediction?.order && (
          <div className={p.features}>
            {sel.prediction.order.slice(0, 4).map((o, i) => (
              <div key={o.config} className={p.feature}><span>尝试 {i + 1}</span><strong>{o.label ?? o.config}</strong></div>
            ))}
          </div>
        )}

        <h3>④ 实际选择</h3>
        {!runs.length && <p className={p.hint}>还没有求解。勾选的候选：{st.chosen.map((id) => algos[id]?.name ?? id).join('、') || '无'}。</p>}
        {runs.length > 0 && (
          <p className={p.hint}>
            运行了 {runs.length} 个算法：{runs.map((r) => `${r.algorithm.name}${r.params?.time_limit_s != null ? `（${r.params.time_limit_s} s）` : ''}`).join('、')}。
            {advice.recommended ? (runs.some((r) => r.algorithm.id === advice.recommended) ? ' 包含推荐算法。' : ' 没有运行推荐算法。') : ''}
          </p>
        )}

        <h3>⑤ 效果</h3>
        {!done.length && <p className={p.hint}>求解完成后在这里对照每个选择的结果。</p>}
        {done.length > 0 && (
          <table className={p.table}>
            <thead><tr><th>算法</th><th className={p.num}>目标值</th><th className={p.num}>与最好差距</th><th>验算</th><th className={p.num}>计算</th></tr></thead>
            <tbody>
              {runs.map((r) => {
                const v = r.objective.value;
                const gap = v != null && best != null && r.check?.valid ? ((v - best) / Math.max(1e-9, best)) * 100 : null;
                return (
                  <tr key={r.run_id} aria-selected={st.focusRun === r.run_id} onClick={() => st.focus(r.run_id)}>
                    <td>{r.algorithm.name}{r.algorithm.id === advice.recommended && <> <Badge tone="green">推荐</Badge></>}</td>
                    <td className={p.num}>{fmt.num(v, 2)} {r.objective.unit === 'euc_2d_rounded' ? '' : r.objective.unit}</td>
                    <td className={p.num}>{gap == null ? '—' : gap < 1e-9 ? '最好' : `+${fmt.num(gap, 2)}%`}</td>
                    <td>{r.check ? (r.check.valid ? <span className={p.ok}><Check size={12} /> 通过</span> : <span className={p.bad}><X size={12} /> {r.check.errors.length}</span>) : r.state}</td>
                    <td className={p.num}>{fmt.sec(r.metrics.wall_s)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {regret != null && (
          <p className={p.hint}>推荐算法的结果{regret < 1e-9 ? '就是本次最好的有效方案' : `比本次最好的有效方案差 ${fmt.num(regret, 2)}%`}（按各方案报告的目标值比较，单位 {recRun?.objective.unit}；只比较通过独立验算的方案）。</p>
        )}
      </div>
    </div>
  );
}
