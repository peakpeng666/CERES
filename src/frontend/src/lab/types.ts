/**
 * Types for the workbench gateway (/lab/api). Mirrors src/lab/CONTRACT.md; change
 * both together. Values the gateway cannot know are null, never invented.
 */

export type ProblemClass = 'turn_routing' | 'euclidean_cvrp' | 'coverage' | 'point_to_point' | 'fleet_routing';
export type Source = 'project' | 'third_party' | 'panel_baseline' | 'nav2';
export type XY = [number, number];

export interface MapMeta {
  world: string;
  resolution: number;
  origin: [number, number, number];
  width: number;
  height: number;
  image: string;
  yaml?: string;
}

export type Frame =
  | { kind: 'map'; units: 'm'; world: string; map: MapMeta & { yaml: string } }
  | { kind: 'benchmark'; units: 'unitless' }
  | { kind: 'field'; units: 'm'; crs?: string; offset?: [number, number] };

export interface SimSupport {
  supported: boolean;
  world: string | null;
  spawn: { x: number; y: number; yaw: number } | null;
  reason: string | null;
}

export interface ScenarioSummary {
  id: string;
  title: string;
  summary: string;
  problem_class: ProblemClass;
  frame: Frame;
  cost_unit: string;
  sim: SimSupport;
  task_count: number;
  node_count: number | null;
}

export interface TurnRoutingInstance {
  depot: number;
  capacity: number;
  customers: { id: number; node: number; demand: number; label?: string }[];
  arcs: [number, number, number][];
  forbidden_turns: [number, number, number][];
  turn_penalties: [number, number, number, number][];
  coordinates: Record<string, XY>;
  node_kinds?: Record<string, 'depot' | 'headland' | 'row' | 'grid' | string>;
  units: string;
  service_heading_reset?: boolean;
  depot_reset?: string;
  fleet_limit?: number | null;
}

export interface EuclideanCvrpInstance {
  coordinates: XY[];
  demands: number[];
  capacity: number;
  depot: 0;
  fleet: string;
  objective: string;
  labels?: string[];
}

export interface CoverageInstance {
  field: XY[];
  obstacles?: XY[][];
  headland_width: number;
  swath_width: number;
  turn_radius: number;
  robot_width: number;
  start: XY;
  route_pattern: 'boustrophedon' | 'snake' | 'spiral';
  angle_deg: number | null;
}

export type Instance = TurnRoutingInstance | EuclideanCvrpInstance | CoverageInstance | Record<string, unknown>;

export interface Scenario extends ScenarioSummary {
  revision?: number;
  provenance: { generator: string; params?: Record<string, unknown>; seed?: number | null; note?: string };
  instance: Instance;
  editable: { tasks?: boolean; depot?: boolean; capacity?: boolean; demands?: boolean };
  layers?: {
    row_polygons?: XY[][];
    headland?: XY[][];
    obstacles?: XY[][];
    field_polygon?: XY[];
    labels?: { xy: XY; text: string }[];
    [key: string]: unknown;
  };
}

export interface AlgorithmParam {
  name: string;
  type: 'number' | 'integer' | 'boolean' | 'string' | 'select';
  default: number | boolean | string | null;
  min?: number;
  max?: number;
  options?: { value: string; label: string }[];
  label: string;
}

export interface Algorithm {
  randomness?: { uses_seed: boolean; note: string };
  id: string;
  name: string;
  problem_classes: ProblemClass[];
  family: 'exact' | 'metaheuristic' | 'heuristic' | 'baseline' | 'coverage' | 'relaxation' | 'graph_search' | 'sampling';
  source: Source;
  origin: string;
  version: string | null;
  turn_aware: boolean;
  guarantee: 'optimal' | 'optimal_for_relaxation' | 'none' | 'resolution_optimal' | 'asymptotically_optimal' | 'probabilistically_complete';
  limits: { max_tasks?: number | null };
  available: boolean;
  unavailable_reason: string | null;
  params: AlgorithmParam[];
  description: string;
  summary?: string;
  library?: string;
  refs?: string[];
  problem_label?: string;
}

export interface AlgorithmStat {
  runs: number; completed: number; valid: number; failed: number; canceled: number;
  wall_s: { median: number | null; p90: number | null };
  last_utc: string | null;
  head_to_head: { instances: number; wins: number; mean_gap: number } | null;
}

export interface AlgorithmHistory extends AlgorithmStat {
  algorithm_id: string;
  physical: { runs: number; passed: number; distance_ratio: number | null; cross_track_max_m: number | null } | null;
  maps: { scenario_id: string; title: string | null; runs: number; best: number | null; unit: string | null; last_utc: string }[];
  recent: { run_id: string; scenario_id: string; title: string | null; state: string; status: string | null; value: number | null; unit: string | null;
    wall_s: number | null; created_utc: string; budget_s: number | null; valid: boolean | null }[];
}

