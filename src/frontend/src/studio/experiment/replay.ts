/**
 * Replay of a finished field simulation on the task map: the recorded ground-truth trails, task arrivals and the
 * executed plan, with derived checks (cross-track error against the executed path, closest approach between
 * vehicles, stalls, unreached tasks). Everything here is computed from the saved result.json, nothing is simulated.
 */
import { create } from 'zustand';
import type { TimeWindowRecord, XY } from '../../lab/types';
import { studio } from '../api';
import type { FieldPhysics, FieldPhysicsEvidence, FieldSimMetrics, FieldVehicleState } from '../types';
import { trafficStatus, type TrafficEvidence } from './traffic';
import { DEFAULT_MAX_GAP_S, gapLimit, orderedTrail, replayIndex } from './replayIndex';

export type TrailPoint = [number, number, number, number]; // t (sim s), x, y, yaw

export interface SimTask {
  vehicle: string;
  stop: XY | null;
  id: number;
  label: string;
  xy: XY;
  state: string;
  t_sim: number | null;
  distance_m: number | null;
  requested_service_s?: number | null;
  actual_service_s?: number | null;
  serviced_at?: number | null;
  service_complete?: boolean;
}
export interface SimPlanVehicle { name: string; label: string; color: string; start: [number, number, number]; path: XY[]; planned_length_m: number; planned_duration_s: number }
export interface SimResult extends TrafficEvidence {
  traffic_mode?: 'serial' | 'coordinated' | null;
  time_windows?: TimeWindowRecord | null;
  session_id: string; run_id: string; scenario_id: string; algorithm: string | null; world: string; finished_utc: string;
  sim_time: number | null; real_time_factor: number | null; state: 'succeeded' | 'failed' | 'canceled';
  metrics: FieldSimMetrics; travel?: 'rows' | 'free';
  vehicles: FieldVehicleState[]; tasks: SimTask[]; trails: Record<string, TrailPoint[]>;
  execution_plan?: { vehicles?: SimPlanVehicle[]; instance?: import('../types').FleetInstance; robot_radius_m?: number; travel?: string; world?: string; bounds?: number[] };
  execution_provenance?: Record<string, unknown> | null; planning_solution?: Record<string, unknown> | null; provenance_warning?: string;
  execution?: { schema?: string; mode?: string; localization?: { preset?: string; gnss?: Record<string, unknown> | null } } | null;
  physics?: FieldPhysics | null;
  physics_audit?: FieldPhysicsEvidence | null;
  physics_runtime?: FieldPhysicsEvidence | null;
  truth_timing?: { scope?: string; [key: string]: unknown } | null;
  replay_contract?: { schema?: string; max_interpolation_gap_s: number; clock?: string; source?: string };
  energy?: Record<string, unknown> | null;
  errors?: string[];
  session_report?: { status?: string; cleanup_status?: string; execution_state?: string; remaining_owned_processes?: unknown[]; errors?: string[] } | null;
}

export interface Anomaly { key: string; tone: 'error' | 'warn'; text: string; xy: XY | null; t: number | null; vehicle?: string }

export const SIM_BODY = { length_m: 0.8, width_m: 0.56 };
export function simBody(result: SimResult): { length_m: number; width_m: number } {
  const size = result.physics?.body?.size_m;
  return size?.length === 3 ? { length_m: size[0], width_m: size[1] } : SIM_BODY;
}

export function poseAt(trail: TrailPoint[], t: number, maxGap = DEFAULT_MAX_GAP_S): [number, number, number] | null {
  if (!trail.length || !orderedTrail(trail) || !Number.isFinite(t)) return null;
  const last = trail[trail.length - 1];
  if (t < trail[0][0] || t > last[0]) return null;
  if (t === trail[0][0]) return [trail[0][1], trail[0][2], trail[0][3]];
  if (t === last[0]) return [last[1], last[2], last[3]];
  let lo = 0, hi = trail.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (trail[mid][0] <= t) lo = mid; else hi = mid;
  }
  const a = trail[lo], b = trail[hi];
  if (t === a[0]) return [a[1], a[2], a[3]];
  if (t === b[0]) return [b[1], b[2], b[3]];
  if (b[0] - a[0] > maxGap + 1e-9) return null;
  const f = (t - a[0]) / Math.max(1e-9, b[0] - a[0]);
  let dyaw = b[3] - a[3];
  if (dyaw > Math.PI) dyaw -= 2 * Math.PI;
  if (dyaw < -Math.PI) dyaw += 2 * Math.PI;
  return [a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]), a[3] + f * dyaw];
}

