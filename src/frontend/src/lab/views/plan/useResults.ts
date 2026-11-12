import { useCallback, useEffect, useMemo, useState } from 'react';
import { lab, LabApiError, useGatewayConnected, useLabEvent } from '../../api';
import type { RunState, Solution } from '../../types';
import { isTerminal } from './planModel';

export interface ResultEntry {
  runId: string;
  algorithmId: string | null;
  state: RunState;
  solution: Solution | null;
  /** Client instance key at solve time; null when the run came from history/URL. */
  key: string | null;
  visible: boolean;
  colorIndex: number;
  fetchError: string | null;
}

type Stored = Pick<ResultEntry, 'runId' | 'algorithmId' | 'key' | 'visible' | 'colorIndex'>;

const MAX_ENTRIES = 16;
const EMPTY: ResultEntry[] = [];
const storageKey = (scenarioId: string) => `ceres-lab:plan:results:${scenarioId}`;

function load(scenarioId: string): { entries: Stored[]; shas: [string, string][] } {
  try {
    const raw = sessionStorage.getItem(storageKey(scenarioId));
    if (raw) return JSON.parse(raw);
  } catch {
    /* storage unavailable */
  }
  return { entries: [], shas: [] };
}

function save(scenarioId: string, entries: ResultEntry[], shas: Map<string, string>) {
  try {
    const stored: Stored[] = entries.map(({ runId, algorithmId, key, visible, colorIndex }) => ({ runId, algorithmId, key, visible, colorIndex }));
    sessionStorage.setItem(storageKey(scenarioId), JSON.stringify({ entries: stored, shas: [...shas] }));
  } catch {
    /* storage unavailable */
  }
}

const nextColor = (entries: ResultEntry[]) => {
  const used = new Set(entries.map((e) => e.colorIndex % 6));
  for (let i = 0; i < 6; i++) if (!used.has(i)) return i;
  return entries.length;
};

const pending = (e: ResultEntry) => !e.fetchError && (!e.solution || !isTerminal(e.solution.state));

/**
 * Results of the planning workspace for one scenario: live from SSE 'job'/'run',
 * polled as a fallback, remembered per tab (sessionStorage) so a reload keeps them.
 */
export function useResults(scenarioId: string | null) {
  const [state, setState] = useState<{ sid: string | null; entries: ResultEntry[]; shas: Map<string, string> }>({
    sid: null,
    entries: [],
    shas: new Map(),
  });
  const connected = useGatewayConnected();
  const setEntries = useCallback(
    (fn: (list: ResultEntry[]) => ResultEntry[]) => setState((st) => ({ ...st, entries: fn(st.entries) })),
    [],
  );

  useEffect(() => {
    if (!scenarioId) return;
    const stored = load(scenarioId);
    setState({
      sid: scenarioId,
      entries: stored.entries.map((e) => ({ ...e, state: 'queued', solution: null, fetchError: null })),
      shas: new Map(stored.shas),
    });
  }, [scenarioId]);

  useEffect(() => {
    if (state.sid && state.sid === scenarioId) save(state.sid, state.entries, state.shas);
  }, [scenarioId, state]);

  const entries = state.sid === scenarioId ? state.entries : EMPTY;
  const shas = state.shas;

  // Learn gateway instance fingerprints for instances this tab solved.
  const apply = useCallback(
    (sol: Solution) =>
      setState((st) => {
        const hit = st.entries.find((e) => e.runId === sol.run_id);
        if (!hit) return st;
        let shas = st.shas;
        if (hit.key && sol.instance_sha256 && shas.get(hit.key) !== sol.instance_sha256) {
          shas = new Map(shas);
          shas.set(hit.key, sol.instance_sha256);
        }
        return {
          ...st,
          shas,
          entries: st.entries.map((e) => (e.runId === sol.run_id ? { ...e, solution: sol, state: sol.state, fetchError: null } : e)),
        };
      }),
    [],
  );

  useLabEvent('job', (data) => {
    const d = data as { run_id?: string; state?: RunState };
    if (!d?.run_id || !d.state) return;
    setEntries((list) => list.map((e) => (e.runId === d.run_id && !e.solution ? { ...e, state: d.state! } : e)));
  });
  useLabEvent('run', (data) => {
    const sol = data as Solution;
    if (sol?.run_id) apply(sol);
  });

  // Poll unfinished runs: quickly while SSE is down, slowly as a safety net otherwise.
  const pendingIds = entries.filter(pending).map((e) => e.runId).join(',');
  useEffect(() => {
    if (!pendingIds) return;
    let cancelled = false;
    const tick = () => {
      for (const id of pendingIds.split(',')) {
        lab
          .run(id)
          .then((sol) => !cancelled && apply(sol))
          .catch((err: Error) => {
            if (cancelled) return;
            if (err instanceof LabApiError && err.status === 404)
              setEntries((list) => list.map((e) => (e.runId === id ? { ...e, fetchError: '网关中找不到该运行记录' } : e)));
          });
      }
    };
    tick();
    const timer = window.setInterval(tick, connected ? 3000 : 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pendingIds, connected, apply, setEntries]);

  const add = useCallback(
    (runIds: string[], algorithmIds: (string | null)[], key: string) =>
      setEntries((list) => {
        const fresh: ResultEntry[] = [];
        runIds.forEach((runId, i) => {
          const colorIndex = nextColor([...fresh, ...list]);
          fresh.push({ runId, algorithmId: algorithmIds[i] ?? null, state: 'queued', solution: null, key, visible: true, colorIndex, fetchError: null });
        });
        return [...fresh, ...list.filter((e) => !runIds.includes(e.runId))].slice(0, MAX_ENTRIES);
      }),
    [setEntries],
  );

  const addExisting = useCallback(
    (sol: Solution) =>
      setEntries((list) => {
        if (list.some((e) => e.runId === sol.run_id))
          return list.map((e) => (e.runId === sol.run_id ? { ...e, solution: sol, state: sol.state, visible: true } : e));
        const entry: ResultEntry = {
          runId: sol.run_id,
          algorithmId: sol.algorithm.id,
          state: sol.state,
          solution: sol,
          key: null,
          visible: true,
          colorIndex: nextColor(list),
          fetchError: null,
        };
        return [entry, ...list].slice(0, MAX_ENTRIES);
      }),
    [setEntries],
  );

  const setVisible = useCallback(
    (runId: string, visible: boolean) => setEntries((list) => list.map((e) => (e.runId === runId ? { ...e, visible } : e))),
    [setEntries],
  );

  const remove = useCallback((runIds: string[]) => setEntries((list) => list.filter((e) => !runIds.includes(e.runId))), [setEntries]);

  const hide = useCallback(
    (runIds: string[]) => {
      if (runIds.length) setEntries((list) => list.map((e) => (runIds.includes(e.runId) ? { ...e, visible: false } : e)));
    },
    [setEntries],
  );

  return useMemo(
    () => ({ entries, shas, add, addExisting, setVisible, remove, hide }),
    [entries, shas, add, addExisting, setVisible, remove, hide],
  );
}
