import { useEffect, useState } from 'react';
import type { Instance, Scenario } from '../../lab/types';
import { Button, Input, Notice, Select } from '../../lab/ui';
import { TaskEditor } from '../../lab/views/plan/TaskEditor';
import { studio } from '../api';
import { retrySave, saveDraftCopy, useStudio } from '../store';
import s from './panels.module.css';

type Pose = [number, number, number];
type Motion = { start: Pose; goal: Pose; motion_model: string; turning_radius: number; robot_radius: number; clearance_margin: number; goal_tolerance: number };

export function ProblemEditor({ scenario, draft }: { scenario: Scenario; draft: Instance }) {
  const edit = useStudio((st) => st.edit);
  const saveError = useStudio((st) => st.saveError);
  const canonical = JSON.stringify(draft, null, 2);
  const [text, setText] = useState(canonical);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (!dirty) setText(canonical); }, [canonical, dirty]);
  const motion = draft as unknown as Motion;
  const change = (patch: Partial<Motion>) => edit((inst) => ({ ...inst, ...patch }));
  async function apply() {
    setBusy(true); setError(null);
    try {
      const candidate: unknown = JSON.parse(text);
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) throw new Error('任务输入须为 JSON 对象');
      const checked = await studio.validateInstance(scenario.id, candidate as Instance);
      const current = useStudio.getState();
      if (current.scenarioId !== scenario.id || current.draft !== draft) throw new Error('校验期间草稿已改变；文本仍保留，请核对后再次应用');
      edit(() => checked.instance);
      setDirty(false);
      setText(JSON.stringify(checked.instance, null, 2));
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <div className={s.form}>
    <h3>规划问题与约束</h3>
    {saveError && <Notice tone="error">未保存：{saveError}
      <div className={s.row}><Button size="sm" onClick={() => void retrySave()}>重试保存</Button>
        <Button size="sm" onClick={() => void saveDraftCopy().catch((cause) => setError((cause as Error).message))}>另存草稿副本</Button></div>
    </Notice>}
    <p className={s.meta}>坐标：{scenario.frame.kind} · {scenario.frame.units} · X 向右 / Y 向上。角度输入为度，提交保存为弧度。</p>
    {scenario.problem_class === 'point_to_point' ? <>
      {(['start', 'goal'] as const).map((key) => <fieldset key={key} style={{ border: 0, padding: 0, margin: 0 }}>
        <legend>{key === 'start' ? '起点' : '终点'}</legend>
        <div className={s.grid2}>{(['X', 'Y', '朝向 °'] as const).map((label, index) => <label key={label} className={s.field}>
          <span>{label}</span><Input aria-label={`${key === 'start' ? '起点' : '终点'} ${label}`} type="number" step="any" disabled={index === 2 && motion.motion_model === 'holonomic'}
            value={index === 2 ? Number((motion[key][index] * 180 / Math.PI).toFixed(8)) : motion[key][index]}
            onChange={(event) => { const n = event.target.valueAsNumber; if (!Number.isFinite(n)) return;
              const pose = [...motion[key]] as Pose; pose[index] = index === 2 ? n * Math.PI / 180 : n; change({ [key]: pose }); }} />
        </label>)}</div>
      </fieldset>)}
      <label className={s.field}><span>运动模型</span><Select aria-label="运动模型" value={motion.motion_model} onChange={(event) => change({ motion_model: event.target.value })}>
        <option value="holonomic">全向几何路径</option><option value="dubins">Dubins · 仅前进</option><option value="reeds_shepp">Reeds–Shepp · 可倒车</option>
      </Select></label>
      <div className={s.grid2}>{([['turning_radius', '最小转弯半径 m'], ['robot_radius', '机器人半径 m'], ['clearance_margin', '净空余量 m'], ['goal_tolerance', '终点容差 m']] as const).map(([key, label]) =>
        <label key={key} className={s.field}><span>{label}</span><Input aria-label={label} type="number" min={0} step="any" value={motion[key]}
          onChange={(event) => { const n = event.target.valueAsNumber; if (Number.isFinite(n)) change({ [key]: n }); }} /></label>)}</div>
      <p className={s.hint}>改变运动模型会重新筛选适用算法。</p>
    </> : <TaskEditor scenario={scenario} instance={draft} onChange={(value) => edit(() => value)} />}
    <details><summary>完整任务输入 · JSON</summary>
      <p className={s.meta}>编辑边界、孔洞、路网和约束，应用后自动保存。</p>
      <textarea aria-label="完整规划输入 JSON" value={text} spellCheck={false} rows={15}
        style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'monospace', fontSize: 11 }}
        onChange={(event) => { setText(event.target.value); setDirty(true); setError(null); }} />
      <div className={s.row}><Button size="sm" loading={busy} disabled={!dirty} onClick={() => void apply()}>校验并应用</Button>
        <Button size="sm" disabled={!dirty || busy} onClick={() => { setText(canonical); setDirty(false); setError(null); }}>放弃文本修改</Button></div>
      {error && <Notice tone="error">{error}</Notice>}
    </details>
  </div>;
}
