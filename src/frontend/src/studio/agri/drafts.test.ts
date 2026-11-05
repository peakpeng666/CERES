import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AGRI_PATTERNS } from './patterns';
import { defaultValues, buildPattern } from './catalog';
import { clearDraft, debouncedDraftWriter, draftKey, DRAFT_SCHEMA, loadDraft, loadFilters, saveDraft, saveFilters } from './drafts';
import { extractPreview, previewIssues } from './preview';
import { selfIntersects, signedArea } from './geometry';
import { pointInPolygon as editorPip, distToSegment } from '../editor/geometry';

class MemStorage {
  m = new Map<string, string>(); quota = Infinity;
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { if (k !== '__urp_probe__' && v.length > this.quota) throw new DOMException('full', 'QuotaExceededError'); this.m.set(k, v); }
  removeItem(k: string) { this.m.delete(k); }
}
const p = AGRI_PATTERNS.find((x) => x.id === 'field-sampling')!;
let local: MemStorage;
beforeEach(() => { local = new MemStorage(); vi.stubGlobal('localStorage', local); vi.stubGlobal('sessionStorage', new MemStorage()); });
afterEach(() => vi.unstubAllGlobals());

describe('agri drafts', () => {
  it('round-trips values and seed, keeping blank numbers as unfilled', () => {
    const key = p.runnable!.params.find((x) => x.type === 'number')!.key;
    const v = { ...defaultValues(p), [key]: NaN };
    expect(saveDraft(p, v, '7')).toBe('');
    const got = loadDraft(p, defaultValues(p));
    expect(got.restored).toBe(true); expect(got.seed).toBe('7'); expect(Number.isNaN(got.values[key])).toBe(true);
  });
  it('drops a corrupt draft and says so', () => {
    local.setItem(draftKey(p.id), '{not json');
    const got = loadDraft(p, defaultValues(p));
    expect(got.restored).toBe(false); expect(got.notice).toMatch('损坏'); expect(local.getItem(draftKey(p.id))).toBeNull();
  });
  it('refuses an incompatible schema without deleting it', () => {
    local.setItem(draftKey(p.id), JSON.stringify({ schema: DRAFT_SCHEMA + 1, patternId: p.id, values: {} }));
    const got = loadDraft(p, defaultValues(p));
    expect(got.restored).toBe(false); expect(got.notice).toMatch('不兼容'); expect(local.getItem(draftKey(p.id))).not.toBeNull();
  });
  it('migrates a stale signature keeping only type-matching values', () => {
    const [a] = p.runnable!.params;
    local.setItem(draftKey(p.id), JSON.stringify({ schema: DRAFT_SCHEMA, patternId: p.id, signature: 'old', values: { [a.key]: 'wrong-type', ghost: 1 }, seed: '9' }));
    const got = loadDraft(p, defaultValues(p));
    expect(got.values[a.key]).toEqual(a.default); expect('ghost' in got.values).toBe(false); expect(got.notice).toMatch('参数已变更');
  });
  it('reports quota exhaustion instead of throwing', () => {
    local.quota = 10;
    expect(saveDraft(p, defaultValues(p), '1')).toMatch('已满');
  });
  it('works without any storage', () => {
    vi.stubGlobal('localStorage', undefined);
    expect(loadDraft(p, defaultValues(p)).restored).toBe(false);
    expect(saveDraft(p, defaultValues(p), '1')).toMatch('不可用');
    expect(() => clearDraft(p.id)).not.toThrow();
  });
  it('persists catalog filters per session', () => {
    saveFilters({ environment: 'orchard', operation: '', status: 'runnable' });
    expect(loadFilters()).toEqual({ environment: 'orchard', operation: '', status: 'runnable' });
  });
});

