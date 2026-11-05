/**
 * 近似模式（8 个）：可提交求解，但近似点必须显式声明（approximation 字段）。
 * 近似模式一律 simulatable: no——差距未消除前，执行结果不代表该作业。
 */
import type { AgriTaskPattern, BuilderParam } from '../taxonomy';
import { buildFleetInstance, P } from './runnable';

const SIM_NO = '近似模式不进入执行：近似差距未消除前，执行结果不代表该作业。';
const CHECK = 'fleet.check 独立复算容量、趟次与作业位置。';

function approxCap(exp: string, solveNote: string) {
  return {
    defined: { level: 'partial' as const, note: '仅代理点访问语义可表达；目标农业模型的缺失约束见 approximation。' },
    editable: { level: 'yes' as const, note: '构建参数与代理几何可编辑；缺失的农业约束不可编辑。' },
    solvable: { level: 'partial' as const, note: solveNote },
    checkable: { level: 'partial' as const, note: `${CHECK} 仅验算平面点访问代理，不含 approximation 声明的缺失约束。` },
    simulatable: { level: 'no' as const, note: SIM_NO },
    experimented: { level: 'no' as const, note: exp },
  };
}

const NOT_RUN = '未附这些模板的可追溯提交→求解→验算→导出记录；不预先声称实验通过。';

interface ApproxSpec {
  id: string; name: string;
  envs: AgriTaskPattern['environments'];
  op: AgriTaskPattern['operation'];
  obj: AgriTaskPattern['serviceObject'];
  prob: AgriTaskPattern['planningProblem'];
  summary: string; modeling: string; approximation: string;
  refs: string[]; metrics: string[];
  size: [number, number]; tasks: [number, number]; fleet: [number, number, number];
  robot: number; word: string; crop: string;
  distribution: 'rows' | 'uniform'; margin: number;
  solveNote: string;
}

function makeApprox(s: ApproxSpec): AgriTaskPattern {
  const params: BuilderParam[] = [
    ...P.size(...s.size), ...P.tasks(...s.tasks), ...P.fleet(...s.fleet), ...P.robot(s.robot),
    ...P.depot('sw'), ...P.objective('makespan'), ...P.refill(true),
  ];
  return {
    id: s.id, name: s.name,
    environments: s.envs, operation: s.op, serviceObject: s.obj,
    vehicleClasses: ['diff_drive'], planningProblem: s.prob,
    proxyModel: {
      serviceObject: 'point_set',
      planningProblem: s.prob === 'multi_trip_refill' ? 'multi_trip_refill' : s.prob === 'cvrp' || s.prob === 'multi_field_alloc' ? 'cvrp' : 'multi_visit',
      note: '实际提交同构平面点访问代理，自由通行；diff_drive 是代理车类，不代表无人机/船艇/轨道车物理适配。',
    },
    summary: s.summary, modeling: s.modeling, approximation: s.approximation,
    refs: s.refs, metrics: s.metrics,
    capabilities: approxCap(NOT_RUN, s.solveNote),
    runnable: {
      problem_class: 'fleet_routing',
      params,
      build: (v, seed) => {
        const inst = buildFleetInstance(v, seed, {
          word: s.word, crop: s.crop, distribution: s.distribution, travel: 'free',
          plots: { cols: 1, rows: 1, margin: s.margin },
          unload: v.refill_point === false ? 0 : 1,
        });
        return {
          id: `agri-${s.id}-${Date.now().toString(36)}-${seed}`,
          title: s.name,
          summary: `${inst.vehicles.count} 台车 · ${inst.tasks.length} 个${s.word} · 近似模式`,
          problem_class: 'fleet_routing',
          frame: { kind: 'field', units: 'm', crs: 'local' },
          cost_unit: inst.objective === 'makespan' ? 's' : 'm',
          provenance: { generator: 'urp.studio.agri', seed, pattern: s.id, note: `近似：${s.approximation}` },
          sim: { supported: false, world: null, spawn: null, reason: '近似模式不进入执行' },
          instance: inst as unknown as Record<string, unknown>,
          editable: { params, values: { ...v } },
          layers: { boundary: inst.boundary, plots: inst.plots.map((p) => p.polygon), obstacles: inst.obstacles.map((o) => o.polygon), rows: [], depot: inst.depot, unload_points: inst.unload_points ?? [] },
        };
      },
    },
  };
}

