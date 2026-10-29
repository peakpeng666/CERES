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
  newMode: 'auto' | AgentMode;
  newProvider: string | null;
  setNewProvider: (id: string | null) => void;
  loadProviders: () => Promise<void>;
  loadSessions: () => Promise<void>;
  select: (id: string | null) => Promise<void>;
  refresh: () => Promise<void>;
  start: (text: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  control: (action: ControlAction, payload?: Record<string, unknown>) => Promise<void>;
  editContract: (fields: Record<string, unknown>) => Promise<boolean>;
  setNewMode: (mode: 'auto' | AgentMode) => void;
  actionError: string | null;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
let refreshTimer = 0;
let pollTimer = 0;
let listTimer = 0;
let loadToken = 0;

export const useAgent = create<AgentStore>((set, get) => ({
  sessions: [],
  sessionId: null,
  session: null,
  providers: null,
  providersError: null,
  loadError: null,
  sending: false,
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
    try {
      set({ sessions: (await agentApi.sessions()).sessions });
    } catch {
      /* list is advisory; the open session keeps working */
    }
  },

  async select(id) {
    if (!id) {
      set({ sessionId: null, session: null, loadError: null, actionError: null });
      return;
    }
    if (get().sessionId === id && get().session) return;
    const token = ++loadToken;
    set({ sessionId: id, session: null, loadError: null, actionError: null });
    try {
      const s = await agentApi.session(id);
      if (token === loadToken) set({ session: s });
      schedulePoll();
    } catch (e) {
      if (token === loadToken) set({ loadError: message(e) });
    }
  },

  async refresh() {
    const id = get().sessionId;
    if (!id) return;
    try {
      const s = await agentApi.session(id);
      if (get().sessionId === id) set({ session: s, loadError: null });
    } catch (e) {
      if (get().sessionId === id) set({ loadError: message(e) });
    }
    schedulePoll();
  },

  async start(text) {
    set({ sending: true, actionError: null });
    try {
      const s = await agentApi.create({ text, mode: get().newMode, provider_id: get().newMode === 'rule' ? undefined : get().newProvider ?? undefined });
      set({ session: s, sessionId: s.session_id, sending: false });
      navigate(`#/agent/${encodeURIComponent(s.session_id)}`);
      void get().loadSessions();
      schedulePoll();
    } catch (e) {
      set({ sending: false, actionError: message(e) });
    }
  },

  async send(text) {
    const id = get().sessionId;
    if (!id) return get().start(text);
    set({ sending: true, actionError: null });
    try {
      const s = await agentApi.message(id, text);
      if (get().sessionId === id) set({ session: s });
      schedulePoll();
    } catch (e) {
      set({ actionError: message(e) });
    } finally {
      set({ sending: false });
    }
  },

  async control(action, payload) {
    const id = get().sessionId;
    if (!id) return;
    set({ actionError: null });
    try {
      const s = await agentApi.control(id, action, payload);
      if (get().sessionId === id) set({ session: s });
      schedulePoll();
    } catch (e) {
      set({ actionError: message(e) });
    }
  },

  async editContract(fields) {
    const id = get().sessionId;
    if (!id) return false;
    set({ actionError: null });
    try {
      const s = await agentApi.editContract(id, fields);
      if (get().sessionId === id) set({ session: s });
      return true;
    } catch (e) {
      set({ actionError: message(e) });
      return false;
    }
  },

  setNewMode: (newMode) => set({ newMode }),
  setNewProvider: (newProvider) => set({ newProvider }),
}));

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