/** Index of the last trail sample at or before t. */
export function indexAt(trail: TrailPoint[], t: number): number {
  let lo = 0, hi = trail.length - 1;
  if (!trail.length || !orderedTrail(trail) || t < trail[0][0]) return -1;
  if (t >= trail[hi][0]) return hi;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (trail[mid][0] <= t) lo = mid; else hi = mid;
  }
  return lo;
}

export interface ReplayMetric {
  distance_m: number | null;
  distance_complete: boolean;
  missing_s: number;
  elapsed_s: number;
  tasks_reached: number;
  tasks_completed: number | null;
  cross_track_mean_m: number | null;
  cross_track_max_m: number | null;
}

export function metricsAt(r: SimResult, vehicle: string, t: number): ReplayMetric {
  const trail = r.trails[vehicle] ?? [];
  const index = indexAt(trail, t);
  const indexed = replayIndex(r, vehicle);
  let distance = indexed.distance[index] ?? 0;
  const pose = poseAt(trail, t, indexed.gapLimit);
  if (pose && index >= 0) distance += Math.hypot(pose[0] - trail[index][1], pose[1] - trail[index][2]);
  const start = trail[0]?.[0] ?? 0;
  const end = trail.at(-1)?.[0] ?? start;
  const elapsed = trail.length ? Math.max(0, Math.min(end, Math.max(start, t)) - start) : 0;
  const tasks = r.tasks.filter((task) => task.vehicle === vehicle);
  const reached = tasks.filter((task) => task.state === 'reached' && task.t_sim != null && task.t_sim <= t).length;
  const hasCompletion = tasks.every((task) => 'serviced_at' in task || task.service_complete !== undefined);
  const completed = hasCompletion ? tasks.filter((task) => task.service_complete !== false && task.serviced_at != null && task.serviced_at <= t).length : null;
  let missing = indexed.missing[index] ?? 0;
  if (index >= 0 && index + 1 < trail.length && trail[index + 1][0] - trail[index][0] > indexed.gapLimit) {
    missing += Math.max(0, Math.min(t, trail[index + 1][0]) - trail[index][0]);
  }
  const measured = indexed.deviationCount[index] ?? 0;
  return {
    distance_m: indexed.ordered && index >= 0 ? distance : null,
    distance_complete: indexed.ordered && index >= 0 && missing === 0,
    missing_s: missing,
    elapsed_s: elapsed,
    tasks_reached: reached,
    tasks_completed: completed,
    cross_track_mean_m: measured ? indexed.deviationSum[index] / measured : null,
    cross_track_max_m: measured ? indexed.deviationMax[index] : null,
  };
}

export interface SimTaskEvent {
  key: string;
  kind: 'reached' | 'completed';
  t: number;
  xy: XY;
  task: SimTask;
}

export function taskEvents(r: SimResult): SimTaskEvent[] {
  return r.tasks.flatMap((task) => {
    const events: SimTaskEvent[] = [];
    if (task.state === 'reached' && task.t_sim != null) events.push({ key: `${task.vehicle}-${task.id}-reached`, kind: 'reached', t: task.t_sim, xy: task.stop ?? task.xy, task });
    if (task.service_complete !== false && task.serviced_at != null) events.push({ key: `${task.vehicle}-${task.id}-completed`, kind: 'completed', t: task.serviced_at, xy: task.stop ?? task.xy, task });
    return events;
  }).sort((a, b) => a.t - b.t || (a.kind === b.kind ? 0 : a.kind === 'reached' ? -1 : 1));
}

export interface Derived {
  startTime: number;
  duration: number;
  crossTrack: Record<string, { max: number; at: TrailPoint | null; mean: number }>;
  separation: { d: number; t: number; a: string; b: string; xy: XY } | null;
  stalls: { vehicle: string; t0: number; t1: number; xy: XY }[];
  anomalies: Anomaly[];
}

