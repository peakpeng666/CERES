import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentSession } from './api';

const mocks = vi.hoisted(() => ({
  providers: vi.fn(), sessions: vi.fn(), session: vi.fn(), create: vi.fn(), message: vi.fn(),
  control: vi.fn(), editContract: vi.fn(), navigate: vi.fn(), adoptHash: vi.fn(), subscribe: vi.fn(),
}));
vi.mock('./api', () => ({ agentApi: mocks }));
vi.mock('../lab/api', () => ({ events: { subscribe: mocks.subscribe } }));
vi.mock('../app/router', () => ({ navigate: mocks.navigate, adoptHash: mocks.adoptHash }));

import { useAgent } from './store';

function session(id = 'a', state: AgentSession['state'] = 'running', ms = 1): AgentSession {
  return { session_id: id, state, updated_utc: `2026-10-30T00:00:00.${String(ms).padStart(3, '0')}Z` } as AgentSession;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { setTimeout: globalThis.setTimeout.bind(globalThis), clearTimeout: globalThis.clearTimeout.bind(globalThis) });
  vi.resetAllMocks();
  await useAgent.getState().select(null);
  useAgent.setState({ sessions: [], newMode: 'auto', newProvider: null });
  mocks.sessions.mockResolvedValue({ sessions: [] });
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function open(id = 'a', state: AgentSession['state'] = 'running') {
  useAgent.setState({ sessionId: id, session: session(id, state) });
}

describe('agent session selection and response ordering', () => {
  it.each(['resolve', 'reject'] as const)('leaving for a new task invalidates a pending session load (%s)', async (outcome) => {
    const response = deferred<AgentSession>();
    mocks.session.mockReturnValue(response.promise);
    const loading = useAgent.getState().select('a');
    await useAgent.getState().select(null);
    if (outcome === 'resolve') response.resolve(session());
    else response.reject(new Error('old failure'));
    await loading;
    expect(useAgent.getState()).toMatchObject({ sessionId: null, session: null, loadError: null });
  });

  it('an earlier visit cannot replace a reopened session with the same id', async () => {
    const old = deferred<AgentSession>();
    mocks.session.mockReturnValueOnce(old.promise).mockResolvedValueOnce(session('b')).mockResolvedValueOnce(session('a', 'done', 2));
    const loading = useAgent.getState().select('a');
    await useAgent.getState().select('b');
    await useAgent.getState().select('a');
    old.resolve(session('a', 'running', 3));
    await loading;
    expect(useAgent.getState().session?.state).toBe('done');
  });

  it('a late running snapshot cannot overwrite cancellation, even with equal millisecond timestamps', async () => {
    open();
    const old = deferred<AgentSession>();
    mocks.session.mockReturnValueOnce(old.promise);
    const refreshing = useAgent.getState().refresh();
    mocks.control.mockResolvedValueOnce(session('a', 'cancelled'));
    await useAgent.getState().control('cancel');
    old.resolve(session());
    await refreshing;
    expect(useAgent.getState().session?.state).toBe('cancelled');
  });

  it('a newer request carrying an older server snapshot cannot regress an approval state', async () => {
    open();
    const old = deferred<AgentSession>();
    const newer = deferred<AgentSession>();
    mocks.session.mockReturnValueOnce(newer.promise).mockReturnValueOnce(old.promise);
    const a = useAgent.getState().refresh();
    const b = useAgent.getState().refresh();
    newer.resolve(session('a', 'awaiting_approval', 3));
    await a;
    old.resolve(session('a', 'running', 2));
    await b;
    expect(useAgent.getState().session?.state).toBe('awaiting_approval');
  });

  it('a delayed control response cannot replace a later poll of completed work', async () => {
    open('a', 'awaiting_confirmation');
    const response = deferred<AgentSession>();
    mocks.control.mockReturnValue(response.promise);
    const confirming = useAgent.getState().control('confirm');
    mocks.session.mockResolvedValueOnce(session('a', 'done', 3));
    await useAgent.getState().refresh();
    response.resolve(session('a', 'running', 2));
    await confirming;
    expect(useAgent.getState()).toMatchObject({ session: { state: 'done' }, pendingAction: null });
  });

  it('an earlier refresh error cannot hide a newer successful response', async () => {
    open();
    const response = deferred<AgentSession>();
    mocks.session.mockReturnValueOnce(response.promise).mockResolvedValueOnce(session('a', 'done', 2));
    const old = useAgent.getState().refresh();
    await useAgent.getState().refresh();
    response.reject(new Error('old network failure'));
    await old;
    expect(useAgent.getState()).toMatchObject({ session: { state: 'done' }, loadError: null });
  });

  it('an old session action cannot put its error on the newly selected session', async () => {
    open('a', 'awaiting_approval');
    const response = deferred<AgentSession>();
    mocks.control.mockReturnValue(response.promise);
    const approving = useAgent.getState().control('approve');
    mocks.session.mockResolvedValueOnce(session('b', 'done'));
    await useAgent.getState().select('b');
    response.reject(new Error('old approval failed'));
    await approving;
    expect(useAgent.getState()).toMatchObject({ sessionId: 'b', actionError: null, pendingAction: null });
  });

  it('creating a session does not navigate back after the user leaves', async () => {
    const response = deferred<AgentSession>();
    mocks.create.mockReturnValue(response.promise);
    const creating = useAgent.getState().start('task');
    mocks.session.mockResolvedValueOnce(session('b', 'done'));
    await useAgent.getState().select('b');
    response.resolve(session('created'));
    expect(await creating).toBe(false);
    expect(useAgent.getState()).toMatchObject({ sessionId: 'b', sending: false });
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(mocks.sessions).toHaveBeenCalled();
  });

  it('finishing an old send cannot clear the new session sending flag', async () => {
    open('a', 'awaiting_user');
    const first = deferred<AgentSession>(), second = deferred<AgentSession>();
    mocks.message.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const sendingA = useAgent.getState().send('first answer');
    mocks.session.mockResolvedValueOnce(session('b', 'awaiting_user'));
    await useAgent.getState().select('b');
    const sendingB = useAgent.getState().send('second answer');
    first.resolve(session('a'));
    expect(await sendingA).toBe(false);
    expect(useAgent.getState().sending).toBe(true);
    second.resolve(session('b'));
    expect(await sendingB).toBe(true);
    expect(useAgent.getState().sending).toBe(false);
  });

  it('older list responses cannot replace a refreshed session list', async () => {
    const old = deferred<{ sessions: unknown[] }>();
    mocks.sessions.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ sessions: [{ session_id: 'new' }] });
    const listing = useAgent.getState().loadSessions();
    await useAgent.getState().loadSessions();
    old.resolve({ sessions: [{ session_id: 'old' }] });
    await listing;
    expect(useAgent.getState().sessions[0].session_id).toBe('new');
  });
});

