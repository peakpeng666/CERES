import { describe, expect, it, vi } from 'vitest';
import { buildPattern, createImportGuard, defaultValues } from './catalog';
import { AGRI_PATTERNS } from './patterns';
const pattern = AGRI_PATTERNS.find((p) => p.id === 'field-sampling')!;
describe('catalog request lifecycle', () => {
  it('ignores repeated clicks until success or failure, then permits an explicit retry', () => {
    const guard = createImportGuard(); const ticket = guard.begin()!;
    expect(guard.begin()).toBeNull(); expect(guard.canNavigate(ticket)).toBe(true);
    guard.finish(); expect(guard.canNavigate(ticket)).toBe(false); expect(guard.begin()).not.toBeNull();
  });
  it('never redirects after leaving and returning to the original location', () => {
    const guard = createImportGuard(); const ticket = guard.begin()!;
    guard.invalidateNavigation(); guard.invalidateNavigation();
    expect(guard.canNavigate(ticket)).toBe(false);
    guard.finish(); const next = guard.begin()!; expect(guard.canNavigate(next)).toBe(true);
  });
  it('invalidates a response on unmount without claiming the server canceled its write', () => {
    const guard = createImportGuard(); const ticket = guard.begin()!;
    guard.invalidateNavigation(); expect(guard.canNavigate(ticket)).toBe(false); expect(guard.begin()).toBeNull();
    guard.finish();
  });
});
describe('scenario identity and numeric boundaries', () => {
  it('never reuses an ID for repeated same-millisecond builds or changed parameters', () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(123456);
    try {
      const a = buildPattern(pattern, defaultValues(pattern), 42);
      const b = buildPattern(pattern, defaultValues(pattern), 42);
      const c = buildPattern(pattern, { ...defaultValues(pattern), task_count: 6 }, 42);
      expect(new Set([a.id, b.id, c.id]).size).toBe(3);
      for (const id of [a.id, b.id, c.id]) expect(id).toMatch(/^[a-z0-9][a-z0-9_.-]{1,63}$/);
      expect(a.instance).toEqual(b.instance); expect(c.instance).not.toEqual(a.instance);
    } finally { now.mockRestore(); }
  });
  it.each([NaN, Infinity, -Infinity, -1, 0.5, 4294967296])('rejects invalid seed %s', (seed) => {
    expect(() => buildPattern(pattern, defaultValues(pattern), seed)).toThrow('种子');
  });
  it.each([0, 4294967295])('supports inclusive uint32 seed %s and JSON roundtrip', (seed) => {
    const built = buildPattern(pattern, defaultValues(pattern), seed);
    expect(JSON.parse(JSON.stringify(built))).toEqual(built);
  });
  it.each([NaN, Infinity, -Infinity, 0, 600.1])('rejects invalid numeric parameter %s', (field_w) => {
    expect(() => buildPattern(pattern, { ...defaultValues(pattern), field_w }, 42)).toThrow();
  });
});

describe('HTTP recovery and duplicate writes', () => {
  it('shares one pending import across callers and retries with a new ID after a 409', async () => {
    const { importPattern } = await import('./catalog');
    let complete!: (value: Response) => void;
    const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { complete = resolve; }))
      .mockImplementationOnce((_url, options) => Promise.resolve(new Response(JSON.stringify({ id: JSON.parse(options.body).id, title: 'saved' }), { status: 201 })));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const values = defaultValues(pattern);
      const a = importPattern(pattern, values, 42); const b = importPattern(pattern, Object.fromEntries(Object.entries(values).reverse()), 42);
      const failures = Promise.allSettled([a, b]);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const firstId = JSON.parse(fetchMock.mock.calls[0][1].body).id;
      complete(new Response(JSON.stringify({ message: '场景 id 已存在' }), { status: 409 }));
      expect((await failures).map((r) => r.status)).toEqual(['rejected', 'rejected']);
      expect(values).toEqual(defaultValues(pattern));
      expect((await importPattern(pattern, values, 42)).title).toBe('saved');
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(JSON.parse(fetchMock.mock.calls[1][1].body).id).not.toBe(firstId);
    } finally { vi.unstubAllGlobals(); }
  });
  it('rejects malformed success responses rather than navigating to an undefined scene', async () => {
    const { importPattern } = await import('./catalog');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 201 })));
    try { await expect(importPattern(pattern, defaultValues(pattern), 42)).rejects.toThrow('核对保存结果'); }
    finally { vi.unstubAllGlobals(); }
  });
});

