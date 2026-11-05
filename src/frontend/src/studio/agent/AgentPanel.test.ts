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
    useCallback(callback: any, deps: unknown[]) {
      const i = cursor++, old = slots[i];
      if (!old || !deps.every((dep, j) => Object.is(dep, old.deps[j]))) slots[i] = { deps, callback };
      return slots[i].callback;
    },
    useEffect(effect: () => any, deps: unknown[]) {
      const i = cursor++, old = slots[i];
      if (old && deps.every((dep, j) => Object.is(dep, old.deps[j]))) return;
      effects.push(() => { old?.cleanup?.(); slots[i] = { deps, cleanup: effect() }; });
    },
  };
});
vi.mock('react', async (load) => ({ ...await load<typeof import('react')>(), ...hooks }));

const mocks = vi.hoisted(() => ({ sessionId: null as string | null, session: vi.fn(), send: vi.fn(), startSession: vi.fn(), cancel: vi.fn() }));
vi.mock('./store', () => {
  const state = () => ({ sessionId: mocks.sessionId, pendingAsk: null, select: (id: string | null) => { mocks.sessionId = id; } });
  return { useAgent: Object.assign((select: (snapshot: ReturnType<typeof state>) => unknown) => select(state()), { getState: state }) };
});
vi.mock('../../lab/api', async (load) => ({
  ...await load<typeof import('../../lab/api')>(),
  useLabEvent: vi.fn(),
  useLabResource: (key: string) => ({ data: key === 'agent-status' ? { configured: true } : { sessions: [] }, error: null, reload: vi.fn() }),
}));
vi.mock('../api', () => ({ studio: { agentSession: mocks.session, agentSend: mocks.send, agentStart: mocks.startSession, agentCancel: mocks.cancel } }));
import { AgentPanel } from './AgentPanel';
import { LabApiError } from '../../lab/api';
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() { for (let i = 0; i < 10; i++) await Promise.resolve(); }
function render(): any { hooks.start(); return AgentPanel(); }
function find(node: any, predicate: (node: any) => boolean): any {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) { const match = find(child, predicate); if (match) return match; }
  return null;
}
beforeEach(() => { mocks.sessionId = null; vi.clearAllMocks(); });
afterEach(() => hooks.reset());

describe('agent session navigation and sending', () => {
  it('does not clear a newer selection when an old session returns 404', async () => {
    const a = deferred<any>(), b = deferred<any>();
    mocks.session.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    mocks.sessionId = 'a'; render(); hooks.commit();
    mocks.sessionId = 'b'; render(); hooks.commit();
    a.reject(new LabApiError('not found', 'missing', 404)); await settle();
    expect(mocks.sessionId).toBe('b');
    b.resolve({ session_id: 'b', steps: [], state: 'idle', usage: { model_calls: 0, tool_calls: 0, total_tokens: 0 }, evidence: { scenarios: [], runs: [], studies: [], field_sessions: [] } }); await settle();
    expect(find(render(), (node) => node.props?.session?.session_id === 'b')).not.toBeNull();
  });

  it('deduplicates same-tick sends and does not navigate back after the user switches sessions', async () => {
    const response = deferred<any>(); mocks.startSession.mockReturnValue(response.promise);
    let tree = render(); hooks.commit();
    find(tree, (node) => node.type === 'textarea').props.onChange({ target: { value: 'test request' } });
    tree = render(); hooks.commit();
    const send = find(tree, (node) => node.props?.title === '发送（⌘/Ctrl + Enter）');
    send.props.onClick(); send.props.onClick();
    expect(mocks.startSession).toHaveBeenCalledTimes(1);
    mocks.sessionId = 'other';
    response.resolve({ session_id: 'created', steps: [], state: 'running' }); await settle();
    expect(mocks.sessionId).toBe('other');
  });
});
