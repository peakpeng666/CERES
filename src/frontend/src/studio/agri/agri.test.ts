/**
 * 分类体系完整性测试：规模、唯一性、空白清单、引用完整性、能力快照。
 * 数值快照必须与 matrix.ts 实际计算一致（capabilitySummary 的真实分布）。
 */
import { describe, expect, it } from 'vitest';
import {
  ENVIRONMENTS, METRICS, OPERATIONS, PLANNING_PROBLEMS, SERVICE_OBJECTS, VEHICLE_CLASSES,
} from './taxonomy';
import { REFS, REF_MAP } from './refs';
import {
  AGRI_PATTERNS, APPROX_IDS, PROPOSAL_IDS, patternStatus,
} from './patterns';
import { gapReport } from './matrix';

describe('分类体系规模', () => {
  it('14 环境 / 30 作业 / 6 服务对象 / 13 规划问题 / 6 车类 / 16 指标', () => {
    expect(ENVIRONMENTS).toHaveLength(14);
    expect(OPERATIONS).toHaveLength(30);
    expect(SERVICE_OBJECTS).toHaveLength(6);
    expect(PLANNING_PROBLEMS).toHaveLength(13);
    expect(VEHICLE_CLASSES).toHaveLength(6);
    expect(METRICS).toHaveLength(16);
  });

  it('模式目录 40 个 = 11 可构建 + 12 近似 + 17 提案', () => {
    expect(AGRI_PATTERNS).toHaveLength(40);
    const counts = { runnable: 0, approx: 0, proposal: 0 };
    for (const p of AGRI_PATTERNS) counts[patternStatus(p)] += 1;
    // 3 个可运行模板带 approximation（果园植保离散、多田块欧氏转场、茶园无坡度）→ 状态计入 approx
    expect(counts).toEqual({ runnable: 11, approx: 12, proposal: 17 });
    expect(PROPOSAL_IDS.size).toBe(17);
    expect(APPROX_IDS.size).toBe(12);
  });

  it('模式 id 全局唯一', () => {
    const ids = AGRI_PATTERNS.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('每个模式的环境/作业/问题/对象引用合法', () => {
    const envIds = new Set(ENVIRONMENTS.map((e) => e.id));
    const opIds = new Set(OPERATIONS.map((o) => o.id));
    const probIds = new Set(PLANNING_PROBLEMS.map((p) => p.id));
    const objIds = new Set(SERVICE_OBJECTS.map((s) => s.id));
    for (const p of AGRI_PATTERNS) {
      for (const e of p.environments) expect(envIds.has(e), `${p.id} env ${e}`).toBe(true);
      expect(opIds.has(p.operation), `${p.id} op`).toBe(true);
      expect(probIds.has(p.planningProblem), `${p.id} prob`).toBe(true);
      expect(objIds.has(p.serviceObject), `${p.id} obj`).toBe(true);
    }
  });
});

describe('空白清单（gapReport）', () => {
  const gap = gapReport();

  it('14 类环境每类至少 1 个模式', () => {
    expect(gap.envsWithout).toEqual([]);
  });

  it('12 个作业当前无任何模式（字面清单）', () => {
    expect(gap.opsWithout).toHaveLength(12);
    expect([...gap.opsWithout].sort()).toEqual([
      '施肥（变量追肥）', '灌溉设施巡检', '果园修剪', '联合收获',
      '秸秆处理', '割草', '搂草', '打捆', '圈舍清扫',
      '补水补药（站点补给）', '充电（充电桩调度）', '卸载（港池/粮库排队）',
    ].sort());
  });

  it('4 类规划问题只有提案（无运行/近似）', () => {
    expect([...gap.problemsProposalOnly].sort()).toEqual([
      '动态任务、障碍与重规划', '局部/选择性覆盖', '多机器人任务分配与路径协调', '时间窗与先后关系',
    ].sort());
  });

  it('仅「点到点通行」完全无模式', () => {
    expect(gap.problemsWithout).toEqual(['点到点通行']);
  });
});

describe('文献登记', () => {
  it('每条引用有题名、年份与可核查出处（doi/url/repo_note 之一）', () => {
    for (const r of REFS) {
      expect(r.title.length, r.id).toBeGreaterThan(0);
      expect(r.year, r.id).toBeGreaterThan(1900);
      expect(Boolean(r.doi || r.url || r.repo_note), `${r.id} 缺出处`).toBe(true);
      expect(r.supports.length, r.id).toBeGreaterThan(0);
    }
  });

  it('模式引用的 ref id 全部存在', () => {
    for (const p of AGRI_PATTERNS) {
      for (const rid of p.refs) expect(REF_MAP.has(rid), `${p.id} -> ${rid}`).toBe(true);
    }
  });

  it('verified 条目必须有 doi 或 url 或 repo_note', () => {
    for (const r of REFS) {
      if (r.status === 'verified') expect(Boolean(r.doi || r.url || r.repo_note), r.id).toBe(true);
    }
  });
});

describe('指标纪律', () => {
  it('模式引用的指标 id 全部存在', () => {
    const mIds = new Set(METRICS.map((m) => m.id as string));
    for (const p of AGRI_PATTERNS) {
      for (const m of p.metrics) expect(mIds.has(m), `${p.id} metric ${m}`).toBe(true);
    }
  });

  it('覆盖率/重叠率指标注明口径', () => {
    for (const m of METRICS) {
      if (['coverage_ratio', 'overlap_ratio'].includes(m.id)) expect(m.note).toMatch(/分母|幅宽/);
    }
  });
});

describe('六维能力标注', () => {
  it('每个模式六维齐全且 level 合法', () => {
    for (const p of AGRI_PATTERNS) {
      for (const axis of ['defined', 'editable', 'solvable', 'checkable', 'simulatable', 'experimented'] as const) {
        const c = p.capabilities[axis];
        expect(['yes', 'partial', 'no']).toContain(c.level);
        expect(c.note.length, `${p.id}.${axis}`).toBeGreaterThan(0);
      }
    }
  });

  it('yes 级必须有具体依据（note 不得为空话）', () => {
    for (const p of AGRI_PATTERNS) {
      for (const axis of ['defined', 'editable', 'solvable', 'checkable', 'simulatable', 'experimented'] as const) {
        const c = p.capabilities[axis];
        if (c.level === 'yes') expect(c.note.length, `${p.id}.${axis}`).toBeGreaterThanOrEqual(5);
      }
    }
  });
});