describe('download failure cleanup', () => {
  it('releases object URLs even if the browser refuses the click', async () => {
    const { downloadFile } = await import('./export');
    vi.useFakeTimers();
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.stubGlobal('document', { createElement: () => ({ click() { throw new Error('blocked download'); } }) });
    vi.stubGlobal('window', { setTimeout: globalThis.setTimeout });
    try {
      expect(() => downloadFile({ serialized: '{}', mime: 'application/json', fileName: 'matrix.json' })).toThrow('blocked download');
      vi.runAllTimers(); expect(revoke).toHaveBeenCalledWith('blob:test');
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); create.mockRestore(); revoke.mockRestore(); }
  });
});

describe('LAN HTTP compatibility', () => {
  it('builds distinct IDs without secure-context randomUUID', () => {
    const getRandomValues = vi.fn(crypto.getRandomValues.bind(crypto));
    vi.stubGlobal('crypto', { getRandomValues });
    try {
      const p = AGRI_PATTERNS.find((p) => p.id === 'field-sampling')!;
      const values = defaultValues(p);
      const first = buildPattern(p, values, 42);
      const second = buildPattern(p, values, 42);
      expect(first.id).not.toBe(second.id);
      expect(first.id).toMatch(/-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      expect(getRandomValues).toHaveBeenCalledTimes(2);
    } finally { vi.unstubAllGlobals(); }
  });
});

describe('uncertain import recovery', () => {
  it('queries the original ID after a lost response instead of writing a duplicate', async () => {
    const { importPattern } = await import('./catalog');
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      saved = JSON.parse(options.body); return Promise.reject(new TypeError('response lost'));
    }).mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(saved))));
    vi.stubGlobal('fetch', fetch);
    try {
      await expect(importPattern(pattern, defaultValues(pattern), 1001)).rejects.toThrow('再次点击将先查询');
      expect((await importPattern(pattern, defaultValues(pattern), 1001)).id).toBe(saved.id);
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(fetch.mock.calls[1][0]).toBe(`/lab/api/scenarios/${saved.id}`);
      expect(fetch.mock.calls[1][1].method).toBe('GET');
    } finally { vi.unstubAllGlobals(); }
  });
  it('only resubmits after a definitive 404 and keeps the same ID', async () => {
    const { importPattern } = await import('./catalog');
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      saved = JSON.parse(options.body); return Promise.reject(new TypeError('response lost'));
    }).mockResolvedValueOnce(new Response('{}', { status: 404 }))
      .mockImplementationOnce((_url, options) => {
        expect(JSON.parse(options.body).id).toBe(saved.id);
        return Promise.resolve(new Response(JSON.stringify(saved), { status: 201 }));
      });
    vi.stubGlobal('fetch', fetch);
    try {
      await expect(importPattern(pattern, defaultValues(pattern), 1002)).rejects.toThrow();
      expect((await importPattern(pattern, defaultValues(pattern), 1002)).id).toBe(saved.id);
      expect(fetch.mock.calls.map((c) => c[1].method)).toEqual(['POST', 'GET', 'POST']);
    } finally { vi.unstubAllGlobals(); }
  });
  it('handles a late original write winning the read/retry race without overwriting', async () => {
    const { importPattern } = await import('./catalog');
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      saved = JSON.parse(options.body); return Promise.reject(new TypeError('response lost'));
    }).mockResolvedValueOnce(new Response('{}', { status: 404 }))
      .mockResolvedValueOnce(new Response('{}', { status: 409 }))
      .mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(saved))));
    vi.stubGlobal('fetch', fetch);
    try {
      await expect(importPattern(pattern, defaultValues(pattern), 1003)).rejects.toThrow();
      expect((await importPattern(pattern, defaultValues(pattern), 1003)).id).toBe(saved.id);
      expect(fetch.mock.calls.map((c) => c[1].method)).toEqual(['POST', 'GET', 'POST', 'GET']);
    } finally { vi.unstubAllGlobals(); }
  });
  it('does not resubmit when reconciliation fails or metadata differs', async () => {
    const { importPattern } = await import('./catalog');
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      saved = JSON.parse(options.body); return Promise.reject(new TypeError('response lost'));
    }).mockResolvedValueOnce(new Response('{}', { status: 503 }))
      .mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify({ ...saved, provenance: { ...saved.provenance, seed: 999 } }))));
    vi.stubGlobal('fetch', fetch);
    try {
      await expect(importPattern(pattern, defaultValues(pattern), 1004)).rejects.toThrow();
      await expect(importPattern(pattern, defaultValues(pattern), 1004)).rejects.toThrow();
      await expect(importPattern(pattern, defaultValues(pattern), 1004)).rejects.toThrow('不一致');
      expect(fetch.mock.calls.map((c) => c[1].method)).toEqual(['POST', 'GET', 'GET']);
    } finally { vi.unstubAllGlobals(); }
  });
});

