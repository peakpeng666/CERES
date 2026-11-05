import { useEffect, useMemo, useState } from 'react';
import type { Route } from '../../../app/router';
import { lab, exportUrl, fmt, useGatewayConnected, useLabResource } from '../../api';
import type { Instance, Recommendation, Solution } from '../../types';
import { Page } from '../../shell/Page';
import { Button, Card, Notice, Select, Input, SourceBadge, Metric } from '../../ui';
import { MapCanvas } from '../../canvas/MapCanvas';
import { baseScene, solutionPaths, violationMarkers, RUN_STATE_LABEL, STATUS_LABEL, simBlockReason } from './planModel';
import s from '../Workbench.module.css';
import { TaskEditor } from './TaskEditor';
import { asTurn, asCvrp, toggleTurnTask, addCvrpPoint, parseEditableInstance } from './planModel';
import { coveragePercent, withoutInvalidatedResults } from './resultIntegrity';
import { IntegrityNotice } from './IntegrityNotice';

export function PlanView({ route }: { route: Route }) {
  return <PlanWorkspace key={`${route.id??'maze-inspection'}:${route.params.get('run')??''}`} route={route}/>;
}

function PlanWorkspace({ route }: { route: Route }) {
  const connected=useGatewayConnected();const id=route.id ?? 'maze-inspection';
  const scenarios=useLabResource('scenarios',()=>lab.scenarios());
  const scenario=useLabResource(id,()=>lab.scenario(id));
  const algorithms=useLabResource('algorithms',()=>lab.algorithms());
  const [text,setText]=useState('');const [recommendation,setRecommendation]=useState<Recommendation|null>(null);
  const [selected,setSelected]=useState('urp.turn_mode_dp');const [params,setParams]=useState<Record<string,unknown>>({});
  const [runId,setRunId]=useState<string|null>(route.params.get('run'));const [rawRun,setRun]=useState<Solution|null>(null);
  const run=useMemo(()=>withoutInvalidatedResults(rawRun),[rawRun]);
  const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
  const [editing,setEditing]=useState(false);
  const savedRun=route.params.get('run');
  const sc=savedRun&&run?.scenario_snapshot?run.scenario_snapshot:scenario.data;
  useEffect(()=>{if(scenario.data){setText(JSON.stringify(scenario.data.instance,null,2));setRunId(savedRun);setRun(null);setRecommendation(null);}},[scenario.data,savedRun]);
  const inst=useMemo(()=>parseEditableInstance(text,sc?.problem_class),[text,sc?.problem_class]);
  const alg=algorithms.data?.algorithms.find(a=>a.id===selected);
  useEffect(()=>{if(alg)setParams(savedRun&&run?.run_id===savedRun&&run.algorithm.id===alg.id?run.params:recommendation?.ranking.find(r=>r.algorithm_id===alg.id)?.suggested_params??Object.fromEntries(alg.params.map(p=>[p.name,p.default])));},[alg,savedRun,run?.run_id,recommendation]);
  useEffect(()=>{let active=true;if(!scenario.data)return;
    if(savedRun)return;
    lab.recommend(id).then(r=>{if(active){setRecommendation(r);if(r.recommended)setSelected(r.recommended);}}).catch(e=>{if(active)setError(e.message);});
    return()=>{active=false;};
  },[id,scenario.data,savedRun]);
  useEffect(()=>{if(!runId)return;let active=true;let timer:number;
    const poll=async()=>{try{const r=await lab.run(runId);if(active){setRun(r);if(savedRun===runId&&r.instance){setText(JSON.stringify(r.instance,null,2));setSelected(r.algorithm.id);setRecommendation(null);}if(['queued','running'].includes(r.state))timer=window.setTimeout(poll,500);}}catch(e){if(active)setError(String(e));}};
    void poll();return()=>{active=false;window.clearTimeout(timer);};
  },[runId,savedRun]);
  const scene=useMemo(()=>{if(!sc||!inst)return null;try{const out=baseScene(sc,inst);if(run){out.paths=solutionPaths([{solution:run,colorIndex:0,current:JSON.stringify(run.instance)===JSON.stringify(inst),focused:true}],sc.problem_class,run.instance??inst,null);out.markers=violationMarkers([run]);}return out;}catch{return null;}},[sc,inst,run]);
  async function solve(){if(!inst)return;setBusy(true);setError(null);try{const j=await lab.solve(id,[{id:selected,params}],inst);setRun(null);setRunId(j.runs[0]);}catch(e){setError(String(e));}finally{setBusy(false);}}
  const pending=busy||!!run&&['queued','running'].includes(run.state);
  function edit(i:Instance){setText(JSON.stringify(i,null,2));setRun(null);setRunId(null);setRecommendation(null);setError(null);}
  return <Page title="经典视图" subtitle="场景 → 推荐 → 求解 → 验算 → 仿真" actions={<a href="#/runs">运行记录</a>}>
    {!connected&&<Notice tone="error">网关连接已断开，当前显示可能为旧数据；连接恢复后可重试。</Notice>}
    {(error||scenario.error||algorithms.error)&&<Notice tone="error">{error||scenario.error?.message||algorithms.error?.message}</Notice>}
    <div className={s.workspace}><aside className={s.stack}>
      <Card title="1. 场景与任务"><div className={s.pad}>
        <label className={s.field}>演示场景<Select aria-label="演示场景" value={id} onChange={e=>{location.hash=`#/plan/${e.target.value}`;setError(null);}}>{scenarios.data?.scenarios.map(c=><option key={c.id} value={c.id}>{c.title}</option>)}</Select></label>
        {sc&&<><p>{sc.summary}</p><div className={s.meta}>{sc.frame.kind==='map'?`${sc.frame.world} · map 坐标 · 米`:sc.frame.kind==='field'?'实地坐标 · 米':'基准坐标 · 无量纲 · 不可直接仿真'}</div>
          {inst&&<TaskEditor scenario={sc} instance={inst} onChange={edit}/>}
          {sc.problem_class!=='coverage'&&<label className={s.row}><input type="checkbox" checked={editing} onChange={e=>setEditing(e.target.checked)}/>在地图上编辑任务（路网点选；CVRP 点击空白添加）</label>}
          <details><summary>编辑任务与场景参数 (JSON)</summary><textarea aria-label="场景实例 JSON" className={s.editor} value={text} onChange={e=>{setText(e.target.value);setRun(null);setRunId(null);setRecommendation(null);}}/><Button onClick={()=>setText(JSON.stringify(sc.instance,null,2))}>恢复场景默认值</Button>{!inst&&<Notice tone="error">JSON 语法或实例结构无效，请恢复默认值后编辑。</Notice>}</details>
          <details><summary>场景来源与坐标说明</summary><p>{sc.provenance.note}</p><pre>{JSON.stringify(sc.provenance,null,2)}</pre></details></>}
      </div></Card>
      <Card title="2. 推荐与算法"><div className={s.pad}>
        <Button disabled={!inst||!connected} onClick={async()=>{try{const r=await lab.recommend(id,inst!);setRecommendation(r);if(r.recommended)setSelected(r.recommended);}catch(e){setError(String(e));}}}>按当前任务更新推荐</Button>
        {recommendation&&<><p>{recommendation.explanation}</p><div className={s.meta}>推荐策略 {recommendation.policy} · {recommendation.learned_selector.prediction?.kind==='static'?<>经验尝试顺序（开发侧数据拟合，非逐实例学习模型）：{recommendation.learned_selector.status==='available'?'已留出田块评估':'未就绪 / 不适用'}</>:<>学习型：{recommendation.learned_selector.status==='available'?'已训练并独立留出评估':'未就绪 / 不适用'}</>}</div><p className={s.muted}>{recommendation.learned_selector.reason}</p>{recommendation.learned_selector.prediction?.order&&<details><summary>覆盖配置尝试顺序：先试第 1 项，独立验算失败再试下一项</summary><ol className={s.muted}>{recommendation.learned_selector.prediction.order.slice(0,6).map(o=><li key={o.config}>{o.label??o.config}（{o.algorithm_id}）</li>)}</ol></details>}{recommendation.learned_selector.evaluation&&<details><summary>学习推荐的评估证据与适用范围</summary><p>训练 {recommendation.learned_selector.evaluation.train_n}、校准 {recommendation.learned_selector.evaluation.calibration_n}、独立测试 {recommendation.learned_selector.evaluation.test_n}。测试中接受替代 {recommendation.learned_selector.evaluation.alternative_count} 次，成本恶化超过 1% 共 {recommendation.learned_selector.evaluation.harmful_instances} 次；平均改善 {fmt.num(recommendation.learned_selector.evaluation.mean_improvement_pct,3)}%。</p><p>{recommendation.learned_selector.scope}</p><pre>{JSON.stringify(recommendation.learned_selector,null,2)}</pre></details>}</>}
        <label className={s.field}>求解算法<Select aria-label="求解算法" value={selected} onChange={e=>setSelected(e.target.value)}>{algorithms.data?.algorithms.filter(a=>sc&&a.problem_classes.includes(sc.problem_class)).map(a=><option key={a.id} value={a.id} disabled={!a.available}>{a.name}{!a.available?'（不可用）':''}</option>)}</Select></label>
        {alg&&<><SourceBadge source={alg.source}/><p>{alg.description}</p><div className={s.meta}>{alg.origin}</div>{alg.params.map(p=><label className={s.field} key={p.name}>{p.label}<Input type="number" min={p.min??undefined} max={p.max??undefined} step={p.type==='integer'?1:.05} aria-label={p.label} value={Number(params[p.name]??p.default)} onChange={e=>setParams(v=>({...v,[p.name]:Number(e.target.value)}))}/></label>)}</>}
        {recommendation&&<details><summary>全部算法的适用性与推荐理由</summary>{recommendation.ranking.map(r=><p key={r.algorithm_id}><strong>{r.algorithm_id} · {r.suitability}</strong><br/>{r.reasons.join('；')}</p>)}</details>}
        <Button variant="primary" disabled={!sc||!inst||!connected||pending||!alg?.available} onClick={()=>void solve()}>求解并验算</Button>
        {pending&&runId&&<Button variant="destructive" onClick={()=>lab.cancelRun(runId).catch(e=>setError(String(e)))}>取消求解</Button>}
      </div></Card>
    </aside><div className={s.stack}>
      <div className={s.canvas}>{scene?<MapCanvas scene={scene} fitKey={id} ariaLabel="二维场景与规划路径" onNodeClick={editing&&sc?.problem_class==='turn_routing'&&inst?n=>edit(toggleTurnTask(asTurn(inst),Number(n.id))):undefined} onBackgroundClick={editing&&sc?.problem_class==='euclidean_cvrp'&&inst?xy=>edit(addCvrpPoint(asCvrp(inst),xy)):undefined}/>:<div className={s.pad}>正在读取场景；编辑内容无效时不会绘制。</div>}</div>
      <Card title="3. 求解结果与检查"><div className={s.pad}>
        {!run?<p>{pending?'正在求解…':'尚无本次实验结果。选择参数后求解。'}</p>:<>
          <div className={s.row}><strong>{RUN_STATE_LABEL[run.state]} · {run.status?STATUS_LABEL[run.status]:''}</strong><span className={s.meta}>{run.run_id}</span></div>
          <p>{fmt.algorithm(run.algorithm)} · <SourceBadge source={run.algorithm.source}/></p>
          <IntegrityNotice run={rawRun}/>
          {run.error&&<Notice tone="error">{run.error}</Notice>}
          <div className={s.metrics}><Metric label="目标成本" value={fmt.num(run.objective.value)} unit={run.objective.unit}/><Metric label="几何长度" value={fmt.num(run.metrics.length)} unit={run.frame.units}/><Metric label="独立检查" value={run.check?(run.check.valid?'通过':'未通过'):'未检查'}/><Metric label="整段路径净空" value={fmt.num(run.map_check?.min_clearance_m)} unit="m"/></div>
          {run.check&&!run.check.valid&&<Notice tone="error">{run.check.errors.join('；')}</Notice>}
          {run.map_check&&<Notice tone={run.map_check.collision_free?'ok':'warn'}>地图净空：{run.map_check.collision_free?'通过':'未通过'} · 机器人半径 {run.map_check.robot_radius_m} m</Notice>}
          {sc?.problem_class==='coverage'&&coveragePercent(run)!==null&&<Notice>覆盖率 {fmt.num(coveragePercent(run))}%</Notice>}
          <div className={s.row}>{(['json','csv','geojson'] as const).map(f=><a key={f} href={exportUrl.run(run.run_id,f)} download>导出 {f.toUpperCase()}</a>)}{run.problem_class === 'fleet_routing' && <a href={exportUrl.run(run.run_id,'tasks-csv')} download>导出任务/趟次 CSV</a>}{!simBlockReason(run,sc?.sim)&&<a className={s.primaryLink} href={`#/sim?run=${run.run_id}`}>进入仿真</a>}</div>
          {simBlockReason(run,sc?.sim)&&<p className={s.muted}>{simBlockReason(run,sc?.sim)}</p>}
          <details><summary>原始结果、检查器与复现信息（无效运行仅供审计）</summary><pre>{JSON.stringify(rawRun,null,2)}</pre></details>
        </>}
      </div></Card>
    </div></div>
  </Page>;
}