export const APPROX_PATTERNS: AgriTaskPattern[] = [
  makeApprox({
    id: 'aerial-survey-plane', name: '无人机田区航测（平面近似）',
    envs: ['open_field', 'aerial'], op: 'monitoring', obj: 'point_set', prob: 'multi_visit',
    summary: '航测点/航段在平面投影上排序访问。',
    modeling: '三维航测平面化：高度、姿态、能耗不建模。',
    approximation: '无人机三维运动与能耗 → 平面点访问；能耗模型缺失见提案「无人机能耗覆盖航测」。',
    refs: ['urp_p510'], metrics: ['service_completion', 'total_distance'],
    size: [200, 140], tasks: [30, 1], fleet: [1, 1000, 2.5], robot: 0.5,
    word: '航测点', crop: '田区', distribution: 'uniform', margin: 10,
    solveNote: '求解为平面多点访问；高度与能耗不在验算范围。',
  }),
  makeApprox({
    id: 'paddy-transport', name: '水田秧苗/稻谷转运（无下陷近似）',
    envs: ['paddy'], op: 'material_handling', obj: 'point_set', prob: 'cvrp',
    summary: '水田田面点集转运；下陷风险不建模。',
    modeling: '点需求 + 容量；泥泞下陷区无地形字段。',
    approximation: '无下陷近似：水田通过性随含水率变化，本模式按硬地面处理。',
    refs: ['urp_p510'], metrics: ['service_completion', 'trips', 'makespan'],
    size: [160, 110], tasks: [24, 30], fleet: [2, 240, 0.6], robot: 0.7,
    word: '转运点', crop: '水稻', distribution: 'uniform', margin: 8,
    solveNote: '仅求解代理点访问；通过性风险不在验算范围。',
  }),
  makeApprox({
    id: 'vineyard-spray', name: '葡萄园植保（行间近似）',
    envs: ['vineyard'], op: 'spraying_blanket', obj: 'row_set', prob: 'multi_trip_refill',
    summary: '窄行距葡萄园喷施：行服务近似为行上点集 + 药箱补给。',
    modeling: '随机行上点访问代理；不保证每行有代表点，不表达整行服务或连续沉积。',
    approximation: '行服务 → 随机行上点集，自由平面通行；不约束窄行通道、整行服务或转弯空间。',
    refs: ['conesa2016', 'bochtis2009'], metrics: ['service_completion', 'refill_overhead', 'makespan'],
    size: [150, 100], tasks: [40, 15], fleet: [2, 300, 0.6], robot: 0.5,
    word: '喷施点', crop: '葡萄', distribution: 'rows', margin: 6,
    solveNote: '仅求解代理点访问，不保证连续行作业可行；连续行喷施语义见提案「行服务 CARP」。',
  }),
  makeApprox({
    id: 'barn-feed-pushing', name: '牛舍推料（通道近似）',
    envs: ['livestock_barn'], op: 'feed_pushing', obj: 'directed_segment', prob: 'row_service',
    summary: '推料需求的无序点访问代理，不是沿槽定向推料路径。',
    modeling: '饲槽作业暂以随机行上点访问代理；点可任意重排，未保留线段方向或访问顺序。',
    approximation: '带方向线段 → 无序点集；方向、连续推料、通道约束和推料力学均未表达。',
    refs: ['bochtis2009'], metrics: ['service_completion', 'total_distance'],
    size: [60, 30], tasks: [18, 1], fleet: [1, 1000, 0.5], robot: 0.4,
    word: '推料段', crop: '饲槽', distribution: 'rows', margin: 4,
    solveNote: '求解为多点访问；未实现线段方向约束。',
  }),
  makeApprox({
    id: 'nursery-pot-collection', name: '苗圃盆苗集运（无配对近似）',
    envs: ['nursery'], op: 'material_handling', obj: 'point_set', prob: 'cvrp',
    summary: '盆苗从苗床位收集到集货区；取送配对退化为需求点。',
    modeling: '取送的“取”端为需求点，“送”端统一为集货区（卸载点）。',
    approximation: '取送配对 → 需求点 + 统一卸载点；真实的成对取送见提案「农场仓储出入库取送」。',
    refs: ['urp_fleet144'], metrics: ['service_completion', 'trips', 'makespan'],
    size: [120, 80], tasks: [32, 5], fleet: [2, 40, 0.7], robot: 0.5,
    word: '盆苗位', crop: '苗木', distribution: 'rows', margin: 6,
    solveNote: '仅求解代理点访问；配对语义缺失。',
  }),
  makeApprox({
    id: 'terrace-transport', name: '梯田集运（无坡度近似）',
    envs: ['terrace'], op: 'material_handling', obj: 'multi_region', prob: 'multi_field_alloc',
    summary: '梯田台面间集运：仅保留单平面需求点，不生成台面分区或连接坡道。',
    modeling: '单矩形点访问代理；没有多台面拓扑、坡道连接或地块分配语义。',
    approximation: '单平面代理：多台面拓扑、坡道通过性、地块分配与侧翻风险均不表达，见提案「坡地与梯田风险约束路径」。',
    refs: ['urp_p510'], metrics: ['service_completion', 'makespan'],
    size: [180, 120], tasks: [24, 40], fleet: [2, 300, 0.6], robot: 0.6,
    word: '集运点', crop: '梯田作物', distribution: 'uniform', margin: 10,
    solveNote: '仅求解代理点访问；坡度风险不在验算范围。',
  }),
  makeApprox({
    id: 'aqua-feeding', name: '水产养殖投喂巡检（水面近似）',
    envs: ['aquaculture'], op: 'feeding', obj: 'point_set', prob: 'multi_visit',
    summary: '塘面投喂点巡回：船艇水动力学平面化为点访问。',
    modeling: '投喂需求以合成点集表达；船艇回转/水流不建模。',
    approximation: '水面船艇 → 平面点访问；闸口转场连接见提案「多塘水产转场」。',
    refs: ['urp_p510'], metrics: ['service_completion', 'total_distance'],
    size: [200, 120], tasks: [20, 10], fleet: [1, 200, 0.8], robot: 0.8,
    word: '投喂点', crop: '鱼塘', distribution: 'uniform', margin: 10,
    solveNote: '求解为平面多点访问；水动力学不在验算范围。',
  }),
  makeApprox({
    id: 'greenhouse-rail-inspection', name: '温室轨道巡检（自由点访问代理）',
    envs: ['greenhouse'], op: 'plant_inspection', obj: 'directed_segment', prob: 'row_service',
    summary: '逐株检查需求的自由平面点访问代理，不是轨道巡检路径。',
    modeling: '随机行上点可任意重排；没有一维轨道、方向或顺序约束。',
    approximation: '轨道任务 → 无序平面点集；一维通行、方向、顺序、换轨与检查姿态均未表达。',
    refs: ['bochtis2009'], metrics: ['service_completion', 'total_distance'],
    size: [60, 40], tasks: [24, 1], fleet: [1, 1000, 0.5], robot: 0.3,
    word: '检查位', crop: '栽培床', distribution: 'rows', margin: 4,
    solveNote: '求解为多点访问；未实现轨道约束。',
  }),
];
