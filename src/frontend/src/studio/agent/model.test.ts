import { describe, expect, it } from 'vitest';
import { groundingVerdict, isBusy, resultSummary, runScenarios, segments } from './model';
import type { AgentStep, Grounding } from './types';

const grounding = (over: Partial<Grounding> = {}): Grounding => ({
  method: 'heuristic-v1', checked: 3, grounded: 3, skipped: 1, not_found: [], unknown_ids: [], note: '启发式核对', ...over,
});

const tool = (name: string, result: unknown, ok = true): Extract<AgentStep, { kind: 'tool' }> => ({
  i: 1, utc: '', kind: 'tool', call_id: 'c', name, tier: 'compute', label: null, arguments: {}, ok, error: ok ? null : '没有场景 x',
  result, duration_s: 0.1,
});

describe('agent panel helpers', () => {
  it('turns record ids into link segments and keeps the rest as text', () => {
    expect(segments('见 run_20261029T101500_ab12cd 与 study_20261029T101500_ffff。')).toEqual([
      { kind: 'text', text: '见 ' },
      { kind: 'id', text: 'run_20261029T101500_ab12cd', type: 'run' },
      { kind: 'text', text: ' 与 ' },
      { kind: 'id', text: 'study_20261029T101500_ffff', type: 'study' },
      { kind: 'text', text: '。' },
    ]);
    expect(segments('没有编号')).toEqual([{ kind: 'text', text: '没有编号' }]);
  });

  it('reports grounding issues before counts', () => {
    expect(groundingVerdict(grounding()).tone).toBe('green');
    const flagged = groundingVerdict(grounding({ grounded: 2, not_found: ['37.2%'], unknown_ids: ['run_20261029T000000_ffffff'] }));
    expect(flagged.tone).toBe('orange');
    expect(flagged.label).toBe('2 处需要核对');
    expect(flagged.detail).toContain('数字 37.2%');
    expect(groundingVerdict(grounding({ checked: 0, grounded: 0 })).tone).toBe('gray');
    expect(groundingVerdict(null).label).toBe('未核对');
  });

  it('summarises tool results in one line', () => {
    const runs = [{ state: 'done', check: { valid: true } }, { state: 'running', check: null }];
    expect(resultSummary(tool('wait_for_runs', { runs }))).toBe('1/2 个结束，1 个通过独立检查');
    expect(resultSummary(tool('solve', { run_ids: ['a', 'b'] }))).toBe('已提交 2 个运行');
    expect(resultSummary(tool('get_scenario', {}, false))).toBe('没有场景 x');
  });

  it('maps run ids to their scenarios from tool results', () => {
    const steps: AgentStep[] = [
      tool('solve', { scenario_id: 'task-a', run_ids: ['run_1', 'run_2'] }),
      tool('get_runs', { runs: [{ run_id: 'run_3', scenario_id: 'task-b' }] }),
      tool('get_runs', { runs: [{ run_id: 'run_9', scenario_id: 'task-z' }] }, false),
    ];
    expect(runScenarios(steps)).toEqual({ run_1: 'task-a', run_2: 'task-a', run_3: 'task-b' });
  });

  it('treats running and awaiting approval as busy', () => {
    expect([isBusy('running'), isBusy('awaiting_approval'), isBusy('idle'), isBusy(undefined)]).toEqual([true, true, false, false]);
  });
});