describe('reconciliation identity and draft changes', () => {
  it('rejects changed geometry even when the stored parameters match', async () => {
    const { importPattern } = await import('./catalog');
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      saved = JSON.parse(options.body); return Promise.reject(new TypeError('response lost'));
    }).mockImplementationOnce(() => {
      saved.instance.tasks[0].xy[0] += 1;
      return Promise.resolve(new Response(JSON.stringify(saved)));
    });
    vi.stubGlobal('fetch', fetch);
    try {
      await expect(importPattern(pattern, defaultValues(pattern), 1005)).rejects.toThrow();
      await expect(importPattern(pattern, defaultValues(pattern), 1005)).rejects.toThrow('不一致');
      expect(fetch.mock.calls.map((c) => c[1].method)).toEqual(['POST', 'GET']);
    } finally { vi.unstubAllGlobals(); }
  });
  it('keeps the original uncertain ID across failed reads and changed drafts', async () => {
    const { importPattern } = await import('./catalog');
    let first: any; let changed: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      first = JSON.parse(options.body); return Promise.reject(new TypeError('response lost'));
    }).mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(new Response('{}', { status: 401 }))
      .mockImplementationOnce((_url, options) => {
        changed = JSON.parse(options.body); return Promise.resolve(new Response(JSON.stringify(changed)));
      }).mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(first))));
    vi.stubGlobal('fetch', fetch);
    try {
      const values = defaultValues(pattern);
      await expect(importPattern(pattern, values, 1006)).rejects.toThrow();
      await expect(importPattern(pattern, values, 1006)).rejects.toThrow();
      await expect(importPattern(pattern, values, 1006)).rejects.toThrow();
      expect((await importPattern(pattern, { ...values, task_count: 6 }, 1006)).id).not.toBe(first.id);
      expect((await importPattern(pattern, values, 1006)).id).toBe(first.id);
      expect(fetch.mock.calls.map((c) => c[1].method)).toEqual(['POST', 'GET', 'GET', 'POST', 'GET']);
    } finally { vi.unstubAllGlobals(); }
  });
});

describe('successful response identity', () => {
  it('rejects another valid ID then reconciles the original ID without another POST', async () => {
    const { importPattern } = await import('./catalog');
    let saved: any;
    const fetch = vi.fn().mockImplementationOnce((_url, options) => {
      saved = JSON.parse(options.body);
      return Promise.resolve(new Response(JSON.stringify({ id: 'unrelated-valid-id', title: 'wrong scene' }), { status: 201 }));
    }).mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify(saved))));
    vi.stubGlobal('fetch', fetch);
    try {
      await expect(importPattern(pattern, defaultValues(pattern), 1007)).rejects.toThrow('标识与本次提交不一致');
      expect((await importPattern(pattern, defaultValues(pattern), 1007)).id).toBe(saved.id);
      expect(fetch.mock.calls.map((c) => c[1].method)).toEqual(['POST', 'GET']);
      expect(fetch.mock.calls[1][0]).toBe(`/lab/api/scenarios/${saved.id}`);
    } finally { vi.unstubAllGlobals(); }
  });
});
