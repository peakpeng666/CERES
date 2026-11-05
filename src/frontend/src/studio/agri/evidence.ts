/**
 * 六维能力的依据与目录统计的唯一来源。
 * 依据由模式本身的事实推出（分类字段、构建器、近似声明、文献），不是手写结论；
 * 声明等级与事实冲突时列为 conflict，由测试强制目录内为零。
 * 状态统计、能力统计、矩阵导出与界面摘要都调用这里，不各自计数。
 */
import { AGRI_PATTERNS, patternStatus, type PatternStatus } from './patterns';
import type { AgriCapabilities, AgriTaskPattern, CapLevel } from './taxonomy';

export type CapAxis = keyof AgriCapabilities;
export const CAP_AXES: CapAxis[] = ['defined', 'editable', 'solvable', 'checkable', 'simulatable', 'experimented'];
export const CAP_AXIS_LABELS: Record<CapAxis, string> = { defined: '定义', editable: '编辑', solvable: '求解', checkable: '验算', simulatable: '仿真', experimented: '实验' };
export const CAP_LEVEL_LABELS: Record<CapLevel, string> = { yes: '支持', partial: '部分', no: '不支持' };

export interface AxisEvidence {
  level: CapLevel;
  /** 模式作者写的判定说明。 */
  note: string;
  /** 由模式事实推出的依据条目。 */
  basis: string[];
  /** 声明等级超出事实所能支撑的范围时的说明；为空表示一致。 */
  conflict: string;
}

/** 等级上限：事实最多能支撑到哪一级。 */
const RANK: Record<CapLevel, number> = { no: 0, partial: 1, yes: 2 };

export function capabilityEvidence(p: AgriTaskPattern): Record<CapAxis, AxisEvidence> {
  const status = patternStatus(p);
  const r = p.runnable;
  const proxy = p.proxyModel ? `近似为 ${p.proxyModel.serviceObject}/${p.proxyModel.planningProblem}` : '';
  const facts: Record<CapAxis, { basis: string[]; cap: CapLevel }> = {
    defined: {
      basis: [`作业 ${p.operation}`, `目标对象 ${p.serviceObject}`, `规划问题 ${p.planningProblem}`, `${p.environments.length} 类环境`, `${p.metrics.length} 项指标`, `${p.refs.length} 篇文献`],
      cap: p.operation && p.serviceObject && p.planningProblem && p.environments.length && p.modeling ? 'yes' : 'partial',
    },
    editable: {
      basis: r ? [`构建器 ${r.problem_class}`, `${r.params.length} 个可编辑参数`, '导入后可在地图编辑器修改'] : ['无构建器，只有接口草案'],
      cap: r ? 'yes' : 'no',
    },
    solvable: {
      basis: r ? [`提交为 ${r.problem_class}`, p.proxyModel ? `求解的是代理模型（${proxy}）` : status === 'approx' ? '求解器按原生语义处理，近似点在建模层（见近似限制）' : '求解目标模型'] : ['提案模式不可提交'],
      cap: r ? (p.proxyModel ? 'partial' : 'yes') : 'no',
    },
    checkable: {
      basis: r ? ['后端独立验算（契约门禁按真实验证器跑默认值和边界组合）', p.proxyModel ? '只验算代理模型，不验算目标语义' : '验算提交的模型'] : ['无实例可验算'],
      cap: r ? (p.proxyModel ? 'partial' : 'yes') : 'no',
    },
    simulatable: {
      basis: r ? ['规划可行不等于可执行；仿真准入以执行入口检查为准'] : ['无实例'],
      cap: r ? 'partial' : 'no',
    },
    experimented: {
      basis: ['目录内没有可追溯的实验运行记录'],
      cap: 'no',
    },
  };
  const out = {} as Record<CapAxis, AxisEvidence>;
  for (const axis of CAP_AXES) {
    const { level, note } = p.capabilities[axis];
    const { basis, cap } = facts[axis];
    out[axis] = { level, note, basis, conflict: RANK[level] > RANK[cap] ? `声明为「${CAP_LEVEL_LABELS[level]}」，但依据最多支撑「${CAP_LEVEL_LABELS[cap]}」` : '' };
  }
  return out;
}

export interface CatalogStats {
  total: number;
  status: Record<PatternStatus, number>;
  capabilities: Record<CapAxis, Record<CapLevel, number>>;
  conflicts: { pattern: string; axis: CapAxis; message: string }[];
}

/** 任何一组模式（全目录或当前筛选结果）的统计；界面、矩阵与导出共用。 */
export function catalogStats(patterns: AgriTaskPattern[] = AGRI_PATTERNS): CatalogStats {
  const status: Record<PatternStatus, number> = { runnable: 0, approx: 0, proposal: 0 };
  const capabilities = Object.fromEntries(CAP_AXES.map((a) => [a, { yes: 0, partial: 0, no: 0 }])) as CatalogStats['capabilities'];
  const conflicts: CatalogStats['conflicts'] = [];
  for (const p of patterns) {
    status[patternStatus(p)] += 1;
    const ev = capabilityEvidence(p);
    for (const a of CAP_AXES) {
      capabilities[a][ev[a].level] += 1;
      if (ev[a].conflict) conflicts.push({ pattern: p.id, axis: a, message: ev[a].conflict });
    }
  }
  return { total: patterns.length, status, capabilities, conflicts };
}
