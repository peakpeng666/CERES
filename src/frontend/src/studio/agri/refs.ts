/**
 * 文献依据登记。
 *
 * 纪律：
 * - 外部文献必须有 DOI/URL；内部证据必须有准确 repo_note 与固定提交 URL；
 * - supports 写该文献具体支撑的设计结论，不能凭标题推断能力；
 * - DOI 未经逐条核对的条目 status 标 '题录待复核'，不得补一个猜测的 DOI。
 */

export interface RefEntry {
  id: string;
  title: string;
  /** 论文为发表年份；动态文档/仓库源码为核对年份（checked_on）。 */
  year: number;
  authors?: string;
  venue?: string;
  doi?: string;
  url?: string;
  /** 内部资料（契约文档/仓库记录）用 repo_note 而非编造外部出处。 */
  repo_note?: string;
  /** 该引用支撑的具体设计结论。 */
  supports: string;
  /** verified 仅代表题录/源码出处已核对，不代表模板实验通过。 */
  status: 'verified' | '题录待复核';
  kind?: 'paper' | 'documentation' | 'repository';
  checked_on?: string;
}

export const REFS: RefEntry[] = [
  {
    id: 'f2c2023',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Fields2Cover: An open-source coverage path planning library for unmanned agricultural vehicles',
    year: 2023, authors: 'Mier, Valente, de Bruin', venue: 'IEEE Robotics and Automation Letters',
    doi: '10.1109/LRA.2023.3248439', url: 'https://arxiv.org/abs/2210.07838',
    supports: '支持地头→布行→行排序→路径的模块化覆盖流程；不作为 URP 适配器实现或实验通过的证据。',
    status: 'verified',
  },
  {
    id: 'dubins1957',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'On curves of minimal length with a constraint on average curvature, and with prescribed initial and terminal positions and tangents',
    year: 1957, authors: 'L. E. Dubins', venue: 'American Journal of Mathematics',
    doi: '10.2307/2372560',
    supports: '最小转弯半径 → 曲率约束路径族（固定端点姿态时最优长度随最小半径不减）；不可倒车车辆转场的求解语义。',
    status: 'verified',
  },
  {
    id: 'galceran2013',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'A survey on coverage path planning for robotics',
    year: 2013, authors: 'Galceran, Carreras', venue: 'Robotics and Autonomous Systems',
    doi: '10.1016/j.robot.2013.09.004',
    supports: '覆盖路径规划的分解方法分类（梯形/Boustrophedon 单元分解）；覆盖率评价需明确分母口径。',
    status: 'verified',
  },
  {
    id: 'oksanen2009',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Coverage path planning algorithms for agricultural field machines',
    year: 2009, authors: 'Oksanen, Visala', venue: 'Journal of Field Robotics',
    doi: '10.1002/rob.20300',
    supports: '田块覆盖的行排序与地头转弯连接是独立优化层；转弯成本不可从直线里程臆造。',
    status: 'verified',
  },
  {
    id: 'bochtis2008',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Minimising the non-working distance travelled by machines operating in a headland field pattern',
    year: 2008, authors: 'Bochtis, Vougioukas', venue: 'Biosystems Engineering',
    doi: '10.1016/j.biosystemseng.2008.06.008',
    supports: '地头非作业里程是可独立优化的目标；转弯类型与地头空间的匹配关系。',
    status: 'verified',
  },
  {
    id: 'bochtis2009',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'The Vehicle Routing Problem in Field Logistics Part I',
    year: 2009, authors: 'Bochtis, Sørensen', venue: 'Biosystems Engineering',
    doi: '10.1016/j.biosystemseng.2009.09.003',
    supports: '田间物流与作业条带的 VRP 建模背景；不证明中心点访问等价于完整行服务，也不是 CARP 求解器实现证据。',
    status: 'verified',
  },
  {
    id: 'seyyedhasani2017',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Using the Vehicle Routing Problem to reduce field completion times with multiple machines',
    year: 2017, authors: 'Seyyedhasani, Dvorak', venue: 'Computers and Electronics in Agriculture',
    supports: '支持多机田间路径分配与全田完成时间目标；不直接验证果园容量集运或冷链时间窗模型。',
    doi: '10.1016/j.compag.2016.11.010',
    status: 'verified',
  },
  {
    id: 'conesa2016',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Route planning for agricultural tasks: A general approach for fleets of autonomous vehicles in site-specific herbicide applications',
    year: 2016, venue: 'Computers and Electronics in Agriculture',
    supports: '支持选择性除草作业中的车队、补给及多目标路径规划；不证明无序代表点可替代连续行喷施。',
    authors: 'Conesa-Muñoz, Bengochea-Guevara, Andujar, Ribeiro',
    doi: '10.1016/j.compag.2016.06.012',
    status: 'verified',
  },
  {
    id: 'jensen2015',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Coverage planning for capacitated field operations, part II: Optimisation',
    year: 2015,
    supports: '容量约束田间作业的覆盖优化背景；不作为动态会合或周期饲喂求解已实现的证据。',
    authors: 'Jensen, Bochtis, Sørensen',
    venue: 'Biosystems Engineering',
    doi: '10.1016/j.biosystemseng.2015.07.002',
    status: 'verified',
  },
  {
    id: 'zhou2014',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Agricultural operations planning in fields with multiple obstacle areas',
    year: 2014,
    supports: '支持含多个障碍区域的农业覆盖规划研究背景；本仓具体分解算法与几何验算须另核查。',
    authors: 'Zhou, Jensen, Sørensen, Busato, Bochtis',
    venue: 'Computers and Electronics in Agriculture',
    doi: '10.1016/j.compag.2014.08.013',
    status: 'verified',
  },
  {
    id: 'plessen2019',
    kind: 'paper', checked_on: '2026-10-22',
    title: 'Optimal in-field routing for full and partial field coverage with arbitrary non-convex fields and multiple obstacle areas',
    year: 2019,
    supports: '支持给定子集的部分覆盖及非凸含障田块路由；不直接支持按收益选斑块、在线事件流或动态重规划。',
    authors: 'Mogens Graf Plessen',
    venue: 'Biosystems Engineering',
    doi: '10.1016/j.biosystemseng.2019.08.001',
    url: 'https://arxiv.org/abs/1906.04264',
    status: 'verified',
  },
  {
    id: 'pyvrp_docs',
    kind: 'documentation', checked_on: '2026-10-22',
    title: 'PyVRP documentation — vehicle routing with heterogeneous fleets',
    year: 2026,
    url: 'https://pyvrp.org/',
    supports: '官方文档列有异构车队支持；软件能力不等于 URP 适配器支持。文档为动态版本，复现须固定实际依赖版本。',
    status: 'verified',
  },
  {
    id: 'ortools_docs',
    kind: 'documentation', checked_on: '2026-10-22',
    title: 'OR-Tools routing library documentation',
    year: 2026,
    url: 'https://developers.google.com/optimization/routing/vrp',
    supports: 'OR-Tools 车队调度使用路线跨度与里程加权目标；以总里程为搜索目标时不直接均衡各车工作量。',
    status: 'verified',
  },
  {
    id: 'urp_fleet144',
    kind: 'repository', checked_on: '2026-10-22',
    url: 'https://github.com/peakpeng666/URP/blob/85b085fcdd9429deeb8c11708af1851384842893/src/lab/CONTRACT.md#L256',
    title: 'URP fleet_routing 契约文档（多趟/容量/卸载点语义）',
    year: 2026,
    repo_note: 'src/lab/CONTRACT.md §5.1：fleet_routing 点需求、容量、卸载与通行契约；这是接口依据而非运行回执。',
    supports: '果园采收集运等点集需求模板直接落在契约字段内，无需新接口。',
    status: 'verified',
  },
  {
    id: 'urp_p510',
    kind: 'repository', checked_on: '2026-10-22',
    url: 'https://github.com/peakpeng666/URP/blob/85b085fcdd9429deeb8c11708af1851384842893/src/lab/urp_lab/solvers/fleet.py#L387',
    title: 'URP fleet.check 验算源码（保留旧引用 ID，非 P510 实验记录）',
    year: 2026,
    repo_note: 'src/lab/urp_lab/solvers/fleet.py::check；旧 ID urp_p510 仅作兼容，P510 论文笔记不是运行证据。',
    supports: '点集需求代理模型的验算实现出处；不证明某个模板已运行通过，也不检查代理未表达的农业约束。',
    status: 'verified',
  },
  {
    id: 'urp_contract_sim',
    kind: 'repository', checked_on: '2026-10-22',
    url: 'https://github.com/peakpeng666/URP/blob/85b085fcdd9429deeb8c11708af1851384842893/src/lab/urp_lab/assets/bindings.py#L90',
    title: 'URP 执行准入源码与契约（非执行成功记录）',
    year: 2026,
    repo_note: 'src/lab/urp_lab/assets/bindings.py::field_vehicle 与 src/lab/CONTRACT.md：异构/曲率类别执行拒绝；具体模板准入仍由当前执行入口检查。',
    supports: '执行模型边界的源码依据，不代表所有同构差速实例或覆盖模板自动获准执行。',
    status: 'verified',
  },
];

export const REF_MAP: ReadonlyMap<string, RefEntry> = new Map(REFS.map((r) => [r.id, r]));
