import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Download, Play, RefreshCw, Upload } from 'lucide-react';
import { buildHash, navigate, type Route } from '../app/router';
import { useAsync } from '../data/api';
import { Async, EmptyState, ErrorState, Panel } from '../components/ui';
import {
  DEMO_JSON, DEMO_PROBLEM, errorMessage, getAlgorithms, getExperiments, getHealth, getJob, getJobs,
  getRouteScene, getRuns, parseProblem, projectPoints, submitJob, terminalJob,
  type ExperimentSummary, type Job, type RouteScene,
} from '../data/workspace-api';
import s from './JobsView.module.css';

const STATUS: Record<string, string> = {
  queued: '排队中', running: '运行中', completed: '执行完成', failed: '失败', interrupted: '运行中断',
  OK: '可行解', TIMEOUT_WITH_FEASIBLE_INCUMBENT: '到达预算，保留可行解',
  TIMEOUT_NO_SOLUTION: '预算内未返回可行解', NO_SOLUTION: '未返回可行解',
  CONSTRUCTION_FAILURE: '构造失败', INVALID_SOLUTION: '验算未通过',
  feasible: '完整服务', full_service: '完整服务', partial: '部分服务', invalid: '无效候选', no_solution: '未返回解', infeasible: '不可行',
};
const label = (value: string) => STATUS[value] ?? value;
const TIMING_SCOPE: Record<string, string> = {
  '5s_end_to_end': '5 秒端到端',
  '1s_search_loop': '1 秒搜索循环',
};
const timingScope = (value?: string) => value ? (TIMING_SCOPE[value] ?? value) : '未标记';
const number = (value: number | null | undefined, digits = 3) => value == null ? '未记录' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
const COLORS = ['#2f6fb3', '#d08317', '#1a7f4b', '#4b47a8', '#b5482f'];

function download(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function RoutePlot({ points, routes, valid, ids }: {
  points: [number, number][]; routes: { id: number; nodes: number[] }[]; valid: boolean; ids?: number[];
}) {
  const projected = projectPoints(points);
  return <>
    <svg className={s.plot} viewBox="0 0 760 360" role="img" aria-label="XY 坐标中的任务点与车辆访问顺序，Y 轴向上">
      <text x="18" y="24" className={s.axis}>Y ↑</text><text x="724" y="342" className={s.axis}>X →</text>
      {routes.map((route, index) => <polyline key={route.id}
        points={route.nodes.filter((n) => projected[n]).map((n) => projected[n].join(',')).join(' ')}
        fill="none" stroke={valid ? COLORS[index % COLORS.length] : '#b5482f'} strokeWidth="2.5"
        strokeDasharray={valid ? undefined : '6 4'} opacity="0.8" />)}
      {projected.map(([x, y], index) => <g key={ids?.[index] ?? index}>
        {index === 0 ? <rect x={x - 6} y={y - 6} width="12" height="12" rx="2" fill="#1b2126" />
          : <circle cx={x} cy={y} r="4.5" fill="#fff" stroke="#1b2126" strokeWidth="1.8" />}
        <text x={x + 8} y={y - 9} className={s.node}>{index === 0 ? '仓库 0' : ids?.[index] ?? index}</text>
      </g>)}
    </svg>
    {routes.length > 0 && <div className={s.legend}>{routes.map((route, index) => <span key={route.id}>
      <i style={{ background: valid ? COLORS[index % COLORS.length] : '#b5482f' }} />车辆 {route.id}
    </span>)}</div>}
  </>;
}

function JobDetails({ id }: { id: string }) {
  const [loaded, setLoaded] = useState<Job | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    setError(null);
    const poll = async () => {
      try {
        const job = await getJob(id, controller.signal);
        if (controller.signal.aborted) return;
        setLoaded(job);
        if (!terminalJob(job.status)) timer = setTimeout(poll, 700);
      } catch (err) { if (!controller.signal.aborted) setError(err as Error); }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, attempt]);
  if (error) return <ErrorState error={error} onRetry={() => setAttempt((n) => n + 1)} />;
  // Identity check also covers the render before the new effect starts.
  if (!loaded || loaded.id !== id) return <p className={s.note} role="status">正在读取任务…</p>;
  const job = loaded, result = job.result;
  const valid = result?.feasible === true;
  const routes = valid && result.routes ? result.routes.map((nodes, index) => ({ id: index + 1, nodes })) : [];
  return <Panel title="运行结果" subtitle={job.id} aside={<button className={s.button} onClick={() => download(job, `${job.id}.json`)}>
    <Download size={14} /> 导出记录</button>}>
    <div className={s.resultBody}>
      <p className={valid ? s.success : terminalJob(job.status) ? s.failure : s.pending} role="status">
        {label(job.status)}{result ? ` · ${label(result.status)}` : ''}
      </p>
      {job.error != null && <p className={s.failure}>{errorMessage(job.error)}</p>}
      {result?.worker_error && <p className={s.failure}>{result.worker_error.message}</p>}
      <div className={s.metrics}>
        <div><span>验算成本</span><strong>{number(valid ? result.cost : null)}</strong><small>EUC_2D 整数边权</small></div>
        <div><span>车辆数</span><strong>{valid ? routes.length : '未记录'}</strong><small>上限 {job.request.K}</small></div>
        <div><span>求解进程耗时</span><strong>{number(result?.process_wall_seconds)} <small>s</small></strong></div>
        <div><span>独立验算耗时</span><strong>{number(result?.checker_wall_seconds, 6)} <small>s</small></strong></div>
      </div>
      <RoutePlot points={job.request.coordinates} routes={routes} valid={valid} />
      <p className={s.note}>折线表示访问顺序，XY 坐标未标定为米。道路、障碍和车辆运动约束须另行验证。</p>
      {routes.length > 0 && <div className={s.routeList}>{routes.map((route) => <p key={route.id}><b>车辆 {route.id}</b> {route.nodes.join(' → ')}</p>)}</div>}
      <details><summary>任务身份与验算记录</summary><dl className={s.identity}>
        <dt>场景</dt><dd>{job.scene_id} · revision {job.scene_revision}</dd>
        <dt>算法 / 种子 / 预算</dt><dd>{job.request.algorithm} / {job.request.seed} / {job.request.budget_seconds} s</dd>
        <dt>输入 SHA-256</dt><dd>{job.input_sha256}</dd><dt>源码 SHA-256</dt><dd>{job.source_sha256}</dd>
        <dt>开始 / 完成</dt><dd>{job.started_at ?? '尚未开始'} / {job.finished_at ?? '尚未结束'}</dd>
      </dl><pre className={s.code}>{JSON.stringify(result?.checkpoint_checks ?? [], null, 2)}</pre></details>
    </div>
  </Panel>;
}

