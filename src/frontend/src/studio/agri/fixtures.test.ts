/**
 * fixture 完整性测试：18 个提案数据示例。
 *
 * 纪律：
 * - 每个 fixture 有 meta.status='proposal' 与 blocked_by；
 * - 提案模式 ↔ fixture 双向引用完整（模式 fixtures 列表指向存在的文件，且每个 fixture 被引用）；
 * - 内容测试锁定关键结构（方向行、时间窗、事件流计数、闸宽 ≥ 船宽等）。
 */
import { describe, expect, it } from 'vitest';
import { AGRI_PATTERNS } from './patterns';

import carp from './fixtures/carp_rows_orchard.json';
import terraced from './fixtures/row_service_directed_terraced.json';
import airground from './fixtures/airground_1uav_2ugv.json';
import feeding from './fixtures/feeding_2barns_3shifts.json';
import dynstream from './fixtures/dynamic_stream_50events.json';
import pdw from './fixtures/pd_warehouse_20.json';
import dynobst from './fixtures/dynobst_herd_2.json';
import ctf from './fixtures/ctf_9tramlines.json';
import sweep from './fixtures/sweep_turn_headland.json';
import paddy from './fixtures/paddy_rows_10.json';
import ponds from './fixtures/ponds_4_gates.json';
import parallel from './fixtures/parallel_4robots_1lane.json';
import bpattern from './fixtures/bpattern_12rows.json';
import patches from './fixtures/selective_patches.json';
import rendezvous from './fixtures/harvester_rendezvous.json';
import timewindow from './fixtures/timewindow_harvest.json';
import uavenergy from './fixtures/uav_energy_survey.json';
import slope from './fixtures/slope_terraces.json';

const ALL: Record<string, { meta: { status: string; blocked_by: string } }> = {
  'fixtures/carp_rows_orchard.json': carp,
  'fixtures/row_service_directed_terraced.json': terraced,
  'fixtures/airground_1uav_2ugv.json': airground,
  'fixtures/feeding_2barns_3shifts.json': feeding,
  'fixtures/dynamic_stream_50events.json': dynstream,
  'fixtures/pd_warehouse_20.json': pdw,
  'fixtures/dynobst_herd_2.json': dynobst,
  'fixtures/ctf_9tramlines.json': ctf,
  'fixtures/sweep_turn_headland.json': sweep,
  'fixtures/paddy_rows_10.json': paddy,
  'fixtures/ponds_4_gates.json': ponds,
  'fixtures/parallel_4robots_1lane.json': parallel,
  'fixtures/bpattern_12rows.json': bpattern,
  'fixtures/selective_patches.json': patches,
  'fixtures/harvester_rendezvous.json': rendezvous,
  'fixtures/timewindow_harvest.json': timewindow,
  'fixtures/uav_energy_survey.json': uavenergy,
  'fixtures/slope_terraces.json': slope,
};

describe('fixture 元信息', () => {
  it('18 个 fixture 全部登记', () => {
    expect(Object.keys(ALL)).toHaveLength(18);
  });

  it.each(Object.entries(ALL))('%s：status=proposal 且 blocked_by 非空', (_path, fx) => {
    expect(fx.meta.status).toBe('proposal');
    expect(fx.meta.blocked_by.length).toBeGreaterThan(0);
  });
});

describe('提案模式 ↔ fixture 双向完整', () => {
  const proposals = AGRI_PATTERNS.filter((p) => p.proposal);

  it('每个提案模式至少挂 1 个 fixture，且文件存在', () => {
    for (const p of proposals) {
      expect(p.proposal!.fixtures.length, p.id).toBeGreaterThanOrEqual(1);
      for (const f of p.proposal!.fixtures) {
        expect(Object.keys(ALL), `${p.id} -> ${f}`).toContain(f);
      }
    }
  });

  it('每个 fixture 至少被一个提案模式引用', () => {
    const referenced = new Set(proposals.flatMap((p) => p.proposal!.fixtures));
    for (const f of Object.keys(ALL)) expect(referenced.has(f), f).toBe(true);
  });
});

