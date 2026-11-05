import {
  Archive,
  Bot,
  ChartColumn,
  History,
  LayoutDashboard,
  Route as RouteIcon,
  ShieldCheck,
  Split,
  Table2,
  Waypoints,
  type LucideIcon,
} from 'lucide-react';
import { lastViewHref, parseHash, type Route, type ViewName } from '../../app/router';

export interface NavItem {
  view: Exclude<ViewName, 'notfound'>;
  label: string;
  href: string;
  icon: LucideIcon;
}

export interface NavSection {
  id: 'lab' | 'bpp';
  title: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    id: 'lab',
    title: '工作台',
    items: [
      { view: 'lab', label: '概览', href: '#/', icon: LayoutDashboard },
      { view: 'plan', label: '规划', href: '#/plan', icon: RouteIcon },
      { view: 'sim', label: '仿真', href: '#/sim', icon: Bot },
      { view: 'runs', label: '运行记录', href: '#/runs', icon: History },
      { view: 'archive', label: '实验档案', href: '#/archive', icon: Archive },
      { view: 'jobs', label: 'CVRP 数值任务', href: '#/jobs', icon: RouteIcon },
    ],
  },
  {
    id: 'bpp',
    title: 'BPP 历史结果',
    items: [
      { view: 'overview', label: '结果', href: '#/bpp', icon: ChartColumn },
      { view: 'samples', label: '样本', href: '#/samples', icon: Table2 },
      { view: 'replay', label: '回放', href: '#/replay', icon: Waypoints },
      { view: 'risk', label: '同前缀风险', href: '#/risk', icon: Split },
      { view: 'evidence', label: '证据', href: '#/evidence', icon: ShieldCheck },
    ],
  },
];

export const VIEW_TITLES: Record<ViewName, string> = {
  lab: '概览',
  agent: '智能体',
  maps: '任务地图',
  plan: '规划',
  assets: '资产库',
  experiments: '算法实验',
  sim: '仿真',
  library: '资料',
  system: '系统',
  runs: '运行记录',
  archive: '实验档案',
  algorithms: '算法库',
  jobs: 'CVRP 数值任务',
  overview: '结果总览',
  samples: '样本',
  replay: '回放',
  risk: '同前缀风险',
  evidence: '证据',
  notfound: '页面不存在',
};

/** Lab views whose last address (scenario, run, comparison) is restored from the sidebar. */
const REMEMBERED: ReadonlySet<ViewName> = new Set(['plan', 'sim', 'runs']);

const storageKey = (view: ViewName) => `urp:last:${view}`;

export function rememberLabHref(route: Route) {
  if (!REMEMBERED.has(route.view)) return;
  try {
    sessionStorage.setItem(storageKey(route.view), route.hash);
  } catch {
    /* storage unavailable: ignore */
  }
}

function recalled(view: ViewName, fallback: string): string {
  try {
    const stored = sessionStorage.getItem(storageKey(view));
    return stored && parseHash(stored).view === view ? stored : fallback;
  } catch {
    return fallback;
  }
}

/** Where a sidebar item points: the current address when active, else the remembered one. */
export function navHref(item: NavItem, route: Route): string {
  if (route.view === item.view) return route.hash;
  if (item.view === 'replay' || item.view === 'samples') return lastViewHref(item.view);
  if (REMEMBERED.has(item.view)) return recalled(item.view, item.href);
  return item.href;
}

export function sectionOf(view: ViewName): NavSection | null {
  return NAV_SECTIONS.find((s) => s.items.some((i) => i.view === view)) ?? null;
}
