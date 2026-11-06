# 架构：模块、数据流与工作台接入

## 目录
1. 与工作台的接入点
2. 模块职责
3. 数据流
4. 运行目录
5. 文件格式

## 1. 与工作台的接入点

- 入口不变：仍是同一个 4100 端口的科研工作台，不另建平行系统。
- 推荐器钩子：`src/lab/urp_lab/recommend.py` 中的 `_coverage_order` 读取 `serve.py` 提供的已晋升模型 `src/lab/models/coverage-order-v1.json`（`status`、`artifact`、`artifact_sha256`、`evaluation`、`scope_note`、`protocol`）。静态顺序只做机型门控；学习型模型额外做特征指纹与适用范围门控。
- 多车推荐理由：`recommend.py` 中的 `FLEET_EVIDENCE` 把审计证据附在 PyVRP/OR-Tools 的推荐理由后面。
- 账本：`src/lab/models/library-ledger.json`（`urp.library-ledger/1`），同时渲染为 `experiments/reports/进化决策/算法库进化账本.md`。
- 总方案：`docs/持续进化决策与调度.md`（经验库、三条回路、安全门控、阶段计划）。

## 2. 模块职责（`src/lab/urp_lab/evolve/`）

| 模块 | 职责 |
|---|---|
| `features.py` | 农艺结构特征 `agfeat/1`（覆盖与多车实例） |
| `geometry.py` | Dubins 路径长度与多边形工具 |
| `instances.py` | 田块清单（开发 / 封存）、确定性实例构造、机型 `PROFILES`（`robot_s`、`tractor_m`、`sprayer_l`）、分割保护 |
| `plans.py` | 组合定义（`COVERAGE_PORTFOLIO`、`REPAIR_PORTFOLIO`、`CANDIDATE_POOL` 等）与冻结计划构造；`config_label` 被冻结记录使用，不要改 |
| `sweep.py` | 冻结计划的进程隔离扫描（每任务一个记录，可恢复） |
| `reverify.py` | 扫描后复核：路径哈希一致性、2 cm 加密曲率检验 |
| `fillet.py` | 圆角可执行性检验（研究用评价工具） |
| `verdicts.py` | 按检验种类构建数据集；扩展库策略评估 `write()` |
| `audit.py` | 互补性审计：VBS–SBS 差距、Shapley、贪心静态顺序、失效分类 |
| `policy.py` | 尝试顺序策略（default、static、sbs、random、oracle、knn1/knn5/logit × size/agri）、交叉验证、先测后学 |
| `serve.py` | 向推荐器提供已晋升模型（指纹与范围门控） |
| `experience.py` | 经验库：求解记录、离线扫描、田间仿真回合 |
| `repair.py` / `repair2.py` | 修复候选：v1 局部 Dubins 替换原生检验可见的曲率缺陷；v2 针对圆角检验暴露的缺陷再修复并圆角化 |
| `candidates.py` | 准入研究的候选池（修复窗口、分割角、圆角输出） |
| `admission.py` / `admission_rules.py` | 候选是否入库；四类准入规则（单成员、VBS 增量、内层交叉验证策略收益、残差）与随机对照的嵌套评估 |
| `ledger.py` | 算法库演化账本：来历、状态、各检验下证据（可行数、成本比、Shapley、最优次数） |
| `exec_model.py` | 执行时间模型的留一方案评价 |
| `dynamic.py` | 动态任务流的规划层离散事件仿真；增量插入、周期、逐事件、前瞻采样策略 |
| `replan_gate.py` | 学习型重规划触发器（逻辑回归模仿前瞻采样） |
| `determinism.py` / `route_determinism.py` | Fields2Cover 运行间确定性研究与开关探查 |
| `power.py` | 封存田块修订方案的功效模拟 |
| `figures.py` / `figures2.py` | 期刊插图（图 1–7 / 图 8–10） |
| `dashboard.py` | 研究看板生成器 |
| `terms.py` | 规范中英术语 |

## 3. 数据流

```
instances + plans ──► plan.json（冻结） ──► sweep ──► records/<job>.json
                                                   ├─► reverify/<job>.json（加密曲率）
                                                   └─► fillet/<job>.json（圆角检验）
records + 检验 ──► verdicts.dataset_from ──► audit / policy / admission_rules / verdicts.write
                                         └─► ledger.build ──► library-ledger.json
结果 JSON ──► figures / figures2 / dashboard ──► 插图、看板
求解记录 + 田间仿真 ──► experience ──► exec_model；dynamic ──► replan_gate
```

## 4. 运行目录（`experiments/runs/`）

| 目录 | 内容 | 关键输出 |
|---|---|---|
| `evolve-audit-20261023` | 主审计：140 块荷兰田 × 3 机型的覆盖配置与多车任务 | `audit/audit.json`、`policy/policy.json`、`verdict_comparison.json`、`fleet_try_order.json`、`fillet/summary.json`、`figures/` |
| `evolve-repair-20261023` | 修复 v1 的 6 个配置 | `records/`、`fillet/`、`admission/` |
| `evolve-candidates-20261024` | 准入研究候选池（冻结 3104 项） | `admission/admission_rules.json`（完成标志 `t4.done`） |
| `evolve-determinism-20261024` | 运行间确定性（冻结 715 项） | 全链完成标志 `chain.done` |
| `evolve-policy-ext-20261024` | 扩展库 × 三种检验的策略评估 | `policy_ext.json`（完成标志 `t3.done`） |
| `evolve-exec-model-20261024` | 时间模型比较 | `exec_model.json` |
| `evolve-dynamic-20261024` | 180 条动态任务流 | `summary.json`、`streams/`（原始，不入库） |
| `evolve-replan-gate-20261024` | 学习型重规划触发器 | `summary.json`、`decisions/`（原始，不入库） |
| `evolve-dynamic-pilot-20261023` | 动态策略的探索性试探 | README |

每个目录有 README：目的、事先写定的规则、方法、结果、结论。逐任务记录与 `run.stdout` 按根 `.gitignore` 不入库，汇总 JSON 与 README 入库。

## 5. 文件格式

- `plan.json`：`code`（`git_commit`、`source_sha256`、`src_lab_dirty`）、`jobs`（`job_id`、`family`、`config`、`algorithm`、`params`、`instance`、`timeout_s`）、`fields`、`profiles`、`coverage_portfolio`、`fleet_portfolio`、`excluded`、`workbench_default`、`purpose`、`note`。
- 记录 `records/<job>.json`：`status`、`valid`、`objective`、`metrics`、`routes_sha256`、`errors`、`worker_wall_s`、`affinity` 等。
- 圆角检验 `fillet/<job>.json`：`verdict`、`fillet_valid@0.005/0.01/0.02`、`identical`、`attempts`、`n_unfilletable`、`reasons`、`max_deviation_m`。重算最多 5 次以复现记录的路径哈希，复现不了记为 `unverifiable`（计为不可执行）。
- 加密检验 `reverify/<job>.json`：`strict_valid_curvature`、`strict_max_curvature_per_m`、`identical`、`rerun_status`。
