import { afterEach, describe, expect, it, vi } from 'vitest';
import { motionOff, pageChange } from './motion';

function stage(startViewTransition?: (fn: () => void) => { ready: Promise<void>; finished: Promise<void> }, hidden = false) {
  const documentElement = { dataset: {} as Record<string, string> };
  vi.stubGlobal('document', { documentElement, hidden, startViewTransition });
  return documentElement;
}

afterEach(() => vi.unstubAllGlobals());

describe('motion', () => {
  it('reads the app setting and the system setting', () => {
    const root = stage();
    expect(motionOff()).toBe(false);
    root.dataset.motion = 'reduced';
    expect(motionOff()).toBe(true);
    delete root.dataset.motion;
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(motionOff()).toBe(true);
  });

  it('a page change happens at once where it cannot be animated', () => {
    for (const setup of [() => stage(), () => { stage(vi.fn(), true); }, () => { stage(vi.fn()).dataset.motion = 'reduced'; }]) {
      setup();
      const update = vi.fn();
      pageChange(update);
      expect(update).toHaveBeenCalledTimes(1);
    }
  });

  it('a page change names the travelling elements only while it runs', async () => {
    let finish = () => {};
    const finished = new Promise<void>((resolve) => { finish = resolve; });
    const update = vi.fn();
    const root = stage((fn) => { fn(); return { ready: Promise.resolve(), finished }; });
    pageChange(update);
    expect(update).toHaveBeenCalledTimes(1);
    expect(root.dataset.vt).toBe('page');
    finish();
    await new Promise((resolve) => { setTimeout(resolve); });
    expect(root.dataset.vt).toBeUndefined();
  });

  it('a page change overtaken by the next one raises nothing and leaves the mark to the newer one', async () => {
    let finishNewer = () => {};
    const newer = new Promise<void>((resolve) => { finishNewer = resolve; });
    const skipped = () => ({ ready: Promise.reject(new DOMException('Transition was skipped', 'AbortError')), finished: Promise.resolve() });
    const running = () => ({ ready: Promise.resolve(), finished: newer });
    const plays = [skipped, running];
    const root = stage((fn) => { fn(); return plays.shift()!(); });
    pageChange(() => {});
    pageChange(() => {});
    await new Promise((resolve) => { setTimeout(resolve); });
    expect(root.dataset.vt).toBe('page');
    finishNewer();
    await new Promise((resolve) => { setTimeout(resolve); });
    expect(root.dataset.vt).toBeUndefined();
  });
});