export function derive(r: SimResult): Derived {
  const names = Object.keys(r.trails);
  const timedNames = names.filter((n) => orderedTrail(r.trails[n]));
  const duration = Math.max(0, ...timedNames.map((n) => r.trails[n].at(-1)?.[0] ?? 0));
  const starts = timedNames.map((n) => r.trails[n][0]?.[0]).filter((t): t is number => Number.isFinite(t));
  const startTime = starts.length ? Math.min(...starts) : 0;
  const plan = new Map((r.execution_plan?.vehicles ?? []).map((v) => [v.name, v]));
  const label = (n: string) => r.vehicles.find((v) => v.name === n)?.label ?? n;

  // cross-track: distance from each ground-truth sample to the path the vehicle was told to follow
  const crossTrack: Derived['crossTrack'] = {};
  for (const n of names) {
    const path = plan.get(n)?.path;
    if (!path || path.length < 2) continue;
    const indexed = replayIndex(r, n);
    if (!indexed.ordered || !r.trails[n].length) continue;
    const max = indexed.deviationMax.at(-1) ?? 0;
    const where = indexed.deviationMax.findIndex((v) => v === max);
    crossTrack[n] = { max, at: max > 0 ? r.trails[n][where] : null,
      mean: (indexed.deviationSum.at(-1) ?? 0) / Math.max(1, indexed.deviationCount.at(-1) ?? 0) };
  }

  // closest approach between any two vehicles on a common 0.5 s clock
  let separation: Derived['separation'] = null;
  if (names.length > 1) {
    // Sample observed intervals only. A clock jump must not create billions of
    // iterations across missing data or freeze the workbench.
    const observationTimes = new Set<number>();
    for (const name of timedNames) for (const segment of replayIndex(r, name).segments) {
      const first = segment[0][0], last = segment.at(-1)![0];
      observationTimes.add(first); observationTimes.add(last);
      for (let t = first + 0.5; t < last; t += 0.5) observationTimes.add(t);
    }
    for (const t of [...observationTimes].sort((a, b) => a - b)) {
      const ps = names.map((n) => poseAt(r.trails[n], t, gapLimit(r)));
      for (let i = 0; i < names.length; i++) {
        for (let j = i + 1; j < names.length; j++) {
          const a = ps[i], b = ps[j];
          if (!a || !b) continue;
          const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
          if (!separation || d < separation.d) separation = { d, t, a: names[i], b: names[j], xy: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
        }
      }
    }
  }

  // stalls: ≥ 20 s with < 5 cm progress, away from task stops and the depot, before the vehicle finished
  const stalls: Derived['stalls'] = [];
  const stops = r.tasks.map((t) => t.stop ?? t.xy);
  for (const n of names) {
    const tr = r.trails[n];
    if (!orderedTrail(tr)) continue;
    const start = plan.get(n)?.start;
    let i = 0;
    while (i < tr.length) {
      let j = i;
      while (j + 1 < tr.length && tr[j + 1][0] - tr[j][0] <= gapLimit(r) + 1e-9 && Math.hypot(tr[j + 1][1] - tr[i][1], tr[j + 1][2] - tr[i][2]) < 0.05) j++;
      const span = tr[j][0] - tr[i][0];
      const xy: XY = [tr[i][1], tr[i][2]];
      const atStop = stops.some((s) => Math.hypot(s[0] - xy[0], s[1] - xy[1]) < 1.2) || (start && Math.hypot(start[0] - xy[0], start[1] - xy[1]) < 1.2);
      if (span >= 20 && !atStop && j < tr.length - 1) stalls.push({ vehicle: n, t0: tr[i][0], t1: tr[j][0], xy });
      i = j + 1;
    }
  }

  const anomalies: Anomaly[] = [];
  for (const n of names) {
    const indexed = replayIndex(r, n);
    if (!indexed.ordered) anomalies.push({ key: `clock-${n}`, tone: 'error', text: `${label(n)} 时间戳不递增或样本无效，无法用一个时间轴重放`, xy: null, t: null, vehicle: n });
    for (const gap of indexed.gaps) anomalies.push({ key: `gap-${n}-${gap.start}`, tone: 'warn',
      text: `${label(n)} 在 ${gap.start.toFixed(2)}–${gap.end.toFixed(2)} s 缺流；不插值、不累计跨缺口里程`, xy: null, t: gap.start, vehicle: n });
  }
  for (const t of r.tasks) {
    if (t.state !== 'reached') anomalies.push({ key: `task-${t.id}`, tone: 'error', text: `任务 ${t.label} 未到达（${label(t.vehicle)}）`, xy: t.stop ?? t.xy, t: null, vehicle: t.vehicle });
  }
  for (const v of r.vehicles) {
    if (v.state === 'failed') anomalies.push({ key: `veh-${v.name}`, tone: 'error', text: `${v.label} 受阻，未完成路线`, xy: v.pose ? [v.pose[0], v.pose[1]] : null, t: null, vehicle: v.name });
  }
  for (const s of stalls) {
    anomalies.push({ key: `stall-${s.vehicle}-${s.t0}`, tone: 'warn', text: `${label(s.vehicle)} 在 ${s.t0.toFixed(0)}–${s.t1.toFixed(0)} s 停滞 ${(s.t1 - s.t0).toFixed(0)} s`, xy: s.xy, t: s.t0, vehicle: s.vehicle });
  }
  for (const [n, c] of Object.entries(crossTrack)) {
    if (c.at && c.max > 0.3) anomalies.push({ key: `xt-${n}`, tone: 'warn', text: `${label(n)} 最大横向偏差 ${c.max.toFixed(2)} m（t=${c.at[0].toFixed(0)} s）`, xy: [c.at[1], c.at[2]], t: c.at[0], vehicle: n });
  }
  const body = simBody(r);
  const traffic = trafficStatus(r);
  if (separation && separation.d < Math.hypot(body.length_m, body.width_m)) {
    anomalies.push({ key: 'sep', tone: 'warn', text: `${label(separation.a)} 与 ${label(separation.b)} 最近相距 ${separation.d.toFixed(2)} m（t=${separation.t.toFixed(0)} s）：记录轨迹接近；${traffic.value}，以接触事件与独立检查为准`, xy: separation.xy, t: separation.t });
  }
  if (r.row_collisions != null && r.row_collisions > 0) {
    anomalies.push({ key: 'rows', tone: 'warn', text: `车辆与作物行接触消息 ${r.row_collisions} 条（去重时间戳 + 碰撞体对，非事故次数）`, xy: null, t: null });
  }
  if (r.robot_contact_events != null && r.robot_contact_events > 0) {
    anomalies.push({ key: 'robot-contacts', tone: 'error', text: `车间接触消息 ${r.robot_contact_events} 条（去重时间戳 + 碰撞体对，非事故次数）`, xy: null, t: null });
  }
  if (traffic.tone !== 'good') {
    anomalies.push({ key: 'traffic-evidence', tone: traffic.tone === 'bad' ? 'error' : 'warn', text: `${traffic.value}：${traffic.note}`, xy: null, t: null });
  }
  return { startTime, duration, crossTrack, separation, stalls, anomalies };
}

interface ReplayStore {
  sessionId: string | null;
  result: SimResult | null;
  derived: Derived | null;
  loading: boolean;
  error: string | null;
  t: number;
  playing: boolean;
  speed: number;
  show: { planned: boolean; actual: boolean; body: boolean; tasks: boolean; anomalies: boolean };
  hidden: string[];
  focus: Anomaly | null;
  load: (sessionId: string) => Promise<void>;
  clear: () => void;
  setT: (t: number) => void;
  setPlaying: (p: boolean) => void;
  setSpeed: (s: number) => void;
  toggle: (k: keyof ReplayStore['show']) => void;
  toggleVehicle: (name: string) => void;
  setFocus: (a: Anomaly | null) => void;
}

let loadController: AbortController | null = null;

export const useReplay = create<ReplayStore>((set, get) => ({
  sessionId: null,
  result: null,
  derived: null,
  loading: false,
  error: null,
  t: 0,
  playing: false,
  speed: 20,
  show: { planned: true, actual: true, body: true, tasks: true, anomalies: true },
  hidden: [],
  focus: null,
  async load(sessionId) {
    loadController?.abort();
    const controller = new AbortController();
    loadController = controller;
    set({ sessionId, loading: true, error: null, playing: false, focus: null });
    try {
      const result = await studio.fieldsimResult(sessionId, { signal: controller.signal });
      if (get().sessionId !== sessionId || controller.signal.aborted) return;
      const derived = derive(result);
      set({ result, derived, loading: false, t: derived.duration, hidden: [] });
    } catch (e) {
      if (get().sessionId !== sessionId) return;
      set({ loading: false, error: (e as Error).name === 'AbortError' ? '回放读取已取消' : (e as Error).message, result: null, derived: null });
    } finally {
      if (loadController === controller) loadController = null;
    }
  },
  clear: () => {
    loadController?.abort();
    loadController = null;
    set({ sessionId: null, result: null, derived: null, playing: false, focus: null, error: null, loading: false });
  },
  setT: (t) => set({ t }),
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),
  toggle: (k) => set((s) => ({ show: { ...s.show, [k]: !s.show[k] } })),
  toggleVehicle: (name) => set((s) => ({ hidden: s.hidden.includes(name) ? s.hidden.filter((x) => x !== name) : [...s.hidden, name] })),
  setFocus: (focus) => set({ focus }),
}));
