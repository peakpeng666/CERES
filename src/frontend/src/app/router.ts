import { useSyncExternalStore } from 'react';

/**
 * Hash router. Every view state that matters for "refresh restores where I
 * was" lives in the hash: #/<view>/<id>?k=v. Scrubbing the replay uses
 * replace() so the history stack only records deliberate navigation.
 */
export type ViewName =
  | 'lab'
  | 'agent'
  | 'maps'
  | 'plan'
  | 'algorithms'
  | 'assets'
  | 'experiments'
  | 'sim'
  | 'library'
  | 'system'
  | 'runs'
  | 'archive'
  | 'jobs'
  | 'overview'
  | 'samples'
  | 'replay'
  | 'risk'
  | 'evidence'
  | 'notfound';

/** Workbench views do not depend on the historical BPP exports. */
export const LAB_VIEWS: ReadonlySet<ViewName> = new Set(['lab', 'agent', 'maps', 'plan', 'algorithms', 'assets', 'experiments', 'sim', 'library', 'system', 'runs', 'archive']);

export interface Route {
  view: ViewName;
  id: string | null;
  params: URLSearchParams;
  hash: string;
}

const VIEWS: Record<string, ViewName> = {
  '': 'lab',
  lab: 'lab',
  agent: 'agent',
  maps: 'maps',
  plan: 'plan',
  algorithms: 'algorithms',
  assets: 'assets',
  experiments: 'experiments',
  sim: 'sim',
  library: 'library',
  system: 'system',
  settings: 'system',
  runs: 'runs',
  archive: 'archive',
  jobs: 'jobs',
  bpp: 'overview',
  samples: 'samples',
  replay: 'replay',
  risk: 'risk',
  evidence: 'evidence',
};

const safeDecode = (s: string): string => {
  try {
    return decodeURIComponent(s);
  } catch {
    // Malformed percent-encoding (e.g. a pasted broken link) must not throw.
    return s;
  }
};

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '');
  const [pathPart, query = ''] = raw.split('?');
  const segments = pathPart.split('/').filter(Boolean).map(safeDecode);
  const view = VIEWS[segments[0] ?? ''] ?? 'notfound';
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(query);
  } catch {
    params = new URLSearchParams();
  }
  return { view, id: segments[1] ?? null, params, hash: `#${raw}` };
}

export function buildHash(
  view: Exclude<ViewName, 'notfound'>,
  id?: string | null,
  params?: Record<string, string | number | null | undefined> | URLSearchParams,
): string {
  // The lab dashboard is the landing page; the BPP result overview lives at #/bpp.
  const segment = view === 'overview' ? 'bpp' : view;
  const path = view === 'lab' && !id ? '/' : `/${segment}${id ? `/${encodeURIComponent(id)}` : ''}`;
  let search = '';
  if (params) {
    const sp = params instanceof URLSearchParams ? new URLSearchParams(params) : new URLSearchParams();
    if (!(params instanceof URLSearchParams)) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== null && v !== undefined && v !== '') sp.set(k, String(v));
      }
    }
    const s = sp.toString();
    search = s ? `?${s}` : '';
  }
  return `#${path}${search}`;
}

const listeners = new Set<() => void>();
let current = parseHash(typeof window === 'undefined' ? '' : window.location.hash);

function emit() {
  current = parseHash(window.location.hash);
  for (const l of listeners) l();
}

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', emit);
}

export function navigate(hash: string, { replace = false }: { replace?: boolean } = {}) {
  if (hash === window.location.hash) return;
  if (replace) {
    const url = `${window.location.pathname}${window.location.search}${hash}`;
    window.history.replaceState(window.history.state, '', url);
    emit();
  } else {
    window.location.hash = hash;
  }
}

/** Patch query params of the current route (null deletes). */
export function patchParams(
  patch: Record<string, string | number | null | undefined>,
  { replace = true }: { replace?: boolean } = {},
) {
  const route = current;
  const sp = new URLSearchParams(route.params);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === '') sp.delete(k);
    else sp.set(k, String(v));
  }
  if (route.view === 'notfound') return;
  navigate(buildHash(route.view, route.id, sp), { replace });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useRoute(): Route {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => current,
  );
}

export function getRoute(): Route {
  return current;
}

/** Same-tab navigation restores filters/position without adding history entries. */
export function lastViewHref(view: 'replay' | 'samples'): string {
  try {
    const stored = sessionStorage.getItem(view === 'replay' ? 'bpp:lastReplay' : 'bpp:lastSamples');
    return stored && parseHash(stored).view === view ? stored : `#/${view}`;
  } catch {
    return `#/${view}`;
  }
}
