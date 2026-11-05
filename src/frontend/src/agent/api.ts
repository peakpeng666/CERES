import { call } from '../studio/api';

export type AgentState =
  | 'idle'
  | 'running'
  | 'awaiting_user'
  | 'awaiting_confirmation'
  | 'awaiting_approval'
  | 'paused'
  | 'cancelled'
  | 'failed'
  | 'done'
  | 'interrupted';
export type AgentMode = 'hybrid' | 'llm' | 'rule';
export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'waiting' | 'skipped' | 'taken_over' | 'cancelled';
export type FieldSource = 'user_text' | 'llm' | 'user_edit' | 'default' | 'repair';

export interface ContractField {
  value: unknown;
  source: FieldSource;
  evidence: string;
}

export interface ContractCard {
  key: string;
  label: string;
  value: unknown;
  text: string;
  source: FieldSource;
  evidence: string;
}

export interface TaskContract {
  schema: string;
  status: 'draft' | 'confirmed';
  revision: number;
  confirmed_revision: number | null;
  request_text: string;
  fields: Record<string, ContractField>;
  unsupported: string[];
  history: { revision: number; source: FieldSource; note: string; changes: { key: string; old: unknown; new: unknown }[] }[];
}

export interface PlanStep {
  id: string;
  kind: string;
  title: string;
  status: StepStatus;
  detail: string;
  updated_utc?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  label?: string;
  arguments: Record<string, unknown>;
  status: 'ok' | 'error' | 'refused';
  actor: 'llm' | 'rule' | 'system' | 'user' | 'user_approved';
  step: string | null;
  started_utc: string;
  duration_s: number;
  result?: Record<string, unknown>;
  error?: { code: string; message: string };
}

export interface LlmCall {
  phase: string;
  utc: string;
  provider: string;
  model: string;
  latency_s?: number;
  attempts?: number;
  usage: { input_tokens?: number | null; output_tokens?: number | null; total_tokens?: number };
  cost?: number | null;
  currency?: string | null;
  tool_calls?: string[];
  error?: string;
}

export interface Question {
  question: string;
  field?: string;
  options: string[];
}

export interface Pending {
  type: 'questions' | 'approval';
  kind?: 'contract_change' | 'tool_call';
  questions?: Question[];
  changes?: { key: string; old: unknown; new: unknown }[];
  call?: { name: string; arguments: Record<string, unknown> };
  label?: string;
  reason?: string;
}

export interface Evidence {
  contract: Record<string, string>;
  scenario_id: string | null;
  runs: {
    run_id: string;
    algorithm: string;
    state: string;
    status: string | null;
    independent_recheck: boolean | null;
    total_distance_m: number | null;
    makespan_s: number | null;
    vehicles_used: number | null;
    trips: number | null;
    wall_s: number | null;
    diagnosis_label: string;
  }[];
  best_verified: { run_id: string; algorithm: string; algorithm_id: string; total_distance_m: number; makespan_s: number; objective: number; unit: string } | null;
  best_executable?: { run_id: string; algorithm: string; algorithm_id: string; total_distance_m: number; makespan_s: number; objective: number; unit: string } | null;
  unsupported: string[];
  simulation_started: boolean;
  precheck: string[];
}

export interface Report {
  evidence: Evidence;
  narrative: string;
  source: 'llm' | 'template';
  unverified_numbers: string[];
  utc: string;
}

export interface AgentMessage {
  id: string;
  role: 'user' | 'assistant' | 'event';
  kind: 'text' | 'notice' | 'error' | 'questions' | 'confirmation' | 'approval' | 'report' | 'simulation' | 'physics';
  text: string;
  utc: string;
  data?: Record<string, unknown>;
}

export interface ProviderInfo {
  id: string;
  kind: string;
  model: string;
  label: string;
  local?: boolean;
}

export interface AgentSession {
  session_id: string;
  title: string;
  state: AgentState;
  phase: 'understand' | 'confirm' | 'execute' | 'report';
  mode: AgentMode;
  provider: ProviderInfo | null;
  auto_confirm: boolean;
  created_utc: string;
  updated_utc: string;
  messages: AgentMessage[];
  contract: TaskContract;
  contract_cards: ContractCard[];
  missing: { key: string; label: string }[];
  plan: PlanStep[];
  tool_calls: ToolCall[];
  llm_calls: LlmCall[];
  usage: { input_tokens: number; output_tokens: number; cost: number | null; currency: string | null; calls: number; latency_s: number; unpriced_calls?: number };
  scenario_id: string | null;
  run_ids: string[];
  active_run_ids?: string[];
  built_revision?: number;
  scenario_instance_sha256?: string;
  verifications: Record<string, { valid: boolean | null; agrees: boolean | null; category: string }>;
  pending: Pending | null;
  error: { kind: string; message: string; where: string } | null;
  report: Report | null;
  rule_progress?: { batch?: string[] };
  short_title?: string;
  custom_title?: string | null;
  pinned?: boolean;
  archived?: boolean;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  state: AgentState;
  phase: string;
  mode: AgentMode;
  created_utc: string;
  updated_utc: string;
  scenario_id: string | null;
  runs: number;
  provider: string | null;
  short_title?: string;
  custom_title?: string | null;
  pinned?: boolean;
  archived?: boolean;
  usage?: { input_tokens: number; output_tokens: number; calls: number; latency_s: number };
}

