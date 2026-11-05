import { afterEach, describe, expect, it, vi } from 'vitest';
import { focusFormField } from './fieldFocus';

afterEach(() => vi.unstubAllGlobals());

function fakeRoot(fields: Record<string, { focus: () => void; select: () => void; scrollIntoView: () => void }>) {
  const seen: string[] = [];
  return {
    seen,
    root: { querySelector: (sel: string) => { seen.push(sel); const m = /data-field="(.+)"/.exec(sel); return (m && fields[m[1]]) || null; } } as unknown as ParentNode,
  };
}

describe('focusFormField', () => {
  it('announces the request and focuses the input by data-field', () => {
    const win = new EventTarget();
    vi.stubGlobal('window', win);
    const asked: unknown[] = [];
    win.addEventListener('studio:focus-field', (e) => asked.push((e as CustomEvent).detail));
    const input = { focus: vi.fn(), select: vi.fn(), scrollIntoView: vi.fn() };
    const { root, seen } = fakeRoot({ 'fleet.1.speed_mps': input });
    let ok: boolean | undefined;
    focusFormField({ field: 'fleet.1.speed_mps', label: '车速' }, (r) => { ok = r; }, root);
    expect(asked).toEqual([{ field: 'fleet.1.speed_mps', label: '车速' }]);
    expect(seen[0]).toBe('input[data-field="fleet.1.speed_mps"]');
    expect(input.focus).toHaveBeenCalled();
    expect(ok).toBe(true);
  });
  it('retries while the tab renders, then reports failure', () => {
    vi.stubGlobal('window', new EventTarget());
    const frames: (() => void)[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: () => void) => { frames.push(cb); return frames.length; });
    const { root } = fakeRoot({});
    let ok: boolean | undefined;
    focusFormField({ field: 'vehicles.count', label: '车辆数' }, (r) => { ok = r; }, root, 2);
    while (frames.length) frames.shift()!();
    expect(ok).toBe(false);
  });
});
