import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  CircleDashed,
  Download,
  Eye,
  EyeOff,
  Hand,
  Home,
  Maximize,
  MousePointer2,
  Pause,
  Play,
  Plus,
  Redo2,
  RefreshCw,
  Shapes,
  Sprout,
  Tractor,
  Trash2,
  Undo2,
  X,
  BoxSelect,
} from 'lucide-react';
import * as Plot from '@observablehq/plot';
import { exportUrl, fmt } from '../../lab/api';
import { Badge, Button, EmptyState, Input, Notice, Segmented, Select, Spinner } from '../../lab/ui';
import type { Solution } from '../../lab/types';
import { FieldEditor } from '../editor/FieldEditor';
import { OBSTACLE_STYLE, area } from '../editor/geometry';
import { useLanes } from '../editor/lanes';
import { asFleet, isFleet, retrySave, saveDraftCopy, useStudio } from '../store';
import { GeneratedFrom } from '../agri/GeneratedFrom';
import { studio, urls } from '../api';
import type { FleetInstance, FleetRoute, FleetScenario, ObstacleKind, PerVehicle } from '../types';
import { addDepot, depotsOf, fleetClassFor, fleetClasses, fleetSize, planningFingerprint, removeDepot, setDepot } from '../fleet';
import { playbackDuration } from '../planningPlayback';
import { planModelSchema, researchAcceptanceIssues, unacceptedPlanReason } from '../execution';
import { MapCanvas } from '../../lab/canvas/MapCanvas';
import { baseScene, routeColor, shownColor, solutionPaths, violationMarkers } from '../../lab/views/plan/planModel';
import type { Instance, Scenario } from '../../lab/types';
import { logTimeAxis, unitSuffix } from '../chart';
import { BackgroundControl, FleetAssetSection, PlanBackground } from '../assets/PlanLayers';
import { centerMap, ReplayOverlay } from '../experiment/ReplayPanel';
import { comparable, currentInput, exclusionReason, inputIdentity, mapCheckIssue, partitionRuns, requiresCoverageRecheck, resultState, verifiedPlan } from '../comparison';
import { identityState, withoutInvalidatedResults } from '../../lab/views/plan/resultIntegrity';
import { RunTrace } from '../experiment/RunTrace';
import { HistoryRuns } from './HistoryRuns';
import { MapExclusionNote, verifyText } from './MapExclusionNote';
import { IntegrityNotice } from '../../lab/views/plan/IntegrityNotice';
import { SeedBandChart, StatusFlowChart, VerificationChart } from '../researchCharts/RunCharts';
import { ProblemEditor } from './ProblemEditor';
import s from './panels.module.css';

const LIB_TONE: Record<string, 'blue' | 'purple' | 'teal' | 'orange' | 'gray' | 'green'> = {
  本项目: 'blue', PyVRP: 'purple', 'OR-Tools': 'teal', Fields2Cover: 'green', OMPL: 'orange', 基线: 'gray',
};
/** Older run records carry the previous labels. */
const LIB_ALIAS: Record<string, string> = { URP: '本项目', 面板: '基线' };

export function LibraryBadge({ library }: { library?: string }) {
  if (!library) return null;
  const name = LIB_ALIAS[library] ?? library;
  return <Badge tone={LIB_TONE[name] ?? 'gray'}>{name}</Badge>;
}

/** Complete frozen planning input, canonically serialized; pure view state is not part of FleetInstance. */
export function signature(inst: FleetInstance | null | undefined): string {
  return planningFingerprint(inst);
}

/** How one plot's rows are driven, in a few words. */
function laneText(p: { mode: 'every' | 'track'; spacing_m: number; lane_width_m: number; pitch_m: number }): string {
  return p.mode === 'every'
    ? `行距 ${fmt.num(p.spacing_m, 2)} m，行间通道宽 ${fmt.num(p.lane_width_m, 2)} m`
    : `行距 ${fmt.num(p.spacing_m, 2)} m，行间太窄，每两行留一条 ${fmt.num(p.lane_width_m, 2)} m 宽的作业道（间隔 ${fmt.num(p.pitch_m, 2)} m）`;
}

function useFocus() {
  const runs = useStudio((st) => st.runs);
  const focusRun = useStudio((st) => st.focusRun);
  return focusRun ? runs[focusRun] : undefined;
}

/* ------------------------------------------------------------------ map -- */
const TOOLS = [
  { value: 'select', label: '选择', icon: MousePointer2, title: '选择与拖动 (V)' },
  { value: 'box', label: '框选', icon: BoxSelect, title: '拖动框选 (B；⌘/Ctrl 加选；选择工具下 Alt/⌘ + 拖动同效)' },
  { value: 'pan', label: '平移', icon: Hand, title: '平移 (H，或按住 Shift 拖动)' },
  { value: 'task', label: '任务点', icon: Plus, title: '点击添加任务点 (T)' },
  { value: 'obstacle', label: '障碍', icon: Shapes, title: '拖动绘制障碍 (O)' },
  { value: 'plot', label: '地块', icon: Sprout, title: '拖动绘制地块 (P)' },
  { value: 'depot', label: '车库', icon: Home, title: '点击放置车库；多库时追加车库 (D)' },
  { value: 'unload', label: '补给点', icon: Plus, title: '点击添加补给 / 卸载点（makespan 目标）' },
  { value: 'corridor', label: '机耕道', icon: Shapes, title: '拖动绘制机耕道 / 地头走廊' },
] as const;

export function MapPanel() {
  const st = useStudio();
  const focus = useFocus();
  if (st.loadError) return <div className={s.pad}><Notice tone="error">{st.loadError}</Notice></div>;
  if (!st.scenario || !st.draft) {
    return (
      <EmptyState icon={Sprout} title={st.busy ? `${st.busy}…` : st.workspace === 'agent' ? '任务地图' : '从一句话开始'}
        detail={st.workspace === 'agent'
          ? '在左侧描述任务并确认任务合同后，智能体建立的任务地图与方案显示在这里。'
          : '在上方输入任务描述并生成地图，或从左侧场景库打开一个场景。'} />
    );
  }
  if (!isFleet(st.scenario)) return <GenericMap scenario={st.scenario} draft={st.draft} run={focus} runs={st.runIds.map((id) => st.runs[id]).filter(Boolean) as Solution[]} />;
  const inst = asFleet(st.draft);
  return <FleetMap inst={inst} focus={focus} />;
}

function FleetMap({ inst, focus }: { inst: FleetInstance; focus?: Solution }) {
  const st = useStudio();
  const [snapshot, setSnapshot] = useState(false);
  const [overlayIds, setOverlayIds] = useState<string[]>([]);
  const stale = !!focus && signature(focus.instance as unknown as FleetInstance) !== signature(inst);
  useEffect(() => setSnapshot(false), [focus?.run_id, st.scenario?.id]);
  const frozen = snapshot && stale && focus?.state === 'done' && focus.instance ? focus.instance as unknown as FleetInstance : null;
  const shownInst = frozen ?? inst;
  const lanes = useLanes(shownInst, shownInst.vehicles.travel === 'rows');
  if (!st.scenario) return null;
  const routes = useMemo(() => (focus?.state === 'done' ? (focus.routes as FleetRoute[]).map((r) => ({ ...r, color: shownColor(r.color) })) : []), [focus]);
  const showResult = !stale || !!frozen;
  const otherPlans = focus && showResult ? st.runIds.map((id) => st.runs[id]).filter((r): r is Solution =>
    !!r && r.run_id !== focus.run_id && verifiedPlan(r) && comparable(r, focus)
      && signature(r.instance as unknown as FleetInstance) === signature(shownInst)) : [];
  return (
    <div className={s.mapPanel}>
      {frozen ? (
        <div className={s.mapBar}>
          <Badge tone="orange">历史运行冻结输入</Badge>
          <span className={s.meta} style={{ margin: 0 }}>{focus?.run_id} · 只读，不回写当前草稿</span>
          <span className={s.spacer} />
          <Button size="sm" icon={Maximize} title="适配全图 (F)" onClick={() => window.dispatchEvent(new Event('studio:fit'))} />
          <Button size="sm" onClick={() => setSnapshot(false)}>返回当前草稿</Button>
        </div>
      ) : (
        <div className={s.mapBar}>
          <Segmented value={st.tool} onChange={(t) => st.setTool(t)} label="编辑工具"
            options={TOOLS.map((t) => ({ value: t.value, label: <span className={s.toolLabel}>{t.label}</span>, icon: t.icon, title: t.title }))} />
          {st.tool === 'obstacle' && (
            <Select aria-label="障碍类型" value={st.obstacleKind} onChange={(e) => st.setObstacleKind(e.target.value as ObstacleKind)}>
              {(Object.keys(OBSTACLE_STYLE) as ObstacleKind[]).map((k) => <option key={k} value={k}>{OBSTACLE_STYLE[k].label}</option>)}
            </Select>
          )}
          <BackgroundControl />
          <span className={s.spacer} />
          <Button size="sm" icon={Undo2} title="撤销 (⌘Z)" disabled={!st.past.length} onClick={st.undo} />
          <Button size="sm" icon={Redo2} title="重做 (⇧⌘Z)" disabled={!st.future.length} onClick={st.redo} />
          <Button size="sm" icon={Maximize} title="适配全图 (F)" onClick={() => window.dispatchEvent(new Event('studio:fit'))} />
          <SaveChip />
        </div>
      )}
      <div className={s.mapStage}>
        <FieldEditor
          inst={shownInst}
          routes={showResult ? routes : []}
          hidden={st.hiddenVehicles}
          playback={st.playback}
          tool={frozen ? 'select' : st.tool}
          obstacleKind={st.obstacleKind}
          selection={frozen ? null : st.selection}
          editable={!frozen}
          fitKey={`${st.scenario.id}:${frozen ? focus?.run_id : 'draft'}`}
          lanes={lanes?.lanes}
          background={frozen ? undefined : <PlanBackground />}
          overlay={(view) => <>
            {otherPlans.map((plan, color) => overlayIds.includes(plan.run_id) ? <g key={plan.run_id} pointerEvents="none">
              {plan.routes.map((route, index) => <polyline key={index} points={route.polyline.map((xy) => xy.join(',')).join(' ')}
                fill="none" stroke={routeColor(color + 1)} strokeDasharray="7 4" strokeWidth={2} opacity={0.7} vectorEffect="non-scaling-stroke" />)}
            </g> : null)}
            <ReplayOverlay view={view} instance={shownInst} />
          </>}
          onEdit={(fn) => st.edit((i) => fn(i as unknown as FleetInstance) as unknown as typeof i)}
          onSelect={frozen ? () => undefined : st.select}
          onCursor={st.setCursor}
        />
        {stale && !frozen && (
          <div className={s.staleBadge}><RefreshCw size={12} /> 当前输入已修改，历史结果需重新求解
            {focus?.state === 'done' && <Button size="sm" variant="plain" onClick={() => setSnapshot(true)}>查看冻结输入</Button>}
          </div>
        )}
        {st.save === 'error' && st.saveError && (
          <div className={s.saveBanner} role="alert">
            <span>未保存：{st.saveError}。{/连接不上|无法连接/.test(st.saveError) ? '断连时会自动重试；' : ''}草稿仍保留在当前页面。</span>
            <Button size="sm" onClick={() => void retrySave()}>重试保存</Button>
            <Button size="sm" onClick={() => void saveDraftCopy().catch((error) => st.notify('error', (error as Error).message))}>另存草稿副本</Button>
          </div>
        )}
        {showResult && focus && focus.state === 'done' && <VehicleLegend run={focus} comparisons={otherPlans} overlayIds={overlayIds}
          toggleOverlay={(id) => setOverlayIds((ids) => ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id])} />}
        {showResult && focus && verifiedPlan(focus) && <Playback run={focus} inst={shownInst} />}
      </div>
    </div>
  );
}

