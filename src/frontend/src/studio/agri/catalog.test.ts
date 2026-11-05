import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgriCatalog, PatternCard } from './AgriCatalog';
import { AGRI_PATTERNS } from './patterns';
import { buildPattern, defaultValues, filterPatterns, importPattern } from './catalog';
import { downloadMatrices, matrixDocument, matrixFile } from './export';

afterEach(() => vi.unstubAllGlobals());
const runnable = AGRI_PATTERNS.find((p) => p.runnable)!;
const proposal = AGRI_PATTERNS.find((p) => p.proposal)!;

describe('agriculture existing-workbench integration', () => {
  it.each(AGRI_PATTERNS.filter((p) => p.runnable))('UI defaults are buildable: $id', (pattern) => {
    const built = buildPattern(pattern, defaultValues(pattern), 42);
    expect(built.provenance.params.pattern_id).toBe(pattern.id);
    expect(built.provenance.params.builder_version).toBe('urp.agri.builder/3');
  });
  it('renders filters, real download actions, editable forms and blocked proposals', () => {
    const html = renderToStaticMarkup(createElement(AgriCatalog));
    for (const text of ['生产环境', '农业作业', '模式状态', '下载完整矩阵 JSON', '下载完整矩阵 CSV', '校验并导入任务规划', '随机种子']) expect(html).toContain(text);
    expect(html).toContain(runnable.name);
    const blocked = renderToStaticMarkup(createElement(PatternCard, { pattern: proposal }));
    expect(blocked).toContain('提案不可提交');
    expect(blocked).not.toContain('校验并导入任务规划');
  });
  it('combines all three filters and retains genuinely empty combinations', () => {
    for (const p of filterPatterns('orchard', 'picking', 'runnable')) {
      expect(p.environments).toContain('orchard'); expect(p.operation).toBe('picking');
    }
    expect(filterPatterns('', 'pruning')).toEqual([]);
    expect(filterPatterns('', '', 'proposal')).toHaveLength(17);
  });
  it('rejects proposals and invalid numeric/select/seed values before any HTTP write', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    await expect(importPattern(proposal, {}, 42)).rejects.toThrow('提案');
    await expect(importPattern(runnable, { ...defaultValues(runnable), field_w: NaN }, 42)).rejects.toThrow();
    await expect(importPattern(runnable, { ...defaultValues(runnable), field_w: 0 }, 42)).rejects.toThrow();
    await expect(importPattern(runnable, { ...defaultValues(runnable), depot_at: 'bogus' }, 42)).rejects.toThrow();
    await expect(importPattern(runnable, defaultValues(runnable), -1)).rejects.toThrow('种子');
    expect(fetch).not.toHaveBeenCalled();
  });
  it('uses the existing scenario import endpoint and returns only its persisted response', async () => {
    let saved: { id: string; title: string; origin: string };
    const fetch = vi.fn().mockImplementation((_url, options) => {
      saved = { id: JSON.parse(options.body).id, title: 'saved', origin: 'user' };
      return Promise.resolve(new Response(JSON.stringify(saved), { status: 200 }));
    }); vi.stubGlobal('fetch', fetch);
    expect(await importPattern(runnable, defaultValues(runnable), 42)).toEqual(saved!);
    expect(fetch.mock.calls[0][0]).toBe('/lab/api/scenarios');
    const request = JSON.parse(fetch.mock.calls[0][1].body);
    expect(request.provenance.pattern).toBe(runnable.id);
    expect(request.editable.values).toEqual(defaultValues(runnable));
    expect(request.provenance.seed).toBe(42);
    expect(request.provenance.params).toEqual({ ...defaultValues(runnable), pattern_id: runnable.id, builder_version: 'urp.agri.builder/3' });
  });
  it('propagates backend validation failure instead of navigating or inventing success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: '任务位于障碍物内' }), { status: 400 })));
    await expect(importPattern(runnable, defaultValues(runnable), 42)).rejects.toThrow('任务位于障碍物内');
  });
  it('build result preserves raw parameters, seed and pattern for standalone reproduction', () => {
    const built = buildPattern(runnable, defaultValues(runnable), 7);
    const parsed = JSON.parse(JSON.stringify(built));
    expect(parsed.provenance.seed).toBe(7); expect(parsed.provenance.pattern).toBe(runnable.id);
    expect(parsed.editable.values).toEqual(defaultValues(runnable));
  });
});

describe('downloadable matrix artifacts', () => {
  it('JSON round-trip retains all 182+180 cells and every pattern identifier', () => {
    const doc = JSON.parse(matrixFile('json').serialized);
    expect(doc.environment_problem.reduce((n: number, r: { cells: object }) => n + Object.keys(r.cells).length, 0)).toBe(182);
    expect(doc.operation_service.reduce((n: number, r: { cells: object }) => n + Object.keys(r.cells).length, 0)).toBe(180);
    expect(doc).toEqual(matrixDocument());
    const ids = new Set(doc.environment_problem.flatMap((r: { cells: Record<string, string[]> }) => Object.values(r.cells).flat()));
    for (const p of AGRI_PATTERNS) expect(ids.has(p.id)).toBe(true);
  });
  it('CSV includes header and all empty/nonempty cells, not just populated combinations', () => {
    const file = matrixFile('csv');
    expect(file.serialized.split('\r\n')).toHaveLength(363);
    expect(file.serialized).toContain('"environment_problem"');
    expect(file.serialized).toContain('"operation_service"');
    expect(file.serialized).toContain(runnable.id);
  });
  it('download writes the generated bytes to a native Blob with the correct filename', async () => {
    for (const format of ['json', 'csv'] as const) {
      const download = vi.fn(); const file = downloadMatrices(format, download);
      expect(download).toHaveBeenCalledOnce();
      expect(new Uint8Array(await download.mock.calls[0][0].arrayBuffer())).toEqual(new TextEncoder().encode(file.serialized));
      expect(download.mock.calls[0][1]).toBe(file.fileName);
    }
  });
});
