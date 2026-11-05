/**
 * 农业任务分类体系（taxonomy）。
 *
 * 设计原则（来自任务书）：
 * - 凡农业生产中有明确路径规划问题的场景与任务都纳入版图，不以现有求解器支持范围为边界；
 * - 分类按四个正交轴组合：生产环境 × 农业作业 × 服务对象 × 规划问题；
 * - 无农业语义的组合不留模式占位，矩阵中如实留白（—）。
 *
 * 本文件只含分类学常量与类型，不含构建逻辑（构建器见 patterns/）。
 */

/** 六维能力声明：每个模式按轴分别标注；不是测试结果，experimented 仅凭可追溯运行证据。 */
export type CapLevel = 'yes' | 'partial' | 'no';

export interface CapNote {
  level: CapLevel;
  /** 判定依据；yes 必须有可核查的契约/代码/运行记录支撑。 */
  note: string;
}

export interface AgriCapabilities {
  /** 任务定义完整性：作业语义要素是否齐备（行方向、幅宽、起终点、容量、时间窗……）。 */
  defined: CapNote;
  /** 可编辑性：构建后能否在既有工作台逐字段修改。 */
  editable: CapNote;
  /** 求解支持：后端求解器能否处理该实例。 */
  solvable: CapNote;
  /** 独立验算：是否有独立于求解器的几何/容量/可行性验算。 */
  checkable: CapNote;
  /** 仿真适配：是否满足执行入口的模型/数量/资产等准入条件；规划可行不代表可执行。 */
  simulatable: CapNote;
  /** 实验验证：是否有真实运行记录（提交→求解→验算→导出）。 */
  experimented: CapNote;
}

/** 生产环境（14 类）。 */
export interface Environment {
  id: string;
  name: string;
  /** 该环境典型的空间结构（行/区/通道/水面/空域……）。 */
  spatial: string;
}

export const ENVIRONMENTS: Environment[] = [
  { id: 'open_field', name: '大田与旱作地', spatial: '大面积规则/不规则田块 + 机耕道' },
  { id: 'paddy', name: '水田', spatial: '田块 + 田埂，泥泞下陷风险' },
  { id: 'orchard', name: '果园', spatial: '作物行 + 行间通道 + 地头' },
  { id: 'vineyard', name: '葡萄园', spatial: '窄行距行架 + 端部转弯区' },
  { id: 'tea_garden', name: '茶园', spatial: '坡地条垄 + 等高行' },
  { id: 'greenhouse', name: '设施温室', spatial: '室内栽培床 + 窄通道/轨道' },
  { id: 'nursery', name: '苗圃', spatial: '苗床/盆阵 + 集货区' },
  { id: 'terrace', name: '丘陵梯田', spatial: '梯台 + 连接坡道' },
  { id: 'pasture', name: '牧草地与牧场', spatial: '开阔草地 + 围栏 + 饮水点' },
  { id: 'livestock_barn', name: '畜禽养殖设施', spatial: '舍内通道 + 饲槽线 + 栏位' },
  { id: 'farm_depot', name: '农场仓储与装卸区', spatial: '库房 + 月台 + 堆场' },
  { id: 'multi_field', name: '多田块农场及连接路网', spatial: '田块群 + 公共道路网' },
  { id: 'aquaculture', name: '水产养殖水面', spatial: '塘面 + 闸口 + 投喂点' },
  { id: 'aerial', name: '农业空域（无人机）', spatial: '低空三维，含起降点与禁飞区' },
];

/** 农业作业（30 项；其中 13 项当前无任何模式，字面值即空白清单）。 */
export interface Operation {
  id: string;
  name: string;
  /** 该作业路径规划的核心服务对象。 */
  typicalObjects: string[];
}