/** Read-only scenario canvas for routing, coverage and motion-planning problems, following the focused solution. */
function GenericMap({ scenario, draft, run, runs }: { scenario: Scenario; draft: Instance; run?: Solution; runs: Solution[] }) {
  const focusSolution = useStudio((st) => st.focus);
  const [frozen, setFrozen] = useState(false);
  const [legendOpen, setLegendOpen] = useState(true);
  const [hiddenRuns, setHiddenRuns] = useState<string[]>([]);
  const [center, setCenter] = useState<{ xy: [number, number]; span?: number; token: number }>();
  const shownDraft = frozen && run?.instance ? run.instance : draft;
  const shownScenario = frozen && run?.scenario_snapshot ? run.scenario_snapshot : scenario;
  useEffect(() => { setFrozen(false); setHiddenRuns([]); }, [scenario.id]);
  useEffect(() => {
    const onCenter = (event: Event) => {
      const detail = (event as CustomEvent<{ xy: [number, number]; span?: number; expectedInput?: string }>).detail;
      if (detail?.expectedInput && detail.expectedInput !== inputIdentity(shownScenario.problem_class, shownDraft)) return;
      if (detail?.xy?.length === 2 && detail.xy.every(Number.isFinite)) setCenter({ ...detail, token: performance.now() });
    };
    window.addEventListener('studio:center', onCenter);
    return () => window.removeEventListener('studio:center', onCenter);
  }, [shownScenario.problem_class, shownDraft]);
  const { scene, entries, stale } = useMemo(() => {
    const out = baseScene(shownScenario, shownDraft);
    if (shownScenario.problem_class === 'point_to_point') {
      const inst = shownDraft as unknown as { start: [number, number, number]; goal: [number, number, number] };
      out.nodes = [...(out.nodes ?? []),
        { id: 'start', xy: [inst.start[0], inst.start[1]], kind: 'start', label: '起点' },
        { id: 'goal', xy: [inst.goal[0], inst.goal[1]], kind: 'task', label: '终点' }];
    }
    const current = !!run && run.state === 'done' && run.routes.length > 0 && !!run.instance && (frozen || currentInput(run, scenario, draft));
    const available = run && !runs.some((r) => r.run_id === run.run_id) ? [run, ...runs] : runs;
    const candidates = current && run ? available.filter((candidate) => candidate.state === 'done' && candidate.routes.length > 0
      && comparable(candidate, run) && !!candidate.instance && inputIdentity(candidate.problem_class, candidate.instance) === inputIdentity(shownScenario.problem_class, shownDraft)) : [];
    const entries = candidates.map((solution, colorIndex) => ({ solution, colorIndex, current: true, focused: solution.run_id === run?.run_id }));
    if (entries.length) {
      const swath = shownScenario.problem_class === 'coverage' ? (shownDraft as { swath_width?: number }).swath_width ?? null : null;
      const visible = entries.filter((entry) => !hiddenRuns.includes(entry.solution.run_id));
      out.paths = solutionPaths(visible, shownScenario.problem_class, shownDraft, swath);
      out.markers = violationMarkers(visible.map((entry) => entry.solution));
    }
    return { scene: out, entries, stale: !!run && run.state === 'done' && run.routes.length > 0 && !current };
  }, [scenario, draft, run, runs, shownDraft, shownScenario, frozen, hiddenRuns]);
  return (
    <div className={s.mapPanel}>
      <div className={s.mapBar}>
        <span className={s.hint} style={{ margin: 0 }}>{frozen ? '历史运行冻结输入 · 只读' : scenario.summary}</span>
        <span className={s.spacer} />
        {frozen ? <Button size="sm" onClick={() => setFrozen(false)}>返回当前草稿</Button> : <SaveChip />}
        <Button size="sm" title="打开经典视图：可编辑 JSON、逐项调整任务" onClick={() => window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: 'classic' }))}>经典视图</Button>
      </div>
      <div className={s.mapStage}>
        <MapCanvas scene={scene} fitKey={`${scenario.id}:${frozen ? run?.run_id : 'draft'}`} centerRequest={center} ariaLabel="场景地图"
          overlay={entries.length && run ? <span className={s.saveChip}>{run.algorithm.name} · {fmt.num(run.objective.value, 2)} {run.objective.unit === 'euc_2d_rounded' ? '' : run.objective.unit}</span> : undefined} />
        {entries.length > 0 && (
          <div className={`${s.legend} ${s.algorithmLegend}`} data-collapsed={!legendOpen || undefined}>
            <button type="button" className={s.legendHead} aria-expanded={legendOpen} title={legendOpen ? '收起算法图例' : '展开算法图例'} onClick={() => setLegendOpen(!legendOpen)}><span>同一输入 · {entries.length} 个方案</span><ChevronDown size={12} /></button>
            {legendOpen && entries.map((entry) => (
              <div key={entry.solution.run_id} className={s.legendRow} data-off={hiddenRuns.includes(entry.solution.run_id) || undefined}>
                <span className={s.swatch} style={{ background: routeColor(entry.colorIndex) }} />
                <button type="button" className={s.legendFocus} aria-pressed={entry.focused} onClick={() => focusSolution(entry.solution.run_id)}>{entry.solution.algorithm.name}</button>
                <span className={s.legendMeta}>{fmt.num(entry.solution.objective.value, 2)} {entry.solution.objective.unit}</span>
                <button type="button" className={s.simLink} aria-label={`显示 ${entry.solution.algorithm.name} 路线`} aria-pressed={!hiddenRuns.includes(entry.solution.run_id)}
                  onClick={() => setHiddenRuns((ids) => ids.includes(entry.solution.run_id) ? ids.filter((id) => id !== entry.solution.run_id) : [...ids, entry.solution.run_id])}>{hiddenRuns.includes(entry.solution.run_id) ? <EyeOff size={12} /> : <Eye size={12} />}</button>
              </div>
            ))}
          </div>
        )}
        {stale && <div className={s.staleBadge}><RefreshCw size={12} /> 历史输入或来源未知，未将路线叠加到当前草稿
          {run?.instance && <Button size="sm" onClick={() => setFrozen(true)}>查看冻结输入</Button>}</div>}
      </div>
    </div>
  );
}

function SaveChip() {
  const save = useStudio((st) => st.save);
  const err = useStudio((st) => st.saveError);
  const text = { idle: '', dirty: '未保存', saving: '保存中…', saved: '已保存', error: '保存失败' }[save];
  if (!text) return null;
  return <span className={save === 'error' ? s.saveErr : s.saveChip} title={err ?? undefined}>{text}</span>;
}