export interface ProviderStatus {
  id: string;
  kind: string;
  model: string;
  label: string;
  base_url: string;
  enabled: boolean;
  has_credential: boolean;
  local: boolean;
  terms_note: string | null;
  price: { input: number; output: number; currency: string } | null;
  status: { reachable: boolean | null; model_present: boolean | null; detail: string };
}

export interface ProvidersInfo {
  providers: ProviderStatus[];
  default: string | null;
  config: string;
  prompt_version: string;
}

export interface ToolInfo {
  name: string;
  label: string;
  description: string;
  effect: string;
  step: string | null;
  needs_scenario: boolean;
  needs_confirmed: boolean;
  phases: string[];
  needs_approval: boolean;
}

export interface HarnessInfo {
  tools: ToolInfo[];
  limits: { model_calls_per_turn: number; tool_calls_per_turn: number };
  prompt_version: string;
  runner_version: string;
}

export interface EvalRun {
  id: string;
  kind: 'execution' | 'understanding';
  mode: string | null;
  provider: string | null;
  split?: string | null;
  prompt_version?: string | null;
  runner_version?: string | null;
  finished_utc?: string | null;
  passed?: number | null;
  total?: number | null;
  metrics?: Record<string, number | null>;
  cases?: { id: string; passed: boolean | null; state?: string | null; note?: string | null }[];
}

export type ControlAction = 'pause' | 'resume' | 'cancel' | 'retry' | 'confirm' | 'approve' | 'reject' | 'takeover' | 'simulate';

export interface Capability { id: string; kind: string; name: string; status: 'ready' | 'unavailable'; reason?: string | null; summary?: string | null; detail?: string | null; version?: string | null; href?: string; approval?: boolean; problem_class?: string; library?: string }
export interface Capabilities { algorithms: Capability[]; simulators: Capability[]; learners: Capability[]; transfers: Capability[] }
export interface BackendJob { id: string; kind: 'solve' | 'study' | 'physics' | 'fieldsim' | 'tb4'; label: string; state: string; progress: number | null; detail?: string | null; started_utc: string | null; href: string }

export const agentApi = {
  providers: () => call<ProvidersInfo>('GET', '/agent/providers', undefined, { timeoutMs: 15_000 }),
  sessions: () => call<{ sessions: SessionSummary[] }>('GET', '/agent/sessions'),
  session: (id: string, signal?: AbortSignal) => call<AgentSession>('GET', `/agent/sessions/${encodeURIComponent(id)}`, undefined, { signal }),
  create: (body: { text?: string; mode?: 'auto' | AgentMode; provider_id?: string; auto_confirm?: boolean; scenario_id?: string }) =>
    call<AgentSession>('POST', '/agent/sessions', body),
  patch: (id: string, body: { title?: string | null; pinned?: boolean; archived?: boolean }) =>
    call<SessionSummary>('PATCH', `/agent/sessions/${encodeURIComponent(id)}`, body),
  harness: () => call<HarnessInfo>('GET', '/agent/harness'),
  capabilities: () => call<Capabilities>('GET', '/agent/capabilities', undefined, { timeoutMs: 40_000 }),
  jobs: () => call<{ jobs: BackendJob[] }>('GET', '/agent/jobs'),
  evals: () => call<{ runs: EvalRun[] }>('GET', '/agent/evals', undefined, { timeoutMs: 20_000 }),
  localModel: (action: 'start' | 'stop') => call<{ ok: boolean; detail: string }>('POST', `/agent/providers/local/${action}`, {}, { timeoutMs: 30_000 }),
  message: (id: string, text: string) => call<AgentSession>('POST', `/agent/sessions/${encodeURIComponent(id)}/messages`, { text }),
  control: (id: string, action: ControlAction, payload: Record<string, unknown> = {}) =>
    call<AgentSession>('POST', `/agent/sessions/${encodeURIComponent(id)}/control`, { ...payload, action }),
  editContract: (id: string, fields: Record<string, unknown>) =>
    call<AgentSession>('PATCH', `/agent/sessions/${encodeURIComponent(id)}/contract`, { fields }),
};

export const STATE_LABEL: Record<AgentState, string> = {
  idle: '待输入',
  running: '执行中',
  awaiting_user: '等待补充信息',
  awaiting_confirmation: '等待确认任务',
  awaiting_approval: '等待批准',
  paused: '已暂停',
  cancelled: '已取消',
  failed: '失败',
  done: '已完成',
  interrupted: '已中断',
};

export const SOURCE_LABEL: Record<FieldSource, string> = {
  user_text: '原话',
  llm: '模型理解',
  user_edit: '人工修改',
  default: '默认值',
  repair: '批准的修复',
};

export const ACTOR_LABEL: Record<ToolCall['actor'], string> = {
  llm: '模型',
  rule: '规则',
  system: '系统校验',
  user: '用户',
  user_approved: '用户批准',
};
