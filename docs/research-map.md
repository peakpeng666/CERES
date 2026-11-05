# 研究决策图

更新：2026-10-30。目标是在农业规划任务中找到值得验证的问题，而不是由旧代码、热门模型或框架名称决定研究路线。

**证据标记**：〔项目〕现有原始结果或工程证据；〔论文〕正式发表的一手文献；〔预印本〕作者研究稿；〔工程〕官方实现或文档；〔候选〕本项目待验证假设。外部方法的结果与 URP 实验分别记账。

## 任务族：先确定在解决什么

| 任务族 | 值得检验的问题〔候选〕 | 基线与一手入口 | 主要指标 / 成本 |
|---|---|---|---|
| 点到点几何路径 | 障碍密度、狭窄度和距离能否解释算法优势区间？ | Dijkstra/A*/Theta*；[Neural A*，ICML 2021](https://proceedings.mlr.press/v139/yonetani21a.html)〔论文〕 | 可行率、长度、展开节点、p95时间；低 |
| 车辆运动规划 | 先按车体与转弯约束筛选，是否减少无效推荐？ | [Nav2 Smac](https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/planners_plugins/smac/)、[OMPL](https://ompl.kavrakilab.org/core/planners.html)〔工程〕 | 首解时间、曲率/边界违规、预算内成本；中 |
| 单车完整覆盖 | 分区、田头、条带方向与顺序的组合选择是否优于固定流程？ | [Fields2Cover](https://fields2cover.github.io/)〔工程〕；[Fields2Benchmark](https://research.wur.nl/en/publications/fields2benchmark-an-open-source-benchmark-for-coverage-path-plann/)〔论文〕、[数据记录](https://zenodo.org/records/14524735) | 漏/重覆盖、转弯、非作业路程、工时；中低 |
| 田头与地形 | 最短距离方案何时因边界、曲率或坡度失去优势？ | [连续曲率田头，JFR](https://onlinelibrary.wiley.com/doi/abs/10.1002/rob.22489)〔论文〕；固定offset、Dubins/Reeds–Shepp | 覆盖、连续性、越界、能耗代理；中 |
| 多车覆盖与前后序作业 | 联合分区调度是否优于先分区后独立求解？ | [顺序依赖农业覆盖，Biosystems Engineering 2024](https://www.sciencedirect.com/science/article/pii/S1537511024000801)〔论文〕；优先级、小规模精确模型 | 工期、等待、前后序违规、负载；中高 |
| 路由、补给、异构分配 | 非欧代价与约束组合下，学习增强是否仍优于现代 OR？ | [PyVRP](https://pyvrp.org/)〔工程〕；[RouteFinder](https://github.com/ai4co/routefinder)、[RL4CO](https://github.com/ai4co/rl4co)〔论文/工程〕 | 可行率、gap、延误、补给次数；中 |
| 时空通行与动态重规划 | 窄道、变化地图和移动障碍分别需要怎样的协调与增量决策？ | [D* Lite](https://www.cs.cmu.edu/afs/cs/Web/People/motionplanning/papers/sbp_papers/integrated3/koenig_dstarlite_aaai02b.pdf)、[SIPP](https://www.cs.cmu.edu/~maxim/files/sipp_icra11.pdf)〔论文〕；[MPPI](https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/controller_plugins/mppi_controller/configuring_mppic/)〔工程〕 | 冲突、死锁、恢复、尾延迟；中高 |

覆盖数据提供几何，不自动提供完整作业约束；应另记车辆宽度、作业幅宽、转弯半径、起终位置、服务规则与参数来源。数据条款以具体版本的记录为准，代码许可与数据许可分开核对。

## 方法族：什么条件下值得进入

| 方法 | 进入条件与研究问题〔候选〕 | 对照与代表证据 |
|---|---|---|
| 实例/状态感知选择 | 候选在不同实例上确有互补；特征提取成本低于选择收益 | 固定最佳、规则、随机、简单模型与oracle上界；[现有选择器结果](../src/research/README.md)〔项目〕 |
| 学习启发式与局部改进 | 能明确定位搜索瓶颈，并与同搜索框架比较 | 学习/非学习消融；[Learning to Delegate，NeurIPS 2021](https://proceedings.neurips.cc/paper_files/paper/2021/hash/dc9fa5f217a1e57b8a6adeb065560b38-Abstract.html)〔论文〕 |
| 预训练组合优化 | 有跨规模、跨约束或非欧代价适配需求 | 现代OR、小选择器、从零训练；[RouteFinder](https://github.com/ai4co/routefinder)、[RL4CO](https://github.com/ai4co/rl4co)〔论文/工程〕 |
| 模仿/RL/课程 | 有可信转移、专家轨迹或长期耦合；静态选择先比较监督方法 | BC、规则、随机课程、相同交互预算；[DAgger](https://proceedings.mlr.press/v15/ross11a)、[PLR](https://proceedings.mlr.press/v139/jiang21b)〔论文〕 |
| 决策聚焦学习 | 行驶时间、能耗或需求预测真正影响优化决策 | 比较预测误差与最终决策regret；[决策聚焦排序，ICML 2022](https://proceedings.mlr.press/v162/mandi22a/mandi22a.pdf)〔论文〕 |
| 世界模型/扩散 | 未知动力学或约束生成有明确瓶颈；已有可靠交互数据或可用已知模型 | 已知模型MPC、残差模型、直接优化；[DreamerV3](https://www.nature.com/articles/s41586-025-08744-2)〔论文〕、[MD-COAS](https://arxiv.org/abs/2607.14455)〔预印本〕 |
| 启发式生成与进化 | 有可冻结的代码接口、独立检查器和可承受的评估成本 | 独立LLM采样、强人工先验、[EoH](https://proceedings.mlr.press/v235/liu24bs.html)、[ReEvo](https://papers.nips.cc/paper_files/paper/2024/hash/4ced59d480e07d290b6f29fc8798f195-Abstract-Conference.html)〔论文〕 |
| 预算感知演化/跨任务迁移 | 父代、算子、模型或保真度选择具有可测收益空间 | 同模型同总预算；[ShinkaEvolve官方项目](https://github.com/SakanaAI/ShinkaEvolve)〔工程/作者确认ICLR 2026〕、[MTHS，ICML 2026](https://proceedings.mlr.press/v306/liu26ge.html)〔论文〕 |
| 科研 Agent 与证据记忆 | 下一实验选择、失败修复或接续确有人工负担 | 固定流程、规则调度、单Agent与结构化反馈消融；[MLAgentBench](https://proceedings.mlr.press/v235/huang24y.html)〔论文〕、[HeuriGym官方项目](https://github.com/cornell-zhang/heurigym)〔工程/作者确认ICLR 2026〕 |
| 后训练/测试时学习 | 已有可信轨迹、适用模型、计算预算与训练条件 | 同总预算纯推理搜索、检索、监督适配；[ThetaEvolve，ICML 2026](https://proceedings.mlr.press/v306/wang26ha.html)〔论文〕 |

## 对现有证据的判断

- 〔项目〕CVRP 主批次有 4,505 项终态，53问题来自51底本；这是运行规模，不是4,505个独立场景。[重审与证明等级](../protocol/audits/20261017/README.md)决定哪些比较可用。
- 〔项目〕现有 train60/cal30/test30 选择器实验中，测试集 PyVRP 为27胜3平、OR-Tools严格更优0次。当前候选的互补性不足，优先检查任务分布和候选差异，再决定选择模型复杂度。
- 〔项目〕Agent 工具链和离线 evolve 已具工程基础。[整合记录](../setup/2026-10-30/engineering/REPORT.md)与[离线边界](../src/lab/urp_lab/evolve/INTEGRATION.md)分别对应工程能力和研究候选；真实模型任务质量需要独立评价。
- 〔候选〕生成、进化、推荐分别设问题与指标；多Agent是待比较机制，不以角色数量或对话长度计贡献。

## 共用实验决策单

每条候选路线进入实验前填一页：

1. **假设**：改善什么、为何可能有效、什么结果会否定它。
2. **任务和数据**：独立田块/base、派生variant、种子与数据谱系；按来源分组划分开发、验证、最终测试。
3. **约束与评价**：可行性检查器、主指标、全任务分母、失败分类；oracle只作事后上界。
4. **对照**：固定强基线、简单机制与关键消融；拆开模型升级、额外预算、先验和方法贡献。
5. **预算**：求解时间、端到端延迟、LLM调用/token、训练与搜索成本；资源配置随记录保存。
6. **证据**：输入输出hash、代码/环境/提示版本、原始结果、退出状态、独立验算、分析版本；图表可回到单次决策。
7. **继续标准**：增益、稳定性、泛化与成本达到何种门槛；上界不足、失败密度过高或预算不合适时调整问题。

工程工作以恢复成功率、预算控制、证据覆盖和复核效率验收；学术工作以同预算强基线、消融、独立测试和失效条件验收。两者共同服务于 [五项交付](requirements.md)，无需先锁定唯一路线。

[返回当前索引](current-index.md)
