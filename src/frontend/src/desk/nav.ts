import { useEffect, useState } from 'react';
import { create } from 'zustand';
import { lab } from '../lab/api';
import type { Solution } from '../lab/types';
import type { AgentSession } from '../agent/api';
import { useStudio } from '../studio/store';
import { useDesk } from './store';
import type { Frame, RootFrame } from './frames';

/** Open the session pane at a layer path. */
export function openFrames(stack: Frame[], dir: 'push' | 'pop' = 'push') {
  const d = useDesk.getState();
  d.setStack(stack, dir);
  d.setPane('agent', true);
}
export const openPlan = (run: string) => openFrames([{ t: 'plans' }, { t: 'plan', run }]);
export const openRoot = (t: RootFrame) => openFrames([{ t } as Frame]);

export function saveFile(href: string, name: string) {
  const a = document.createElement('a');
  a.href = href; a.download = name;
  document.body.append(a); a.click(); a.remove();
}
export const sessionExportHref = (id: string) => `/lab/api/agent/sessions/${encodeURIComponent(id)}/export`;
export const runRecordHref = (id: string) => `/lab/api/runs/${encodeURIComponent(id)}/record`;

export function sessionLabel(s: Pick<AgentSession, 'title' | 'short_title' | 'custom_title'>) {
  return s.custom_title || s.short_title || s.title;
}

/** Runs outside the shared map's current batch (older contract versions) are read on demand. */
interface RunCache { runs: Record<string, Solution>; asked: Set<string>; want: (id: string) => void }
export const useRunCache = create<RunCache>((set, get) => ({
  runs: {},
  asked: new Set(),
  want(id) {
    if (get().asked.has(id) || get().runs[id]) return;
    get().asked.add(id);
    lab.run(id).then((run) => set({ runs: { ...get().runs, [id]: run } })).catch(() => get().asked.delete(id));
  },
}));

/** A run record from wherever it is already loaded, else fetched once. */
export function useRun(id: string | null | undefined): Solution | undefined {
  const shared = useStudio((x) => (id ? x.runs[id] : undefined));
  const cached = useRunCache((x) => (id ? x.runs[id] : undefined));
  const want = useRunCache((x) => x.want);
  useEffect(() => { if (id && !shared && !cached) want(id); }, [id, shared, cached, want]);
  return shared ?? cached;
}

export function useMedia(query: string) {
  const [match, setMatch] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const on = () => setMatch(m.matches);
    on();
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return match;
}
