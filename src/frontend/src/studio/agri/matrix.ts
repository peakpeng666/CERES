/**
 * 目录声明矩阵的导出：环境×规划问题、作业×服务对象、能力汇总、缺口报告。
 * 全部由 AGRI_PATTERNS 计算，不手写——文档中的矩阵必须与代码目录一致。
 */
import {
  AGRI_PATTERNS, patternStatus,
} from './patterns';
import {
  ENVIRONMENTS, OPERATIONS, PLANNING_PROBLEMS, SERVICE_OBJECTS,
  type AgriTaskPattern, type CapLevel,
} from './taxonomy';
import { catalogStats, type CapAxis } from './evidence';

export interface MatrixCell {
  patterns: AgriTaskPattern[];
}

export interface EnvProblemRow {
  envId: string;
  name: string;
  cells: Map<string, MatrixCell>;
}

/** 环境 × 规划问题矩阵。 */
export function envProblemMatrix(): EnvProblemRow[] {
  return ENVIRONMENTS.map((env) => {
    const cells = new Map<string, MatrixCell>();
    for (const prob of PLANNING_PROBLEMS) {
      const pats = AGRI_PATTERNS.filter(
        (p) => p.environments.includes(env.id) && p.planningProblem === prob.id,
      );
      cells.set(prob.id, { patterns: pats });
    }
    return { envId: env.id, name: env.name, cells };
  });
}

export interface OpServiceRow {
  opId: string;
  name: string;
  cells: Map<string, MatrixCell>;
}

/** 作业 × 服务对象矩阵。 */
export function operationServiceMatrix(): OpServiceRow[] {
  return OPERATIONS.map((op) => {
    const cells = new Map<string, MatrixCell>();
    for (const so of SERVICE_OBJECTS) {
      const pats = AGRI_PATTERNS.filter(
        (p) => p.operation === op.id && p.serviceObject === so.id,
      );
      cells.set(so.id, { patterns: pats });
    }
    return { opId: op.id, name: op.name, cells };
  });
}

/** 六维能力声明的分布；不是运行成功率或实验统计。与界面摘要、导出同源（evidence.catalogStats）。 */
export function capabilitySummary(): Record<CapAxis, Record<CapLevel, number>> {
  return catalogStats().capabilities;
}

export interface GapReport {
  /** 无任何模式的环境（应为空——14 类环境均 ≥1 模式，由测试强制）。 */
  envsWithout: string[];
  /** 无任何模式的作业（字面列出，即空白清单）。 */
  opsWithout: string[];
  /** 只有提案、无可运行/近似模式的规划问题。 */
  problemsProposalOnly: string[];
  /** 无任何模式的规划问题。 */
  problemsWithout: string[];
  /** 无非近似、已声明可求解且可验算模板的目标问题（仍非运行证明）。 */
  problemsWithoutNativeSupport: string[];
}

export function gapReport(): GapReport {
  const envsWithout = ENVIRONMENTS.filter(
    (e) => !AGRI_PATTERNS.some((p) => p.environments.includes(e.id)),
  ).map((e) => e.name);
  const opsWithout = OPERATIONS.filter(
    (o) => !AGRI_PATTERNS.some((p) => p.operation === o.id),
  ).map((o) => o.name);
  const problemsProposalOnly = PLANNING_PROBLEMS.filter((prob) => {
    const pats = AGRI_PATTERNS.filter((p) => p.planningProblem === prob.id);
    return pats.length > 0 && pats.every((p) => patternStatus(p) === 'proposal');
  }).map((p) => p.name);
  const problemsWithout = PLANNING_PROBLEMS.filter(
    (prob) => !AGRI_PATTERNS.some((p) => p.planningProblem === prob.id),
  ).map((p) => p.name);
  const problemsWithoutNativeSupport = PLANNING_PROBLEMS.filter(
    (prob) => !AGRI_PATTERNS.some((p) => p.planningProblem === prob.id
      && patternStatus(p) === 'runnable' && !p.proxyModel
      && p.capabilities.solvable.level === 'yes' && p.capabilities.checkable.level === 'yes'),
  ).map((p) => p.name);
  return { envsWithout, opsWithout, problemsProposalOnly, problemsWithout, problemsWithoutNativeSupport };
}
