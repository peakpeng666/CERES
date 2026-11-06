import type { AgentState, FieldSource, ToolCall } from '../agent/api';

export const nf = (v: number | null | undefined, digits: number) =>
  (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—');
export const n0 = (v?: number | null) => nf(v, 0);
export const n1 = (v?: number | null) => nf(v, 1);
export const n2 = (v?: number | null) => nf(v, 2);
export const n3 = (v?: number | null) => nf(v, 3);

export const dateCN = (utc: string) => {
  const d = new Date(utc);
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
};
export const timeCN = (utc: string) => new Date(utc).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

/** Session list heading: the two nearest days by name, the rest by date. */
export function dayLabel(utc: string, now = new Date()): string {
  const d = new Date(utc);
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(now) - start(d)) / 86_400_000);
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  return dateCN(utc);
}

export const STATE_SHORT: Partial<Record<AgentState, string>> = {
  awaiting_user: '待补充', awaiting_confirmation: '待确认', awaiting_approval: '待批准', running: '执行中', paused: '已暂停', failed: '失败', interrupted: '已中断', cancelled: '已取消',
};
export type Tone = 'wait' | 'run' | 'bad' | 'done' | 'idle';
export function stateTone(state: AgentState): Tone {
  if (state === 'awaiting_user' || state === 'awaiting_confirmation' || state === 'awaiting_approval') return 'wait';
  if (state === 'running') return 'run';
  if (state === 'failed' || state === 'cancelled' || state === 'interrupted') return 'bad';
  if (state === 'done') return 'done';
  return 'idle';
}

export const SOURCE: Record<FieldSource, string> = { user_text: '原话', llm: '模型理解', user_edit: '人工修改', default: '默认值', repair: '批准的修复' };
export const ACTOR: Record<ToolCall['actor'], string> = { llm: '模型', rule: '规则', system: '系统校验', user: '用户', user_approved: '用户批准' };

export const TOOL: Record<string, string> = {
  rule_parse: '规则解析', update_task_contract: '更新任务合同', confirm_task_contract: '确认任务合同', ask_user: '向用户澄清', find_real_fields: '检索真实田块',
  build_scenario: '建立任务地图', list_algorithms: '查看算法库', recommend_algorithms: '推荐算法', solve: '求解对比', run_study: '重复实验', verify_run: '独立复核',
  compare_runs: '方案对比', diagnose_run: '诊断失败', propose_task_change: '提议修改任务', physics_check: '物理预演', physics_robustness: '鲁棒性评估', start_simulation: '启动田间仿真', simulation_status: '查看仿真状态', takeover: '用户接管',
};
export const toolName = (c: Pick<ToolCall, 'name' | 'label'>) => (!c.label || c.label === c.name ? TOOL[c.name] ?? c.name : c.label);

export const FIELD: Record<string, string> = {
  field_size_m: '场地尺寸', field_area_m2: '场地面积', vehicle_count: '车辆数', task_count: '任务点数', vehicle_capacity: '单车容量', obstacles: '障碍', task_kind: '作业类型',
  depot_location: '车库位置', simulate: '规划后进入仿真执行', trip_mode: '趟次', objective: '优化目标', plot_count: '地块数', travel_mode: '通行方式', task_demand: '单点需求',
  task_distribution: '任务分布', vehicle_speed_mps: '车速', time_limit_s: '时间预算', compare_count: '对比算法数', seed: '随机种子', real_field_id: '真实田块',
  start_clock: '出发时刻', task_deadline: '截止时间', task_earliest: '最早时间',
};
const VALUE: Record<string, string> = {
  sampling: '采样', inspection: '巡检', transport: '运输', harvest: '采收', spraying: '喷施', sw: '西南角', se: '东南角', nw: '西北角', ne: '东北角', center: '中部',
  single: '每车单趟', multi: '允许多趟往返', total_distance: '总里程最短', makespan: '最晚完成时间最短', rows: '沿行间与地头', free: '自由通行',
  pond: '池塘', building: '建筑', tree: '树木', ditch: '水渠', canal: '水渠', greenhouse: '温室', rock: '石块',
};
export function showValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (Array.isArray(v)) return v.join(' × ');
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${VALUE[k] ?? k} ${String(x)}`).join('、');
  return VALUE[String(v)] ?? String(v);
}
export const obstacleName = (kind: string) => VALUE[kind] ?? kind;

export const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(' ');
