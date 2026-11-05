/**
 * 矩阵与能力快照测试：矩阵由目录计算（不手写），
 * 能力快照是目录声明的锁定值（非实测）——改动目录必须同步更新快照，防止口径漂移。
 */
import { describe, expect, it } from 'vitest';
import { capabilitySummary, envProblemMatrix, gapReport, operationServiceMatrix } from './matrix';
import { AGRI_PATTERNS } from './patterns';
import { ENVIRONMENTS, OPERATIONS, PLANNING_PROBLEMS, SERVICE_OBJECTS } from './taxonomy';

describe('矩阵一致性', () => {
  it('环境×问题矩阵覆盖 14×13', () => {
    const m = envProblemMatrix();
    expect(m).toHaveLength(ENVIRONMENTS.length);
    for (const row of m) expect(row.cells.size).toBe(PLANNING_PROBLEMS.length);
  });

  it('作业×服务对象矩阵覆盖 30×6', () => {
    const m = operationServiceMatrix();
    expect(m).toHaveLength(OPERATIONS.length);
    for (const row of m) expect(row.cells.size).toBe(SERVICE_OBJECTS.length);
  });

  it('矩阵单元中的模式都属于目录', () => {
    const all = new Set(AGRI_PATTERNS.map((p) => p.id));
    for (const row of envProblemMatrix()) {
      for (const cell of row.cells.values()) {
        for (const p of cell.patterns) expect(all.has(p.id)).toBe(true);
      }
    }
  });

  it('每个模式至少出现在一个矩阵单元', () => {
    const seen = new Set<string>();
    for (const row of envProblemMatrix()) {
      for (const cell of row.cells.values()) for (const p of cell.patterns) seen.add(p.id);
    }
    for (const p of AGRI_PATTERNS) expect(seen.has(p.id), p.id).toBe(true);
  });
});

describe('能力声明快照（非实验成功率）', () => {
  it('六维分布与目录一致', () => {
    expect(capabilitySummary()).toEqual({
      defined: { yes: 26, partial: 14, no: 0 },
      editable: { yes: 21, partial: 2, no: 17 },
      solvable: { yes: 13, partial: 10, no: 17 },
      checkable: { yes: 11, partial: 12, no: 17 },
      simulatable: { yes: 0, partial: 9, no: 31 },
      experimented: { yes: 0, partial: 0, no: 40 },
    });
  });

  it('快照与 gapReport 交叉自洽：提案 17 个均 solvable=no', () => {
    const s = capabilitySummary();
    expect(s.solvable.no).toBe(17);
    expect(gapReport().problemsProposalOnly).toHaveLength(4);
  });
});
