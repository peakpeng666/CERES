import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const state = vi.hoisted(() => ({ studyId: null as string | null, failedKey: 'studies-a' }));
vi.mock('../../lab/api', async (load) => ({
  ...await load<typeof import('../../lab/api')>(),
  useLabResource: (key: string) => ({ data: null, error: key === state.failedKey ? new Error('gateway unavailable') : null, loading: false, reload: vi.fn() }),
}));
vi.mock('../store', () => ({ useStudio: (select: (s: unknown) => unknown) => select({ studyId: state.studyId, setStudy: vi.fn(), algorithms: [] }) }));
import { AnalysisPanel, StudiesPanel } from './ResearchPanels';
afterEach(() => { state.studyId = null; state.failedKey = 'studies-a'; });
describe('study failure rendering', () => {
  it('shows a failed study list before a study is selected', () => {
    expect(renderToStaticMarkup(createElement(AnalysisPanel))).toContain('gateway unavailable');
  });
  it('shows failed studies instead of presenting an empty list', () => {
    state.failedKey = 'studies';
    const html = renderToStaticMarkup(createElement(StudiesPanel));
    expect(html).toContain('gateway unavailable');
    expect(html).not.toContain('还没有课题。');
  });
  it('shows a failed selected study instead of an endless spinner', () => {
    state.studyId = 'chosen'; state.failedKey = 'study-chosen';
    const html = renderToStaticMarkup(createElement(StudiesPanel));
    expect(html).toContain('gateway unavailable'); expect(html).toContain('重试');
  });
});


describe('seed-only study design copy', () => {
  it('does not equate lack of seed control with deterministic execution', () => {
    const html = renderToStaticMarkup(createElement(StudiesPanel));
    expect(html).toContain('可控随机种子数');
    expect(html).toContain('不使用 seed 的算法每个场景/预算只运行一次');
    expect(html).toContain('普通重复');
    expect(html).not.toContain('确定性算法只跑一次');
  });
});