function vehicleFacts(run: Solution, vehicle: PerVehicle, index: number) {
  const inst = run.instance as unknown as FleetInstance | undefined;
  const routes = run.routes as FleetRoute[];
  const route = routes.find((item) => item.vehicle_name === vehicle.vehicle) ?? routes[index];
  const classes = inst?.vehicles ? fleetClasses(inst) : [];
  const classRef = vehicle.fleet_class ?? route?.fleet_class;
  const depotIndex = vehicle.depot ?? route?.depot;
  const className = classRef != null
    ? (inst ? fleetClassFor(inst, classRef)?.name : undefined) ?? `未记录类别 ${classRef}`
    : classes.length > 1 ? '类别未记录' : classes[0]?.name ?? '同构';
  const home = depotIndex != null ? `D${depotIndex + 1}` : inst && depotsOf(inst).length > 1 ? '未记录' : inst ? 'D1' : '未记录';
  return { className, home };
}

function VehicleLegend({ run, comparisons = [], overlayIds = [], toggleOverlay }: { run: Solution; comparisons?: Solution[]; overlayIds?: string[]; toggleOverlay?: (id: string) => void }) {
  const hidden = useStudio((st) => st.hiddenVehicles);
  const toggle = useStudio((st) => st.toggleVehicle);
  const per = (run.metrics.per_vehicle as unknown as PerVehicle[] | undefined) ?? [];
  const [open, setOpen] = useState(false);
  if (!per.length) return null;
  return (
    <div className={s.legend} data-collapsed={!open || undefined}>
      <button type="button" className={s.legendHead} onClick={() => setOpen(!open)} title={open ? '收起图例' : '展开图例'}>
        {!open && per.map((v) => <span key={v.vehicle} className={s.swatch} style={{ background: shownColor(v.color) }} />)}
        <span>{run.algorithm.name}</span>
        <ChevronDown size={12} className={s.legendChevron} />
      </button>
      {open && per.map((v, index) => {
        const off = hidden.includes(v.vehicle);
        const facts = vehicleFacts(run, v, index);
        return (
          <button key={v.vehicle} type="button" className={s.legendRow} data-off={off || undefined} onClick={() => toggle(v.vehicle)}>
            <span className={s.swatch} style={{ background: shownColor(v.color) }} />
            <span className={s.legendName}>{v.vehicle}<div className={s.legendMeta}>{facts.className} · 家库 {facts.home}</div></span>
            <span className={s.legendMeta}>{v.tasks} 点 · {v.trips} 趟</span>
            <span className={s.legendMeta}>{fmt.num(v.length_m, 0)} m</span>
            <span className={s.legendMeta}>{fmt.sec(v.duration_s)}</span>
            {off ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
        );
      })}
      {open && comparisons.length > 0 && <div style={{ padding: '6px' }}>
        <p className={s.meta}>叠加其他同组方案（虚线 · 全部车辆）</p>
        {comparisons.map((plan, color) => <Button key={plan.run_id} size="sm" aria-pressed={overlayIds.includes(plan.run_id)}
          onClick={() => toggleOverlay?.(plan.run_id)} icon={overlayIds.includes(plan.run_id) ? Eye : EyeOff}><span className={s.swatch} style={{ background: routeColor(color + 1) }} />叠加 {plan.algorithm.name}</Button>)}
      </div>}
    </div>
  );
}

/** Playback clock, m:ss. */
function clock(t: number): string {
  const sec = Math.round(t);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

function Playback({ run, inst }: { run: Solution; inst: FleetInstance }) {
  const t = useStudio((st) => st.playback);
  const set = useStudio((st) => st.setPlayback);
  const [playing, setPlaying] = useState(false);
  const routes = run.routes as FleetRoute[];
  const total = playbackDuration(inst, routes);
  const raf = useRef(0);
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    const step = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const cur = useStudio.getState().playback ?? 0;
      const next = cur + dt * Math.max(10, total / 25);
      if (next >= total) {
        set(total);
        setPlaying(false);
        return;
      }
      set(next);
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, total, set]);
  return (
    <div className={s.playback}>
      <span className={s.meta}>规划预览 · 匀速代理，非仿真</span>
      <Button size="sm" icon={playing ? Pause : Play} title={playing ? '暂停规划预览' : '播放规划预览（含逐车速度与任务停留，非仿真）'}
        onClick={() => { if (!playing && (t === null || t >= total)) set(0); setPlaying(!playing); }} />
      <input type="range" min={0} max={total} step={total / 400} value={t ?? 0} aria-label="规划预览时间"
        onChange={(e) => { setPlaying(false); set(Number(e.target.value)); }} />
      <span className={s.mono}>{clock(t ?? 0)} / {clock(total)}</span>
      {t !== null && <Button size="sm" icon={X} title="关闭规划预览" onClick={() => { setPlaying(false); set(null); }} />}
    </div>
  );
}

/* ------------------------------------------------------------ inspector -- */
export function InspectorPanel() {
  const sc = useStudio((st) => st.scenario);
  const draft = useStudio((st) => st.draft);
  const [tab, setTab] = useState<'fleet' | 'selection' | 'scenario'>('fleet');
  const selection = useStudio((st) => st.selection);
  useEffect(() => {
    if (selection) setTab('selection');
  }, [selection]);
  // The map's issue list asks for a fleet parameter (editor/fieldFocus.ts): open the fleet tab so it can be focused.
  useEffect(() => {
    const onFocusField = () => setTab('fleet');
    window.addEventListener('studio:focus-field', onFocusField);
    return () => window.removeEventListener('studio:focus-field', onFocusField);
  }, []);
  if (!sc || !draft) return <EmptyState title="未打开场景" />;
  if (!isFleet(sc)) return <div className={s.scroll}><ProblemEditor key={useStudio.getState().editorSession ?? sc.id} scenario={sc} draft={draft} /><ScenarioInfo /></div>;
  return (
    <div className={s.inspector}>
      <div className={s.tabs}>
        <Segmented value={tab} onChange={setTab} full label="检查器"
          options={[{ value: 'fleet', label: '车队' }, { value: 'selection', label: '选中' }, { value: 'scenario', label: '场景' }]} />
      </div>
      <div className={s.scroll}>
        {tab === 'fleet' && <FleetForm />}
        {tab === 'selection' && <SelectionForm />}
        {tab === 'scenario' && <ScenarioInfo />}
      </div>
    </div>
  );
}

function Num({ label, value, onChange, min, step = 1, unit, field }: { label: string; value: number; onChange: (v: number) => void; min?: number; step?: number; unit?: string; field?: string }) {
  return (
    <label className={s.field}>
      <span>{label}</span>
      <span className={s.inputUnit}>
        <Input type="number" data-field={field} value={value} min={min} step={step} onChange={(e) => Number.isFinite(e.target.valueAsNumber) && onChange(e.target.valueAsNumber)} />
        {unit && <em>{unit}</em>}
      </span>
    </label>
  );
}

