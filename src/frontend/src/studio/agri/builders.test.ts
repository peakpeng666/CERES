/**
 * 构建器测试：22 个可构建模式 × 3 种子的契约对齐。
 *
 * 关键对齐：放置校验镜像后端 validate.py——有效半径取
 * max(robot_radius, fleet 各类别半径)（混合机队曾因此 400，见交接文档）。
 *
 * 另守：构建产物不得包含未实现的概念键（moving_servers/terrain/event_stream 等），
 * 那些只属于 fixtures/ 提案示例；提交带这些键的实例会被后端 400 拒绝。
 */
import { describe, expect, it } from 'vitest';
import { AGRI_PATTERNS } from './patterns';
import { placeable } from './geometry';
import type { BuiltScenario } from './taxonomy';
import type { FleetInstance } from '../types';

const SEEDS = [42, 7, 2026];

/** 未实现概念键：构建产物（提交给后端的 instance）中绝不允许出现。 */
const ABSENT_CONCEPTS = [
  'moving_servers', 'terrain', 'frame.kind', 'event_stream', 'tramline_network',
  'articulated_kinematics', 'water_graph', 'reservation_table', 'turn_library',
];

function defaultsOf(p: (typeof AGRI_PATTERNS)[number]): Record<string, unknown> {
  const v: Record<string, unknown> = {};
  for (const prm of p.runnable!.params) v[prm.key] = prm.default;
  return v;
}

/** instance 的 fleet 有效放置半径：max(robot_radius, fleet 各类别 robot_radius)。 */
function effectiveRadius(inst: Record<string, unknown>): number {
  const base = Number(inst.robot_radius ?? 0);
  const veh = inst.vehicles as { fleet?: { robot_radius?: number }[] } | undefined;
  const fleetR = (veh?.fleet ?? []).map((f) => Number(f.robot_radius ?? 0));
  return Math.max(base, ...fleetR, 0);
}

function deepKeys(o: unknown, prefix = ''): string[] {
  if (o === null || typeof o !== 'object') return [];
  if (Array.isArray(o)) return o.flatMap((x) => deepKeys(x, prefix));
  return Object.entries(o as Record<string, unknown>).flatMap(([k, v]) => [
    prefix ? `${prefix}.${k}` : k,
    ...deepKeys(v, prefix ? `${prefix}.${k}` : k),
  ]);
}

const buildable = AGRI_PATTERNS.filter((p) => p.runnable);

describe('可构建模式规模', () => {
  it('23 个模式可构建（含行间机械除草覆盖近似）', () => {
    expect(buildable).toHaveLength(23);
  });
});

describe.each(buildable.map((p) => [p.id, p] as const))('构建器 %s', (_id, pattern) => {
  it.each(SEEDS)('种子 %i：构建成功且字段契约齐全', (seed) => {
    const built = pattern.runnable!.build(defaultsOf(pattern), seed);
    expect(built.problem_class).toBe(pattern.runnable!.problem_class);
    expect(built.frame.kind).toBe('field');
    expect(built.provenance.generator).toBe('urp.studio.agri');
    expect(built.provenance.pattern).toBe(pattern.id);
    expect(built.provenance.seed).toBe(seed);
    expect(built.sim.supported).toBe(false);
    expect(built.editable.values).toEqual(defaultsOf(pattern));
  });

  it.each(SEEDS)('种子 %i：同种子同实例（确定性）', (seed) => {
    const a = pattern.runnable!.build(defaultsOf(pattern), seed);
    const b = pattern.runnable!.build(defaultsOf(pattern), seed);
    expect(a.instance).toEqual(b.instance);
  });

  it('instance 不含未实现概念键', () => {
    const built = pattern.runnable!.build(defaultsOf(pattern), SEEDS[0]);
    const keys = deepKeys(built.instance);
    for (const bad of ABSENT_CONCEPTS) {
      expect(keys.includes(bad), `${pattern.id} 含未实现键 ${bad}`).toBe(false);
    }
  });
});

describe('fleet_routing 构建产物的放置与容量', () => {
  const fleetPatterns = buildable.filter((p) => p.runnable!.problem_class === 'fleet_routing');

  it('共 20 个 fleet_routing 模板', () => {
    expect(fleetPatterns).toHaveLength(20);
  });

  it.each(fleetPatterns.map((p) => [p.id, p] as const))(
    '%s：任务点/车库/卸载点按 fleet 最大半径通过镜像放置校验', (_id, pattern) => {
      for (const seed of SEEDS) {
        const built = pattern.runnable!.build(defaultsOf(pattern), seed) as BuiltScenario;
        const inst = built.instance as unknown as FleetInstance;
        const obstacles = inst.obstacles.map((o) => o.polygon);
        const r = effectiveRadius(built.instance);
        for (const plot of inst.plots) expect(plot.polygon.length).toBeGreaterThanOrEqual(3);
        for (const obstacle of inst.obstacles) expect(obstacle.polygon.length).toBeGreaterThanOrEqual(3);
        for (const task of inst.tasks) {
          expect(Number.isInteger(task.id)).toBe(true);
          expect(task.id).toBeGreaterThan(0);
          expect(task.xy).toHaveLength(2);
          expect(task.xy.every(Number.isFinite)).toBe(true);
        }
        for (const t of inst.tasks) {
          expect(placeable(t.xy, inst.boundary, obstacles, r), `${_id} 任务点 ${t.xy}`).toBe(true);
        }
        expect(placeable(inst.depot, inst.boundary, obstacles, r), `${_id} depot`).toBe(true);
        for (const u of inst.unload_points ?? []) {
          expect(placeable(u, inst.boundary, obstacles, r), `${_id} unload`).toBe(true);
        }
      }
    },
  );

  it.each(fleetPatterns.map((p) => [p.id, p] as const))(
    '%s：容量为整数且 ≥ 单点需求', (_id, pattern) => {
      const built = pattern.runnable!.build(defaultsOf(pattern), SEEDS[0]);
      const inst = built.instance as unknown as {
        tasks: { demand: number }[];
        vehicles: { capacity?: number; fleet?: { capacity: number }[] };
      };
      const maxDemand = Math.max(...inst.tasks.map((t) => t.demand));
      if (inst.vehicles.fleet) {
        for (const f of inst.vehicles.fleet) {
          expect(Number.isInteger(f.capacity)).toBe(true);
          expect(f.capacity).toBeGreaterThanOrEqual(maxDemand);
        }
      } else {
        expect(Number.isInteger(inst.vehicles.capacity)).toBe(true);
        expect(inst.vehicles.capacity!).toBeGreaterThanOrEqual(maxDemand);
      }
    },
  );
});

describe('coverage 构建产物', () => {
  const covPatterns = buildable.filter((p) => p.runnable!.problem_class === 'coverage');

  it('共 3 个 coverage 模板', () => {
    expect(covPatterns).toHaveLength(3);
  });

  it.each(covPatterns.map((p) => [p.id, p] as const))(
    '%s：幅宽/地头/转弯半径/起点齐备，分母口径在 note 中注明', (_id, pattern) => {
      const built = pattern.runnable!.build(defaultsOf(pattern), SEEDS[0]);
      const inst = built.instance as Record<string, unknown>;
      expect(inst.field).toBeDefined();
      expect(Number(inst.swath_width)).toBeGreaterThan(0);
      expect(Number(inst.headland_width)).toBeGreaterThanOrEqual(0);
      expect(Number(inst.turn_radius)).toBeGreaterThan(0);
      expect(inst.start).toBeDefined();
      expect(built.provenance.note).toMatch(/内缩|口径|障碍/);
    },
  );
});