export const OPERATIONS: Operation[] = [
  { id: 'tillage', name: '耕整地', typicalObjects: ['area', 'row_set'] },
  { id: 'seeding', name: '播种', typicalObjects: ['area'] },
  { id: 'transplanting', name: '移栽', typicalObjects: ['row_set'] },
  { id: 'intertillage', name: '中耕', typicalObjects: ['row_set'] },
  { id: 'spraying_blanket', name: '植保（全田喷施）', typicalObjects: ['area', 'row_set'] },
  { id: 'spraying_selective', name: '植保（选择性/斑块）', typicalObjects: ['multi_region', 'point_set'] },
  { id: 'spraying_spot', name: '植保（逐株定点）', typicalObjects: ['point_set'] },
  { id: 'weeding', name: '除草（机械）', typicalObjects: ['row_set'] },
  { id: 'fertilizing', name: '施肥（变量追肥）', typicalObjects: ['row_set', 'area'] },
  { id: 'sampling', name: '土壤及作物采样', typicalObjects: ['point_set'] },
  { id: 'monitoring', name: '环境监测', typicalObjects: ['point_set', 'area'] },
  { id: 'plant_inspection', name: '逐株检查', typicalObjects: ['directed_segment'] },
  { id: 'irrigation_check', name: '灌溉设施巡检', typicalObjects: ['point_set'] },
  { id: 'picking', name: '采收', typicalObjects: ['point_set'] },
  { id: 'pruning', name: '果园修剪', typicalObjects: ['row_set', 'point_set'] },
  { id: 'grain_cart', name: '接粮与运输', typicalObjects: ['point_set', 'composite'] },
  { id: 'combine_harvest', name: '联合收获', typicalObjects: ['area', 'composite'] },
  { id: 'bale_collection', name: '草捆收集', typicalObjects: ['point_set'] },
  { id: 'straw_handling', name: '秸秆处理', typicalObjects: ['area', 'point_set'] },
  { id: 'mowing', name: '割草', typicalObjects: ['area'] },
  { id: 'raking', name: '搂草', typicalObjects: ['row_set'] },
  { id: 'baling', name: '打捆', typicalObjects: ['row_set'] },
  { id: 'material_handling', name: '苗木及物料搬运', typicalObjects: ['point_set', 'multi_region'] },
  { id: 'feeding', name: '饲喂', typicalObjects: ['point_set'] },
  { id: 'feed_pushing', name: '推料', typicalObjects: ['directed_segment'] },
  { id: 'barn_cleaning', name: '圈舍清扫', typicalObjects: ['area', 'row_set'] },
  { id: 'refill_supply', name: '补水补药（站点补给）', typicalObjects: ['point_set'] },
  { id: 'charging', name: '充电（充电桩调度）', typicalObjects: ['point_set'] },
  { id: 'unloading', name: '卸载（港池/粮库排队）', typicalObjects: ['point_set'] },
  { id: 'transfer', name: '入库与转场', typicalObjects: ['point_set', 'multi_region', 'composite'] },
];

/** 服务对象类型。 */
export const SERVICE_OBJECTS = [
  { id: 'point_set', name: '点集（可带需求）' },
  { id: 'row_set', name: '作业行集' },
  { id: 'directed_segment', name: '线段（带方向）' },
  { id: 'area', name: '区域' },
  { id: 'multi_region', name: '多区域' },
  { id: 'composite', name: '复合对象（车—车/空地协同）' },
] as const;

/** 规划问题（13 类）。 */
export interface PlanningProblem {
  id: string;
  name: string;
  /** 对应的经典运筹/规划问题族。 */
  family: string;
}

export const PLANNING_PROBLEMS: PlanningProblem[] = [
  { id: 'point_to_point', name: '点到点通行', family: '最短路径（A*/Dijkstra/混合 A*）' },
  { id: 'multi_visit', name: '多点访问', family: 'TSP / 路径覆盖' },
  { id: 'cvrp', name: '容量车辆路径', family: 'CVRP' },
  { id: 'pickup_delivery', name: '取送与运输', family: 'PDP / 取送问题' },
  { id: 'row_service', name: '作物行/道路服务', family: 'CARP（弧路径）' },
  { id: 'full_coverage', name: '区域全覆盖', family: 'CPP（覆盖路径规划）' },
  { id: 'selective_coverage', name: '局部/选择性覆盖', family: '选择性覆盖 / 定向问题' },
  { id: 'mrta', name: '多机器人任务分配与路径协调', family: 'MRTA + 冲突消解' },
  { id: 'multi_field_alloc', name: '多地块作业分配', family: '分配 + 转场调度' },
  { id: 'multi_trip_refill', name: '多趟补给', family: '多趟 VRP + 补给决策' },
  { id: 'heterogeneous_coop', name: '异构车辆协作', family: '异构车队 VRP / 会合调度' },
  { id: 'time_window', name: '时间窗与先后关系', family: 'VRPTW / 时序约束' },
  { id: 'dynamic_replan', name: '动态任务、障碍与重规划', family: '在线重规划 / 滚动时域' },
];

/** 车辆/机具类别与运动学边界。 */
export const VEHICLE_CLASSES = [
  { id: 'diff_drive', name: '差速轮式', kinematics: '可原地转向；契约 sim 唯一执行模型（同质 ≤15 台）' },
  { id: 'ackermann', name: '阿克曼转向', kinematics: '曲率约束；求解可进 Dubins 路径族，执行端 422' },
  { id: 'tractor_implement', name: '拖拉机+牵引机具', kinematics: '铰接扫掠；仅提案，扫掠包络需专门模型' },
  { id: 'uav', name: '无人机', kinematics: '三维空域；无执行模型，能耗模型缺失' },
  { id: 'boat', name: '无人船艇', kinematics: '水面；无执行模型' },
  { id: 'rail', name: '轨道/输送', kinematics: '一维约束；借线段近似' },
] as const;