function FleetForm() {
  const draft = useStudio((st) => st.draft);
  const edit = useStudio((st) => st.edit);
  const inst = asFleet(draft);
  const lanes = useLanes(inst, inst.vehicles.travel === 'rows');
  const set = (fn: (i: FleetInstance) => FleetInstance) => edit((i) => fn(i as unknown as FleetInstance) as unknown as typeof i);
  const v = inst.vehicles;
  const classes = fleetClasses(inst);
  const classMode = !!v.fleet?.length;
  const demand = inst.tasks.reduce((a, t) => a + t.demand, 0);
  const perWave = classes.reduce((sum, item) => sum + item.count * item.capacity, 0);
  const waves = Math.ceil(demand / Math.max(1, perWave));
  const setClass = (index: number, patch: Partial<(typeof classes)[number]>) => set((i) => {
    const next = fleetClasses(i).map((item, k) => (k === index ? { ...item, ...patch } : item));
    return { ...i, robot_radius: Math.max(...next.map((item) => item.robot_radius ?? 0.1)), vehicles: { ...i.vehicles, fleet: next } };
  });
  return (
    <div className={s.form}>
      <div className={s.rowSpace}><h3>车队</h3><Badge tone="gray">{fleetSize(inst)} 台 · {classes.length} 类</Badge></div>
      {!classMode ? (
        <>
          <div className={s.grid2}>
            <Num field="vehicles.count" label="车辆数" value={v.count ?? 1} min={1} onChange={(n) => set((i) => ({ ...i, vehicles: { ...i.vehicles, count: Math.max(1, Math.round(n)), names: undefined } }))} />
            <Num field="vehicles.capacity" label="单车容量" value={v.capacity ?? 1} min={1} onChange={(n) => set((i) => ({ ...i, vehicles: { ...i.vehicles, capacity: Math.max(1, Math.round(n)) } }))} />
            <Num field="vehicles.speed_mps" label="车速" value={v.speed_mps ?? 1} min={0.1} step={0.1} unit="m/s" onChange={(n) => set((i) => ({ ...i, vehicles: { ...i.vehicles, speed_mps: n } }))} />
            <Num field="robot_radius" label="车体半径" value={inst.robot_radius} min={0.1} step={0.05} unit="m" onChange={(n) => set((i) => ({ ...i, robot_radius: n }))} />
          </div>
          <Button size="sm" onClick={() => set((i) => ({ ...i, vehicles: { ...i.vehicles, fleet: fleetClasses(i) } }))}>按类别编辑车队</Button>
        </>
      ) : (
        <>
          {classes.map((item, index) => (
            <div key={index} className={s.fleetClass}>
              <div className={s.rowSpace}>
                <strong>{item.name ?? `车型 ${index + 1}`}</strong>
                <Button size="sm" variant="plain" icon={Trash2} title="删除此车型" disabled={classes.length <= 1} onClick={() => set((i) => ({ ...i, vehicles: { ...i.vehicles, fleet: fleetClasses(i).filter((_, k) => k !== index) } }))} />
              </div>
              <label className={s.field}><span>类别名称</span><Input value={item.name ?? `车型 ${index + 1}`} onChange={(e) => setClass(index, { name: e.target.value })} /></label>
              <div className={s.grid2}>
                <Num field={`fleet.${index}.count`} label="数量" value={item.count} min={1} onChange={(n) => setClass(index, { count: Math.max(1, Math.round(n)) })} />
                <Num field={`fleet.${index}.capacity`} label="容量（抽象单位）" value={item.capacity} min={1} onChange={(n) => setClass(index, { capacity: Math.max(1, Math.round(n)) })} />
                <Num field={`fleet.${index}.speed_mps`} label="车速" value={item.speed_mps ?? 1} min={0.1} step={0.1} unit="m/s" onChange={(n) => setClass(index, { speed_mps: n })} />
                <Num field={`fleet.${index}.robot_radius`} label="规划半径" value={item.robot_radius ?? inst.robot_radius} min={0.1} step={0.05} unit="m" onChange={(n) => setClass(index, { robot_radius: n })} />
                <Num label="最小转弯半径" value={item.min_turn_radius_m ?? 0} min={0} step={0.1} unit="m" onChange={(n) => setClass(index, { min_turn_radius_m: Math.max(0, n) })} />
              </div>
              <label className={s.field}><span>载货语义</span><Select value={item.payload} onChange={(e) => setClass(index, { payload: e.target.value as 'load' | 'none' })}><option value="load">载货 / 采样容量</option><option value="none">不载货（容量不构成约束）</option></Select></label>
              {item.asset && <p className={s.meta}>资产 {item.asset.name ?? item.asset.id} · v{item.asset.version} · {item.asset.sha256.slice(0, 12)}</p>}
            </div>
          ))}
          <div className={s.row}>
            <Button size="sm" icon={Plus} disabled={classes.length >= 8} onClick={() => set((i) => ({ ...i, vehicles: { ...i.vehicles, fleet: [...fleetClasses(i), { ...fleetClasses(i)[0], name: `车型 ${classes.length + 1}`, count: 1 }] } }))}>添加车型</Button>
            <Button size="sm" variant="plain" onClick={() => set((i) => { const first = fleetClasses(i)[0]; return { ...i, robot_radius: first.robot_radius ?? i.robot_radius, vehicles: { ...i.vehicles, fleet: undefined, count: first.count, capacity: first.capacity, speed_mps: first.speed_mps ?? 1, names: undefined } }; })}>返回同构设置</Button>
          </div>
          {classes.some((item) => (item.min_turn_radius_m ?? 0) > 0) && <p className={s.hint}><strong className={s.warn}>执行限制：</strong>曲率约束支持规划、保存和查看，并提供采样几何检查；尚无动力学验证。rows 模式不支持，Gazebo 执行返回 422 curvature_constrained_execution。</p>}
          {classes.length > 1 && <p className={s.hint}>异构车队支持 PyVRP / OR-Tools；Gazebo 执行返回 422 heterogeneous_fleet_execution。各车按设定容量求解，分配错误会直接报出。</p>}
        </>
      )}
      <label className={s.field}>
        <span>往返方式</span>
        <Segmented value={v.trips ?? 'multi'} full onChange={(t) => set((i) => ({ ...i, vehicles: { ...i.vehicles, trips: t } }))}
          options={[{ value: 'multi', label: '装满回库，可多趟' }, { value: 'single', label: '每车只跑一趟' }]} />
      </label>
      <label className={s.field}>
        <span>优化目标</span>
        <Segmented value={inst.objective} full onChange={(o) => set((i) => ({ ...i, objective: o }))}
          options={[{ value: 'total_distance', label: '总里程最短' }, { value: 'makespan', label: '尽快完成（均衡分工）' }]} />
      </label>
      <label className={s.field}>
        <span>通行方式</span>
        <Segmented value={v.travel ?? 'free'} full onChange={(t) => set((i) => ({ ...i, vehicles: { ...i.vehicles, travel: t } }))}
          options={[{ value: 'rows', label: '沿行间与地头' }, { value: 'free', label: '自由通行' }]} />
      </label>
      <p className={s.hint}>
        {v.travel === 'rows'
          ? `车辆只在作物行之间的通道里行驶，经地头和机耕路换行，不斜穿作物；任务在离它最近的通道上作业。${lanes ? `共 ${lanes.lane_count} 条行间通道。` : ''}`
          : '车辆可在地块内任意穿行，只避开障碍物；走廊在 free 模式不构成通行约束。'}
      </p>
      <p className={s.hint}>
        {inst.tasks.length} 个任务点，总需求 {demand}（抽象容量单位）；每车各一趟的总容量为 {perWave}，估算至少 {waves} 轮，实际趟次由求解结果确定
        {v.trips === 'single' && waves > 1 ? <strong className={s.warn}>，单趟模式容量不足</strong> : ''}。
      </p>
      <FleetAssetSection />
      <h3>任务点</h3>
      <div className={s.row}>
        <Button size="sm" onClick={() => set((i) => ({ ...i, tasks: i.tasks.map((t) => ({ ...t, demand: 1 })) }))}>需求全部设为 1</Button>
        <Button size="sm" onClick={() => set((i) => ({ ...i, tasks: i.tasks.map((t, k) => ({ ...t, id: k + 1, label: t.label || `T${k + 1}` })) }))}>重新编号</Button>
      </div>
      <h3>地图元素</h3>
      <p className={s.hint}>{inst.plots.length} 块地 · {inst.obstacles.length} 处障碍 · {depotsOf(inst).length} 个车库 · {(inst.unload_points ?? []).length} 个补给点 · {(inst.corridors ?? []).length} 条走廊 · 场地 {fmt.num(area(inst.boundary) / 10000, 2)} 公顷</p>
      {!!inst.unload_points?.length && <p className={s.hint}><strong className={s.warn}>补给语义：</strong>补给输入无损保存；OR-Tools makespan 使用中途补给，总里程目标仍按家库分趟。</p>}
    </div>
  );
}

