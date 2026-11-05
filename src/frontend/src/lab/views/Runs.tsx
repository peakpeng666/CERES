import { useState } from 'react';
import type { Route } from '../../app/router';
import { lab,exportUrl,useLabResource,fmt,downloadCompare } from '../api';
import { Page } from '../shell/Page';
import { Card,Notice,SourceBadge,tableClass,Button,Select } from '../ui';
import { RUN_STATE_LABEL,STATUS_LABEL,simBlockReason } from './plan/planModel';
import { analyzeComparability,filterRuns,distinct,toggleCompare,parseCompare } from './Runs.logic';
import { IDENTITY_LABEL, identityState } from './plan/resultIntegrity';
import s from './Workbench.module.css';
export function Runs({route}:{route:Route}){
  const data=useLabResource('runs-'+route.id,()=>lab.runs({limit:100}),{pollMs:3000});
  const [filters,setFilters]=useState({scenario:'',problemClass:'',source:'',state:''});
  const [selected,setSelected]=useState<string[]>(parseCompare(route.params.get('compare')));const [error,setError]=useState('');
  const all=data.data?.runs??[];const rows=filterRuns(all,filters);const chosen=all.filter(r=>selected.includes(r.run_id));const compare=analyzeComparability(chosen);
  return <Page title="运行记录" subtitle="成功、失败、超时与取消均保留；同实例、同单位结果才可直接比较">
    {(data.error||error)&&<Notice tone="error">{error||data.error?.message}</Notice>}
    <Card title="筛选与对比"><div className={s.pad}>
      <div className={s.filterBar}><label className={s.field}>场景<Select aria-label="筛选场景" value={filters.scenario} onChange={e=>setFilters({...filters,scenario:e.target.value})}><option value="">全部场景</option>{distinct(all.map(r=>r.scenario_id)).map(v=><option key={v}>{v}</option>)}</Select></label><label className={s.field}>状态<Select aria-label="筛选状态" value={filters.state} onChange={e=>setFilters({...filters,state:e.target.value})}><option value="">全部状态</option>{Object.entries(RUN_STATE_LABEL).map(([k,v])=><option value={k} key={k}>{v}</option>)}</Select></label><label className={s.field}>来源<Select aria-label="筛选来源" value={filters.source} onChange={e=>setFilters({...filters,source:e.target.value})}><option value="">全部来源</option><option value="project">项目算法</option><option value="third_party">第三方</option><option value="panel_baseline">演示基线</option></Select></label></div>
      <p>显示 {rows.length} / 最近 {all.length} 次；已选 {chosen.length} 次。</p>
      {chosen.length>1&&!compare.allComparable&&<Notice tone="warn">所选结果包含不同实例或成本单位，不汇总排名。{Object.values(compare.reasons).flat().join('；')}</Notice>}
      {chosen.length>0&&<><div className={s.compareList}>{chosen.map(r=><div key={r.run_id} className={s.compareItem}><strong>{fmt.algorithm(r.algorithm)}</strong><p>{identityState(r)==='quarantined'?'已隔离 · 不显示数值':r.check?.valid?`成本 ${fmt.num(r.objective.value)} ${r.objective.unit}`:'无检查通过的解'} · {IDENTITY_LABEL[identityState(r)]}<br/>时间 {fmt.num(r.metrics.wall_s,3)} s · {RUN_STATE_LABEL[r.state]}</p><small>{r.run_id}<br/>实例 {r.instance_sha256.slice(0,12)} · seed {String(r.params.seed??'—')}</small><div><Button onClick={()=>setSelected(toggleCompare(selected,r.run_id))}>取消选择</Button></div></div>)}</div><div className={s.row}><Button onClick={()=>void downloadCompare(selected,'csv').catch(e=>setError(String(e)))}>导出对比 CSV</Button><Button onClick={()=>void downloadCompare(selected,'json').catch(e=>setError(String(e)))}>导出对比 JSON</Button><Button onClick={()=>setSelected([])}>清空选择</Button></div></>}
    </div></Card>
    <Card><div className={s.pad}><div className={s.tableScroll}><table className={tableClass}><thead><tr><th>对比</th><th>时间 / 场景</th><th>算法来源</th><th>结果</th><th>成本 / 检查</th><th>操作</th></tr></thead><tbody>
      {rows.map(r=><tr key={r.run_id}><td><input type="checkbox" aria-label={`对比 ${r.run_id}`} checked={selected.includes(r.run_id)} onChange={()=>setSelected(toggleCompare(selected,r.run_id))}/></td><td>{fmt.time(r.created_utc)}<div>{r.scenario_id}</div><small>{r.run_id}</small></td><td>{fmt.algorithm(r.algorithm)}<div><SourceBadge source={r.algorithm.source}/></div></td><td>{RUN_STATE_LABEL[r.state]} · {r.status?STATUS_LABEL[r.status]:''}<div>{r.error}</div><div data-identity={identityState(r)}>{IDENTITY_LABEL[identityState(r)]}</div></td><td>{identityState(r)==='quarantined'?<>已隔离 · 不显示数值<div>原始记录仅供审计</div></>:<>{fmt.num(r.objective.value)} {r.objective.unit}<div>{r.check?(r.check.valid?'检查通过':'检查不通过'):'未检查'}</div></>}</td><td><div className={s.row}><a href={`#/plan/${r.scenario_id}?run=${r.run_id}`}>查看</a><a href={exportUrl.run(r.run_id,'json')} download>JSON</a>{!simBlockReason(r,r.scenario_snapshot?.sim)&&<a href={`#/sim?run=${r.run_id}`}>仿真</a>}</div></td></tr>)}
    </tbody></table></div>{rows.length===0&&<p>没有匹配的实验记录。</p>}</div></Card>
  </Page>;
}