describe('agri preview', () => {
  it('extracts every runnable builder without modifying the instance', () => {
    for (const pat of AGRI_PATTERNS.filter((x) => x.runnable)) {
      const built = buildPattern(pat, defaultValues(pat), 42);
      const before = JSON.stringify(built.instance);
      const pv = extractPreview(built);
      expect(pv.boundary.length).toBeGreaterThanOrEqual(3);
      expect(previewIssues(pv).filter((i) => i.level === 'error'), pat.id).toEqual([]);
      expect(JSON.stringify(built.instance)).toBe(before);
    }
  });
  it('flags self-intersection, degenerate area and tasks inside obstacles', () => {
    const built = buildPattern(p, defaultValues(p), 42);
    const pv = extractPreview(built);
    const t = pv.tasks[0];
    const box: [number, number][] = [[t.xy[0] - 1, t.xy[1] - 1], [t.xy[0] + 1, t.xy[1] - 1], [t.xy[0] + 1, t.xy[1] + 1], [t.xy[0] - 1, t.xy[1] + 1]];
    const issues = previewIssues({ ...pv, obstacles: [...pv.obstacles, { id: 'X', kind: 'rock', polygon: box }] });
    expect(issues.some((i) => i.target === `task:${t.id}`)).toBe(true);
    expect(selfIntersects([[0, 0], [2, 2], [2, 0], [0, 2]])).toBe(true);
    expect(selfIntersects([[0, 0], [2, 0], [2, 2], [0, 2]])).toBe(false);
    expect(signedArea([[0, 0], [1, 1], [2, 2]])).toBe(0);
    expect(previewIssues({ ...pv, boundary: [[0, 0], [2, 2], [2, 0], [0, 2]] }).some((i) => i.message === '边界自交')).toBe(true);
  });
  it('editor geometry now shares the agri implementation', () => {
    expect(editorPip([1, 1], [[0, 0], [2, 0], [2, 2], [0, 2]])).toBe(true);
    expect(distToSegment([0, 1], [-1, 0], [1, 0])).toBe(1);
  });
});

import { lastImport, recordImport } from './drafts';
describe('last import record', () => {
  it('remembers the last imported scenario per pattern and tolerates garbage', () => {
    expect(lastImport(p.id)).toBeNull();
    recordImport(p.id, { scenarioId: 's1', title: 'T', seed: '3', at: 'x' });
    expect(lastImport(p.id)?.scenarioId).toBe('s1');
    local.setItem(`urp.agri.lastImport.v1:${p.id}`, '{bad');
    expect(lastImport(p.id)).toBeNull();
  });
});

describe('debouncedDraftWriter', () => {
  it('flushes a pending write once and never writes twice', async () => {
    const { vi } = await import('vitest');
    vi.useFakeTimers();
    try {
      let n = 0;
      const w = debouncedDraftWriter(() => { n += 1; }, 300);
      expect(w.pending()).toBe(true);
      w.flush(); expect(n).toBe(1); expect(w.pending()).toBe(false);
      vi.advanceTimersByTime(500); w.flush(); expect(n).toBe(1);
      const w2 = debouncedDraftWriter(() => { n += 1; }, 300);
      vi.advanceTimersByTime(300); expect(n).toBe(2); w2.flush(); expect(n).toBe(2);
      const w3 = debouncedDraftWriter(() => { n += 1; }, 300);
      w3.cancel(); vi.advanceTimersByTime(500); w3.flush(); expect(n).toBe(2);
    } finally { vi.useRealTimers(); }
  });
});

