import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { parseHash, useRoute } from '../../app/router';
import { lab } from '../../lab/api';
import { Spinner, Select } from '../../lab/ui';
import { ViewErrorBoundary } from '../../lab/shell/Page';
import { useStudio } from '../store';
import s from './panels.module.css';

const PlanView = lazy(() => import('../../lab/views/plan/PlanView').then((m) => ({ default: m.PlanView })));
const SimView = lazy(() => import('../../lab/views/sim/SimView').then((m) => ({ default: m.SimView })));
const Runs = lazy(() => import('../../lab/views/Runs').then((m) => ({ default: m.Runs })));
const Dashboard = lazy(() => import('../../lab/views/Dashboard').then((m) => ({ default: m.Dashboard })));

const fallback = <div className={s.pad}><Spinner /> 载入中…</div>;

function Embed({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className={s.embed}>
      <ViewErrorBoundary name={name}>
        <Suspense fallback={fallback}>{children}</Suspense>
      </ViewErrorBoundary>
    </div>
  );
}

/** Classic single-scenario planning view (routing, coverage, point-to-point problems). */
export function LegacyPlan({ id }: { id: string }) {
  const route = useMemo(() => parseHash(`#/plan/${encodeURIComponent(id)}`), [id]);
  return <Embed name="规划">{<PlanView route={route} />}</Embed>;
}

export function SimPanel() {
  const route = useRoute();
  const focus = useStudio((st) => st.focusRun ? st.runs[st.focusRun] : null);
  const rid = focus?.frame.kind === 'map' ? focus.run_id : null;
  const simRoute = useMemo(() => (route.view === 'sim' && route.params.has('run') ? route
    : parseHash(`#/sim${rid ? `?run=${encodeURIComponent(rid)}` : ''}`)), [route, rid]);
  return <Embed name="仿真">{<SimView route={simRoute} />}</Embed>;
}

export function RunsPanel() {
  const route = useRoute();
  const runsRoute = useMemo(() => (route.view === 'runs' ? route : parseHash('#/runs')), [route]);
  return <Embed name="运行记录">{<Runs route={runsRoute} />}</Embed>;
}

export function ServicesPanel() {
  return <Embed name="系统">{<Dashboard embedded />}</Embed>;
}

export function ConsolePanel() {
  const [logs, setLogs] = useState<{ log: string; path: string }[]>([]);
  const [name, setName] = useState('');
  const [lines, setLines] = useState<string[]>([]);
  // follow the tail like a terminal, unless the reader scrolled up
  const pre = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const el = pre.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines]);
  useEffect(() => {
    follow.current = true;
  }, [name]);
  useEffect(() => {
    lab.logs().then((r) => {
      setLogs(r.logs);
      setName((n) => n || r.logs.find((l) => l.log === 'gateway')?.log || r.logs[0]?.log || '');
    }).catch(() => setLogs([]));
  }, []);
  useEffect(() => {
    if (!name) return;
    let alive = true;
    const tick = () => lab.log(name, 300).then((r) => alive && setLines(r.lines)).catch(() => undefined);
    void tick();
    const id = window.setInterval(tick, 1500);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [name]);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '6px 10px', borderBottom: '0.5px solid var(--mac-separator)' }}>
        <Select aria-label="日志" value={name} onChange={(e) => setName(e.target.value)}>
          {logs.map((l) => <option key={l.log} value={l.log}>{l.log}</option>)}
        </Select>
      </div>
      <pre ref={pre} onScroll={(e) => { const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}
        style={{ flex: 1, margin: 0, padding: '8px 12px', overflow: 'auto', font: '11.5px/1.6 var(--mac-mono)', background: 'var(--mac-inset)', color: 'var(--mac-label-2)' }}>
        {lines.join('\n') || '（暂无输出）'}
      </pre>
    </div>
  );
}