/** 评价指标（覆盖率类指标必须注明分母口径）。 */
export const METRICS = [
  { id: 'service_completion', name: '任务完成率', note: '已服务任务数 / 总任务数' },
  { id: 'makespan', name: '最晚完成时间', note: '匀速代理，不含交通排队' },
  { id: 'total_distance', name: '总里程', note: '工作段与非工作段分列' },
  { id: 'deadhead_distance', name: '空驶里程', note: '不含作业段' },
  { id: 'trips', name: '趟数', note: '回库/补给次数' },
  { id: 'capacity_utilization', name: '容量利用率', note: '实载 / 容量（按趟）' },
  { id: 'refill_overhead', name: '补给开销', note: '补给往返里程与时间占比' },
  { id: 'coverage_ratio', name: '覆盖率', note: '分母口径必须注明：全田 / 可作业区 / 内缩区；地头单列' },
  { id: 'overlap_ratio', name: '重喷/重叠率', note: '按幅宽×作业段长度计算，不用轨迹点数代替' },
  { id: 'missed_area', name: '漏作业面积', note: '内缩作业区内未覆盖面积' },
  { id: 'headland_area', name: '地头面积', note: '单列统计，不混入作业区' },
  { id: 'turn_count', name: '转弯次数', note: '按转弯类型分列' },
  { id: 'nonworking_distance', name: '非作业里程', note: '转场+地头转弯段' },
  { id: 'soil_compaction_risk', name: '土壤压实风险', note: '需轮载/接地比压模型；无模型时不得臆造' },
  { id: 'energy_use', name: '能耗', note: '需能耗模型；无模型时不得从路线长度臆造' },
  { id: 'slope_risk', name: '坡地风险', note: '需坡度/重心模型；仅提案' },
] as const;

export type EnvironmentId = (typeof ENVIRONMENTS)[number]['id'];
export type OperationId = (typeof OPERATIONS)[number]['id'];
export type ProblemId = (typeof PLANNING_PROBLEMS)[number]['id'];

/** 农业任务模式：分类轴 + 能力 + 构建器挂接点。 */
export interface AgriTaskPattern {
  id: string;
  name: string;
  environments: EnvironmentId[];
  operation: OperationId;
  /** 目录所描述的目标作业对象；代理实际对象见 proxyModel。 */
  serviceObject: (typeof SERVICE_OBJECTS)[number]['id'];
  vehicleClasses: string[];
  /** 目标规划问题，不表示现有适配器原生支持。 */
  planningProblem: ProblemId;
  /** 可提交近似模型实际表达的对象/问题。不得当作目标模型的可行性证明。 */
  proxyModel?: { serviceObject: (typeof SERVICE_OBJECTS)[number]['id']; planningProblem: ProblemId; note: string };
  summary: string;
  /** 建模纪律声明：该模式如何忠实表达作业语义（不用中心点代替整行等）。 */
  modeling: string;
  /** 引用文献 id（refs.ts），只挂支撑具体设计结论的条目。 */
  refs: string[];
  capabilities: AgriCapabilities;
  /** 若为近似模式，必须显式声明近似点与差距。 */
  approximation?: string;
  metrics: string[];
  /** 可运行或近似模式的构建器；提案模式没有。 */
  runnable?: {
    problem_class: string;
    params: BuilderParam[];
    build: (values: Record<string, unknown>, seed: number) => BuiltScenario;
  };
  /** 提案模式的接口草案与阻塞项。 */
  proposal?: {
    missing: string[];
    apiDraft: string;
    fixtures: string[];
  };
  experimentFactors?: { name: string; range: string; basis: string }[];
}

/** 构建参数（表单字段）。 */
export interface BuilderParam {
  key: string;
  label: string;
  unit?: string;
  type: 'number' | 'select' | 'boolean';
  default: number | string | boolean;
  min?: number;
  max?: number;
  options?: { value: string; label: string }[];
  help?: string;
}

/** 构建产物：与 urp_lab.compose 的输出契约镜像对齐。 */
export interface BuiltScenario {
  id: string;
  title: string;
  summary: string;
  problem_class: string;
  frame: { kind: 'field'; units: 'm'; crs: 'local' };
  cost_unit: string;
  provenance: { generator: 'urp.studio.agri'; seed: number; pattern: string; note: string };
  sim: { supported: false; world: null; spawn: null; reason: string };
  instance: Record<string, unknown>;
  editable: { params: BuilderParam[]; values: Record<string, unknown> };
  layers?: Record<string, unknown>;
}
