import type { Service, SimState } from '../types';

/** SSE 'services' carries the full list, either bare or as {services}. */
export function selectServices(payload: unknown): { services: Service[] } | null {
  if (Array.isArray(payload)) return { services: payload as Service[] };
  const list = (payload as { services?: unknown } | null)?.services;
  return Array.isArray(list) ? { services: list as Service[] } : null;
}

export interface KnownIssue {
  source: string;
  text: string;
}

/** Fixed scope statements shown with the live known issues. */
export const SCOPE_NOTES: KnownIssue[] = [
  {
    source: '历史数据',
    text: 'BPP 历史结果在 资料 → BPP 历史，在 设置 → 数据 下载历史档案后显示。',
  },
  {
    source: '仿真机器人',
    text: '仿真使用官方 TurtleBot4，warehouse / depot / maze 世界里的通道对应作物行与地头。',
  },
  {
    source: '规划来源',
    text: '「沿规划路径行驶」由 Nav2 控制器跟踪工作台算出的路径；「逐点导航」只用工作台给的任务顺序，每段路径由 Nav2 自己规划；从当前位置到路线起点的接驳段都由 Nav2 规划。',
  },
  {
    source: '坐标系',
    text: '基准算例没有物理单位；实地坐标和仿真地图坐标都以米计，仿真地图上的方案可以直接送进仿真。',
  },
];

/** Live known issues from services and the simulation session, de-duplicated by text. */
export function collectKnownIssues(services: Service[] | null | undefined, sim: SimState | null | undefined): KnownIssue[] {
  const seen = new Set<string>();
  const out: KnownIssue[] = [];
  const add = (source: string, text: string) => {
    const t = text.trim();
    if (!t || seen.has(t)) return;
    seen.add(t);
    out.push({ source, text: t });
  };
  for (const s of services ?? []) for (const issue of s.known_issues ?? []) add(s.name || s.id, issue);
  for (const issue of sim?.known_issues ?? []) add('仿真会话', issue);
  return out;
}

/** Order services the way a researcher reads them: core, solvers, ROS stack, GUIs. */
const ORDER = ['gateway', 'archive', 'solver', 'fields2cover', 'ros', 'sim', 'rviz', 'gazebo_gui'];

export function sortServices(services: Service[]): Service[] {
  const rank = (id: string) => {
    const i = ORDER.indexOf(id);
    return i === -1 ? ORDER.length : i;
  };
  return [...services].sort((a, b) => rank(a.id) - rank(b.id));
}

export const pendingKey = (id: string, action: string) => `${id}:${action}`;