export interface DataPack {
  id: string; name: string; summary: string; bytes: number; unpacked_bytes: number; version: number;
  state: 'absent' | 'downloading' | 'installing' | 'installed' | 'failed';
  progress: number | null; error: string | null;
}

export interface EngineGap {
  run_id: string;
  a: { engine: string; session_id?: string; state?: string };
  b: { engine: string; version?: string; state?: string };
  path_mean_m: number | null; path_max_m: number | null; time_rmse_m: number | null;
  vehicles: { name: string; label: string; path: { mean_m: number; p95_m: number; max_m: number }; at_time: { rmse_m: number | null; max_m: number | null };
    task_arrival_s: { tasks: number; mean_abs: number | null; max_abs: number | null }; duration_s: { a: number; b: number } }[];
  metrics: Record<string, { a?: number | null; b?: number | null }>;
  scope: string;
}

export interface PhysicsRun {
  physics_run_id: string; run_id: string | null; session_id: string | null; scenario_id?: string; algorithm?: string;
  engine: string; engine_version?: string; state: string; created_utc: string; finished_utc: string | null;
  progress?: { sim_time?: number; tasks_done?: number; tasks_total?: number } | null;
  errors: string[]; sim_time?: number; wall_s?: number; real_time_factor?: number; servo_kv?: number; model_sha256?: string;
  metrics?: Record<string, number | null>;
  vehicles?: { name: string; label: string; state: string; distance_m: number; tasks_done: number; tasks_total: number; cross_track_max_m: number | null; duration_s: number | null }[];
  contacts?: { t_sim: number; between: string[] }[];
  check?: { valid?: boolean; errors?: string[]; scope?: string };
  comparison?: EngineGap;
  trails?: Record<string, number[][]>;
  traffic_mode?: 'serial' | 'coordinated';
  traffic?: { policy?: string | null; holds?: number; held_s?: number | null; predicted_makespan_s?: number | null; meetings?: number | null; min_gap_m?: number | null };
}

export interface Recommendation {
  problem_class: ProblemClass;
  policy: string;
  features: { name: string; key: string; value: number | string | null; unit: string | null }[];
  learned_selector: {
    id: string;
    status: 'unavailable' | 'available' | 'not_applicable' | 'out_of_scope' | 'out_of_profile';
    evaluation?: {test_n:number;train_n:number;calibration_n:number;alternative_count:number;harmful_instances:number;mean_improvement_pct:number;sbs:string};
    model_sha256?: string;
    scope?: string;
    reason: string | null;
    required: string[];
    prediction: {
      kind?: string;
      order?: { config: string; label?: string; algorithm_id: string; expected_ratio?: number }[];
      [k: string]: unknown;
    } | null;
  };
  ranking: {
    algorithm_id: string;
    rank: number;
    applicable: boolean;
    suitability: '推荐' | '可用' | '不适用' | string;
    reasons: string[];
    suggested_params: Record<string, number | string | boolean>;
  }[];
  recommended: string | null;
  explanation: string;
}

export type RunState = 'queued' | 'running' | 'done' | 'error' | 'timeout' | 'canceled';
export type SolveStatus = 'optimal' | 'feasible' | 'infeasible' | 'no_solution' | null;

export interface Route {
  vehicle: number;
  nodes: number[] | null;
  sequence: number[];
  services: { task: number; position: number }[];
  polyline: XY[];
  cost: number | null;
  load: number | null;
  mode_chain?: unknown[] | null;
}

export interface CheckResult {
  checker: string;
  source: Source;
  valid: boolean;
  errors: string[];
  computed_cost: number | null;
  details?: unknown;
}

export interface MapCheck {
  checked: boolean;
  collision_free: boolean | null;
  min_clearance_m: number | null;
  robot_radius_m: number;
  violations: { route: number; segment: number; point: XY; clearance_m: number }[];
  margin_m?: number;
  violation_count?: number;
  method?: string;
}

export interface Solution {
  process?: { returncode?: number; budget_s?: number; evidence?: string; source_identity_verified?: boolean };
  planning?: PlanningContext;
  trace_contract?: { schema?: string; semantic: 'reported_objective' | 'solver_search_proxy'; note: string; independent_check_at_each_sample?: boolean };
  raw_result?: { model_contract?: { schema?: string; [key: string]: unknown }; [key: string]: unknown };
  instance?: Instance;
  scenario_snapshot?: Scenario;
  run_id: string;
  job_id: string;
  scenario_id: string;
  problem_class: ProblemClass;
  instance_sha256: string;
  frame: Frame;
  cost_unit: string;
  algorithm: { id: string; name: string; source: Source; origin: string; version: string | null; randomness?: { uses_seed: boolean; note: string } | null };
  params: Record<string, unknown>;
  state: RunState;
  status: SolveStatus;
  objective: { value: number | null; unit: string; lower_bound: number | null };
  routes: Route[];
  check: CheckResult | null;
  map_check: MapCheck | null;
  metrics: {
    cost: number | null;
    length: number | null;
    length_unit: 'm' | 'unitless';
    n_routes: number | null;
    n_tasks: number | null;
    turns: number | null;
    turn_penalty_total: number | null;
    forbidden_turn_violations: number | null;
    wall_s: number | null;
    cpu_s: number | null;
    [key: string]: number | string | null;
  };
  log: string | null;
  error: string | null;
  created_utc: string;
  finished_utc: string | null;
  label: string | null;
}