function SelectionForm() {
  const selection = useStudio((st) => st.selection);
  const draft = useStudio((st) => st.draft);
  const edit = useStudio((st) => st.edit);
  const select = useStudio((st) => st.select);
  const inst = asFleet(draft);
  const lanes = useLanes(inst, inst.vehicles.travel === 'rows');
  const set = (fn: (i: FleetInstance) => FleetInstance) => edit((i) => fn(i as unknown as FleetInstance) as unknown as typeof i);
  if (!selection) return <p className={s.hint}>在地图上点选任务点、障碍、地块、车库或边界查看属性。按 Delete 删除选中对象。</p>;
  if (selection.type === 'task') {
    const t = inst.tasks.find((x) => x.id === selection.id);
    if (!t) return null;
    const setT = (patch: Partial<typeof t>) => set((i) => ({ ...i, tasks: i.tasks.map((x) => (x.id === t.id ? { ...x, ...patch } : x)) }));
    return (
      <div className={s.form}>
        <h3>任务点 {t.label}</h3>
        <label className={s.field}><span>名称</span><Input value={t.label} onChange={(e) => setT({ label: e.target.value })} /></label>
        <div className={s.grid2}>
          <Num label="需求" value={t.demand} min={1} onChange={(n) => setT({ demand: Math.max(1, Math.round(n)) })} />
          <Num label="作业时间" value={t.service_s ?? 0} min={0} unit="s" onChange={(n) => setT({ service_s: Math.max(0, n) })} />
          <Num label="X" value={t.xy[0]} step={0.5} unit="m" onChange={(n) => setT({ xy: [n, t.xy[1]] })} />
          <Num label="Y" value={t.xy[1]} step={0.5} unit="m" onChange={(n) => setT({ xy: [t.xy[0], n] })} />
        </div>
        <Button variant="destructive" icon={Trash2} onClick={() => { set((i) => ({ ...i, tasks: i.tasks.filter((x) => x.id !== t.id) })); select(null); }}>删除任务点</Button>
      </div>
    );
  }
  if (selection.type === 'obstacle') {
    const o = inst.obstacles.find((x) => x.id === selection.id);
    if (!o) return null;
    const setO = (patch: Partial<typeof o>) => set((i) => ({ ...i, obstacles: i.obstacles.map((x) => (x.id === o.id ? { ...x, ...patch } : x)) }));
    return (
      <div className={s.form}>
        <h3>障碍 {o.name || o.id}</h3>
        <label className={s.field}><span>类型</span>
          <Select value={o.kind} onChange={(e) => setO({ kind: e.target.value as ObstacleKind })}>
            {(Object.keys(OBSTACLE_STYLE) as ObstacleKind[]).map((k) => <option key={k} value={k}>{OBSTACLE_STYLE[k].label}</option>)}
          </Select>
        </label>
        <label className={s.field}><span>名称</span><Input value={o.name ?? ''} onChange={(e) => setO({ name: e.target.value })} /></label>
        <p className={s.hint}>{o.polygon.length} 个顶点 · 面积 {fmt.num(area(o.polygon), 1)} m²。拖动整体移动，拖动顶点改形。</p>
        <Button variant="destructive" icon={Trash2} onClick={() => { set((i) => ({ ...i, obstacles: i.obstacles.filter((x) => x.id !== o.id) })); select(null); }}>删除障碍</Button>
      </div>
    );
  }
  if (selection.type === 'plot') {
    const p = inst.plots.find((x) => x.id === selection.id);
    if (!p) return null;
    const setP = (patch: Partial<typeof p>) => set((i) => ({ ...i, plots: i.plots.map((x) => (x.id === p.id ? { ...x, ...patch } : x)) }));
    const rows = lanes?.plots.find((x) => x.id === p.id);
    return (
      <div className={s.form}>
        <h3>地块 {p.name}</h3>
        <label className={s.field}><span>名称</span><Input value={p.name} onChange={(e) => setP({ name: e.target.value })} /></label>
        <label className={s.field}><span>作物</span><Input value={p.crop ?? ''} onChange={(e) => setP({ crop: e.target.value })} /></label>
        <label className={s.field}><span>作物行方向</span>
          <Segmented value={String(p.rows_deg ?? 0)} full onChange={(v) => setP({ rows_deg: Number(v) })}
            options={[{ value: '0', label: '东西向' }, { value: '90', label: '南北向' }, { value: '45', label: '斜向' }]} />
        </label>
        <p className={s.hint}>面积 {fmt.num(area(p.polygon), 0)} m²{rows ? ` · ${rows.rows} 行作物、${rows.lanes} 条通道` : ''}</p>
        {rows && <p className={s.hint}>{laneText(rows)}。</p>}
        <Button variant="destructive" icon={Trash2} onClick={() => { set((i) => ({ ...i, plots: i.plots.filter((x) => x.id !== p.id) })); select(null); }}>删除地块</Button>
      </div>
    );
  }
  if (selection.type === 'corridor') {
    const corridor = inst.corridors?.find((x) => x.id === selection.id);
    if (!corridor) return null;
    return (
      <div className={s.form}>
        <h3>机耕道 / 地头 {corridor.id}</h3>
        <label className={s.field}><span>名称</span><Input value={corridor.name ?? ''} onChange={(e) => set((i) => ({ ...i, corridors: (i.corridors ?? []).map((x) => x.id === corridor.id ? { ...x, name: e.target.value } : x) }))} /></label>
        <p className={s.hint}>{corridor.polygon.length} 个顶点 · 面积 {fmt.num(area(corridor.polygon), 1)} m²。rows 模式并入通行网络；free 模式保留几何但不构成该通行约束。</p>
        <Button variant="destructive" icon={Trash2} onClick={() => { set((i) => ({ ...i, corridors: (i.corridors ?? []).filter((x) => x.id !== corridor.id) })); select(null); }}>删除走廊</Button>
      </div>
    );
  }
  if (selection.type === 'depot') {
    const index = selection.index ?? 0;
    const xy = depotsOf(inst)[index];
    if (!xy) return null;
    return (
      <div className={s.form}>
        <h3>车库 D{index + 1}</h3>
        <div className={s.row}>{depotsOf(inst).map((_, k) => <Button key={k} size="sm" variant={k === index ? 'primary' : 'plain'} onClick={() => select({ type: 'depot', index: k })}>D{k + 1}</Button>)}</div>
        <div className={s.grid2}>
          <Num label="X" value={xy[0]} step={0.5} unit="m" onChange={(n) => set((i) => setDepot(i, index, [n, xy[1]]))} />
          <Num label="Y" value={xy[1]} step={0.5} unit="m" onChange={(n) => set((i) => setDepot(i, index, [xy[0], n]))} />
        </div>
        <p className={s.hint}>车辆按类别展开后，依车库原有顺序轮询分配家库；选库未参与优化。</p>
        <div className={s.row}>
          <Button size="sm" icon={Plus} disabled={depotsOf(inst).length >= 8} onClick={() => { set((i) => addDepot(i, [xy[0] + 2, xy[1]])); select({ type: 'depot', index: depotsOf(inst).length }); }}>添加车库</Button>
          <Button size="sm" variant="destructive" icon={Trash2} disabled={depotsOf(inst).length <= 1} onClick={() => { set((i) => removeDepot(i, index)); select(null); }}>删除此库</Button>
        </div>
      </div>
    );
  }
  if (selection.type === 'unload') {
    const index = selection.index;
    const xy = inst.unload_points?.[index];
    if (!xy) return null;
    return (
      <div className={s.form}>
        <h3>补给 / 卸载点 U{index + 1}</h3>
        <div className={s.grid2}>
          <Num label="X" value={xy[0]} step={0.5} unit="m" onChange={(n) => set((i) => ({ ...i, unload_points: (i.unload_points ?? []).map((q, k) => k === index ? [n, xy[1]] : q) }))} />
          <Num label="Y" value={xy[1]} step={0.5} unit="m" onChange={(n) => set((i) => ({ ...i, unload_points: (i.unload_points ?? []).map((q, k) => k === index ? [xy[0], n] : q) }))} />
        </div>
        <p className={s.hint}><strong className={s.warn}>补给语义：</strong>在声明的补给点瞬时卸载后继续任务，最终返回家库；不包含补给作业具和处理时长。</p>
        <Button variant="destructive" icon={Trash2} onClick={() => { set((i) => ({ ...i, unload_points: (i.unload_points ?? []).filter((_, k) => k !== index) })); select(null); }}>删除补给点</Button>
      </div>
    );
  }
  return (
    <div className={s.form}>
      <h3>场地边界</h3>
      <p className={s.hint}>{inst.boundary.length} 个顶点 · {fmt.num(area(inst.boundary) / 10000, 3)} 公顷。拖动顶点修改边界形状。</p>
    </div>
  );
}

