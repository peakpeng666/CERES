import { lazy, Suspense, useEffect, useRef } from 'react';
import { LAB_VIEWS, useRoute, type Route } from './router';
import { explainLoadError, loadMeta, useAsync, type AsyncState } from '../data/api';
import type { Meta } from '../data/types';
import { Async } from '../components/ui';
import { ProvenanceDrawer } from '../components/ProvenanceDrawer';
import { StatusMark } from '../components/Status';
import { Page, ViewErrorBoundary } from '../lab/shell/Page';
import { VIEW_TITLES, rememberLabHref } from '../lab/shell/nav';
import { Spinner } from '../lab/ui';
import { Desk } from '../desk/Desk';
import s from './App.module.css';

const Overview = lazy(() => import('../views/Overview').then((m) => ({ default: m.Overview })));
const Samples = lazy(() => import('../views/Samples').then((m) => ({ default: m.Samples })));
const ReplayView = lazy(() => import('../views/ReplayView').then((m) => ({ default: m.ReplayView })));
const RiskView = lazy(() => import('../views/RiskView').then((m) => ({ default: m.RiskView })));
const EvidenceView = lazy(() => import('../views/EvidenceView').then((m) => ({ default: m.EvidenceView })));
const JobsView = lazy(() => import('../views/JobsView').then((m) => ({ default: m.JobsView })));

function ValidationChip({ meta }: { meta: AsyncState<Meta> }) {
  if (meta.status !== 'ready') {
    return (
      <a className={s.statusChip} href="#/evidence">
        <StatusMark status="pending" />
        <span>{meta.status === 'error' ? explainLoadError(meta.error).title : '读取核验状态'}</span>
      </a>
    );
  }
  const v = meta.data.project_validation;
  const status = v?.status === 'PASS' ? 'pass' : v?.status === 'FAIL' ? 'fail' : 'pending';
  const text = status === 'pass' ? '本项目数据核验通过' : status === 'fail' ? '本项目数据核验失败' : '本项目数据核验未完成';
  return (
    <a className={s.statusChip} href="#/evidence" title="查看核验范围与证据">
      <StatusMark status={status} />
      <span>{text}</span>
    </a>
  );
}

const loading = (
  <div className={s.viewLoading}>
    <Spinner /> 正在载入视图…
  </div>
);

/** BPP history views stay as they were; only they are gated on /data/meta.json. */
function BppContent({ route, meta }: { route: Route; meta: AsyncState<Meta> & { retry: () => void } }) {
  return (
    <div className={`${s.bpp} ${s.bppType}`}>
      <div className={s.bppChip}><ValidationChip meta={meta} /></div>
      <ViewErrorBoundary key={route.view} name={VIEW_TITLES[route.view]}>
        <Async state={meta}>
          {() => (
            <Suspense fallback={<div className={s.bppLoading}>正在载入视图…</div>}>
              {route.view === 'overview' && <Overview />}
              {route.view === 'samples' && <Samples route={route} />}
              {route.view === 'replay' && <ReplayView route={route} />}
              {route.view === 'risk' && <RiskView route={route} />}
              {route.view === 'evidence' && <EvidenceView route={route} />}
            </Suspense>
          )}
        </Async>
      </ViewErrorBoundary>
    </div>
  );
}

export function App() {
  const route = useRoute();
  const isLab = LAB_VIEWS.has(route.view);
  const isBpp = !isLab && route.view !== 'notfound' && route.view !== 'jobs';
  const meta = useAsync(isBpp ? 'meta' : null, () => loadMeta());
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    document.title = isBpp ? `${VIEW_TITLES[route.view]} · BPP 历史结果 · 农业机器人路径规划工作台` : '农业机器人路径规划工作台';
  }, [route.view, isBpp]);

  useEffect(() => {
    if ((route.view === 'replay' && route.id) || route.view === 'samples') {
      try {
        sessionStorage.setItem(route.view === 'replay' ? 'bpp:lastReplay' : 'bpp:lastSamples', route.hash);
      } catch {
        /* storage unavailable: ignore */
      }
    }
    rememberLabHref(route);
  }, [route]);

  // Move focus to the content region on view change (not on param changes).
  const lastView = useRef(route.view);
  useEffect(() => {
    if (lastView.current !== route.view) {
      lastView.current = route.view;
      window.scrollTo({ top: 0 });
      mainRef.current?.focus({ preventScroll: true });
    }
  }, [route.view]);

  return (
    <div className="lab-root">
      <Desk route={route}>
        {!isLab && (
          <>
            <button type="button" className="skip-link" onClick={() => mainRef.current?.focus()}>
              跳到主要内容
            </button>
            <main ref={mainRef} className={s.main} tabIndex={-1} aria-label={VIEW_TITLES[route.view]}>
              {isBpp && <BppContent route={route} meta={meta} />}
              {route.view === 'jobs' && <div className={`${s.bpp} ${s.bppType}`}><ViewErrorBoundary name="CVRP 数值任务"><Suspense fallback={loading}><JobsView route={route} /></Suspense></ViewErrorBoundary></div>}
              {route.view === 'notfound' && (
                <Page title="没有这个页面">
                  <p className={s.notFound}>
                    地址 <code className="mono">{route.hash}</code> 不对应任何视图。<a href="#/">回到工作台</a> ·{' '}
                    <a href="#/bpp">BPP 历史结果</a>
                  </p>
                </Page>
              )}
            </main>
            <div className={s.bppType}>
              <ProvenanceDrawer />
            </div>
          </>
        )}
      </Desk>
    </div>
  );
}
