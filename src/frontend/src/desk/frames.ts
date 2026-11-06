import type { PanelId } from '../studio/panelRegistry';

/** One layer of the right pane. A click pushes a frame; Esc or the crumbs pop back. */
export type Frame =
  | { t: 'map' }
  | { t: 'plots' }
  | { t: 'plot'; id: string }
  | { t: 'obstacles' }
  | { t: 'obstacle'; id: string }
  | { t: 'tasks' }
  | { t: 'task'; id: number }
  | { t: 'plans' }
  | { t: 'plan'; run: string }
  | { t: 'vehicle'; run: string; v: number }
  | { t: 'stop'; run: string; v: number; task: number }
  | { t: 'checks'; run: string }
  | { t: 'arrivals'; run: string }
  | { t: 'check'; run: string; id: string }
  | { t: 'params'; run: string }
  | { t: 'repro'; run: string }
  | { t: 'raw'; run: string }
  | { t: 'exec'; run: string; session: string }
  | { t: 'execv'; run: string; session: string; name: string }
  | { t: 'phys'; run: string; id: string }
  | { t: 'contract' }
  | { t: 'field'; key: string }
  | { t: 'rev'; i: number }
  | { t: 'trace' }
  | { t: 'tool'; id: string }
  | { t: 'llm'; i: number }
  | { t: 'sim' }
  | { t: 'panel'; id: PanelId };

export type RootFrame = 'map' | 'plans' | 'contract' | 'trace' | 'sim';
export const ROOTS: { t: RootFrame; label: string }[] = [
  { t: 'map', label: '地图' }, { t: 'plans', label: '方案' }, { t: 'contract', label: '合同' }, { t: 'trace', label: '记录' }, { t: 'sim', label: '仿真' },
];

const SEP = '~';
const ARG = '.';
const NUM = /^-?\d+$/;

function encodeFrame(f: Frame): string {
  const { t, ...rest } = f;
  const args = Object.values(rest).map(String);
  return [t, ...args].join(ARG);
}

/** Pane position in the address, so a refresh or a pasted link returns to the same layer. */
export function encodeStack(stack: Frame[]): string {
  return stack.map(encodeFrame).join(SEP);
}

function decodeFrame(raw: string): Frame | null {
  const [t, a, b, c] = raw.split(ARG);
  const int = (x?: string) => (x !== undefined && NUM.test(x) ? Number(x) : null);
  switch (t) {
    case 'map': case 'plots': case 'obstacles': case 'tasks': case 'plans': case 'contract': case 'trace': case 'sim':
      return { t };
    case 'plot': case 'obstacle': case 'tool': return a ? { t, id: a } : null;
    case 'task': { const id = int(a); return id === null ? null : { t, id }; }
    case 'plan': case 'checks': case 'arrivals': case 'params': case 'repro': case 'raw': return a ? { t, run: a } : null;
    case 'vehicle': { const v = int(b); return a && v !== null ? { t, run: a, v } : null; }
    case 'stop': { const v = int(b), task = int(c); return a && v !== null && task !== null ? { t, run: a, v, task } : null; }
    case 'check': return a && b ? { t, run: a, id: b } : null;
    case 'exec': return a && b ? { t, run: a, session: b } : null;
    case 'execv': return a && b && c ? { t, run: a, session: b, name: c } : null;
    case 'phys': return a && b ? { t, run: a, id: b } : null;
    case 'field': return a ? { t, key: a } : null;
    case 'rev': { const i = int(a); return i === null ? null : { t, i }; }
    case 'llm': { const i = int(a); return i === null ? null : { t, i }; }
    case 'panel': return a ? { t, id: a as PanelId } : null;
    default: return null;
  }
}

export function decodeStack(raw: string | null | undefined): Frame[] | null {
  if (!raw) return null;
  const frames = raw.split(SEP).map(decodeFrame);
  return frames.length && frames.every(Boolean) ? (frames as Frame[]) : null;
}

export const rootOf = (stack: Frame[]): RootFrame | null => {
  const t = stack[0]?.t;
  return ROOTS.some((r) => r.t === t) ? (t as RootFrame) : null;
};