export function ScenarioInfo() {
  const sc = useStudio((st) => st.scenario) as unknown as FleetScenario | null;
  const notify = useStudio((st) => st.notify);
  if (!sc) return null;
  const cards = sc.compose?.cards ?? [];
  const found = new Set((sc.compose?.found ?? []).map((f) => f.key.split('.')[0]));
  return (
    <div className={s.form}>
      <h3>{sc.title}</h3>
      <p className={s.hint}>{sc.summary}</p>
      {sc.compose?.text && <blockquote className={s.quote}>{sc.compose.text}</blockquote>}
      {cards.length > 0 && (
        <div className={s.cards}>
          {cards.map((c) => (
            <div key={c.key} className={s.card} data-default={!found.has(c.key) || undefined} title={found.has(c.key) ? '来自你的描述' : '未提到，使用默认值'}>
              <span>{c.label}</span>
              <strong>{c.value}</strong>
            </div>
          ))}
        </div>
      )}
      <p className={s.meta}>{sc.provenance?.note}</p>
      <GeneratedFrom />
      <div className={s.row}>
        <a className={s.link} href={urls.scenarioExport(sc.id, 'json')} download><Download size={12} /> 场景 JSON</a>
        {sc.problem_class === 'fleet_routing' && <a className={s.link} href={urls.scenarioExport(sc.id, 'geojson')} download><Download size={12} /> GeoJSON</a>}
        {sc.compose?.text && (
          <Button size="sm" icon={RefreshCw} onClick={async () => {
            try {
              const next = await studio.compose(sc.compose!.text, { seed: (sc.compose!.seed ?? 0) + 1 });
              location.hash = `#/plan/${encodeURIComponent(next.id)}`;
            } catch (e) { notify('error', String((e as Error).message)); }
          }}>换一种布局</Button>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- advisor -- */
export function AdvisorPanel() {
  const st = useStudio();
  const rec = st.recommendation;
  const algos = useMemo(() => Object.fromEntries(st.algorithms.map((a) => [a.id, a])), [st.algorithms]);
  const budgetParams = st.chosen.flatMap((id) => (algos[id]?.params ?? []).filter((p) => p.name === 'time_limit_s'));
  const seedParams = st.chosen.flatMap((id) => algos[id]?.randomness?.uses_seed === false ? [] : (algos[id]?.params ?? []).filter((p) => p.name === 'seed'));
  const minBudget = Math.max(0.05, ...budgetParams.map((p) => p.min ?? 0.05));
  const maxBudget = Math.min(600, ...budgetParams.map((p) => p.max ?? 600));
  const maxSeed = Math.min(2147483647, ...seedParams.map((p) => p.max ?? 2147483647));
  const paramsValid = st.budget >= minBudget && st.budget <= maxBudget && st.seed >= 0 && st.seed <= maxSeed;
  if (!st.scenario) return <EmptyState title="未打开场景" />;
  return (
    <div className={s.scroll}>
      <div className={s.form}>
        <div className={s.rowSpace}>
          <h3>推荐算法</h3>
          {st.recommending ? <Spinner /> : <Button size="sm" variant="plain" icon={RefreshCw} onClick={() => void st.refreshRecommendation()}>重新分析</Button>}
        </div>
        {st.solveError && <Notice tone="error">{st.solveError}</Notice>}
        {rec && <p className={s.lead}>{rec.explanation}</p>}
        {rec && rec.features.length > 0 && (
          <div className={s.features}>
            {rec.features.map((f) => (
              <div key={f.key} className={s.feature} title={(f as { hint?: string }).hint ?? ''}>
                <span>{f.name}</span>
                <strong>{f.value ?? '—'}{f.unit ? ` ${f.unit}` : ''}</strong>
              </div>
            ))}
          </div>
        )}
        <ul className={s.ranking}>
          {(rec?.ranking ?? []).map((r) => {
            const a = algos[r.algorithm_id];
            const on = st.chosen.includes(r.algorithm_id);
            return (
              <li key={r.algorithm_id} data-off={!r.applicable || undefined}>
                <label className={s.rankHead}>
                  <input type="checkbox" checked={on} disabled={!r.applicable}
                    onChange={() => st.setChosen(on ? st.chosen.filter((x) => x !== r.algorithm_id) : [...st.chosen, r.algorithm_id])} />
                  <span className={s.rankNo}>{r.rank}</span>
                  <span className={s.rankName}>{a?.name ?? r.algorithm_id}</span>
                  <LibraryBadge library={a?.library} />
                  {r.suitability === '推荐' && <Badge tone="green">推荐</Badge>}
                </label>
                <p>{r.reasons[0]}</p>
                {r.reasons.length > 1 && <p className={s.meta}>{r.reasons.slice(1).join('；')}</p>}
                {on && <AlgorithmParameterFields algorithmId={r.algorithm_id} />}
              </li>
            );
          })}
        </ul>
        <div className={s.solveBar}>
          <label className={s.field} title={`允许范围 ${minBudget}–${maxBudget} s`}><span>时间预算</span>
            <span className={s.inputUnit}><Input type="number" min={minBudget} max={maxBudget} step={0.05} value={st.budget} onChange={(e) => {
              const n = e.target.valueAsNumber;
              if (Number.isFinite(n)) st.setBudget(Math.min(maxBudget, Math.max(minBudget, n)));
            }} /><em>秒/算法</em></span>
          </label>
          <label className={s.field} title={`允许范围 0–${maxSeed}`}><span>随机种子</span>
            <span className={s.inputUnit}><Input type="number" disabled={!seedParams.length} min={0} max={maxSeed} step={1} value={st.seed} onChange={(e) => {
              const n = e.target.valueAsNumber;
              if (Number.isFinite(n)) st.setSeed(Math.min(maxSeed, Math.max(0, Math.round(n))));
            }} /><em>{seedParams.length ? '提交时冻结' : '所选算法不使用'}</em></span>
          </label>
          <Button variant="primary" size="lg" icon={Play} loading={st.busy === '求解中'} disabled={!st.chosen.length || !paramsValid}
            onClick={() => void st.solve()}>
            {st.chosen.length > 1 ? `对比 ${st.chosen.length} 个算法` : '求解'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function AlgorithmParameterFields({ algorithmId }: { algorithmId: string }) {
  const algorithm = useStudio((st) => st.algorithms.find((a) => a.id === algorithmId));
  const suggested = useStudio((st) => st.recommendation?.ranking.find((r) => r.algorithm_id === algorithmId)?.suggested_params);
  const overrides = useStudio((st) => st.paramOverrides[algorithmId]) ?? {};
  const setAlgorithmParam = useStudio((st) => st.setAlgorithmParam);
  const params = (algorithm?.params ?? []).filter((p) => !['time_limit_s', 'seed'].includes(p.name));
  if (!algorithm || !params.length) return null;
  return (
    <div className={s.grid2}>
      {params.map((p) => {
        const recommended = suggested?.[p.name];
        const value = overrides[p.name] ?? recommended ?? p.default;
        const provenance = `默认 ${String(p.default ?? '后端决定')}${recommended !== undefined ? ` · 推荐 ${String(recommended)}` : ''}`;
        const range = p.min !== undefined || p.max !== undefined ? ` · 范围 ${p.min ?? '−∞'}–${p.max ?? '+∞'}` : '';
        const label = `${algorithm.name} ${p.label}`;
        return (
          <label key={p.name} className={s.field} title={`${provenance}${range}`}>
            <span>{p.label}</span>
            {p.type === 'boolean' ? (
              <input aria-label={label} type="checkbox" checked={value === true} onChange={(e) => setAlgorithmParam(algorithmId, p.name, e.target.checked)} />
            ) : p.type === 'select' ? (
              <Select aria-label={label} value={String(value ?? '')} onChange={(e) => setAlgorithmParam(algorithmId, p.name, e.target.value)}>
                {(p.options ?? []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
            ) : p.type === 'number' || p.type === 'integer' ? (
              <Input aria-label={label} type="number" value={typeof value === 'number' ? value : ''} min={p.min} max={p.max} step={p.type === 'integer' ? 1 : 'any'} onChange={(e) => {
                const n = e.target.valueAsNumber;
                if (Number.isFinite(n) && (p.min === undefined || n >= p.min) && (p.max === undefined || n <= p.max)) {
                  setAlgorithmParam(algorithmId, p.name, p.type === 'integer' ? Math.round(n) : n);
                }
              }} />
            ) : (
              <Input aria-label={label} value={String(value ?? '')} onChange={(e) => setAlgorithmParam(algorithmId, p.name, e.target.value)} />
            )}
            <small className={s.meta}>{provenance}{range}</small>
          </label>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------- compare -- */
type Col = {
  label: string;
  get: (r: Solution) => number | string | boolean | null | undefined;
  digits?: number;
  unit?: string | ((r: Solution) => string);
  better?: 'min' | 'max';
  pct?: boolean;
};
const metric = (k: string) => (r: Solution) => r.metrics[k] as number | string | boolean | null | undefined;
const objectiveUnit = (r: Solution) => ` ${r.objective.unit}`;
const COLUMNS: Record<string, Col[]> = {
  fleet_routing: [
    { label: '总里程', get: (r) => metric('total_distance_m')(r), digits: 1, unit: ' m', better: 'min' },
    { label: '最晚完成', get: metric('makespan_s'), digits: 0, unit: ' s', better: 'min' },
    { label: '预计实际工期', get: metric('predicted_makespan_s'), digits: 0, unit: ' s' },
    { label: '车辆', get: metric('vehicles_used'), digits: 0 },
    { label: '趟数', get: metric('trips'), digits: 0 },
    { label: '均衡度', get: metric('balance_cv'), digits: 2, better: 'min' },
  ],
  euclidean_cvrp: [
    { label: '目标值', get: (r) => r.objective.value, digits: 2, unit: objectiveUnit, better: 'min' },
    { label: '与 BKS 差距', get: metric('gap_to_bks_pct'), digits: 2, unit: '%', better: 'min' },
    { label: '路线数', get: metric('n_routes'), digits: 0 },
  ],
  coverage: [
    { label: '总长度', get: (r) => r.objective.value, digits: 1, unit: objectiveUnit, better: 'min' },
    { label: '作业距离', get: metric('working_length_m'), digits: 1, unit: ' m' },
    { label: '非作业距离', get: metric('non_working_length_m'), digits: 1, unit: ' m', better: 'min' },
    { label: '作业效率', get: metric('efficiency'), digits: 1, pct: true, better: 'max' },
    { label: '全田块覆盖率', get: metric('coverage_fraction'), digits: 1, pct: true, better: 'max' },
    { label: '内缩作业区覆盖率', get: metric('working_area_fraction'), digits: 1, pct: true, better: 'max' },
    { label: '覆盖口径', get: metric('coverage_scope') },
    { label: '转弯半径约束', get: metric('turn_radius_enforced') },
  ],
  point_to_point: [
    { label: '路径长度', get: (r) => r.objective.value, digits: 2, unit: objectiveUnit, better: 'min' },
    { label: '最大曲率', get: metric('max_curvature_per_m'), digits: 2, unit: ' /m', better: 'min' },
    { label: '最小净空', get: metric('min_clearance_m'), digits: 3, unit: ' m', better: 'max' },
    { label: '拐点', get: metric('turns'), digits: 0, better: 'min' },
  ],
  turn_routing: [
    { label: '成本', get: (r) => r.objective.value, digits: 0, unit: objectiveUnit, better: 'min' },
    { label: '长度', get: (r) => r.metrics.length, digits: 2, unit: (r) => ` ${r.metrics.length_unit}` },
    { label: '转弯惩罚', get: metric('turn_penalty_total'), digits: 0, unit: objectiveUnit },
    { label: '违规转弯', get: metric('forbidden_turn_violations'), digits: 0, better: 'min' },
  ],
};

export function ComparePanel() {
  const st = useStudio();
  const [canceling, setCanceling] = useState<string | null>(null);
  const rows = st.runIds.map((id) => st.runs[id]).filter(Boolean) as Solution[];
  const cols = COLUMNS[st.scenario?.problem_class ?? ''] ?? COLUMNS.euclidean_cvrp;
  const anchor = rows.find((r) => r.run_id === st.focusRun) ?? rows[0];
  const group = anchor ? rows.filter((r) => comparable(r, anchor)) : [];
  const partition = partitionRuns(rows);
  const valid = group.filter((r) => verifiedPlan(r) && !exclusionReason(r));
  const groups = partition.groups;
  const bests = cols.map((c) => {
    if (!c.better) return null;
    const vals = valid.map(c.get).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    return vals.length ? (c.better === 'min' ? Math.min(...vals) : Math.max(...vals)) : null;
  });
  const readErrors = st.runIds.filter((id) => st.runErrors[id]).map((id) => <Notice key={id} tone="error">{id}：{st.runErrors[id]}</Notice>);
  if (!rows.length) {
    return <>{readErrors}<EmptyState icon={CircleDashed} title="还没有方案" detail={useStudio.getState().workspace === 'agent'
      ? '智能体求解后，各方案的指标与复核结论会出现在这里。'
      : '在“推荐与求解”里勾选算法后点击对比，结果会出现在这里。'} /><HistoryRuns /></>;
  }
  return (
    <div className={s.compare}>
      {readErrors}
      <div className={s.pad} style={{ paddingBottom: 4 }}>
        <p className={s.meta}>本批 {rows.length} 次运行 · {rows.filter(verifiedPlan).length} 个已验算可行解。最佳值仅在相同输入、目标、单位和时间预算内标记。预计工期仅作展示，不跨预测模型标记最佳。</p>
        {rows.some((r) => r.check?.valid === true && mapCheckIssue(r)) && (
          <div className={s.meta} data-exclusion-summary="map_check" aria-label="地图检查未计入的运行">
            {rows.filter((r) => r.check?.valid === true && mapCheckIssue(r)).map((r) => <MapExclusionNote key={r.run_id} run={r} />)}
          </div>
        )}
        {anchor?.planning && <p className={s.meta}>{anchor.planning.objective_label} · {anchor.planning.objective_unit} · 输入 {anchor.instance_sha256.slice(0, 12)}</p>}
        <HistoryRuns />
        {groups.length > 1 && <Notice tone="warn">存在 {groups.length} 种比较口径{groups.some((g) => g.identity === 'unknown') ? '（含身份未核验组）' : ''}；当前最佳值只针对选中方案所在组，不跨组排名。</Notice>}
      </div>
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead>
            <tr>
              <th>算法</th><th>状态</th>
              {cols.map((c) => <th key={c.label} className={s.num}>{c.label}</th>)}
              <th className={s.num}>计算</th><th>验算</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const state = resultState(r);
              const historicalInput = !!st.scenario && !!st.draft && !currentInput(r, st.scenario, st.draft);
              const algorithm = st.algorithms.find((a) => a.id === r.algorithm.id);
              const supportsBudget = !!algorithm?.params.some((param) => param.name === 'time_limit_s');
              const supportsSeed = !!algorithm?.params.some((param) => param.name === 'seed') && algorithm.randomness?.uses_seed !== false;
              const differentParams = (supportsBudget && r.params.time_limit_s !== st.budget) || (supportsSeed && r.params.seed !== st.seed);
              const frozenParams = [
                supportsBudget ? `预算 ${String(r.params.time_limit_s ?? '默认/未记录')} s` : '',
                supportsSeed ? `种子 ${String(r.params.seed ?? '默认/未记录')}` : '',
                ...Object.entries(r.params).filter(([key]) => !['time_limit_s', 'seed'].includes(key))
                  .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`),
              ].filter(Boolean).join(' · ');
              const instance = r.problem_class === 'fleet_routing' ? r.instance as unknown as FleetInstance | undefined : undefined;
              const blocked = instance ? unacceptedPlanReason(instance, planModelSchema(r)) ?? (instance.vehicles?.fleet?.some((item) => (item.min_turn_radius_m ?? 0) > 0) ? '仅规划，未适配执行' : '') : '';
              return (
                <tr key={r.run_id} data-run={r.run_id} aria-selected={st.focusRun === r.run_id} onClick={() => st.focus(r.run_id)}>
                  <td>
                    <span className={s.algName} title={`${r.algorithm.origin}${r.algorithm.version ? ` · 版本/源码 ${r.algorithm.version}` : ''}`}>{r.algorithm.name}</span>
                    <div className={s.meta} title={`冻结 run_id：${r.run_id}；${frozenParams || '参数见原始记录'}`}>{r.run_id} · {frozenParams || '参数见原始记录'}</div>
                    <div className={s.row}>
                      {historicalInput && <Badge tone="orange" >历史输入</Badge>}
                      {differentParams && <Badge tone="gray">当前参数不同</Badge>}
                      {anchor && !comparable(r, anchor) && <Badge tone="gray">独立口径</Badge>}
                      {exclusionReason(r) && <Badge tone="orange">不参与排名</Badge>}
                      {identityState(r) === 'unknown' && r.state === 'done' && <Badge tone="gray">身份未核验</Badge>}
                      {identityState(r) === 'quarantined' && <Badge tone="red">已隔离</Badge>}
                      {blocked && <Badge tone="orange">{blocked}</Badge>}
                    </div>
                  </td>
                  <td title={state.title}>{state.text === '计算中' ? <span className={s.running}><Spinner /> 计算中</span> : state.text}{state.title ? ' ⓘ' : ''}</td>
                  {cols.map((c, i) => {
                    const shown = withoutInvalidatedResults(r)!;
                    const v = shown.state === 'done' && shown.routes.length ? c.get(shown) : null;
                    const isBest = valid.includes(r) && typeof v === 'number' && bests[i] != null && Math.abs(v - (bests[i] as number)) < 1e-9 && valid.length > 1;
                    const unit = typeof c.unit === 'function' ? c.unit(r) : c.unit ?? '';
                    const text = v == null
                      ? (shown.state === 'done' && shown.routes.length ? '未记录' : '—')
                      : typeof v === 'number'
                        ? `${fmt.num(c.pct ? v * 100 : v, c.digits ?? 2)}${c.pct ? '%' : unit}`
                        : typeof v === 'boolean' ? (v ? '是' : '否') : v;
                    return <td key={c.label} className={s.num} data-best={isBest || undefined}>{text}</td>;
                  })}
                  <td className={s.num}>{fmt.sec(r.metrics.wall_s)}</td>
                  <td data-verify={identityState(r) === 'quarantined' ? 'quarantined' : requiresCoverageRecheck(r) ? 'legacy' : verifyText(r).code}>{identityState(r) === 'quarantined' ? '已隔离 · 不适用' : requiresCoverageRecheck(r) ? '旧版检查 · 待重验' : (() => {
                    const v = verifyText(r);
                    return v.code === 'pass' ? <span className={s.ok}><Check size={13} /> 通过</span>
                      : v.code === 'unchecked' ? v.text
                      : <span className={s.bad} title={v.code === 'route_fail' ? r.check?.errors.join('；') : '路线检查通过，但地图碰撞检查未通过或未完成'}><X size={13} /> {v.text}</span>;
                  })()}</td>
                  <td className={s.rowActions} onClick={(e) => e.stopPropagation()}>
                    <a href={exportUrl.run(r.run_id, 'json')} download title="求解结果 JSON（路线与指标）">JSON</a>
                    <a href={exportUrl.run(r.run_id, 'csv')} download title="几何点 CSV（兼容格式）">几何 CSV</a>
                    {r.problem_class === 'fleet_routing' && <a href={exportUrl.run(r.run_id, 'tasks-csv')} download title="任务服务行，按车辆/趟次/顺序；不包含回仓几何点">任务/趟次 CSV</a>}
                    <a href={exportUrl.run(r.run_id, 'geojson')} download title="地图与路线">GeoJSON</a>
                    {['queued', 'running'].includes(r.state) && (
                      <button type="button" className={s.simLink} title="取消这个求解运行" disabled={canceling === r.run_id}
                        onClick={async () => {
                          setCanceling(r.run_id);
                          try {
                            await st.cancelRun(r.run_id);
                          } catch (e) {
                            st.notify('error', `取消 ${r.algorithm.name} 失败：${(e as Error).message}`);
                          } finally {
                            setCanceling(null);
                          }
                        }}>
                        <X size={12} /> {canceling === r.run_id ? '取消中…' : '取消'}
                      </button>
                    )}
                    {r.problem_class === 'fleet_routing' && r.state === 'done' && r.routes.length > 0 && (
                      <button type="button" className={s.simLink} title="在 Gazebo 田间世界里让各车按此方案执行"
                        onClick={() => void import('./FieldSimPanel').then((m) => m.startFieldSim(r.run_id))}>
                        <Tractor size={12} /> Gazebo
                      </button>
                    )}
                    {r.problem_class !== 'fleet_routing' && r.frame.kind === 'map' && verifiedPlan(r) && (
                      <button type="button" className={s.simLink} title="将此方案交给独立的 TurtleBot4 仿真；执行方式在仿真面板确认"
                        onClick={() => { st.focus(r.run_id); window.dispatchEvent(new CustomEvent('studio:open-panel', { detail: 'sim' })); }}>
                        <Tractor size={12} /> TB4 仿真
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {anchor && <div className={s.pad} data-selected-run={anchor.run_id}><IntegrityNotice run={anchor} /><RunTrace run={anchor} title="选中方案追溯" /></div>}
        {partition.excluded.length > 0 && (
          <div className={s.pad}>
            <h3>未参与比较与排名（{partition.excluded.length}）</h3>
            <ul className={s.meta} aria-label="未参与比较的运行">
              {partition.excluded.map(({ run, reason }) => (
                <li key={run.run_id} data-exclusion={reason.code}>{run.algorithm.name} · {run.run_id} · {resultState(run).text} · {reason.text}
                  {reason.map && <MapExclusionNote run={run} label="碰撞明细" />}</li>
              ))}
            </ul>
          </div>
        )}
        {rows.some((r) => r.error) && <Notice tone="error">{rows.filter((r) => r.error).map((r) => `${r.algorithm.name}：${r.error}`).join('；')}</Notice>}
      </div>
      <Convergence runs={group.filter((r) => r.process?.source_identity_verified !== false)} />
      <SeedBandChart runs={group} />
      <VerificationChart runs={rows} />
      <StatusFlowChart runs={rows} />
    </div>
  );
}

function Convergence({ runs }: { runs: Solution[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const eligible = runs.filter((r) => r.trace_contract?.semantic === 'reported_objective');
  const excluded = runs.filter((r) => r.trace_contract?.semantic !== 'reported_objective' && ((r as Solution & { trace?: [number, number][] }).trace?.length ?? 0) > 0);
  const data = eligible.flatMap((r) => ((r as Solution & { trace?: [number, number][] }).trace ?? []).map(([t, v]) => ({ t: Math.max(t, 1e-3), v, alg: r.algorithm.name })));
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.replaceChildren();
    if (data.length < 2) return;
    const chart = Plot.plot({
      width: el.clientWidth || 360, height: 150, marginLeft: 56, marginBottom: 30, style: { fontFamily: 'Inter, PingFang SC, sans-serif', fontSize: '10px' },
      x: logTimeAxis(data.map((d) => d.t)), y: { label: `目标值${unitSuffix(runs[0]?.objective.unit)}`, grid: true }, color: { legend: true },
      marks: [Plot.line(data, { x: 't', y: 'v', stroke: 'alg', curve: 'step-after', strokeWidth: 1.6 }), Plot.dot(data, { x: 't', y: 'v', fill: 'alg', r: 1.6 })],
    });
    el.append(chart);
  }, [data]);
  if (data.length < 2) return excluded.length ? <div className={s.pad}><p className={s.meta}>内部搜索曲线使用量化、松弛或加权成本，与最终验算目标不同，可在原始记录中查看。</p></div> : null;
  return (
    <div className={s.convergence}>
      <h4>求解过程中最好解的变化</h4>
      <div ref={ref} />
    </div>
  );
}

/* --------------------------------------------------------------- verify -- */
export function VerifyPanel() {
  const st = useStudio();
  const run = st.focusRun ? st.runs[st.focusRun] : undefined;
  if (!run) return <EmptyState icon={Check} title="选择一个方案" detail="求解完成后，这里逐项列出独立验算结果。" />;
  if (run.state !== 'done') {
    const label = { queued: '方案排队中', running: '方案计算中', error: '方案计算出错', timeout: '方案计算超时', canceled: '方案已取消' }[run.state] ?? '方案未完成';
    return <EmptyState title={run.error ?? label} detail="没有可供独立验算的最终解。" />;
  }
  const check = run.check;
  if (!check) return <EmptyState icon={CircleDashed} title="未检查" detail="缺少独立检查结果，可行性待确认。" />;
  const errors = check.errors;
  const recheck = requiresCoverageRecheck(run);
  const fleet = isFleet(st.scenario);
  const rows = (run.instance as unknown as FleetInstance | undefined)?.vehicles?.travel === 'rows';
  const has = (re: RegExp) => errors.filter((e) => re.test(e));
  const items = fleet ? [
    { label: '每个任务点恰好服务一次', bad: has(/未被分配|重复/) },
    { label: '每趟载重不超过容量', bad: has(/载重/) },
    { label: '使用车辆不超过车队规模', bad: has(/超过车队|单趟/) },
    { label: '路线避开障碍、不出场地', bad: has(/穿过障碍|越出/) },
    ...(rows ? [{ label: '只走行间通道和地头，不斜穿作物', bad: has(/离开行间/) }] : []),
    { label: '路线经过每个任务的作业位置', bad: has(/作业位置/) },
    { label: '从车库出发并返回', bad: has(/车库/) },
  ] : [{ label: '独立检查器', bad: errors }];
  const explained = new Set(items.flatMap((item) => item.bad));
  const unexplained = errors.filter((error) => !explained.has(error));
  const computed = check.computed_cost;
  const reported = (run.metrics.total_distance_m as number | undefined) ?? run.metrics.length;
  const costState = computed == null || reported == null
    ? 'unknown'
    : Math.abs(computed - reported) <= 1e-6 * Math.max(1, Math.abs(reported)) ? 'pass' : 'fail';
  return (
    <div className={s.scroll}>
      <div className={s.form}>
        <div className={s.rowSpace}>
          <h3>{run.algorithm.name} 的验算</h3>
          {recheck ? <Badge tone="orange">旧版检查 · 须重求解</Badge> : check.valid ? <Badge tone="green">全部通过</Badge> : <Badge tone="red">未通过{errors.length ? ` · ${errors.length} 项` : ''}</Badge>}
        </div>
        {recheck && <Notice tone="warn">旧版检查遗漏了最后作业段，请重新求解并按新版验收。以下保留历史检查记录。</Notice>}
        {fleet && researchAcceptanceIssues(run.instance as unknown as FleetInstance, planModelSchema(run)).map((issue) => <Notice key={issue} tone="warn">{issue} 验算通过后仍需满足此限制。</Notice>)}
        {!!unexplained.length && <Notice tone="error">其他检查失败：{unexplained.join('；')}</Notice>}
        <ul className={s.checks}>
          {items.map((it) => {
            const failed = !check.valid && it.bad.length > 0;
            return (
              <li key={it.label} data-bad={failed || undefined}>
                {failed ? <X size={14} /> : check.valid ? <Check size={14} /> : <CircleDashed size={14} />}
                <span>{it.label}</span>
                {failed ? <small>{it.bad.join('；')}</small> : !check.valid && <small>未确认</small>}
              </li>
            );
          })}
          {fleet && (
            <li data-bad={costState === 'fail' || undefined}>
              {costState === 'pass' ? <Check size={14} /> : costState === 'fail' ? <X size={14} /> : <CircleDashed size={14} />}
              <span>里程复算{costState === 'pass' ? '一致' : costState === 'fail' ? '不一致' : '未记录'}</span>
              <small>{fmt.num(computed, 2)} m（验算） vs {fmt.num(reported, 2)} m（求解器）</small>
            </li>
          )}
          {run.map_check && (
            <li data-bad={run.map_check.collision_free === false || undefined}>
              {run.map_check.checked && run.map_check.collision_free === true ? <Check size={14} /> : run.map_check.collision_free === false ? <X size={14} /> : <CircleDashed size={14} />}
              <span>地图净空{run.map_check.checked && run.map_check.collision_free !== null ? '' : '未确认'}</span>
              <small>最小 {fmt.num(run.map_check.min_clearance_m, 3)} m</small>
            </li>
          )}
        </ul>
        {!!run.map_check?.violations?.length && <div>
          <h4>地图违规定位</h4>
          {!st.scenario || !st.draft || !currentInput(run, st.scenario, st.draft)
            ? <p className={s.hint}>这是历史输入。请先在地图查看冻结输入，再定位检查点。</p> : null}
          {run.map_check.violations.slice(0, 30).map((violation, i) => <Button key={i} size="sm"
            onClick={() => centerMap(violation.point, 24, inputIdentity(run.problem_class, run.instance))} title={`坐标 (${violation.point.join(', ')}) m；净空 ${violation.clearance_m} m`}>
            定位 路线 {violation.route + 1} · 段 {violation.segment + 1}
          </Button>)}
          {run.map_check.violations.length > 30 && <p className={s.meta}>此处显示前 30 项，完整检查点保留在记录中。</p>}
        </div>}
        <p className={s.meta}>验算方：{check.checker}。数据来源：{run.algorithm.origin}</p>
        {fleet && <PerVehicleTable run={run} />}
      </div>
    </div>
  );
}

function PerVehicleTable({ run }: { run: Solution }) {
  const per = (run.metrics.per_vehicle as unknown as PerVehicle[] | undefined) ?? [];
  const routes = run.routes as FleetRoute[];
  return (
    <table className={s.table}>
      <thead><tr><th>车辆</th><th className={s.num}>任务</th><th className={s.num}>趟数</th><th className={s.num}>最大单趟载重</th><th className={s.num}>里程</th><th className={s.num}>用时</th><th>顺序</th></tr></thead>
      <tbody>
        {per.map((v, i) => {
          const facts = vehicleFacts(run, v, i);
          const route = routes.find((item) => item.vehicle_name === v.vehicle) ?? routes[i];
          return (
            <tr key={v.vehicle}>
              <td><span className={s.swatch} style={{ background: shownColor(v.color) }} /> {v.vehicle}<div className={s.meta}>{facts.className} · 家库 {facts.home}</div></td>
              <td className={s.num}>{v.tasks}</td>
              <td className={s.num}>{v.trips}</td>
              <td className={s.num}>{v.load}</td>
              <td className={s.num}>{fmt.num(v.length_m, 1)} m</td>
              <td className={s.num}>{fmt.sec(v.duration_s)}</td>
              <td className={s.seq}>
                <div>{(route?.trips ?? [route?.sequence ?? []]).map((t) => t.join('→')).join(' ｜ ')}</div>
                {!!route?.reload_stops?.length && <div title="原始 reload_stops 坐标；按运行时冻结的补给顺序显示">中途停靠：{route.reload_stops.map((xy) => `(${fmt.num(xy[0], 2)}, ${fmt.num(xy[1], 2)})`).join('；')}</div>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
