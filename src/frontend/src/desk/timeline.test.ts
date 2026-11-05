import { describe, expect, it } from 'vitest';
import type { AgentMessage, ToolCall } from '../agent/api';
import { activityTitle, approvalVerdict, buildTimeline, planSummary } from './timeline';

const msg = (id: string, utc: string, role: AgentMessage['role'] = 'assistant', kind: AgentMessage['kind'] = 'text', text = id): AgentMessage => ({ id, role, kind, text, utc });
const call = (id: string, utc: string, name: string, extra: Partial<ToolCall> = {}): ToolCall => ({ id, name, arguments: {}, status: 'ok', actor: 'llm', step: null, started_utc: utc, duration_s: 0.1, ...extra });

describe('buildTimeline', () => {
  it('folds calls between messages and keeps the request first', () => {
    const t = buildTimeline({
      messages: [msg('m1', '2026-11-05T00:00:00.000Z', 'user'), msg('m2', '2026-11-05T00:00:05.000Z')],
      tool_calls: [call('c1', '2026-11-05T00:00:00.000Z', 'rule_parse'), call('c2', '2026-11-05T00:00:03.000Z', 'update_task_contract'), call('c3', '2026-11-05T00:00:09.000Z', 'solve')],
    });
    expect(t.map((x) => x.kind)).toEqual(['message', 'activity', 'message', 'activity']);
    expect(t[1].kind === 'activity' && t[1].calls.map((c) => c.id)).toEqual(['c1', 'c2']);
  });
});

describe('activityTitle', () => {
  it('counts repeats and leaves refused calls out', () => {
    const title = activityTitle([
      call('c1', '2026-11-05T00:00:00Z', 'confirm_task_contract', { status: 'refused' }),
      call('c2', '2026-11-05T00:00:01Z', 'solve', { arguments: { algorithms: ['a', 'b', 'c'] } }),
      call('c3', '2026-11-05T00:00:02Z', 'verify_run'), call('c4', '2026-11-05T00:00:03Z', 'verify_run'),
    ]);
    expect(title).toBe('求解 3 个算法，复核 2 个方案');
  });
  it('tells an updated map from a new one', () => {
    expect(activityTitle([call('c1', '2026-11-05T00:00:00Z', 'build_scenario', { result: { mode: 'updated' } })])).toBe('更新任务地图');
  });
});

describe('planSummary', () => {
  const step = (kind: string, status: string) => ({ id: kind, kind, title: kind, status, detail: '' }) as never;
  it('points at the waiting step', () => {
    const p = planSummary({ state: 'awaiting_approval', plan: [step('solve', 'done'), step('repair', 'pending'), step('simulate', 'waiting'), step('report', 'pending')] });
    expect(p.steps).toHaveLength(3);
    expect(p.current?.kind).toBe('simulate');
    expect(p.waiting).toBe(true);
  });
  it('treats a finished session as finished even with a stale waiting step', () => {
    const p = planSummary({ state: 'done', plan: [step('solve', 'done'), step('repair', 'waiting'), step('report', 'done')] });
    expect(p.current).toBeNull();
    expect(p.done).toBe(3);
  });
});

describe('approvalVerdict', () => {
  it('reads the decision from the following notice', () => {
    expect(approvalVerdict(msg('m', '2026-11-05T00:00:00Z', 'event', 'notice', '已批准修改任务：车辆'))).toBe('已批准');
    expect(approvalVerdict(msg('m', '2026-11-05T00:00:00Z', 'event', 'notice', '已拒绝：start_simulation'))).toBe('已拒绝');
    expect(approvalVerdict(msg('m', '2026-11-05T00:00:00Z'))).toBeNull();
  });
});
