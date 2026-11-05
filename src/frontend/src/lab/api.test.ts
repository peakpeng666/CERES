import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportUrl, fmt, lab } from './api';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
function stalledServer() {
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
  })));
}
describe('bounded gateway requests', () => {
  it('a hung gateway returns a recoverable timeout instead of leaving the UI busy forever', async () => {
    vi.useFakeTimers(); stalledServer();
    const failure = expect(lab.health()).rejects.toMatchObject({ code: 'timeout', status: 0 });
    await vi.advanceTimersByTimeAsync(30_000);
    await failure;
    expect(vi.getTimerCount()).toBe(0);
  });
  it('normal simulation shutdown has a separate ninety-second deadline', async () => {
    vi.useFakeTimers(); stalledServer();
    let finished = false;
    const request = lab.simStop();
    void request.then(() => { finished = true; }, () => { finished = true; });
    const failure = expect(request).rejects.toMatchObject({ code: 'timeout' });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(finished).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    await failure;
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('PyVRP historical method labels', () => {
  it('displays the current canonical ILS name unchanged', () => {
    expect(fmt.algorithm({ id: 'pyvrp.ils', name: 'PyVRP 迭代局部搜索', version: '0.14.0' }))
      .toBe('PyVRP 迭代局部搜索');
  });
  it.each(['pyvrp.hgs', 'pyvrp.hgs+urp.layered'])('corrects a confirmed 0.14.0 legacy label without mutating %s', (id) => {
    const record = { id, name: 'PyVRP HGS', version: '0.14.0' };
    expect(fmt.algorithm(record)).toBe('PyVRP ILS（原记录名称已纠正）');
    expect(record).toEqual({ id, name: 'PyVRP HGS', version: '0.14.0' });
  });
  it.each(['0.9.1', null])('preserves HGS labels for old or unknown version %s', (version) => {
    expect(fmt.algorithm({ id: 'pyvrp.hgs', name: 'PyVRP HGS', version })).toBe('PyVRP HGS');
  });
  it('does not relabel another solver', () => {
    expect(fmt.algorithm({ id: 'other.hgs', name: 'Other HGS', version: '0.14.0' })).toBe('Other HGS');
  });
});


describe('explicit fleet export format', () => {
  it('preserves geometry CSV and makes task services a distinct request', () => {
    expect(exportUrl.run('original/run', 'csv')).toBe('/lab/api/runs/original%2Frun/export?format=csv');
    expect(exportUrl.run('original/run', 'tasks-csv')).toBe('/lab/api/runs/original%2Frun/export?format=tasks-csv');
  });
});
