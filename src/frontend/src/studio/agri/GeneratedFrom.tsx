/**
 * 规划页上的「生成来源」：场景若由农业模式生成，显示生成参数与种子，
 * 并把当前编辑器草稿（含未保存修改）与按参数重新生成的结果比较，区分「参数化生成」与「手工编辑」状态。
 * 「回到目录改参数」把这组参数写入目录的共享草稿（drafts.ts），目录卡片打开即读到同一份。
 */
import { useMemo } from 'react';
import { useStudio } from '../store';
import { getPattern } from './patterns';
import { defaultValues } from './catalog';
import { generatedFrom, requestCatalogFocus, saveDraft } from './drafts';
import { manualEdits } from './manualEdits';

export function GeneratedFrom() {
  const scenario = useStudio((st) => st.scenario);
  const draft = useStudio((st) => st.draft);
  const save = useStudio((st) => st.save);
  const prov = (scenario as { provenance?: unknown } | null)?.provenance;
  const src = useMemo(() => generatedFrom(prov), [prov]);
  const pattern = src ? getPattern(src.patternId) : undefined;
  const report = useMemo(() => (pattern && draft ? manualEdits(pattern, { instance: draft, provenance: prov }) : null), [pattern, draft, prov]);
  if (!src) return null;
  if (!pattern) return <p role="note" data-generated="unknown">由农业模式 {src.patternId} 生成，但当前目录里没有这个模式，无法复现或回到表单。</p>;
  const label = (k: string) => pattern.runnable?.params.find((p) => p.key === k);
  const shown = (pattern.runnable?.params ?? []).filter((p) => p.key in src.params);
  const mode = !report?.reproducible ? 'unknown' : report.changes.length ? 'manual' : 'parametric';
  function backToCatalog() {
    if (!pattern || !report) return;
    saveDraft(pattern, { ...defaultValues(pattern), ...report.params }, String(src!.seed ?? 42));
    // 留在计划页：农业目录在场景库面板里，由 Studio 收到聚焦请求后激活该面板。
    requestCatalogFocus(pattern.id);
  }
  return <section aria-label="生成来源" data-generated={mode} style={{ fontSize: 12 }}>
    <h4>生成来源：{pattern.name}</h4>
    <p>种子 {src.seed ?? '未记录'}；{shown.map((p) => `${label(p.key)?.label ?? p.key} ${String(src.params[p.key])}${p.unit ? ` ${p.unit}` : ''}`).join('，')}</p>
    {mode === 'parametric' && <p role="status">参数化生成：当前草稿与按这组参数重新生成的结果一致。</p>}
    {mode === 'manual' && <div role="status">手工编辑{save === 'dirty' || save === 'saving' ? '（含未保存修改）' : ''}：{report!.changes.join('；')}。回到目录改参数重新生成会得到新场景，这些手工编辑只留在本场景。</div>}
    {mode === 'unknown' && <p role="status">无法判断是否手工编辑过：{report?.reason ?? '草稿未载入'}</p>}
    <button type="button" onClick={backToCatalog} disabled={!report || report.seed === null}>回到目录改参数</button>
  </section>;
}
