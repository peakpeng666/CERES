import { create } from 'zustand';
import { decodeStack, encodeStack, type Frame } from './frames';
import type { PanelId } from '../studio/panelRegistry';

export type Theme = 'system' | 'light' | 'dark';

const KEY = 'workbench.desk.v1';
interface Saved { sideOpen?: boolean; paneW?: number; dock?: boolean; theme?: Theme; pinnedPane?: Record<string, boolean> }

function load(): Saved {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Saved;
  } catch {
    return {};
  }
}

interface DeskState {
  sideOpen: boolean;
  /** Right pane open, per surface. */
  pane: Record<string, boolean>;
  paneW: number;
  paneWide: boolean;
  /** Layers of the session pane. */
  stack: Frame[];
  dir: 'push' | 'pop';
  /** Tab in each surface region, keyed `surface:region`. */
  tabs: Record<string, PanelId>;
  dock: boolean;
  theme: Theme;
  overlay: 'help' | null;
  setSide: (open: boolean) => void;
  setPane: (surface: string, open: boolean) => void;
  setPaneW: (w: number) => void;
  setPaneWide: (wide: boolean) => void;
  setStack: (stack: Frame[], dir?: 'push' | 'pop') => void;
  push: (frame: Frame) => void;
  pop: () => boolean;
  setTab: (key: string, id: PanelId) => void;
  setDock: (dock: boolean) => void;
  setTheme: (theme: Theme) => void;
  setOverlay: (overlay: DeskState['overlay']) => void;
}

const saved = load();
const defaultSide = typeof window === 'undefined' ? true : window.innerWidth > 1060;

function persist(state: DeskState) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ sideOpen: state.sideOpen, paneW: state.paneW, dock: state.dock, theme: state.theme, pinnedPane: state.pane } satisfies Saved));
  } catch {
    /* storage full or blocked */
  }
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
}

export const useDesk = create<DeskState>((set, get) => ({
  sideOpen: saved.sideOpen ?? defaultSide,
  pane: saved.pinnedPane ?? {},
  paneW: saved.paneW ?? 0,
  paneWide: false,
  stack: [],
  dir: 'push',
  tabs: {},
  dock: saved.dock ?? false,
  theme: saved.theme ?? 'system',
  overlay: null,
  setSide: (sideOpen) => { set({ sideOpen }); persist(get()); },
  setPane: (surface, open) => { set({ pane: { ...get().pane, [surface]: open } }); persist(get()); },
  setPaneW: (paneW) => { set({ paneW }); persist(get()); },
  setPaneWide: (paneWide) => set({ paneWide }),
  setStack: (stack, dir = 'push') => set({ stack, dir }),
  push: (frame) => set({ stack: [...get().stack, frame], dir: 'push' }),
  pop: () => {
    const { stack } = get();
    if (stack.length < 2) return false;
    set({ stack: stack.slice(0, -1), dir: 'pop' });
    return true;
  },
  setTab: (key, id) => set({ tabs: { ...get().tabs, [key]: id } }),
  setDock: (dock) => { set({ dock }); persist(get()); },
  setTheme: (theme) => { set({ theme }); applyTheme(theme); persist(get()); },
  setOverlay: (overlay) => set({ overlay }),
}));

export const stackParam = (stack: Frame[]) => encodeStack(stack);
export const stackFromParam = (raw: string | null) => decodeStack(raw);