function TaskWorkspace({ route }: { route: Route }) {
  const parsedOffset = Number(route.params.get('offset') ?? 0);
  const offset = Number.isInteger(parsedOffset) && parsedOffset >= 0 && parsedOffset <= 2147483647 ? parsedOffset : 0;
  const pageSize = 50;
  const algorithms = useAsync('algorithms', (signal) => getAlgorithms(signal));
  const [raw, setRaw] = useState(DEMO_JSON);
  const [algorithm, setAlgorithm] = useState('savings');
  const [seed, setSeed] = useState('0');
  const [budget, setBudget] = useState('1');
  const [submitting, setSubmitting] = useState(false);
  const sending = useRef(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [jobs, setJobs] = useState<Awaited<ReturnType<typeof getJobs>> | null>(null);
  const [listError, setListError] = useState<Error | null>(null);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    setListError(null);
    const poll = async () => {
      try {
        const value = await getJobs(controller.signal, offset, pageSize);
        if (controller.signal.aborted) return;
        setJobs(value); setListError(null);
        timer = setTimeout(poll, value.counts.queued + value.counts.running > 0 ? 1200 : 6000);
      } catch (error) { if (!controller.signal.aborted) setListError(error as Error); }
    };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [revision, offset]);
  const displayedJobs = jobs?.offset === offset ? jobs : null;
  const turnPage = (next: number) => navigate(buildHash('jobs', route.id, { offset: Math.max(0, next) }));

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (sending.current) return;
    setSubmitError(null);
    try {
      const problem = parseProblem(raw), parsedSeed = Number(seed);
      if (seed.trim() === '' || !Number.isInteger(parsedSeed) || parsedSeed < 0 || parsedSeed > 4294967295) throw new Error('种子须是 0 至 4294967295 的整数。');
      sending.current = true; setSubmitting(true);
      const job = await submitJob({ ...problem, algorithm, seed: parsedSeed, budget_seconds: Number(budget) });
      navigate(buildHash('jobs', job.id)); setRevision((n) => n + 1);
    } catch (error) { setSubmitError(errorMessage(error)); setRevision((n) => n + 1); }
    finally { sending.current = false; setSubmitting(false); }
  };

  return <>
    <div className={s.workspace}>
      <Panel title="提交路径任务" subtitle="容量约束车辆路径规划 · 工程运行">
        <form className={s.form} onSubmit={submit}>
          <p className={s.note}>导入 XY 坐标、需求、容量及车队上限。第 0 个点为仓库，以下为可编辑工程示例。</p>
          <div className={s.actions}>
            <label className={s.button}><Upload size={14} /> 导入 JSON<input type="file" accept=".json,application/json" className={s.file}
              onChange={async (event) => {
                const file = event.target.files?.[0]; if (!file) return;
                try {
                  if (file.size > 1024 * 1024) throw new Error('JSON 文件上限为 1 MiB。');
                  const text = await file.text(); parseProblem(text); setRaw(text); setSubmitError(null);
                } catch (error) { setSubmitError(errorMessage(error)); }
                event.target.value = '';
              }} /></label>
            <button type="button" className={s.button} onClick={() => { setRaw(DEMO_JSON); setSubmitError(null); }}>载入示例</button>
          </div>
          <label>任务 JSON<textarea aria-label="任务 JSON" className={s.editor} value={raw} onChange={(e) => setRaw(e.target.value)} spellCheck={false} /></label>
          <Async state={algorithms}>{(data) => <label>算法<select aria-label="算法" value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
            {data.algorithms.map((a) => <option key={a.id} value={a.id} disabled={!a.available}>{a.id}{a.available ? '' : `（${a.reason ?? '不可用'}）`}</option>)}
          </select></label>}</Async>
          <div className={s.fields}><label>随机种子<input value={seed} type="number" min="0" max="4294967295" step="1" onChange={(e) => setSeed(e.target.value)} /></label>
            <label>求解预算<select value={budget} onChange={(e) => setBudget(e.target.value)}><option value="1">1 秒</option><option value="5">5 秒</option></select></label></div>
          {submitError && <p className={s.failure} role="alert">{submitError}</p>}
          <button className={s.primary} type="submit" disabled={submitting || algorithms.status !== 'ready' || !algorithms.data?.algorithms.some((a) => a.id === algorithm && a.available)}>
            <Play size={16} />{submitting ? '提交中…' : '运行并独立验算'}</button>
          <p className={s.note}>任务按顺序执行，每次提交保留独立记录。这里的工程运行不计入正式基线批次。</p>
        </form>
      </Panel>
      <div className={s.resultColumn}>{route.id ? <JobDetails key={route.id} id={route.id} />
        : <Panel title="任务与路线"><RoutePlot points={DEMO_PROBLEM.coordinates} routes={[]} valid={false} />
          <EmptyState title="提交任务后查看结果" detail="将显示实际返回的路线、运行状态、独立验算结果和可导出的完整记录。" /></Panel>}</div>
    </div>
    <Panel title="运行记录" subtitle="失败和中断记录会保留。点击任一任务继续查看。"
      aside={<button className={s.button} onClick={() => setRevision((n) => n + 1)}><RefreshCw size={14} /> 刷新</button>}>
      {listError ? <ErrorState error={listError} onRetry={() => setRevision((n) => n + 1)} />
        : !displayedJobs ? <p className={s.note}>正在读取…</p> : <>
          <div className={s.pagination}>
            <p>共 {displayedJobs.total} 项 · 排队 {displayedJobs.counts.queued} · 运行 {displayedJobs.counts.running} · 失败 {displayedJobs.counts.failed} · 中断 {displayedJobs.counts.interrupted}</p>
            <div><button className={s.button} disabled={offset === 0} onClick={() => turnPage(offset - pageSize)}>上一页</button>
              <span>{displayedJobs.jobs.length ? `${offset + 1}–${offset + displayedJobs.jobs.length}` : '无记录'}</span>
              <button className={s.button} disabled={offset + pageSize >= displayedJobs.total} onClick={() => turnPage(offset + pageSize)}>下一页</button></div>
          </div>
          {displayedJobs.jobs.length === 0 ? <EmptyState title={offset ? '本页暂无记录' : '暂无运行记录'} />
            : <div className={s.tableWrap}><table><thead><tr><th>任务</th><th>状态</th><th>可行性</th><th>成本</th><th>提交时间</th></tr></thead>
              <tbody>{displayedJobs.jobs.map((job) => <tr key={job.id} aria-selected={route.id === job.id}>
                <td><a href={buildHash('jobs', job.id, { offset })}>{job.id}</a></td><td>{label(job.status)}{job.result_status ? ` · ${label(job.result_status)}` : ''}</td>
                <td>{job.feasible == null ? terminalJob(job.status) ? '未产生验算结果' : '待验算' : job.feasible ? '通过' : '未通过'}</td><td>{number(job.cost)}</td><td>{job.created_at}</td>
              </tr>)}</tbody></table></div>}
        </>}
    </Panel>
  </>;
}

