import type { AgentMessage, AgentSession, PlanStep, ToolCall } from '../agent/api';
import { toolName } from './format';

export type TimelineItem =
  | { kind: 'message'; key: string; msg: AgentMessage; next?: AgentMessage }
  | { kind: 'activity'; key: string; calls: ToolCall[] };

/**
 * One reading order for messages and tool calls. Calls between two messages fold into one line;
 * the user's request always opens the conversation even when a parse call shares its millisecond.
 */
export function buildTimeline(session: Pick<AgentSession, 'messages' | 'tool_calls'>): TimelineItem[] {
  const merged = [
    ...session.messages.map((msg, i) => ({ at: Date.parse(msg.utc), order: 1, i, msg, call: null as ToolCall | null })),
    ...session.tool_calls.map((call, i) => ({ at: Date.parse(call.started_utc), order: 0, i, msg: null as AgentMessage | null, call })),
  ].sort((a, b) => a.at - b.at || a.order - b.order || a.i - b.i);
  const first = merged.findIndex((x) => x.msg?.role === 'user');
  if (first > 0) merged.unshift(...merged.splice(first, 1));
  const out: TimelineItem[] = [];
  let calls: ToolCall[] = [];
  const flush = () => {
    if (calls.length) out.push({ kind: 'activity', key: `a-${calls[0].id}`, calls });
    calls = [];
  };
  merged.forEach((item, index) => {
    if (item.call) {
      calls.push(item.call);
      return;
    }
    flush();
    const next = merged.slice(index + 1).find((x) => x.msg)?.msg ?? undefined;
    out.push({ kind: 'message', key: item.msg!.id, msg: item.msg!, next });
  });
  flush();
  return out;
}

/** “建立任务地图，求解 3 个算法，复核 3 个方案”: what the folded calls did, in order, repeats counted. */
export function activityTitle(calls: ToolCall[]): string {
  const parts: string[] = [];
  const seen = new Set<string>();
  for (const c of calls) {
    if (c.status !== 'ok') continue;
    const mode = (c.result as { mode?: string } | undefined)?.mode;
    const key = c.name === 'build_scenario' ? `${c.name}:${mode}` : c.name;
    if (seen.has(key)) continue;
    seen.add(key);
    const same = calls.filter((x) => x.name === c.name && x.status === 'ok').length;
    const algorithms = (c.arguments as { algorithms?: unknown[] }).algorithms?.length;
    const phrase: Record<string, string> = {
      rule_parse: '解析原文', update_task_contract: '更新任务合同', build_scenario: mode === 'updated' ? '更新任务地图' : '建立任务地图', recommend_algorithms: '推荐算法',
      solve: algorithms ? `求解 ${algorithms} 个算法` : '求解', verify_run: `复核 ${same} 个方案`, compare_runs: '对比方案', propose_task_change: '提议修改任务',
      start_simulation: '启动仿真', diagnose_run: '诊断方案',
    };
    parts.push(phrase[c.name] ?? toolName(c));
  }
  return parts.join('，') || '工具调用';
}

export function activitySeconds(calls: ToolCall[]): number {
  const start = Date.parse(calls[0].started_utc);
  const last = calls[calls.length - 1];
  return Math.max(0, (Date.parse(last.started_utc) + last.duration_s * 1000 - start) / 1000);
}

export interface PlanSummary {
  steps: PlanStep[];
  done: number;
  current: PlanStep | null;
  waiting: boolean;
}

/** Progress for the strip above the composer. A finished session shows as finished whatever a stale step says. */
export function planSummary(session: Pick<AgentSession, 'plan' | 'state'>): PlanSummary {
  const steps = session.plan.filter((p) => !(p.status === 'pending' && p.kind === 'repair'));
  const finished = session.state === 'done';
  const done = steps.filter((p) => ['done', 'skipped', 'taken_over'].includes(p.status) || (finished && p.status !== 'failed')).length;
  const current = finished ? null
    : steps.find((p) => p.status === 'running') ?? steps.find((p) => p.status === 'waiting') ?? steps.find((p) => p.status === 'pending') ?? null;
  return { steps, done, current, waiting: current?.status === 'waiting' };
}

/** Text after “approved / rejected” events is what decided an approval card. */
export function approvalVerdict(next?: AgentMessage): '已批准' | '已拒绝' | null {
  if (!next || next.kind !== 'notice') return null;
  if (next.text.startsWith('已批准')) return '已批准';
  if (next.text.startsWith('已拒绝')) return '已拒绝';
  return null;
}
