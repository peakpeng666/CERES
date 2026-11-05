import type { Route, Scenario, Solution, XY } from '../lab/types';

export type ObstacleKind = 'pond' | 'building' | 'greenhouse' | 'tree' | 'ditch' | 'rock' | 'other';

export interface FleetTask {
  id: number;
  xy: XY;
  demand: number;
  label: string;
  plot?: string | null;
  service_s?: number;
}

export interface FleetObstacle {
  id: string;
  kind: ObstacleKind;
  name?: string;
  polygon: XY[];
}

export interface FleetPlot {
  id: string;
  name: string;
  crop?: string;
  rows_deg?: number;
  polygon: XY[];
}

export interface FleetAssetRef {
  id: string;
  version: number;
  sha256: string;
  name?: string;
}

export interface FleetClass {
  name?: string;
  count: number;
  capacity: number;
  speed_mps?: number;
  robot_radius?: number;
  payload: 'load' | 'none';
  min_turn_radius_m?: number;
  asset?: FleetAssetRef;
  footprint?: { type: 'polygon'; points: XY[] };
}

export interface FleetVehicles {
  count?: number;
  capacity?: number;
  speed_mps?: number;
  trips?: 'single' | 'multi';
  /** rows: along the lanes between crop rows and the headland / farm roads; free: anywhere clear of obstacles. */
  travel?: 'rows' | 'free';
  names?: string[];
  fleet?: FleetClass[];
}

export interface FleetCorridor {
  id: string;
  name?: string;
  polygon: XY[];
}

export interface FleetInstance {
  boundary: XY[];
  plots: FleetPlot[];
  obstacles: FleetObstacle[];
  depot: XY;
  depots?: XY[];
  unload_points?: XY[];
  corridors?: FleetCorridor[];
  tasks: FleetTask[];
  vehicles: FleetVehicles;
  objective: 'total_distance' | 'makespan';
  robot_radius: number;
  units: 'm';
  meta?: { task_word?: string; margin_m?: number; road_m?: number };
}

/** Crop-row facts of one plot, as the planner lays the rows out. */
export interface LanePlot {
  id: string;
  crop: string;
  spacing_m: number;
  row_width_m: number;
  /** every: a lane between every two rows; track: a driving track left every few rows */
  mode: 'every' | 'track';
  lane_width_m: number;
  pitch_m: number;
  rows: number;
  lanes: number;
}

export interface LaneInfo {
  lanes: XY[][];
  lane_count: number;
  closed_lanes: number;
  plots: LanePlot[];
}

export interface FleetRoute extends Route {
  vehicle_name: string;
  color: string;
  trips: number[][];
  duration_s: number;
  speed_mps?: number;
  fleet_class?: string | number;
  depot?: number;
  depot_xy?: XY;
  reload_stops?: XY[];
  min_turn_radius_m?: number;
}

export interface PerVehicle {
  vehicle: string;
  color: string;
  tasks: number;
  trips: number;
  load: number;
  length_m: number;
  duration_s: number;
  fleet_class?: string | number;
  depot?: number;
}

export interface ComposeCard {
  key: string;
  label: string;
  value: string;
}

export interface ParseResult {
  spec: Record<string, unknown>;
  found: { key: string; label: string; value: unknown; text: string }[];
  defaults: { key: string; label: string; value: unknown }[];
  cards: ComposeCard[];
}

export interface FleetScenario extends Omit<Scenario, 'instance'> {
  instance: FleetInstance;
  origin?: string;
  compose?: { text: string; cards: ComposeCard[]; found: ParseResult['found']; defaults: ParseResult['defaults']; seed: number };
}

export type Selection =
  | { type: 'task'; id: number }
  | { type: 'obstacle'; id: string }
  | { type: 'plot'; id: string }
  | { type: 'corridor'; id: string }
  | { type: 'depot'; index?: number }
  | { type: 'unload'; index: number }
  | { type: 'boundary' }
  | null;

export type Tool = 'select' | 'box' | 'pan' | 'task' | 'obstacle' | 'plot' | 'depot' | 'unload' | 'corridor';

export interface Reference {
  key: string;
  type: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  url: string | null;
  tags: string[];
}

export interface CvrplibSet {
  id: string;
  problem: string;
  citation: string;
  year: number;
  link: string | null;
  ref: string | null;
  count: number;
  note: string;
  cite: string;
  supported: boolean;
}

export interface CvrplibInstance {
  name: string;
  set: string;
  problem: string;
  n: number | null;
  k: number | null;
  q: number | null;
  bks: number | null;
  optimal: boolean;
  cached: boolean;
  supported: boolean;
  scenario_id: string;
}

export interface FieldEntry {
  id: string;
  country: string;
  country_name: string;
  area_ha: number;
  perimeter_m: number;
  vertices: number;
  holes: number;
  convexity: number;
  size_m: [number, number];
  scenario_id: string;
  image: boolean;
}

export interface StudySpec {
  title: string;
  note?: string;
  scenarios: string[];
  algorithms: { id: string; params?: Record<string, unknown> }[];
  budgets?: number[];
  seeds?: number[];
}

