/**
 * 行间机械除草（中耕除草）：目标是“沿作物行方向、按行距对齐的整行服务”，
 * 现有契约里最忠实的可提交表达是固定扫描方向的区域全覆盖（coverage + angle_deg）。
 *
 * 映射关系（每项都影响生成结果）：
 * - 作业幅宽 = 每趟覆盖行数 × 行距，覆盖求解按此幅宽布趟，保证趟间不留整行空白；
 * - 扫描方向 angle_deg = 作物行方向，趟与行平行，不允许求解器自行选最优方向；
 * - 地头宽度须 ≥ 转弯半径，否则掉头会压到作物行；地头单列，不计入作业面积；
 * - 机具宽度不得超过幅宽，超过即意味着相邻趟机具重叠压行。
 *
 * 仍缺失的语义（因此是近似模式，不是可构建模板）：
 * - 趟的横向相位没有锁定到真实行中线，可能整体偏移最多半个行距；
 * - 没有“不得横穿作物行”的通行约束，跨区连接段可能斜穿；
 * - 杂草密度与除草深度不进入目标，所有行等权；
 * - 独立验算只能核对面积覆盖率，不能核对逐行完成度。
 */
import type { AgriTaskPattern, BuilderParam, BuiltScenario } from '../taxonomy';
import { validateSeed } from '../geometry';

const PARAMS: BuilderParam[] = [
  { key: 'field_w', label: '田块长', unit: 'm', type: 'number', default: 160, min: 20, max: 600, help: '沿作物行方向的田块长度。' },
  { key: 'field_h', label: '田块宽', unit: 'm', type: 'number', default: 90, min: 20, max: 600, help: '垂直于作物行方向的田块宽度。' },
  { key: 'row_spacing', label: '行距', unit: 'm', type: 'number', default: 0.6, min: 0.2, max: 1.5, help: '相邻作物行中线距离，如玉米 0.6 m、大豆 0.45 m。' },
  { key: 'rows_per_pass', label: '每趟行数', type: 'number', default: 6, min: 1, max: 24, help: '中耕机一次跨过的行数；幅宽 = 每趟行数 × 行距。' },
  { key: 'row_angle_deg', label: '作物行方向', unit: '°', type: 'number', default: 0, min: -90, max: 90, help: '相对 x 轴的方向角，扫描方向与之相同。' },
  { key: 'headland_width', label: '地头宽度', unit: 'm', type: 'number', default: 8, min: 0, max: 20, help: '须不小于转弯半径，掉头才不压行。' },
  { key: 'turn_radius', label: '最小转弯半径', unit: 'm', type: 'number', default: 5, min: 1, max: 12, help: '拖拉机加中耕机的最小转弯半径。' },
  { key: 'implement_width', label: '机具宽度', unit: 'm', type: 'number', default: 3.4, min: 0.3, max: 12, help: '不得超过幅宽，否则相邻趟重叠压行。' },
];

function value(v: Record<string, unknown>, key: string, integer = false): number {
  const p = PARAMS.find((x) => x.key === key)!;
  const n = v[key];
  if (typeof n !== 'number' || !Number.isFinite(n) || n < p.min! || n > p.max! || (integer && !Number.isInteger(n))) {
    throw new Error(`${p.label}须为 ${p.min}–${p.max}${p.unit ?? ''} 的${integer ? '整数' : '有限数值'}`);
  }
  return n;
}

