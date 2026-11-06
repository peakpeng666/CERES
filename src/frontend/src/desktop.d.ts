/** What the desktop shell exposes to the page. Absent in a plain browser. */
export interface UpdateState { status: 'unavailable' | 'idle' | 'checking' | 'current' | 'no-release' | 'downloading' | 'ready' | 'error'; version: string | null; percent: number | null; detail: string | null }
export interface SimBackendStatus {
  platform: string; image: string; download: string;
  docker: { ready: boolean; version: string | null; detail: string | null };
  compose: { ready: boolean; version: string | null };
  imagePresent: boolean; imageBytes: number | null;
  wsl: { ready: boolean } | null;
  running: boolean;
}
export interface DesktopBridge {
  info: () => Promise<{ version: string; platform: string; backend: 'native' | 'container'; logs: string; electron: string }>;
  openLogs: () => Promise<void>;
  setTheme?: (theme: 'system' | 'light' | 'dark') => void;
  setMotion?: (motion: 'full' | 'reduced') => void;
  update: { state: () => Promise<UpdateState>; check: () => Promise<UpdateState>; install: () => Promise<void>; onChange: (fn: (s: UpdateState) => void) => () => void };
  sim: { status: () => Promise<SimBackendStatus>; enable: () => Promise<unknown>; disable: () => Promise<unknown> };
  data: {
    info: () => Promise<{ root: string; runtime: string; archiveDb: string; dataset: { dataset_id: string }; lastTransfer?: { state: string; message: string; sha256?: string } | null }>;
    export: () => Promise<{ canceled?: boolean; path?: string; sha256?: string }>;
    restore: () => Promise<{ canceled?: boolean; destination?: string; sha256?: string }>;
  };
}
declare global { interface Window { desktop?: DesktopBridge } }
