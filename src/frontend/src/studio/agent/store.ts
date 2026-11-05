import { create } from 'zustand';

const KEY = 'workbench.agent.session';

function remembered(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

interface AgentUi {
  /** Session shown in the panel; remembered per browser so a reload keeps the conversation open. */
  sessionId: string | null;
  /** Text handed over from the compose bar, consumed once by the panel. */
  pendingAsk: string | null;
  select: (id: string | null) => void;
  ask: (text: string) => void;
  takeAsk: () => string | null;
}

export const useAgent = create<AgentUi>((set, get) => ({
  sessionId: remembered(),
  pendingAsk: null,
  select(sessionId) {
    set({ sessionId });
    try {
      if (sessionId) localStorage.setItem(KEY, sessionId);
      else localStorage.removeItem(KEY);
    } catch {
      /* storage blocked: the panel still works for this page */
    }
  },
  ask: (pendingAsk) => set({ pendingAsk }),
  takeAsk() {
    const text = get().pendingAsk;
    if (text !== null) set({ pendingAsk: null });
    return text;
  },
}));
