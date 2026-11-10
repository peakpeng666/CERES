import { useState } from 'react';
import {
  AppWindow,
  ArrowRight,
  FileText,
  History,
  Monitor,
  Play,
  RefreshCw,
  RotateCw,
  Square,
  TriangleAlert,
} from 'lucide-react';
import { buildHash } from '../../app/router';
import { fmt, lab, useGatewayConnected, useLabResource } from '../api';
import type { Service, SimState, Solution } from '../types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Notice,
  SERVICE_STATE_LABEL,
  Segmented,
  SourceBadge,
  Spinner,
  StatusDot,
  StatusText,
  Switch,
  serviceTone,
  tableClass,
} from '../ui';
import { Page } from '../shell/Page';
import { LinkButton } from '../shell/LinkButton';
import { fallbackPoll, useEventReload, useReconnectReload } from '../shell/hooks';
import {
  COMPONENT_LABEL,
  COMPONENT_STATE_LABEL,
  SIM_WORLDS,
  WORLD_LABEL,
  componentTone,
  simPhaseLabel,
  simPhaseTone,
  simSessionActive,
  type SimWorld,
} from '../shell/labels';
import { LogSheet } from './LogSheet';
import { SCOPE_NOTES, collectKnownIssues, pendingKey, selectServices, sortServices } from './Dashboard.logic';
import { COST_UNIT_LABEL, RUN_STATE_LABEL, SOLVE_STATUS_LABEL, checkVerdict, label, newestFirst, runTone } from './Runs.logic';
import s from './Dashboard.module.css';

type Action = 'start' | 'stop' | 'restart';

interface SimOptions {
  world: SimWorld;
  gui: boolean;
  rviz: boolean;
}

const ACTION_LABEL: Record<Action, string> = { start: '启动', stop: '停止', restart: '重启' };
const ACTION_ICON = { start: Play, stop: Square, restart: RotateCw };

function actionDisabled(action: Action, state: string): boolean {
  if (action === 'start') return ['running', 'ready', 'starting', 'stopping'].includes(state);
  if (action === 'stop') return ['stopped', 'stopping', 'unavailable'].includes(state);
  return ['starting', 'stopping', 'unavailable'].includes(state);
}

