import { afterEach, describe, expect, it, vi } from 'vitest';
import { AGRI_PATTERNS } from './patterns';

const pattern = AGRI_PATTERNS.find((p) => p.id === 'field-sampling')!;
afterEach(() => vi.unstubAllGlobals());

describe('agri import survives a page reload', () => {
  it('after a reload the retry reads the original id first instead of creating a second scenario', async () => {
    const data = new Map<string, string>();
    vi.stubGlobal('sessionStorage', { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => data.set(k, v), removeItem: (k: string) => data.delete(k) });
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_u, o) => { saved = JSON.parse(o.body); return Promise.reject(new TypeError('lost')); })
      .mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(saved))));
    vi.stubGlobal('fetch', fetch);
    vi.resetModules();
    let cat = await import('./catalog');
    await expect(cat.importPattern(pattern, cat.defaultValues(pattern), 4242)).rejects.toThrow('再次点击将先查询');
    vi.resetModules(); // reload: in-memory maps are gone, tab storage remains
    cat = await import('./catalog');
    expect((await cat.importPattern(pattern, cat.defaultValues(pattern), 4242)).id).toBe(saved.id);
    expect(fetch.mock.calls.map((c) => c[1]?.method ?? 'GET')).toEqual(['POST', 'GET']);
    expect(JSON.parse(data.get('urp.studio.pending-writes')!).entries).toEqual({});
  });
  it('a reload while the write is still in flight also recovers by id', async () => {
    const data = new Map<string, string>();
    vi.stubGlobal('sessionStorage', { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => data.set(k, v) });
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_u, o) => { saved = JSON.parse(o.body); return new Promise(() => undefined); })
      .mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(saved))));
    vi.stubGlobal('fetch', fetch);
    vi.resetModules();
    let cat = await import('./catalog');
    void cat.importPattern(pattern, cat.defaultValues(pattern), 4343);
    await vi.waitFor(() => expect(saved).toBeDefined());
    vi.resetModules();
    cat = await import('./catalog');
    expect((await cat.importPattern(pattern, cat.defaultValues(pattern), 4343)).id).toBe(saved.id);
    expect(fetch.mock.calls[1][1]?.method ?? 'GET').toBe('GET');
  });
  it('corrupt storage degrades to a normal single import', async () => {
    vi.stubGlobal('sessionStorage', { getItem: () => '{oops', setItem: () => { throw new Error('quota'); } });
    const fetch = vi.fn().mockImplementation((_u, o) => Promise.resolve(new Response(o.body, { status: 201 })));
    vi.stubGlobal('fetch', fetch);
    vi.resetModules();
    const cat = await import('./catalog');
    await cat.importPattern(pattern, cat.defaultValues(pattern), 4444);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