export interface Study {
  study_id: string;
  title: string;
  note: string;
  created_utc: string;
  spec: StudySpec;
  design?: { scenario_id: string; algorithm_id: string; params: Record<string, unknown>; run_id: string; state?: string; status?: string; objective?: number | null }[];
  skipped: string[];
  progress: { total: number; finished: number; counts: Record<string, number>; state: 'done' | 'running' };
}

export interface AnalysisSummary {
  algorithm: string;
  id: string;
  name: string;
  source: string;
  instances: number;
  runs: number;
  successes: number;
  success_rate: number | null;
  gap_mean: number | null;
  gap_sd: number | null;
  gap_sd_status?: 'ok' | 'insufficient_samples' | 'out_of_range';
  gap_median: number | null;
  gap_best: number | null;
  time_mean: number | null;
  time_median: number | null;
  mean_rank: number | null;
  wins: number;
}

/** Terminal run counts, not independent seeds or equally weighted instances. */
export interface GapCounts {
  terminal_runs: number;
  eligible_terminal_runs: number;
  finite_gap_runs: number;
  eligible_failed_runs: number;
  eligible_nonfinite_gap_runs: number;
  missing_reference_runs: number;
  failed_runs: number;
  failed_missing_reference_runs: number;
}

export interface Analysis {
  empty: boolean;
  n_runs: number;
  n_instances: number;
  n_complete_instances: number;
  algorithms: string[];
  summary: AnalysisSummary[];
  per_instance: { instance: string; scenario_id: string; title: string; reference: number | null; reference_kind: string; cells: Record<string, { mean: number | null; sd?: number | null; sd_status?: 'ok' | 'insufficient_samples' | 'out_of_range'; gap_mean: number | null; rank: number | null; runs: number; failures: number; time_mean: number | null }> }[];
  tests: {
    alpha: number;
    k: number;
    n: number;
    protocol?: string;
    protocol_note?: string;
    sample_unit?: 'paired_instance_block';
    mean_ranks?: Record<string, number>;
    posthoc_reason?: string;
    friedman?: { status?: 'ok' | 'not_testable'; testable?: boolean; reason?: string | null; reason_code?: string | null; method?: string; min_instances?: number; min_algorithms?: number; statistic?: number | null; p_value?: number | null; significant?: boolean | null; error?: string };
    nemenyi?: { status?: 'ok'; q_alpha: number; critical_difference: number; mean_ranks: Record<string, number> };
    holm_family_size?: number;
    wilcoxon_holm: {
      a: string; b: string; n: number; a_better: number; b_better: number; ties: number;
      n_complete_blocks?: number; excluded_nonfinite_cost_pairs?: number; excluded_nonfinite_differences?: number;
      status?: 'ok' | 'not_testable'; testable?: boolean; method?: string; reason?: string | null; reason_code?: string | null;
      statistic?: number | null; p_value?: number | null; p_holm?: number | null; significant?: boolean | null;
    }[];
  };
  performance_profile: { taus: number[]; curves: Record<string, number[]>; n_instances?: number; note?: string };
  ecdf: Record<string, number[]>;
  ecdf_metadata?: {
    schema: 'urp.gap-ecdf/1'; normalization: 'per_algorithm_eligible_terminal_runs';
    weighting: 'one_terminal_run_one_observation'; denominator_field: 'eligible_terminal_runs';
    counts: Record<string, GapCounts>; totals: GapCounts; note: string;
  };
  block_contract?: { schema: string; n_comparison_blocks: number; n_complete_blocks: number; n_declared_groups: number; alias_blocks_collapsed: number; inference_eligible: boolean; reason_code: string | null; note: string };
  anytime: { grid: number[]; median: Record<string, (number | null)[]>; excluded?: number; note?: string;
    schema?: string; conditioning?: string; observed_counts?: Record<string, number[]>;
    counts?: Record<string, { terminal_runs: number; terminal_successes: number; valid_trace_runs: number; reasons: Record<string, number>; run_ids: string[] }> };
  scatter: { algorithm: string; time: number | null; gap: number | null; instance: string; run_id: string }[];
  reference_note: string;
  comparison_note?: string;
}

export interface InstanceSpacePoint {
  x?: number; y?: number; best: string | null; scenario_id: string; title: string;
  comparison_key?: string; run_ids?: string[]; winner_status?: string; missing_algorithms?: string[];
}
export interface InstanceSpacePanel {
  panel_id: string; problem_class: string; feature_schema: string; budget_s: number | null;
  algorithms: string[]; points: InstanceSpacePoint[]; projection_status: string;
  comparison_contract: { objective_rule?: string; objective_unit?: string; [key: string]: unknown };
  features?: string[]; loadings?: number[][]; explained_variance?: number[]; method?: string; note?: string;
}
export interface InstanceSpace {
  schema?: string; panels?: InstanceSpacePanel[]; points: InstanceSpacePoint[];
  omitted_runs?: { run_id: string; reason: string }[];
  features?: string[]; loadings?: number[][]; explained_variance?: number[];
  footprints?: Record<string, XY[]>; method?: string; note?: string;
}

