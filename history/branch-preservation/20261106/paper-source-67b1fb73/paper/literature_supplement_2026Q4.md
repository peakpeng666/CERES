# 外部补充调研摘要（2026-10-21，五路并行）

仓库内已有 565 篇精读笔记；本次调研**向外补充** 2024–2026 前沿与发表情报，验证并修正 `PAPER_PLAN.md` 的选题与假设。五路并行、独立检索（各路 ≥15 次搜索），原件存档于调研工作区（未随仓库公开），本文件为决策摘要。

## 五路分工与核心结论

### 路 1：农业机器人路径规划前沿（2024–2026）
- 经典优化/几何分解仍是落地主流；DRL 覆盖规划论文激增但多为网格玩具场景；LLM 规划无农业实证——**论文不蹭 DRL/LLM 热点**。
- **纯仿真可发 Q1**：Fields2Benchmark（Smart Agricultural Technology 2025，350 田块纯基准、无实机）为直接先例。
- **Fields2Benchmark 数据集（Zenodo，350 块荷/爱/立真实田块，源自 EuroCrops）与 Fields of The World（160 万田界、24 国）已公开** → 用作外部交叉验证；Fields2Cover/Fields2Benchmark 升级为强制基线。
- 转弯建模必引：JFR 2024（受限地头转向优化）、JFR 2025（连续曲率地头覆盖）、Soil2Cover（Precision Agriculture 2025，土壤压实）。
- 打滑/不确定性感知规划主要在行星机器人领域，**农业迁移是空白**（远期方向）。

### 路 2：实例级算法选择前沿（2023–2026）
- **零收益有标准叙事模板**：GNNAS-TSP（2026）的 VBS–SBS gap 协议（平局率单独报告、gap 闭合率仅在非平局子集计算）；MolAS（2026）的"选择崩溃 + SBS 回退即正确默认"；Tornede et al.（MLJ 2023）的 headroom 理论。
- **1NN 零收益更可能是特征问题而非模型问题**：ZeroFolio（2026）证明 kNN+预训练嵌入全面超过手工特征 RF → 支持"农业结构特征"主线。
- **CRC 拒判是差异化创新**：conformal/选择性预测用于求解器选择基本无先例；项目原型新分布 32/32 全拒与该文献互证。
- 农业 per-instance 选择：**未发现直接先例**（与路 1 独立印证）。
- 可借鉴：CVRP 实例空间分析（2025）的 23 特征清单。

### 路 3：LLM 自动算法设计前沿（2024–2026）
- **"准入控制闭环"基本空白（中高置信）**，窗口估计 **6–12 个月**。
- 必须正面区分的最近邻：EoH-S（AAAI'26，ΔCPI 属演化内部种群管理，非异源准入规则对比）、HeurAgenix（有生成池→在线选择，无准入/拒判层）。
- 动机锚点：LLM 演化启发式跨分布过拟合（Sim/Renau/Hart 2025）、FunSearch/EoH 装箱结果科学价值遭质疑（2026）——**准入控制正是"生成物不可信"痛点的对症治理**。
- 术语改名：避免与云计算 admission control 冲突，用 **portfolio admission / component admission**。
- 投稿：GECCO workshop 性价比最高；完整版 TEVC / AAAI。

### 路 4：VRP 求解 SOTA 与转弯约束
- 经典 SOTA：PyVRP 仍是首选开源基线；AILS-II（IJOC 2024）大规模更强；FILO2 可解 100 万点；2026 起 CVRPLib **XL 基准 + BKS 挑战赛**成新规范（冠军方案已用 LLM+进化计算自动设计启发式）。
- 神经路由：BQ/PolyNet/RouteFinder 繁荣但**无法挑战 HGS/PyVRP，OOD 是公认短板** → 补 RL4CO 小规模对照作必要性论证，不作竞争目标。
- **转弯约束 novelty 确认**：转弯代价仅三条孤立文献线（Angle-TSP 理论、弧路由 turn-penalty 变换、Dubins-TSP 解耦法），农业侧无人把转弯代价嵌入 HGS 级求解器；本项目"欧氏排序多走 18.94%"正好攻击 DTSP 解耦范式。
- 同预算比较规范：DIMACS 式限时 + primal integral，或 XL 式多种子单线程同机比较。

### 路 5：发表策略与期刊情报
- **CompAE**：IF 10.3，订阅模式**零 APC**，首决 18 天、录用 ~188 天（官网）/ ~12 个月（网友口径），录用率 ~15%；已有 4 篇仿真为主路径规划先例 + CWD30 基准数据集先例。
- **会议时钟**：GECCO 2026 已结束；GECCO 2027（克拉科夫）全文 **2027-01-26** 截稿（完全赶得上，且有学生 workshop 收本科生一作）；AAAI-27 workshop **2026-11-20** 截止（多为 non-archival，可练手）。
- **推荐路径**：AAAI-27 workshop 短文（2026-11）→ arXiv 预印本（Elsevier 官方允许）→ GECCO 2027 全文（主线 B，双盲需匿名化仓库）→ CompAE 主稿（主线 A，导师通讯，可配 Data in Brief 数据短文）→ 被拒转 Biosystems Engineering / C&OR。

## 对主计划的修正（已写入 PAPER_PLAN.md §7）

1. 主线 A 不变；强制基线新增 Fields2Cover/Fields2Benchmark；RL4CO 小规模神经对照（必要性论证）。
2. 主线 B 由"备选"升级为**并行推进**（空白窗口 6–12 个月，GECCO 2027 倒排）。
3. 负结果叙事模板就位（GNNAS-TSP / MolAS / Tornede），H1 失败仍有顶刊出口。
4. 统计协议：gap 闭合率仅在非平局子集计算、平局率单独报告；引入 autorank 多指标比较。

## 不确定项（如实保留）

- CompAE 审稿周期官网与网友口径差异大（188 天 vs ~12 个月），时间线按保守值。
- Smart Agricultural Technology / Precision Agriculture 影响因子为聚合站数据，未经 JCR 核对。
- PyVRP 0.13.0 起已从 HGS 遗传框架改为 ILS，论文须注明归档版本（本项目 0.14.0，默认 ILS，记录一致）。
- feature-free 选择是否全面超越手工特征仍有文献分歧；农业空白判断基于有限查询的阴性结果。