function ServiceRow({
  svc,
  pending,
  error,
  onAction,
  onLog,
}: {
  svc: Service;
  pending: string | null;
  error: string | null;
  onAction: (svc: Service, action: Action) => void;
  onLog: (log: string) => void;
}) {
  const tone = serviceTone(svc.state);
  const stateText = SERVICE_STATE_LABEL[svc.state] ?? svc.state;
  const controls = (svc.controls ?? []) as Action[];
  return (
    <div className={s.svc}>
      <span className={s.svcDot}>
        <StatusDot tone={tone} />
      </span>
      <div className={s.svcBody}>
        <div className={s.svcHead}>
          <span className={s.svcName}>{svc.name || svc.id}</span>
          <span className={s.svcState} data-tone={tone}>
            {stateText}
          </span>
          {svc.kind === 'check' && <Badge tone="gray">检查项</Badge>}
          {svc.port !== null && svc.port !== undefined && <span className={s.svcMeta}>127.0.0.1:{svc.port}</span>}
          {svc.pid !== null && svc.pid !== undefined && <span className={s.svcMeta}>PID {svc.pid}</span>}
        </div>
        {svc.detail && <p className={s.svcDetail}>{svc.detail}</p>}
        {svc.facts?.length > 0 && (
          <dl className={s.facts}>
            {svc.facts.map((f) => (
              <div key={`${f.label}:${f.value}`} className={s.fact}>
                <dt>{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {svc.known_issues?.map((issue) => (
          <p key={issue} className={s.svcIssue}>
            <TriangleAlert size={12} aria-hidden />
            <span>{issue}</span>
          </p>
        ))}
        {error && (
          <div className={s.svcError}>
            <Notice tone="error">
              {svc.name}操作失败：{error}
            </Notice>
          </div>
        )}
      </div>
      {(controls.length > 0 || svc.log) && (
        <div className={s.svcControls}>
          {controls.map((action) => (
            <Button
              key={action}
              size="sm"
              variant={action === 'stop' ? 'destructive' : 'secondary'}
              icon={ACTION_ICON[action]}
              loading={pending === pendingKey(svc.id, action)}
              disabled={!!pending || actionDisabled(action, svc.state)}
              onClick={() => onAction(svc, action)}
            >
              {ACTION_LABEL[action]}
            </Button>
          ))}
          {svc.log && (
            <Button size="sm" variant="plain" icon={FileText} onClick={() => onLog(svc.log as string)}>
              日志
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function SimCard({
  sim,
  error,
  options,
  onOptions,
  onChanged,
}: {
  sim: SimState | null;
  error: Error | null;
  options: SimOptions;
  onOptions: (o: SimOptions) => void;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const phase = sim?.phase ?? null;
  const active = simSessionActive(phase);
  const rvizOn = sim?.components?.rviz === 'running';
  const guiOn = sim?.components?.gui === 'running';

  const act = async (key: string, run: () => Promise<unknown>) => {
    setPending(key);
    setActionError(null);
    try {
      await run();
      onChanged();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setPending(null);
    }
  };

  const pose = (p: [number, number, number] | null | undefined) =>
    p ? `${fmt.num(p[0])}, ${fmt.num(p[1])} · ${fmt.num(p[2])} rad` : '—';

  return (
    <Card
      title="仿真会话"
      subtitle="官方 TurtleBot4 · Gazebo Fortress · Nav2 · ROS 2 Humble"
      actions={sim && <StatusText tone={simPhaseTone(phase)}>{simPhaseLabel(phase)}</StatusText>}
    >
      <div className={s.simBody}>
        {!sim && error && <Notice tone="error">无法读取仿真状态：{error.message}</Notice>}
        {!sim && !error && <Spinner label="读取仿真状态" />}
        {sim && (
          <dl className={s.kv}>
            <div>
              <dt>世界</dt>
              <dd>{sim.world ? WORLD_LABEL[sim.world] ?? sim.world : '—'}</dd>
            </div>
            <div>
              <dt>出生点（map，m）</dt>
              <dd className="num">{sim.spawn ? pose([sim.spawn.x, sim.spawn.y, sim.spawn.yaw]) : '—'}</dd>
            </div>
            <div>
              <dt>会话</dt>
              <dd className="mono">{sim.session_id ?? '—'}</dd>
            </div>
            {sim.session_id&&<div><dt>控制使用的定位</dt><dd>{sim.localization==='ground_truth'?'Gazebo 真值定位（AMCL 旁路观测）':'AMCL + 里程计'}</dd></div>}
            {sim.robot && (
              <>
                <div>
                  <dt>位姿 · Gazebo 真值（world）</dt>
                  <dd className="num">{pose(sim.robot.ground_truth)}</dd>
                </div>
                <div>
                  <dt>位姿 · AMCL 估计（map）</dt>
                  <dd className="num">{pose(sim.robot.amcl)}</dd>
                </div>
              </>
            )}
            {sim.execution && (
              <div>
                <dt>当前执行</dt>
                <dd>
                  <a href={buildHash('sim', null, { run: sim.execution.run_id })} className="mono">
                    {sim.execution.sim_run_id}
                  </a>{' '}
                  · {sim.execution.state}
                </dd>
              </div>
            )}
          </dl>
        )}

        {sim && Object.keys(sim.components ?? {}).length > 0 && (
          <div className={s.chips} aria-label="组件">
            {Object.entries(sim.components).map(([k, v]) => (
              <span key={k} className={s.chip}>
                <StatusDot tone={componentTone(v)} />
                {COMPONENT_LABEL[k] ?? k}
                <span className={s.chipState}>{COMPONENT_STATE_LABEL[v] ?? v}</span>
              </span>
            ))}
          </div>
        )}

        {sim && sim.stages?.length > 0 && (
          <div>
            <h3 className={s.subhead}>阶段耗时（墙钟）</h3>
            <ol className={s.stages}>
              {sim.stages.map((st, i) => (
                <li key={`${st.name}:${i}`}>
                  <span>{st.name}</span>
                  <span className="num">{fmt.sec(st.wall_s)}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {sim?.errors?.map((e) => (
          <Notice key={e} tone="error">
            {e}
          </Notice>
        ))}
        {sim?.warnings?.map((w) => (
          <Notice key={w} tone="warn">
            {w}
          </Notice>
        ))}
        {actionError && <Notice tone="error">{actionError}</Notice>}

        {!active ? (
          <div className={s.simStart}>
            <Segmented
              label="仿真世界"
              value={options.world}
              options={SIM_WORLDS.map((w) => ({ value: w, label: w, disabled: !!pending }))}
              onChange={(world) => onOptions({ ...options, world })}
            />
            <div className={s.switches}>
              <Switch
                checked={options.gui}
                onChange={(gui) => onOptions({ ...options, gui })}
                label="Gazebo 窗口"
                disabled={!!pending}
              />
              <Switch
                checked={options.rviz}
                onChange={(rviz) => onOptions({ ...options, rviz })}
                label="RViz2"
                disabled={!!pending}
              />
            </div>
            <Button
              variant="primary"
              icon={Play}
              loading={pending === 'start'}
              disabled={!!pending || phase === 'stopping'}
              onClick={() => act('start', () => lab.simStart({ world: options.world, gui: options.gui, rviz: options.rviz }))}
            >
              启动仿真
            </Button>
          </div>
        ) : (
          <div className={s.simActions}>
            <Button
              icon={AppWindow}
              loading={pending === 'rviz'}
              disabled={!!pending || phase === 'stopping'}
              onClick={() => act('rviz', () => lab.simRviz(!rvizOn))}
            >
              {rvizOn ? '关闭 RViz' : '打开 RViz'}
            </Button>
            <Button
              icon={Monitor}
              loading={pending === 'gui'}
              disabled={!!pending || phase === 'stopping'}
              onClick={() => act('gui', () => lab.simGui(!guiOn))}
            >
              {guiOn ? '关闭 Gazebo' : '打开 Gazebo'}
            </Button>
            <Button
              variant="destructive"
              icon={Square}
              loading={pending === 'stop'}
              disabled={!!pending || phase === 'stopping'}
              onClick={() => act('stop', () => lab.simStop())}
            >
              停止仿真
            </Button>
            <LinkButton href="#/sim" variant="plain" icon={ArrowRight}>
              仿真视图
            </LinkButton>
          </div>
        )}
      </div>
    </Card>
  );
}

function RecentRuns({ runs, error }: { runs: Solution[] | null; error: Error | null }) {
  return (
    <Card
      title="最近运行"
      subtitle="最新 6 条求解记录"
      actions={
        <LinkButton href="#/runs" size="sm" variant="plain" icon={ArrowRight}>
          全部运行记录
        </LinkButton>
      }
      flush
    >
      {!runs && error && (
        <div className={s.pad}>
          <Notice tone="error">无法读取运行记录：{error.message}</Notice>
        </div>
      )}
      {!runs && !error && (
        <div className={s.pad}>
          <Spinner />
        </div>
      )}
      {runs && runs.length === 0 && (
        <EmptyState
          icon={History}
          title="还没有求解记录"
          detail="在规划视图选择场景与算法后求解，结果会出现在这里。"
          action={
            <LinkButton href="#/plan" size="sm" icon={ArrowRight}>
              去规划
            </LinkButton>
          }
        />
      )}
      {runs && runs.length > 0 && (
        <div className={s.tableWrap}>
          <table className={tableClass}>
            <thead>
              <tr>
                <th>时间</th>
                <th>场景 · 算法</th>
                <th>状态</th>
                <th>检查</th>
                <th className="num">目标值</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const check = checkVerdict(r);
                return (
                  <tr key={r.run_id}>
                    <td className="num">{fmt.time(r.created_utc)}</td>
                    <td>
                      <div className={s.cellMain}>{r.scenario_id}</div>
                      <div className={s.cellSub}>
                        {r.algorithm?.name ?? r.algorithm?.id} <SourceBadge source={r.algorithm?.source ?? ''} />
                      </div>
                    </td>
                    <td>
                      <StatusText tone={runTone(r)}>
                        {label(RUN_STATE_LABEL, r.state)}
                        {r.status ? ` · ${label(SOLVE_STATUS_LABEL, r.status)}` : ''}
                      </StatusText>
                    </td>
                    <td>
                      <StatusText tone={check.tone}>
                        {check.mark} {check.text}
                      </StatusText>
                    </td>
                    <td className="num">
                      {fmt.num(r.objective?.value)}
                      {r.objective?.value !== null && r.objective?.value !== undefined && (
                        <span className={s.unit}>{label(COST_UNIT_LABEL, r.objective.unit, '')}</span>
                      )}
                    </td>
                    <td className={s.linksCell}>
                      <a href={buildHash('plan', r.scenario_id, { run: r.run_id })}>规划</a>
                      <a href={buildHash('runs', r.run_id)}>详情</a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

const STEPS: { text: string; detail: string; href?: string }[] = [
  { text: '描述任务或打开场景', detail: '一句话生成任务地图，也可以打开仿真地图、CVRPLIB 算例或真实田块。', href: '#/plan' },
  { text: '在地图上调整', detail: '拖动任务点、地块、障碍和车库，修改自动保存。' },
  { text: '推荐算法', detail: '按任务规模、障碍、聚集度和目标排序，并给出理由。' },
  { text: '求解与验算', detail: '多车路线按颜色区分，每个方案逐项验算。' },
  { text: '对比与导出', detail: '同一场景的方案并排比较，导出 JSON / CSV / GeoJSON。', href: '#/runs' },
  { text: '送进仿真', detail: '仿真地图上的方案交给 TurtleBot4 + Nav2 执行，回传实际轨迹。', href: '#/sim' },
];

/** `embedded`: inside the studio's System workspace, without the page header and getting-started card. */
export function Dashboard({ embedded = false }: { embedded?: boolean }) {
  const connected = useGatewayConnected();
  const services = useLabResource('dash:services', () => lab.services(), {
    event: 'services',
    select: selectServices,
    pollMs: fallbackPoll(connected),
  });
  const sim = useLabResource<SimState>('dash:sim', () => lab.simState(), {
    event: 'sim_state',
    pollMs: fallbackPoll(connected),
  });
  const runs = useLabResource('dash:runs', () => lab.runs({ limit: 6 }), { pollMs: fallbackPoll(connected, 10000) });
  const reloadAll = () => {
    services.reload();
    sim.reload();
    runs.reload();
  };
  useReconnectReload(connected, reloadAll);
  useEventReload(['run', 'job'], runs.reload);

  const [log, setLog] = useState<{ open: boolean; name: string | null }>({ open: false, name: null });
  const [simOptions, setSimOptions] = useState<SimOptions>({ world: 'warehouse', gui: false, rviz: true });
  const [pending, setPending] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string | null>>({});

  const onAction = async (svc: Service, action: Action) => {
    setPending(pendingKey(svc.id, action));
    setErrors((e) => ({ ...e, [svc.id]: null }));
    try {
      const body = svc.id === 'sim' && action !== 'stop' ? { ...simOptions } : undefined;
      await lab.serviceAction(svc.id, action, body);
      services.reload();
      if (svc.id === 'sim' || svc.id === 'rviz' || svc.id === 'gazebo_gui') sim.reload();
    } catch (e) {
      setErrors((prev) => ({ ...prev, [svc.id]: (e as Error).message }));
    } finally {
      setPending(null);
    }
  };

  const serviceList = services.data ? sortServices(services.data.services) : null;
  const issues = collectKnownIssues(serviceList, sim.data);
  const recent = runs.data ? newestFirst(runs.data.runs).slice(0, 6) : null;
  const stale = !!services.error && !!services.data;

  const refresh = (
    <Button size="sm" icon={RefreshCw} onClick={reloadAll} aria-label="刷新全部状态">
      刷新
    </Button>
  );
  const body = (
    <>
      {!connected && services.error && (
        <Notice tone="error">
          {services.error.message}
          {stale ? ' 下方为最后一次读取到的状态。' : ''}
        </Notice>
      )}

      <div className={s.grid}>
        <div className={s.col}>
          <Card
            title="服务与环境"
            subtitle={connected ? '实时' : '连接中断，每 5 秒重试'}
            actions={
              <>
                {embedded && refresh}
                <Button size="sm" variant="plain" icon={FileText} onClick={() => setLog({ open: true, name: null })}>
                  全部日志
                </Button>
              </>
            }
            flush
          >
            {!serviceList && !services.error && (
              <div className={s.pad}>
                <Spinner label="读取服务状态" />
              </div>
            )}
            {!serviceList && services.error && (
              <EmptyState title="服务状态不可用" detail={services.error.message} />
            )}
            {serviceList && serviceList.length === 0 && <EmptyState title="网关未报告任何服务" />}
            {serviceList && serviceList.length > 0 && (
              <div className={stale ? `${s.services} ${s.stale}` : s.services}>
                {serviceList.map((svc) => (
                  <ServiceRow
                    key={svc.id}
                    svc={svc}
                    pending={pending}
                    error={errors[svc.id] ?? null}
                    onAction={onAction}
                    onLog={(name) => setLog({ open: true, name })}
                  />
                ))}
              </div>
            )}
          </Card>

          <RecentRuns runs={recent} error={runs.error} />
        </div>

        <div className={s.col}>
          <SimCard
            sim={sim.data}
            error={sim.error}
            options={simOptions}
            onOptions={setSimOptions}
            onChanged={() => {
              sim.reload();
              services.reload();
            }}
          />

          {!embedded && <Card title="快速开始">
            <ol className={s.steps}>
              {STEPS.map((step, i) => (
                <li key={step.text}>
                  <span className={s.stepNo}>{i + 1}</span>
                  <div>
                    <div className={s.stepText}>{step.href ? <a href={step.href}>{step.text}</a> : step.text}</div>
                    <div className={s.stepDetail}>{step.detail}</div>
                  </div>
                </li>
              ))}
            </ol>
          </Card>}

          <Card title="已知问题与说明">
            <ul className={s.issues}>
              {issues.map((i) => (
                <li key={`${i.source}:${i.text}`}>
                  <Badge tone="orange">{i.source}</Badge>
                  <span>{i.text}</span>
                </li>
              ))}
              {!services.data && (
                <li>
                  <Badge tone="gray">服务</Badge>
                  <span className={s.muted}>服务状态未读取，实时已知问题暂缺。</span>
                </li>
              )}
              {SCOPE_NOTES.map((i) => (
                <li key={i.source}>
                  <Badge tone="gray">{i.source}</Badge>
                  <span>{i.text}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      <LogSheet open={log.open} initialLog={log.name} onClose={() => setLog({ open: false, name: null })} />
    </>
  );
  if (embedded) return <div className={s.embedded}>{body}</div>;
  return (
    <Page
      title="CERES"
      subtitle="可验证农业机器人路径规划算法库与智能推荐基准"
      actions={
        <>
          {refresh}
          <LinkButton href="#/plan" size="sm" variant="primary" icon={ArrowRight}>
            开始规划
          </LinkButton>
        </>
      }
    >
      {body}
    </Page>
  );
}