export interface PlanningContext {
  schema: 'urp.planning/1';
  service_kind: 'point_visit' | 'area_coverage' | 'navigation';
  comparison_key: string;
  environment_sha256?: string | null;
  validation_policy?: string;
  instance_sha256: string;
  objective_rule: string;
  objective_label: string;
  objective_unit: string;
  relaxed_constraints: string[];
  axes: string;
  angle_unit: 'rad';
  metric_scope: string;
}

export type ServiceState =
  | 'running'
  | 'stopped'
  | 'starting'
  | 'stopping'
  | 'ready'
  | 'degraded'
  | 'failed'
  | 'unavailable';

export interface Service {
  id: 'gateway' | 'archive' | 'solver' | 'ros' | 'sim' | 'rviz' | 'gazebo_gui' | 'fields2cover' | string;
  name: string;
  kind: 'process' | 'check';
  state: ServiceState;
  detail: string;
  pid: number | null;
  port: number | null;
  url: string | null;
  started_utc: string | null;
  controls: ('start' | 'stop' | 'restart')[];
  log: string | null;
  facts: { label: string; value: string }[];
  known_issues: string[];
}

export type SimPhase =
  | 'stopped'
  | 'starting'
  | 'undocking'
  | 'localizing'
  | 'nav2_starting'
  | 'ready'
  | 'executing'
  | 'stopping'
  | 'failed';

export type SimRunState = 'queued' | 'approach' | 'executing' | 'succeeded' | 'failed' | 'canceled';
export type SimMode = 'follow_path' | 'nav2_waypoints';

export interface LocalizationAnalysis {
  mode: 'ground_truth' | 'amcl';
  scope?: string;
  error?: string;
  estimate_error?: string | null | {
    paired_samples?: number;
    position_rmse_m?: number;
    position_mean_m?: number;
    position_p50_m?: number;
    position_p95_m?: number;
    position_max_m?: number;
    position_final_m?: number;
    yaw_rmse_rad?: number;
  };
  replans?: {
    count: number;
    first_t_sim: number | null;
    last_t_sim: number | null;
    path_length_first_m: number | null;
    path_length_last_m: number | null;
    note?: string;
  };
}

export interface SimRun {
  localization?: 'ground_truth' | 'amcl' | LocalizationAnalysis;
  progress?: {leg:number;legs_total:number};
  sim_run_id: string;
  run_id: string;
  session_id: string;
  world: string;
  mode: SimMode;
  state: SimRunState;
  sources: { route_plan: string; approach: string; tracking: string };
  planned: { path: XY[]; length_m: number | null };
  tasks: {
    task: number;
    label: string;
    xy: XY;
    state: 'pending' | 'reached' | 'missed';
    t_sim: number | null;
    distance_m: number | null;
  }[];
  actual: { ground_truth: [number, number, number, number][]; amcl: [number, number, number, number][] };
  nav2: { action: string; status: number | null; status_text: string; t_sim: number | null }[];
  metrics: {
    duration_sim_s: number | null;
    duration_wall_s: number | null;
    actual_length_m: number | null;
    cross_track_mean_m: number | null;
    cross_track_max_m: number | null;
    cross_track_p95_m: number | null;
    final_error_m: number | null;
    goal_tolerance_m?: number;
    independent_goal_error_m?: number | null;
    goal_reached_within_tolerance?: boolean;
    tasks_reached: number | null;
    tasks_total: number | null;
    actual_working_coverage_fraction?: number;
    actual_field_coverage_fraction?: number;
    actual_footprint_in_field?: boolean;
    actual_map_check?: MapCheck;
    actual_coverage_basis?: string;
    actual_coverage_kind?: string;
    operation_state_observed?: boolean;
  };
  error: string | null;
  report: string | null;
  created_utc: string;
  finished_utc: string | null;
}

export interface SimState {
  localization?: 'ground_truth' | 'amcl';
  telemetry?: { counts: Record<string, number>; age_s: Record<string, number>; map_base_tf_age_s: number | null };
  session_id: string | null;
  phase: SimPhase;
  world: string | null;
  spawn: { x: number; y: number; yaw: number } | null;
  components: Record<string, string>;
  robot: {
    t_sim: number | null;
    ground_truth: [number, number, number] | null;
    amcl: [number, number, number] | null;
    speed: number | null;
  } | null;
  execution: SimRun | null;
  errors: string[];
  warnings: string[];
  stages: { name: string; wall_s: number }[];
  known_issues: string[];
}

export interface ApiError {
  error: string;
  message: string;
}
