/** Research agent records (CONTRACT §9). Kept apart from lab/types: the agent is a client of those contracts. */

export type AgentTier = 'read' | 'compute' | 'actuate';
export type AgentState = 'idle' | 'running' | 'awaiting_approval' | 'error' | 'canceled';

export interface AgentTool {
  name: string;
  tier: AgentTier;
  label: string;
  description: string;
}

export interface AgentModel {
  provider: string;
  endpoint: string | null;
  model: string;
  temperature?: number;
}

export interface AgentStatus {
  configured: boolean;
  reason: string | null;
  model: AgentModel | null;
  prompt: { id: string; sha256: string };
  policy: Record<string, number | string>;
  tools: AgentTool[];
}

export interface Grounding {
  method: string;
  checked: number;
  grounded: number;
  skipped: number;
  not_found: string[];
  unknown_ids: string[];
  note: string;
}

interface StepBase {
  i: number;
  utc: string;
}

export type AgentStep =
  | (StepBase & { kind: 'user'; text: string })
  | (StepBase & { kind: 'assistant'; text: string; reasoning?: string | null; tool_calls: { id: string; name: string }[]; model_s?: number })
  | (StepBase & {
      kind: 'tool';
      call_id: string;
      name: string;
      tier: AgentTier | null;
      label: string | null;
      arguments: Record<string, unknown>;
      ok: boolean;
      error: string | null;
      result?: unknown;
      duration_s: number;
    })
  | (StepBase & {
      kind: 'approval';
      call_id: string;
      name: string;
      label: string;
      arguments: Record<string, unknown>;
      decision: 'pending' | 'approved' | 'denied' | 'canceled';
      note?: string | null;
      decided_utc?: string;
    })
  | (StepBase & { kind: 'final'; text: string; reasoning?: string | null; grounding: Grounding; model_s?: number })
  | (StepBase & { kind: 'error' | 'notice'; message: string });

export interface AgentEvidence {
  scenarios: string[];
  runs: string[];
  jobs: string[];
  studies: string[];
  field_sessions: string[];
}

export interface AgentUsage {
  model_calls: number;
  tool_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface AgentSession {
  session_id: string;
  title: string;
  state: AgentState;
  created_utc: string;
  updated_utc: string;
  model: AgentModel | null;
  prompt: { id: string; sha256: string };
  steps: AgentStep[];
  pending: { call_id: string; tool: string; label: string; arguments: Record<string, unknown>; requested_utc: string } | null;
  grounding: Grounding | null;
  error: string | null;
  usage: AgentUsage;
  evidence: AgentEvidence;
}

export type AgentSessionSummary = Pick<AgentSession, 'session_id' | 'title' | 'state' | 'created_utc' | 'updated_utc' | 'usage' | 'error'>;