export type FleetSolution = Solution & { routes: FleetRoute[]; trace?: [number, number][] };

/* ------------------------------------------------------------ field sim -- */
export interface FieldVehicleState {
  name: string;
  label: string;
  color: string;
  state: 'waiting' | 'driving' | 'servicing' | 'done' | 'failed';
  pose: [number, number, number] | null;
  speed: number;
  progress: number;
  distance_m: number;
  path_length_m: number;
  tasks_done: number;
  tasks_total: number;
  cross_track_mean_m: number | null;
  cross_track_max_m: number | null;
  duration_s: number | null;
  planned_length_m: number | null;
  planned_duration_s: number | null;
  queue_wait_s?: number;
  parking_xy?: XY;
  fleet_class?: string | number;
}
export interface LiveWeb {
  ros?: { url: string; protocol: string };
  gazebo?: { url: string; key: string; world: string; bounds?: [number, number, number, number] };
}
export interface FieldSimMetrics {
  tasks_total: number;
  tasks_reached: number;
  tasks_completed?: number | null;
  tasks_verified?: number | null;
  vehicles_done: number;
  makespan_s: number | null;
  planned_makespan_s: number | null;
  total_distance_m: number;
  planned_total_distance_m: number | null;
  cross_track_mean_m: number;
  cross_track_max_m: number;
  /** non-ideal execution only: independent evaluation numbers, not gates */
  localization_position_error_mean_m?: number | null;
  localization_position_error_max_m?: number | null;
  localization_yaw_error_max_rad?: number | null;
  odom_excess_total_m?: number | null;
  odom_excess_max_m?: number | null;
  odom_divergence_max_m?: number | null;
  interventions?: number | null;
  localization_outage_total_s?: number | null;
}
/** what the controller actually relied on: ideal reference vs non-ideal experiment */
export interface FieldExecutionInfo {
  mode: 'ideal' | 'nonideal';
  ground?: string | null;
  ground_label?: string | null;
  localization?: string | null;
  localization_label?: string | null;
  seed?: number | null;
}
export interface FieldPhysics {
  schema?: string;
  body?: { size_m?: [number, number, number]; [key: string]: unknown };
  assumptions?: Record<string, string>;
  sha256?: string;
  [key: string]: unknown;
}
export interface FieldPhysicsEvidence {
  valid?: boolean;
  errors?: string[];
  scope?: string;
  [key: string]: unknown;
}
export interface FieldTraffic {
  policy?: string;
  robot_collisions?: boolean;
  scope?: string;
  [key: string]: unknown;
}
export interface FieldCheck {
  valid?: boolean;
  errors?: string[];
  traffic?: {
    valid?: boolean;
    method?: string;
    pairs?: { min_center_distance_m?: number; min_footprint_gap_m?: number; t_sim?: number; valid?: boolean }[];
  } | null;
  dynamics?: { valid?: boolean; max_truth_gap_s?: number; [key: string]: unknown };
  [key: string]: unknown;
}
export interface FieldSimState {
  phase: 'idle' | 'starting' | 'ready' | 'running' | 'finished' | 'failed' | 'stopped';
  running: boolean;
  session_id?: string;
  run_id?: string;
  scenario_id?: string;
  algorithm?: string | null;
  world?: string;
  sim_time?: number;
  real_time_factor?: number | null;
  components: Record<string, string>;
  web: LiveWeb;
  vehicles: FieldVehicleState[];
  metrics?: Partial<FieldSimMetrics>;
  errors: string[];
  warnings: string[];
  result?: string | null;
  physics?: FieldPhysics | null;
  physics_audit?: FieldPhysicsEvidence | null;
  physics_runtime?: FieldPhysicsEvidence | null;
  execution?: FieldExecutionInfo | null;
}
export interface FieldSimSummary {
  session_id: string;
  run_id: string;
  scenario_id: string;
  algorithm: string | null;
  world: string;
  finished_utc: string;
  state: 'succeeded' | 'failed' | 'canceled';
  metrics: FieldSimMetrics;
  real_time_factor: number | null;
  sim_time: number | null;
  /** rows: crop rows were solid and the plan drove along the lanes (older results: absent) */
  travel?: 'rows' | 'free';
  row_collision_shapes?: number | null;
  row_collisions?: number | null;
  robot_contact_events?: number | null;
  traffic?: FieldTraffic | null;
  check?: FieldCheck | null;
  contact_observation?: { status?: string; count_unit?: string; legacy_note?: string | null } | null;
  physics?: FieldPhysics | null;
  physics_audit?: FieldPhysicsEvidence | null;
  physics_runtime?: FieldPhysicsEvidence | null;
  execution?: FieldExecutionInfo | null;
  vehicles: Pick<FieldVehicleState, 'name' | 'label' | 'color' | 'state' | 'distance_m' | 'duration_s' | 'planned_length_m' | 'planned_duration_s' | 'tasks_done' | 'tasks_total' | 'cross_track_mean_m' | 'cross_track_max_m'>[];
}
