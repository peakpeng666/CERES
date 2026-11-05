import { describe, expect, it } from 'vitest';
import { REFS, REF_MAP } from './refs';
import { APPROX_PATTERNS } from './patterns/approx';
import { AGRI_PATTERNS, getPattern } from './patterns';
import { gapReport, capabilitySummary } from './matrix';
import { PLANNING_PROBLEMS } from './taxonomy';
import feeding from './fixtures/feeding_2barns_3shifts.json';
import patches from './fixtures/selective_patches.json';

// Independently recompute fixture geometry, rather than trusting stored summary fields.
function contains([x, y]: number[], ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

describe('学术与内部证据来源回归', () => {
  it('逐条锁定已核对论文 DOI，不允许无标识的占位题录冒充 verified', () => {
    const dois: Record<string, string> = {
      f2c2023: '10.1109/LRA.2023.3248439', dubins1957: '10.2307/2372560',
      galceran2013: '10.1016/j.robot.2013.09.004', oksanen2009: '10.1002/rob.20300',
      bochtis2008: '10.1016/j.biosystemseng.2008.06.008', bochtis2009: '10.1016/j.biosystemseng.2009.09.003',
      seyyedhasani2017: '10.1016/j.compag.2016.11.010', conesa2016: '10.1016/j.compag.2016.06.012',
      jensen2015: '10.1016/j.biosystemseng.2015.07.002', zhou2014: '10.1016/j.compag.2014.08.013',
      plessen2019: '10.1016/j.biosystemseng.2019.08.001',
    };
    expect(REFS.filter((r) => r.kind === 'paper')).toHaveLength(11);
    for (const [id, doi] of Object.entries(dois)) expect(REF_MAP.get(id)?.doi, id).toBe(doi);
    expect(REF_MAP.get('f2c2023')?.venue).toBe('IEEE Robotics and Automation Letters');
    expect(REF_MAP.get('bochtis2009')?.title).toBe('The Vehicle Routing Problem in Field Logistics Part I');
  });
  it('内部证据链接固定代码提交，不声称 P510 是运行记录', () => {
    const internal = REFS.filter((r) => r.kind === 'repository');
    expect(internal).toHaveLength(3);
    for (const r of internal) {
      expect(r.repo_note).toBeTruthy();
      expect(r.url).toContain('/blob/85b085fcdd9429deeb8c11708af1851384842893/src/lab/');
    }
    expect(REF_MAP.get('urp_p510')?.title).toContain('非 P510 实验记录');
  });
});

describe('代理模型不得冒充目标模型', () => {
  it('保持目录分类价值，同时显式披露实际点访问模型', () => {
    for (const p of APPROX_PATTERNS) {
      expect(p.proxyModel?.serviceObject, p.id).toBe('point_set');
      expect(p.capabilities.checkable.level, p.id).toBe('partial');
      expect(p.capabilities.experimented.level, p.id).toBe('no');
      expect(p.capabilities.simulatable.level, p.id).toBe('no');
    }
    for (const id of ['barn-feed-pushing', 'greenhouse-rail-inspection']) {
      expect(getPattern(id)?.planningProblem).toBe('row_service');
      expect(getPattern(id)?.proxyModel?.planningProblem).toBe('multi_visit');
    }
    expect(getPattern('terrace-transport')?.proxyModel?.planningProblem).toBe('cvrp');
    for (const p of AGRI_PATTERNS.filter((p) => p.approximation)) {
      expect(p.capabilities.checkable.level, p.id).toBe('partial');
    }
  });
  it('所有近似构建器的目标量纲和既有地图图层正确', () => {
    for (const p of APPROX_PATTERNS) {
      const b = p.runnable!;
      const values = Object.fromEntries(b.params.map((x) => [x.key, x.default]));
      for (const [objective, unit] of [['makespan', 's'], ['total_distance', 'm']]) {
        const built = b.build({ ...values, objective }, 42);
        expect(built.cost_unit, p.id).toBe(unit);
        expect((built.instance.vehicles as { travel: string }).travel, p.id).toBe('free');
        expect(built.layers?.rows).toEqual([]);
        const plots = built.instance.plots as { polygon: number[][] }[];
        expect(built.layers?.plots).toEqual(plots.map((x) => x.polygon));
      }
    }
  });
  it('目标问题在目录出现，不会抹去原生行服务能力缺口', () => {
    const rowService = PLANNING_PROBLEMS.find((p) => p.id === 'row_service')!.name;
    expect(gapReport().problemsProposalOnly).not.toContain(rowService);
    expect(gapReport().problemsWithoutNativeSupport).toContain(rowService);
    for (const axis of Object.values(capabilitySummary())) expect(axis.yes + axis.partial + axis.no).toBe(AGRI_PATTERNS.length);
    expect(getPattern('row-service-carp')?.capabilities.defined.level).toBe('partial');
    expect(getPattern('row-service-carp')?.modeling).toContain('行长 / 作业速度');
  });
});

describe('合成 fixture 的几何语义', () => {
  it('每个饲槽位于其归属牛舍内，每舍六槽', () => {
    for (const barn of feeding.barns) {
      const troughs = feeding.troughs.filter((t) => t.barn_id === barn.id);
      expect(troughs).toHaveLength(6);
      for (const t of troughs) expect(contains([t.x, t.y], barn.ring), t.id).toBe(true);
    }
  });
  it('斑块面积使用存储的多边形，而非外接圆面积', () => {
    for (const patch of patches.patches) {
      const ring = patch.ring;
      const twice = ring.reduce((sum, a, i) => {
        const b = ring[(i + 1) % ring.length];
        return sum + a[0] * b[1] - b[0] * a[1];
      }, 0);
      expect(patch.area_m2, patch.id).toBeCloseTo(Math.abs(twice) / 2, 3);
    }
  });
});
