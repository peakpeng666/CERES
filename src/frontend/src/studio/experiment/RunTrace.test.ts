import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Solution } from '../../lab/types';
import { RunTrace, traceFields } from './RunTrace';

const run = (over: Record<string, unknown> = {}) => ({
  run_id: 'run-123456789', scenario_id: 'my scene', instance_sha256: 'abcdef0123456789abcdef',
  algorithm: { id: 'pyvrp.fleet', name: 'PyVRP', version: '0.9.1' }, params: { seed: 0, time_limit_s: 5, mode: 'fast' },
  process: { source_identity_verified: true }, ...over }) as unknown as Solution;

describe('run traceability', () => {
  it('shows recorded seed (including 0), hash, algorithm version, budget, params and scenario link', () => {
    const t = traceFields(run());
    expect(t).toMatchObject({ seed: '0', instanceShort: 'abcdef012345', instanceHash: 'abcdef0123456789abcdef', algorithm: 'pyvrp.fleet',
      version: '0.9.1', budget: '5 s', params: 'mode=fast', runId: 'run-123456789', scenarioHref: '#/plan/my%20scene', identity: '身份已核验' });
  });
  it('missing values read 未知, never 0', () => {
    const t = traceFields(run({ params: {}, instance_sha256: '', algorithm: { id: 'x', name: 'x', version: null }, process: undefined }));
    expect(t.seed).toBe('未知');
    expect(t.budget).toBe('未知');
    expect(t.version).toBe('未知');
    expect(t.instanceShort).toBe('未知');
    expect(t.identity).toBe('身份未核验');
    expect(Object.values(t)).not.toContain('0');
  });
  it('falls back to the process budget and labels seedless algorithms', () => {
    const t = traceFields(run({ params: {}, process: { budget_s: 12 }, algorithm: { id: 'x', name: 'x', version: '1', randomness: { uses_seed: false, note: '' } } }));
    expect(t.budget).toBe('12 s（进程预算）');
    expect(t.seed).toBe('算法不使用种子');
  });
  it('renders the link and a copy control for the full hash', () => {
    const html = renderToStaticMarkup(createElement(RunTrace, { run: run() }));
    expect(html).toContain('href="#/plan/my%20scene"');
    expect(html).toContain('title="abcdef0123456789abcdef"');
    expect(html).toContain('复制实例哈希');
  });
});
