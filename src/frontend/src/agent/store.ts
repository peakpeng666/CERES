import { create } from 'zustand';
import { events } from '../lab/api';
import { navigate } from '../app/router';
import { agentApi, type AgentMode, type AgentSession, type ControlAction, type ProvidersInfo, type SessionSummary } from './api';

interface AgentStore {
  sessions: SessionSummary[];
  sessionId: string | null;
  session: AgentSession | null;
  providers: ProvidersInfo | null;
  providersError: string | null;
  loadError: string | null;
  sending: boolean;
  pendingAction: ControlAction | 'edit' | null;
  newMode: 'auto' | AgentMode;
  newProvider: string | null;
  setNewProvider: (id: string | null) => void;
  loadProviders: () => Promise<void>;
  loadSessions: () => Promise<void>;
  select: (id: string | null) => Promise<void>;
  refresh: () => Promise<void>;
  start: (text: string) => Promise<boolean>;
  send: (text: string) => Promise<boolean>;
  control: (action: ControlAction, payload?: Record<string, unknown>) => Promise<void>;
  editContract: (fields: Record<string, unknown>) => Promise<boolean>;
  setNewMode: (mode: 'auto' | AgentMode) => void;
  actionError: string | null;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
let refreshTimer = 0;
let pollTimer = 0;
let listTimer = 0;
let selection = 0;
let requestSequence = 0;
let appliedSequence = 0;
let listSequence = 0;

export const useAgent = create<AgentStore>((set, get) => {
  // A session can be opened, left, and opened again before an old request returns.
  // Its id alone is therefore not enough to decide whether a response still belongs here.
  const request = () => ({ selection, id: get().sessionId, sequence: ++requestSequence });
  type Request = ReturnType<typeof request>;
  const current = (r: Request) => r.selection === selection && r.id === get().sessionId;
  const accept = (r: Request, session: AgentSession) => {
    if (!current(r) || session.session_id !== r.id) return false;
    const previous = get().session;
    if (previous && (session.updated_utc < previous.updated_utc
      || (session.updated_utc === previous.updated_utc && r.sequence < appliedSequence))) return false;
    appliedSequence = Math.max(appliedSequence, r.sequence);
    set({ session, loadError: null });
    return true;
  };
  const actionAvailable = () => !get().sending && !get().pendingAction;

  return {
    sessions: [],
    sessionId: null,
    session: null,
    providers: null,
    providersError: null,
    loadError: null,
    sending: false,
    pendingAction: null,
    newMode: 'auto',
    newProvider: null,
    actionError: null,

    async loadProviders() {
      try {
        set({ providers: await agentApi.providers(), providersError: null });
      } catch (e) {
        set({ providersError: message(e) });
      }
    },

    async loadSessions() {
      const token = ++listSequence;
      try {
        const result = await agentApi.sessions();
        if (token === listSequence) set({ sessions: result.sessions });
      } catch {
        /* list is advisory; the open session keeps working */
      }
    },

    async select(id) {
      if (id && get().sessionId === id && get().session) return;
      selection++;
      appliedSequence = 0;
      window.clearTimeout(refreshTimer);
      window.clearTimeout(pollTimer);
      set({ sessionId: id, session: null, loadError: null, actionError: null, sending: false, pendingAction: null });
      if (!id) return;
      const r = request();
      try {
        accept(r, await agentApi.session(id));
      } catch (e) {
        if (current(r) && r.sequence >= appliedSequence) set({ loadError: message(e) });
      }
      if (current(r)) schedulePoll();
    },

    async refresh() {
      const r = request();
      if (!r.id) return;
      try {
        accept(r, await agentApi.session(r.id));
      } catch (e) {
        if (current(r) && r.sequence >= appliedSequence) set({ loadError: message(e) });
      }
      if (current(r)) schedulePoll();
    },

    async start(text) {
      if (!actionAvailable()) return false;
      const r = request();
      const { newMode, newProvider } = get();
      set({ sending: true, actionError: null });
      try {
        const session = await agentApi.create({ text, mode: newMode, provider_id: newMode === 'rule' ? undefined : newProvider ?? undefined });
        void get().loadSessions();
        if (!current(r)) return false;
        selection++;
        appliedSequence = r.sequence;
        set({ session, sessionId: session.session_id, sending: false, loadError: null });
        navigate(`#/agent/${encodeURIComponent(session.session_id)}`);
        schedulePoll();
        return true;
      } catch (e) {
        if (current(r)) set({ sending: false, actionError: message(e) });
        return false;
      }
    },

    async send(text) {
      if (!actionAvailable()) return false;
      const r = request();
      if (!r.id) return get().start(text);
      if (!get().session || get().session?.state === 'running') return false;
      set({ sending: true, actionError: null });
      try {
        const session = await agentApi.message(r.id, text);
        accept(r, session);
        return current(r);
      } catch (e) {
        if (current(r)) set({ actionError: message(e) });
        return false;
      } finally {
        if (current(r)) {
          set({ sending: false });
          schedulePoll();
        }
      }
    },

    async control(action, payload) {
      const r = request();
      if (!r.id || !get().session || !actionAvailable()) return;
      set({ actionError: null, pendingAction: action });
      try {
        accept(r, await agentApi.control(r.id, action, payload));
      } catch (e) {
        if (current(r)) set({ actionError: message(e) });
      } finally {
        if (current(r)) {
          set({ pendingAction: null });
          schedulePoll();
        }
      }
    },

    async editContract(fields) {
      const r = request();
      if (!r.id || !get().session || !actionAvailable()) return false;
      set({ actionError: null, pendingAction: 'edit' });
      try {
        accept(r, await agentApi.editContract(r.id, fields));
        return current(r);
      } catch (e) {
        if (current(r)) set({ actionError: message(e) });
        return false;
      } finally {
        if (current(r)) {
          set({ pendingAction: null });
          schedulePoll();
        }
      }
    },

    setNewMode: (newMode) => set({ newMode }),
    setNewProvider: (newProvider) => set({ newProvider }),
  };
});

/** While a turn runs, poll as a fallback to the event stream (which may be closed for hidden tabs). */
function schedulePoll() {
  window.clearTimeout(pollTimer);
  const s = useAgent.getState().session;
  if (!s || s.state !== 'running') return;
  pollTimer = window.setTimeout(() => void useAgent.getState().refresh(), 1500);
}

events.subscribe((type, data) => {
  if (type !== 'agent_session') return;
  const d = data as { session_id?: string };
  const state = useAgent.getState();
  if (d.session_id && d.session_id === state.sessionId) {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => void useAgent.getState().refresh(), 120);
  }
  window.clearTimeout(listTimer);
  listTimer = window.setTimeout(() => void useAgent.getState().loadSessions(), 800);
});