describe('describeTarget', () => {
  it('resolves every issue target kind so the map can highlight it', async () => {
    const { describeTarget } = await import('./preview');
    const p = { kind: 'fleet', boundary: [[0, 0], [10, 0], [10, 10], [0, 10]], plots: [], obstacles: [{ id: 'o1', kind: 'pond', polygon: [[1, 1], [2, 1], [2, 2]] }],
      tasks: [{ id: 't1', label: '树 1', xy: [5, 5] }], depots: [[0, 0]], unload: [], bbox: [0, 0, 10, 10] } as never;
    expect(describeTarget(p, 'boundary')?.layer).toBe('boundary');
    expect(describeTarget(p, 'obstacle:o1')?.text).toMatch('障碍 o1');
    expect(describeTarget(p, 'task:t1')?.text).toMatch('(5, 5)');
    expect(describeTarget(p, 'depot:0')?.layer).toBe('depots');
    expect(describeTarget(p, 'obstacle:missing')).toBeNull();
  });
  it('obstacle issues carry a namespaced target', async () => {
    const { previewIssues } = await import('./preview');
    const p = { kind: 'fleet', boundary: [[0, 0], [10, 0], [10, 10], [0, 10]], plots: [], obstacles: [{ id: 'o1', kind: 'pond', polygon: [[8, 8], [20, 8], [20, 20]] }],
      tasks: [], depots: [], unload: [], bbox: [0, 0, 20, 20] } as never;
    expect(previewIssues(p).some((i) => i.target === 'obstacle:o1')).toBe(true);
  });
});

describe('issueFields', () => {
  it('maps each issue to the form fields that drive it', async () => {
    const { issueFields } = await import('./preview');
    const keys = ['field_w', 'field_h', 'headland_width', 'swath_width', 'obstacle_count', 'obstacle_r', 'robot_radius', 'task_count'];
    expect(issueFields({ level: 'warn', message: '地头宽 1 m 小于作业幅宽 4 m', target: 'boundary' }, keys)).toEqual(['headland_width', 'swath_width']);
    expect(issueFields({ level: 'error', message: '边界自交', target: 'boundary' }, keys)[0]).toBe('field_w');
    expect(issueFields({ level: 'warn', message: '障碍 o1 越出边界', target: 'obstacle:o1' }, keys)[0]).toBe('obstacle_count');
    expect(issueFields({ level: 'error', message: '树 1 落在障碍内', target: 'task:t1' }, keys)[0]).toBe('robot_radius');
    expect(issueFields({ level: 'error', message: 'x', target: 'task:t1' }, ['seedless'])).toEqual([]);
  });
});

describe('shared draft between plan page and catalog', () => {
  it('generatedFrom reads pattern, params and seed from provenance', async () => {
    const { generatedFrom } = await import('./drafts');
    expect(generatedFrom({ params: { pattern_id: 'x', field_w: 10 }, seed: 7 })).toEqual({ patternId: 'x', params: { pattern_id: 'x', field_w: 10 }, seed: 7 });
    expect(generatedFrom({ note: 'composed' })).toBeNull();
    expect(generatedFrom(undefined)).toBeNull();
  });
  it('a focus request applies once', async () => {
    const { requestCatalogFocus, peekCatalogFocus, takeCatalogFocus } = await import('./drafts');
    requestCatalogFocus('row-weeding-coverage');
    expect(peekCatalogFocus()).toBe('row-weeding-coverage');
    expect(takeCatalogFocus()).toBe('row-weeding-coverage');
    expect(takeCatalogFocus()).toBe('');
  });
  it('round trip: built scenario params written back load into the catalog form unchanged', async () => {
    const { buildPattern, defaultValues } = await import('./catalog');
    const { generatedFrom } = await import('./drafts');
    const { manualEdits } = await import('./manualEdits');
    const { getPattern } = await import('./patterns');
    const p = getPattern('row-weeding-coverage')!;
    const values = { ...defaultValues(p), rows_per_pass: 6 };
    const built = buildPattern(p, values, 11) as { instance: unknown; provenance: unknown };
    const src = generatedFrom(built.provenance)!;
    expect(src.patternId).toBe(p.id); expect(src.seed).toBe(11);
    const rep = manualEdits(p, built);
    expect(rep.reproducible).toBe(true); expect(rep.changes).toEqual([]);
    saveDraft(p, { ...defaultValues(p), ...rep.params }, String(src.seed));
    const back = loadDraft(p, defaultValues(p));
    expect(back.seed).toBe('11'); expect(back.values.rows_per_pass).toBe(6);
  });
});