export function buildRowWeeding(v: Record<string, unknown>, seed: number): BuiltScenario {
  validateSeed(seed);
  const W = value(v, 'field_w'); const H = value(v, 'field_h');
  const spacing = value(v, 'row_spacing'); const rows = value(v, 'rows_per_pass', true);
  const angle = value(v, 'row_angle_deg'); const head = value(v, 'headland_width');
  const turn = value(v, 'turn_radius'); const implement = value(v, 'implement_width');
  const swath = Math.round(spacing * rows * 1000) / 1000;
  if (swath < 0.5 || swath > 12) throw new Error(`幅宽 ${swath} m（每趟行数 × 行距）须在 0.5–12 m 之间，这是覆盖求解器接受的范围`);
  if (implement > swath) throw new Error(`机具宽度 ${implement} m 超过幅宽 ${swath} m，相邻趟会重叠压行`);
  if (head < turn) throw new Error(`地头宽度 ${head} m 小于最小转弯半径 ${turn} m，掉头会压到作物行`);
  if (Math.min(W, H) <= 2 * head) throw new Error('地头宽度过大，内缩作业区为空');
  if ((W * H) / (swath * swath) > 200000) throw new Error('田块相对幅宽过大，请增大每趟行数或缩小田块');
  const inset = Math.max(head / 2, implement / 2 + 0.01);
  const instance = {
    field: [[0, 0], [W, 0], [W, H], [0, H]],
    headland_width: head, swath_width: swath, turn_radius: turn, robot_width: implement,
    start: [inset, inset], route_pattern: 'boustrophedon', angle_deg: angle,
  };
  const rowsInField = Math.floor((angle === 0 ? H : Math.min(W, H)) / spacing);
  return {
    id: `agri-row-weeding-${Date.now().toString(36)}-${seed}`,
    title: '行间机械除草',
    summary: `${W}×${H} m · 行距 ${spacing} m × ${rows} 行/趟 = 幅宽 ${swath} m · 行向 ${angle}°`,
    problem_class: 'coverage',
    frame: { kind: 'field', units: 'm', crs: 'local' },
    cost_unit: 'm',
    provenance: { generator: 'urp.studio.agri', seed, pattern: 'row-weeding-coverage',
      note: `整行服务以固定方向区域覆盖代理：幅宽按行距对齐，约 ${rowsInField} 行；coverage_fraction 为全田占比，working_area_fraction 为内缩作业区口径，地头单列；趟相位未锁定行中线，验算只核对面积覆盖率。` },
    sim: { supported: false, world: null, spawn: null, reason: 'coverage 问题类不在田间仿真准入范围' },
    instance,
    editable: { params: PARAMS, values: { ...v } },
    layers: { boundary: instance.field, obstacles: [] },
  };
}

export const ROW_WEEDING: AgriTaskPattern = {
  id: 'row-weeding-coverage', name: '行间机械除草（固定行向覆盖近似）',
  environments: ['open_field'], operation: 'weeding', serviceObject: 'row_set',
  vehicleClasses: ['tractor_implement'], planningProblem: 'row_service',
  proxyModel: { serviceObject: 'area', planningProblem: 'full_coverage',
    note: '以扫描方向固定为行向、幅宽等于整数倍行距的区域覆盖代理整行服务。' },
  summary: '大田中耕除草：中耕机沿作物行行进，一趟跨若干行，在地头掉头进入下一组行。',
  modeling: '服务对象是作物行集，不是随机点：幅宽由行距与每趟行数推导，扫描方向锁定为行向，地头宽度与转弯半径相互约束，机具宽度不得超过幅宽。',
  refs: ['bochtis2008', 'oksanen2009', 'f2c2023'],
  approximation: '趟的横向相位未锁定到真实作物行中线，可能整体偏移最多半个行距；没有“不得横穿作物行”的通行约束；杂草密度不进入目标；独立验算只核对面积覆盖率，不核对逐行完成度。',
  capabilities: {
    defined: { level: 'partial', note: '行距、每趟行数、行向、地头、转弯半径、机具宽度齐备；缺行中线相位与杂草密度。' },
    editable: { level: 'yes', note: '全部参数经构建表单编辑，导入后 coverage 编辑器可改田块与地头。' },
    solvable: { level: 'partial', note: '提交为 coverage 问题，angle_deg 固定为行向；求解的是面积覆盖，不是逐行弧路径。' },
    checkable: { level: 'partial', note: '独立验算复算 coverage_fraction 与内缩区 working_area_fraction；不能核对每一行是否被完整服务。' },
    simulatable: { level: 'no', note: '近似模式且 coverage 问题类不在田间仿真准入范围。' },
    experimented: { level: 'no', note: '本模式新加入，尚无可追溯的提交、求解、验算、导出记录。' },
  },
  metrics: ['coverage_ratio', 'headland_area', 'turn_count', 'nonworking_distance', 'overlap_ratio'],
  runnable: { problem_class: 'coverage', params: PARAMS, build: buildRowWeeding },
  experimentFactors: [
    { name: '每趟行数', range: '1–24 行', basis: '中耕机常见 4、6、8、12 行规格；决定幅宽与趟数' },
    { name: '地头宽与转弯半径比', range: '1–3', basis: 'bochtis2008：地头空间决定可用掉头方式' },
    { name: '行向', range: '-90°–90°', basis: '与田块长边夹角影响趟数与转弯次数' },
  ],
};