describe('fixture 内容', () => {
  it('carp_rows_orchard：12 条带方向线段行', () => {
    expect(carp.line_tasks).toHaveLength(12);
    for (const r of carp.line_tasks) {
      expect(r.p1).toHaveLength(2);
      expect(r.p2).toHaveLength(2);
      expect(['up', 'down']).toContain(r.direction);
      expect(r.p1[0]).toBe(r.p2[0]); // 行是竖直线段
    }
  });

  it('row_service_directed_terraced：3 台面 6 行', () => {
    expect(terraced.terraces).toHaveLength(3);
    expect(terraced.line_tasks).toHaveLength(6);
  });

  it('bpattern_12rows：4 类转弯库（flat/u/omega/fishtail）', () => {
    expect(bpattern.line_tasks).toHaveLength(12);
    expect(bpattern.turn_library.map((t) => t.type)).toEqual(['flat', 'u', 'omega', 'fishtail']);
  });

  it('ctf_9tramlines：9 条行走道 + 压实面积指标', () => {
    expect(ctf.tramlines).toHaveLength(9);
    expect(ctf.metrics.compacted_area_m2).toBeGreaterThan(0);
  });

  it('sweep_turn_headland：铰接角 35° + 3 间隙门', () => {
    expect(sweep.vehicle.articulation_max_deg).toBe(35);
    expect(sweep.clearance_gates).toHaveLength(3);
  });

  it('selective_patches：5 个斑块均有收益', () => {
    expect(patches.patches).toHaveLength(5);
    for (const p of patches.patches) expect(p.value).toBeGreaterThan(0);
  });

  it('slope_terraces：4 台面 3 连接，存在超 20% 坡度', () => {
    expect(slope.terraces).toHaveLength(4);
    expect(slope.links).toHaveLength(3);
    expect(slope.links.some((l) => l.grade_percent > 20)).toBe(true);
    expect(slope.constraint.max_grade_percent).toBe(20);
  });

  it('paddy_rows_10：10 行 + 下陷风险区', () => {
    expect(paddy.line_tasks).toHaveLength(10);
    expect(paddy.terrain.sinkage_zones.length).toBeGreaterThan(0);
  });

  it('parallel_4robots_1lane：4 机器人 + 通行湾在边 B-C', () => {
    expect(parallel.robots).toHaveLength(4);
    const bc = parallel.lane_graph.edges.find((e) => e.a === 'B' && e.b === 'C');
    expect(bc?.passing_bay).toBe(true);
  });

  it('harvester_rendezvous：移动服务者 16 航点轨迹', () => {
    expect(rendezvous.moving_servers[0].path).toHaveLength(16);
    expect(rendezvous.carts.length).toBeGreaterThan(0);
  });

  it('airground_1uav_2ugv：5 交接事件 + survey_before_spray 先后约束', () => {
    expect(airground.handoff_events).toHaveLength(5);
    expect(airground.precedence.some((p) => p.rule === 'survey_before_spray')).toBe(true);
  });

  it('timewindow_harvest：15 个带时间窗任务，窗为 [ready, due] 且 ready < due', () => {
    expect(timewindow.tasks).toHaveLength(15);
    for (const t of timewindow.tasks) {
      expect(t.time_window[0]).toBeLessThan(t.time_window[1]);
    }
  });

  it('dynamic_stream_50events：50 事件 = 45 到达 + 5 取消；lock_committed', () => {
    expect(dynstream.event_stream).toHaveLength(50);
    expect(dynstream.event_stream.filter((e) => e.type === 'arrive')).toHaveLength(45);
    expect(dynstream.event_stream.filter((e) => e.type === 'cancel')).toHaveLength(5);
    expect(dynstream.replan.policy).toBe('lock_committed');
  });

  it('dynobst_herd_2：2 畜群各 13 个时间戳航点，裕度 5 m', () => {
    expect(dynobst.dynamic_obstacles).toHaveLength(2);
    for (const h of dynobst.dynamic_obstacles) {
      expect(h.waypoints).toHaveLength(13);
      expect(h.safety_margin_m).toBe(5);
    }
  });

  it('feeding_2barns_3shifts：12 饲槽 3 班次，周期 86400 s', () => {
    expect(feeding.troughs).toHaveLength(12);
    expect(feeding.shifts).toEqual([[21600, 25200], [43200, 46800], [64800, 68400]]);
    expect(feeding.period_s).toBe(86400);
  });

  it('pd_warehouse_20：10 对取送 + precedence 表', () => {
    expect(pdw.pd_pairs).toHaveLength(10);
    expect(pdw.precedence).toHaveLength(10);
  });

  it('ponds_4_gates：4 塘 4 闸，闸宽 ≥ 船宽', () => {
    expect(ponds.water_graph.ponds).toHaveLength(4);
    expect(ponds.water_graph.gates).toHaveLength(4);
    for (const g of ponds.water_graph.gates) {
      expect(g.width_m).toBeGreaterThanOrEqual(ponds.ship.width_m);
    }
  });

  it('uav_energy_survey：能耗参数 + 3 起降点', () => {
    expect(uavenergy.uav.energy.capacity_wh).toBeGreaterThan(0);
    expect(uavenergy.landing_sites).toHaveLength(3);
  });
});

describe('fixture 不混入可提交实例', () => {
  it('fixtures 目录仅作提案示例，构建器产物不含 fixture 专有键（由 builders.test 守住）', () => {
    // 该断言落实在 builders.test.ts 的 ABSENT_CONCEPTS 检查；此处只确认 fixture 本身携带这些键。
    expect(deepHas(rendezvous, 'moving_servers')).toBe(true);
    expect(deepHas(paddy, 'terrain')).toBe(true);
    expect(deepHas(dynstream, 'event_stream')).toBe(true);
  });
});

function deepHas(o: unknown, key: string): boolean {
  if (o === null || typeof o !== 'object') return false;
  if (Array.isArray(o)) return o.some((x) => deepHas(x, key));
  return Object.entries(o as Record<string, unknown>).some(
    ([k, v]) => k === key || deepHas(v, key),
  );
}

// Proposal fixtures are illustrative inputs, not measured agricultural datasets.
it('全部提案 fixture 明确标注合成设计来源', () => {
  for (const [name, fixture] of Object.entries(ALL)) {
    expect((fixture.meta as { data_origin?: string }).data_origin, name).toBe('synthetic_design_example');
  }
});
