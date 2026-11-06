import { useEffect, useMemo, useRef, useState } from 'react';
import type { Route } from '../app/router';
import { lab, useLabResource } from '../lab/api';
import type { ProblemClass, ScenarioSummary } from '../lab/types';
import { ComposeBar } from '../studio/ComposeBar';
import type { FleetInstance } from '../studio/types';
import { Thumb } from './LayerMap';
import { PageHead } from './Surface';
import { Row, Rows } from './ui';
import s from './desk.module.css';

const CLASS: Partial<Record<ProblemClass, string>> = { fleet_routing: '多车任务', turn_routing: '转弯约束', euclidean_cvrp: 'CVRP 算例', point_to_point: '点到点', coverage: '覆盖作业' };

const cache = new Map<string, FleetInstance | null>();
function useThumb(id: string, on: boolean) {
  const [inst, setInst] = useState<FleetInstance | null>(cache.get(id) ?? null);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!on || cache.has(id) || !box.current) return;
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      io.disconnect();
      lab.scenario(id).then((sc) => {
        const i = sc.instance as unknown as FleetInstance;
        const value = i.boundary?.length ? i : null;
        cache.set(id, value); setInst(value);
      }).catch(() => cache.set(id, null));
    }, { rootMargin: '200px' });
    io.observe(box.current);
    return () => io.disconnect();
  }, [id, on]);
  return { inst, box };
}

function SceneRow({ x }: { x: ScenarioSummary }) {
  const fleet = x.problem_class === 'fleet_routing';
  const { inst, box } = useThumb(x.id, fleet);
  return (
    <Row lead={<span ref={box}>{inst ? <Thumb inst={inst} /> : <span className={s.thumbBlank} />}</span>} title={x.title} sub={x.summary}
      onClick={() => { location.hash = `#/plan/${encodeURIComponent(x.id)}`; }} />
  );
}

/** Every task map and benchmark scene. A row opens the planning surface; the bar on top makes a new map from a sentence. */
export function MapsPage({ route }: { route: Route }) {
  const res = useLabResource('desk-maps', () => lab.scenarios());
  const [tab, setTab] = useState<ProblemClass>('fleet_routing');
  const [q, setQ] = useState('');
  const all = useMemo(() => res.data?.scenarios ?? [], [res.data]);
  const classes = (Object.keys(CLASS) as ProblemClass[]).map((k) => [k, all.filter((x) => x.problem_class === k).length] as const);
  const rows = all.filter((x) => x.problem_class === tab && (!q.trim() || `${x.title} ${x.summary}`.includes(q.trim())));
  const mine = (x: ScenarioSummary) => (x as ScenarioSummary & { origin?: string }).origin === 'user';
  const ordered = [...rows.filter(mine).reverse(), ...rows.filter((x) => !mine(x))];
  return (
    <div className={s.main} style={{ flex: 1 }}>
      <PageHead page="maps" route={route} />
      <div className={s.page}>
        <div className={s.pageIn}>
          <div className={s.composeRow}><ComposeBar /></div>
          <div className={s.pageTabs} role="tablist">
            {classes.map(([k, n]) => <button key={k} type="button" role="tab" aria-selected={k === tab} onClick={() => setTab(k)}>{CLASS[k]}<em>{n}</em></button>)}
            <span className={s.grow} />
          </div>
          <label className={s.search} style={{ padding: '4px 10px' }}><input type="search" value={q} placeholder="在这一类里搜索" aria-label="搜索地图" onChange={(e) => setQ(e.target.value)} /></label>
          {res.error && <p className={s.docBad}>{res.error.message}</p>}
          <Rows>{ordered.map((x) => <SceneRow key={x.id} x={x} />)}</Rows>
          {!res.loading && !ordered.length && <p className={s.emptyLine}>这一类还没有地图。</p>}
        </div>
      </div>
    </div>
  );
}