function SceneDetails({ scene }: { scene: RouteScene }) {
  const nodes = [scene.depot, ...scene.task_nodes], indices = new Map(nodes.map((node, i) => [node.node_id, i]));
  const routes = scene.vehicles.map((v) => ({ id: v.vehicle_id, nodes: v.visit_node_ids.map((id) => indices.get(id)!).filter((i) => i != null) }));
  return <Panel title="归档路线" subtitle={`${scene.experiment_id} · revision ${scene.revision} · ${scene.run_id}`}
    aside={<button className={s.button} onClick={() => download(scene, `${scene.run_id}-route-scene.json`)}><Download size={14} /> 导出场景</button>}>
    <div className={s.resultBody}>
      <p className={scene.status.full_service_success ? s.success : s.failure}>{label(scene.status.solution_status)}</p>
      <RoutePlot points={nodes.map((n) => [n.x, n.y])} ids={nodes.map((n) => n.node_id)} routes={routes} valid={scene.status.full_service_success} />
      <p>服务点 {number(scene.metrics.served_task_count, 0)} / {scene.metrics.task_count} · 记录距离 {number(scene.metrics.total_distance)}</p>
      <p className={s.note}>坐标单位：{scene.units.coordinate ?? '未记录'}。访问顺序仅用于展示；该场景未获得机器人导航许可。</p>
    </div>
  </Panel>;
}

