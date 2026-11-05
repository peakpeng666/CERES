/** Pure helpers of the agent panel: labels, id links, grounding verdicts and one-line tool summaries. */
import type { AgentState, AgentStep, AgentTier, Grounding } from './types';

export const TIER_LABEL: Record<AgentTier, string> = { read: '只读', compute: '计算', actuate: '执行' };

export const STATE_LABEL: Record<AgentState, string> = {
  idle: '已答复',
  running: '进行中',
  awaiting_approval: '等待批准',
  error: '已停止',
  canceled: '已取消',
};

export const isBusy = (state: AgentState | null | undefined) => state === 'running' || state === 'awaiting_approval';

export type RecordKind = 'run' | 'job' | 'study' | 'field';
export type Segment = { kind: 'text'; text: string } | { kind: 'id'; text: string; type: RecordKind };

const RECORD = /\b(run|job|study|field)_\d{8}T\d{6}_[0-9a-f]+\b/g;

/** Splits answer text so record ids can be rendered as buttons; everything else stays plain text. */
export function segments(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(RECORD)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: 'text', text: text.slice(last, at) });
    out.push({ kind: 'id', text: m[0], type: m[1] as RecordKind });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

export function groundingVerdict(g: Grounding | null | undefined): { tone: 'green' | 'orange' | 'gray'; label: string; detail: string } {
  if (!g) return { tone: 'gray', label: '未核对', detail: '' };
  const issues = [...g.not_found.map((n) => `数字 ${n}`), ...g.unknown_ids.map((id) => `编号 ${id}`)];
  if (issues.length) return { tone: 'orange', label: `${issues.length} 处需要核对`, detail: `未在工具结果中直接找到：${issues.join('、')}。${g.note}` };
  if (!g.checked) return { tone: 'gray', label: '没有需要核对的数字', detail: g.note };
  return { tone: 'green', label: `${g.grounded}/${g.checked} 个数字可在工具结果中找到`, detail: g.note };
}

type Dict = Record<string, unknown>;
const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v);
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** One line for the timeline; the full arguments and result stay one click away. */
export function resultSummary(step: Extract<AgentStep, { kind: 'tool' }>): string {
  if (!step.ok) return step.error ?? '失败';
  const r = isDict(step.result) ? step.result : {};
  switch (step.name) {
    case 'compose_scenario':
      return `已生成 ${String(r.title ?? r.scenario_id ?? '任务地图')}`;
    case 'recommend':
      return `推荐 ${String(r.recommended ?? '—')}（${String(r.policy ?? '规则')}）`;
    case 'solve':
      return `已提交 ${list(r.run_ids).length} 个运行`;
    case 'wait_for_runs': {
      const runs = list(r.runs).filter(isDict);
      const finished = runs.filter((x) => ['done', 'error', 'timeout', 'canceled'].includes(String(x.state))).length;
      const valid = runs.filter((x) => isDict(x.check) && x.check.valid === true).length;
      return `${finished}/${runs.length} 个结束，${valid} 个通过独立检查`;
    }
    case 'get_runs':
    case 'list_runs':
      return `${list(r.runs).length} 条运行记录`;
    case 'compare_runs':
    case 'study_analysis':
      return r.empty ? '没有可比较的运行' : `${list(r.algorithms).length} 个算法、${String(r.n_runs ?? 0)} 次运行的对比`;
    case 'list_scenarios':
      return `${String(r.total ?? list(r.scenarios).length)} 个场景`;
    case 'list_algorithms':
      return `${list(r.algorithms).length} 个算法`;
    case 'list_fields':
      return `${String(r.total ?? list(r.fields).length)} 块田`;
    case 'create_study':
      return `已建立课题 ${String(r.study_id ?? '')}`;
    case 'start_field_simulation':
      return `已启动仿真 ${String(r.session_id ?? '')}`;
    default:
      return '完成';
  }
}

export function preview(value: unknown, limit = 160): string {
  let text: string;
  try {
    text = JSON.stringify(value);
  } catch {
    text = String(value);
  }
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/** run_id → scenario_id from everything the tools returned, so a run id in the answer can open its map. */
export function runScenarios(steps: AgentStep[]): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (v: unknown, depth: number) => {
    if (depth > 6) return;
    if (Array.isArray(v)) v.forEach((x) => walk(x, depth + 1));
    else if (isDict(v)) {
      if (typeof v.run_id === 'string' && typeof v.scenario_id === 'string') out[v.run_id] = v.scenario_id;
      if (Array.isArray(v.run_ids) && typeof v.scenario_id === 'string') for (const id of v.run_ids) if (typeof id === 'string') out[id] = v.scenario_id;
      Object.values(v).forEach((x) => walk(x, depth + 1));
    }
  };
  for (const step of steps) if (step.kind === 'tool' && step.ok) walk(step.result, 0);
  return out;
}