describe('agent actions and polling', () => {
  it('does not submit duplicate creates while the first request is pending', async () => {
    const response = deferred<AgentSession>();
    mocks.create.mockReturnValue(response.promise);
    const first = useAgent.getState().start('first');
    expect(await useAgent.getState().start('duplicate')).toBe(false);
    response.resolve(session());
    expect(await first).toBe(true);
    expect(mocks.create).toHaveBeenCalledTimes(1);
  });

  it('does not submit another mutation while approval is pending', async () => {
    open('a', 'awaiting_approval');
    const response = deferred<AgentSession>();
    mocks.control.mockReturnValue(response.promise);
    const first = useAgent.getState().control('approve');
    await useAgent.getState().control('approve');
    expect(await useAgent.getState().editContract({ vehicle_count: 4 })).toBe(false);
    expect(await useAgent.getState().send('different request')).toBe(false);
    response.resolve(session());
    await first;
    expect(mocks.control).toHaveBeenCalledTimes(1);
    expect(mocks.editContract).not.toHaveBeenCalled();
    expect(mocks.message).not.toHaveBeenCalled();
  });

  it('does not send to a session that has not loaded', async () => {
    useAgent.setState({ sessionId: 'loading', session: null });
    expect(await useAgent.getState().send('premature answer')).toBe(false);
    expect(mocks.message).not.toHaveBeenCalled();
  });

  it('returns failure and preserves the actual message error for retry', async () => {
    open('a', 'awaiting_user');
    mocks.message.mockRejectedValue(new Error('消息提交失败'));
    expect(await useAgent.getState().send('answer')).toBe(false);
    expect(useAgent.getState()).toMatchObject({ sending: false, actionError: '消息提交失败' });
  });

  it('continues fallback polling after a transient error and stops after completion', async () => {
    mocks.session.mockResolvedValueOnce(session()).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(session('a', 'done', 2));
    await useAgent.getState().select('a');
    await vi.advanceTimersByTimeAsync(1500);
    expect(useAgent.getState().loadError).toBe('offline');
    await vi.advanceTimersByTimeAsync(1500);
    expect(useAgent.getState()).toMatchObject({ session: { state: 'done' }, loadError: null });
    await vi.advanceTimersByTimeAsync(6000);
    expect(mocks.session).toHaveBeenCalledTimes(3);
  });
});
