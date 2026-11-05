import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// A hook lifecycle harness: renders, commits effects and runs cleanup explicitly.
const hooks = vi.hoisted(() => {
  let cursor = 0;
  const slots: any[] = [];
  let effects: (() => void)[] = [];
  return {
    start() { cursor = 0; },
    commit() { const pending = effects; effects = []; pending.forEach((run) => run()); },
    reset() { slots.forEach((slot) => slot?.cleanup?.()); slots.length = 0; effects = []; cursor = 0; },
    useState(initial: any) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], (next: any) => { slots[i] = typeof next === 'function' ? next(slots[i]) : next; }];
    },
    useRef(initial: any) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useEffect(effect: () => any, deps: unknown[]) {
      const i = cursor++, old = slots[i];
      if (old && deps.every((dep, j) => Object.is(dep, old.deps[j]))) return;
      effects.push(() => { old?.cleanup?.(); slots[i] = { deps, cleanup: effect() }; });
    },
  };
});
vi.mock('react', async (load) => ({ ...await load<typeof import('react')>(), ...hooks }));
import { useLabResource } from './api';
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() { for (let i = 0; i < 10; i++) await Promise.resolve(); }
function render(key: string, load: (signal: AbortSignal) => Promise<string>, pollMs?: number) {
  hooks.start(); return useLabResource(key, load, { pollMs });
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => { hooks.reset(); vi.useRealTimers(); });

describe('keyed gateway resources', () => {
  it('hides previous-key data before effects run and ignores late errors after navigation', async () => {
    const a = deferred<string>(), b = deferred<string>();
    render('a', () => a.promise); hooks.commit(); await settle();
    const firstB = render('b', () => b.promise);
    expect(firstB).toMatchObject({ data: null, error: null, loading: true });
    hooks.commit(); await settle();
    b.resolve('study B'); await settle();
    a.reject(new Error('old failure')); await settle();
    expect(render('b', () => b.promise)).toMatchObject({ data: 'study B', error: null, loading: false });
    expect(render('c', () => Promise.resolve('c')).data).toBeNull();
  });
  it('surfaces synchronous loaders and clears errors on successful retry', async () => {
    render('a', () => { throw new Error('bad loader'); }); hooks.commit(); await settle();
    const failed = render('a', () => Promise.resolve('recovered'));
    expect(failed.error?.message).toBe('bad loader'); expect(failed.loading).toBe(false);
    failed.reload(); render('a', () => Promise.resolve('recovered')); hooks.commit(); await settle();
    expect(render('a', () => Promise.resolve('recovered'))).toMatchObject({ data: 'recovered', error: null, loading: false });
  });
  it('does not abort a slow request just because its polling interval elapsed', async () => {
    const response = deferred<string>(); let signal!: AbortSignal;
    render('a', (s) => { signal = s; return response.promise; }, 100); hooks.commit(); await settle();
    await vi.advanceTimersByTimeAsync(500);
    expect(signal.aborted).toBe(false); expect(vi.getTimerCount()).toBe(0);
    response.resolve('ready'); await settle(); expect(vi.getTimerCount()).toBe(1);
    hooks.reset(); expect(signal.aborted).toBe(true); expect(vi.getTimerCount()).toBe(0);
  });
});