function ArchiveRoutes({ experiment }: { experiment: ExperimentSummary }) {
  const runs = useAsync(`${experiment.id}:${experiment.revision}`, (signal) => getRuns(experiment.id, experiment.revision, signal));
  const [run, setRun] = useState('');
  const scene = useAsync(run ? `${experiment.id}:${experiment.revision}:${run}` : null,
    (signal) => getRouteScene(experiment.id, experiment.revision, run, signal));
  return <><Async state={runs}>{(data) => <label className={s.runPicker}>归档运行<select aria-label="归档运行" value={run} onChange={(e) => setRun(e.target.value)}>
    <option value="">请选择运行</option>{data.runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id} · {label(r.solution_status)}</option>)}
  </select></label>}</Async>
    {run && <Async state={scene}>{(value) => value.run_id === run && value.experiment_id === experiment.id && value.revision === experiment.revision
      ? <SceneDetails scene={value} /> : <p role="status">正在切换运行…</p>}</Async>}</>;
}

function ArchiveWorkspace() {
  const experiments = useAsync('live-experiments', (signal) => getExperiments(signal));
  const [selected, setSelected] = useState<ExperimentSummary | null>(null);
  return <><Panel title="实验档案" subtitle="读取后端保存的实验版本。带计时口径的基线分行显示；5 秒端到端和 1 秒搜索循环不合成同一预算。">
    <Async state={experiments}>{(data) => data.experiments.length === 0 ? <EmptyState title="后端暂无已导入实验" detail="通过后端导入命令载入实验包后，在这里刷新查看。" />
      : <div className={s.tableWrap}><table data-testid="archive-experiments"><thead><tr><th>实验</th><th>计时口径</th><th>结果摘要</th><th>领域</th><th>版本</th><th>状态</th><th>路线</th></tr></thead>
        <tbody>{data.experiments.map((item) => <tr key={item.id} data-timing-scope={item.parameters?.timing_scope ?? ''}><td>{item.title}<small className={s.recordId}>{item.id}</small></td>
          <td>{timingScope(item.parameters?.timing_scope)}</td>
          <td className={item.parameters?.headline ? s.wrapCell : undefined}>{item.parameters?.headline ?? '—'}</td>
          <td>{item.domain}</td>
          <td>{item.revision}</td><td>{item.status}</td><td>{item.routing ? <button className={s.button} onClick={() => setSelected(item)}>查看路线</button> : '无路线档案'}</td></tr>)}</tbody></table></div>}</Async>
  </Panel>{selected && <ArchiveRoutes key={`${selected.id}:${selected.revision}`} experiment={selected} />}</>;
}

export function JobsView({ route }: { route: Route }) {
  const [healthRevision, setHealthRevision] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setHealthRevision((value) => value + 1), 6000);
    return () => clearInterval(timer);
  }, []);
  const health = useAsync(`workspace-health:${healthRevision}`, (signal) => getHealth(signal));
  const archive = route.params.get('tab') === 'archives';
  return <div className={s.page}>
    <header className={s.header}><div><p className={s.eyebrow}>路径规划</p><h1>路径任务工作台</h1>
      <p>导入任务，运行算法，核对结果与原始记录。</p></div>
      <div className={s.connection} role="status">{health.status === 'ready' ? '● 本地后端已连接' : health.status === 'loading' ? '连接后端…' : '后端未连接'}</div></header>
    <nav className={s.tabs} aria-label="任务工作台视图"><a aria-current={!archive ? 'page' : undefined} href={buildHash('jobs')}>路径任务</a>
      <a aria-current={archive ? 'page' : undefined} href={buildHash('jobs', null, { tab: 'archives' })}>实验档案</a></nav>
    {health.status === 'error' && <ErrorState error={health.error} onRetry={health.retry} />}
    {archive ? <ArchiveWorkspace /> : <TaskWorkspace route={route} />}
  </div>;
}
