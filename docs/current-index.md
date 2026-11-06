# 当前工程、研究与分支索引

从 [安装与开发](../setup/README.md) 进入同一个工作台；从下表进入对应模块、实验和原始证据。历史结果按原版本、预算与证据读取。

**[交付要求](requirements.md)** · **[研究决策图](research-map.md)**

## 2026-11-06 主线迁移

核对基线为 [`028bae0e`](https://github.com/peakpeng666/URP/tree/028bae0e967894c9370460a0301cdf7c27b10965)。[#22](https://github.com/peakpeng666/URP/pull/22) 已于 2026-11-06 03:21 UTC 合并，其 `c38a4415` 头与整理头 `61cb20ce` 均已包含在该 main；旧“#22 开放、尚未进入 main”的描述不再适用。源码集成不等于新镜像或桌面安装包已经发布。

目标是只保留 main 作为分支。第一阶段发布门禁已推送并通过真实 CI、两镜像 smoke、匿名 digest 拉取和 main 通道提升；本次在验证通道可用后切换安装/Compose 默认值与 CI 消费者。**尚未删除任何分支，也未完成历史恢复与旧客户端迁移验收**。32 条分支头的核对快照见[当前来源清单](current-branches.csv)；其中 snapshot 为独立历史，祖先比较没有返回结果，不能按“已合并”处理。旧 [02:41 快照](history/current-branches-20261106-0241.csv) 按原日期保留。

- [机器可读迁移证据](main-migration-20261106.json)记录基线、来源 SHA、真实第一阶段发布证据、历史覆盖范围与未完成门槛。零 ahead 只表示提交历史已包含；不同 blob 不表示缺少功能。
- 保留现行导入归一化、Agent/请求生命周期、统计合同与离线审批门禁。历史 `studio/pendingWrites.ts` 只是已退役转发入口；四个旧 exit-guard 工件不重新启用；paper 的七份文件是历史草稿，不作为新研究成果或当前根 CITATION。
- 1,738 个 preserved 文件均有历史清单记录。Release 目录的 9 项大小与 SHA256 digest 一致，3 个安全文本 blob 独立重算一致；这不是完整恢复证明。上述是准备阶段的元数据快照。preservation-map 已下载并校验，78,764 条映射正在核对；原件解包恢复尚未完成。此前 15 项是待核对的元数据/路径记录，不等于 15 份已确定缺失的源码。
- 安装入口改为 main；Docker 部署文件继续固定到实际镜像 OCI revision，明确选择的提交必须一致。`deploy/redo` 旧保存选择要求显式迁移，现有数据不被重置，但 Windows/WSL 的 down/status 仍可能依赖旧 URL，必须实际迁移验收后再删分支。第一阶段的发布、匿名拉取、Windows 合同与完整工程 CI 已通过，见下方固定提交记录；本次切换提交仍须单独验收。真实 Windows/WSL 首装与旧客户端迁移没有由合同测试替代。并发争用和旧运行重跑仍仅有静态/mock 覆盖，不宣称做过真实并发故障实验。
- 历史[语义取舍](branch-semantics.md)、[11-01 清退回执](branch-retirement-20261101.json)和[原来源表](branches.csv)保留原日期与范围，不把过去的测试数量当成当前 main 测试结果。

### 已验证的首次 main 镜像通道

源码提交为 [`3481c414`](https://github.com/peakpeng666/URP/commit/3481c414e8b5c4d47804ff6dbd6bbe674b52bbc8)；[工程 CI](https://github.com/peakpeng666/URP/actions/runs/42170504143)和[两镜像发布流程](https://github.com/peakpeng666/URP/actions/runs/42170504225)全部成功。完整 Lab 套件为 1,153 通过、12 跳过、36 子测试通过。独立匿名读取两个 `:main` 的 manifest/config，均与本次 push digest 和源码 revision 一致；六个所需安装源文件也已匿名获取并核对 Git blob。

- `urp-bench@sha256:087fb21fe32b2ebc82967384d36775aacfc7d76c8079329b036f097647b01b58`
- `urp-sim@sha256:724547cc0e9f3afb59bdebfcee000398518b844b69231c93f18097a4f3f3bd9e`

这是第一阶段的固定证据；`main` 通道后续可更新，精确重现使用上述 digest。本次切换默认值的提交将重新触发 CI 与镜像发布，在其结果出来前不得称为最终提交全部通过。

现有实验数据、失败结果、运行库与第三方快照均未被此补丁改写。**只有逐项补齐覆盖、恢复与消费者迁移证据后，才可以执行后续分支清理。**

## 16 个模块入口

以下模块说明与资产规模保留其原核对日期，未在本次迁移中重新执行全仓库计数或科学验收；已完成的工程检查只对应上方固定提交。

| 模块 | 权威入口 | 当前边界与整理重点 |
|---|---|---|
| 安装与启动 | [setup](../setup/README.md)、[部署](../DEPLOY.md) | 同一 4100 入口；`lab-code` 无 ROS，`lab` 准备完整仿真组件 |
| 前端工作台 | [frontend](../src/frontend/README.md) | 编辑、农业模板、Agent、图表；`#/jobs` 数值任务、`#/bpp` 历史装箱 |
| Lab 网关与记录 | [CONTRACT](../src/lab/CONTRACT.md)、[gateway](../src/lab/urp_lab/gateway.py) | 规划、求解、验算、运行身份、事件与导出；档案后端另有任务契约 |
| Agent | [实现](../src/lab/urp_lab/agent/)、[接口 §9](../src/lab/CONTRACT.md) | 显式发送调用模型；批准、取消、恢复已有工程回归，真实模型评估待做 |
| 农业输入与地图 | [compose](../src/lab/urp_lab/compose/)、[library](../src/lab/urp_lab/library/) | 保留来源、坐标单位和导入版本；执行前完成米制标定 |
| 求解与规则推荐 | [solvers](../src/lab/urp_lab/solvers/)、[recommend](../src/lab/urp_lab/recommend.py) | 路由、运动、覆盖和车队适配；名称、版本、预算与适用域分别记录 |
| 学习推荐 | [learned](../src/lab/urp_lab/learned.py)、[冻结模型](../src/lab/models/cvrp-knn-v1.json) | 现有测试固定 PyVRP 占优；选择器实验与工作台默认预算分别记录 |
| 离线 evolve | [整合说明](../src/lab/urp_lab/evolve/INTEGRATION.md) | 30 个离线模块及离线 serving guards 已整合；8 项在线接入测试仍 deferred，不激活历史模型 |
| 执行与非理想模型 | [执行检查](../src/lab/urp_lab/execution_check.py)、[仿真记录](../setup/2026-10-15/integration/REPORT.md) | 同构差速车、串行交通基线；能耗为后验代理，曲率车辆执行适配待验收 |
| ROS 参考适配 | [robotics](../src/robotics/README.md)、[上游入口](../src/robotics/docs/UPSTREAM_REFERENCE.md) | 官方上游适配与历史合成fixture按日期归档；当前现场证据见Lab验收 |
| 档案后端与 CVRP Jobs | [backend](../src/backend/README.md)、[任务契约](../src/backend/docs/JOBS_API.md) | 冻结数据库与可写运行库分开；5秒端到端与1秒搜索循环分别比较 |
| 研究实现 | [research](../src/research/README.md)、[算法登记](../algorithm_inventory.md) | CVRP、BPP、CRC、NAS、父代和转弯任务；按问题与角色组织算法，研究候选并列比较 |
| 独立验算与协议 | [independent](../independent/)、[冻结计划](../protocol/formal_plan.json)、[重审](../protocol/audits/20261017/README.md) | 失败进入计划分母；同条件耗时比较受主机与资源证据等级约束 |
| 工程门禁 | [工程报告](../setup/2026-10-30/engineering/REPORT.md)、[检查入口](../tools/check_project.py)、[工作流](../.github/workflows/) | 工程、部署、Windows 与原生适配分层回归；完整记录见工程报告 |
| 资料与第三方源码 | [资产索引](assets/README.md)、[源码来源](../src/repos/REPOSITORIES.md) | 记录源码来源、适配状态、许可证与可恢复副本 |
| 实验、报告与失败 | [实验目录](../experiments/目录.md)、[导师入口](导师查看.md)、[失败记录](../experiments/failures.md) | 以问题、日期、版本、预算和审计证据串联原件 |

## 数据与证据在哪里

| 类别 | 入口与保存规则 |
|---|---|
| 冻结输入 | [data](../data/)：BPP、benchmarks、research、packages、CIFAR 与历史数据库；BPP NPZ 纳入只读回归检出清单 |
| 实验结果与来源 | [experiments](../experiments/)：results、provenance、reports、baselines、protocols；各批次独立记账 |
| 工程与现场验收 | [setup](../setup/)、[acceptance](../acceptance/)、[ci](../ci/)、[evidence](../evidence/)；按对应源码版本解释 |
| 历史与审计 | [audits](../audits/)、[history](../history/)、[cohort_b](../cohort_b/)；保留失败、修正和中间证据，批次按实验口径比较 |
| 文献与材料 | [literature](../literature/)、[papers](../papers/)、[materials](../materials/)、[reports](../reports/)；先维护来源、版本和书目链接 |
| 新运行 | `.local-state/`、`src/lab/runtime/`；可写数据库、新轨迹与冻结档案分别保存，详见 [数据与恢复](../setup/README.md#数据与恢复) |

准备阶段核对的 `028bae0e` 基线完整树共 **29,450 个文件**，其中 `src/repos` **17,432** 个是外部源码快照；自研与适配能力按模块登记。沿用 [75 个外部目录许可证索引](assets/third-party.csv)，根 LICENSE 尚未选择；11 个无根文件目录的许可证据已分项核定，尚有 5 项授权依据待补。

[原 2,439 项清单](assets/assets.csv)保留历史，现行[派生索引](assets/current/assets.csv)为 **2,443 项**：补入 4 个 `.tar.xz` 实验包，共 **19,983,780 字节**，原路径和 blob 均不变。[外部副本登记](assets/copies.json)仅依独立验证更新对应 Drive 记录；[本次回执](assets/drive-restoration-20261101.json)加入两项成功、两项未分类 fetch 错误，联合为 **Space 4、Drive 2，覆盖 4 个独立资产**，迁移与原件删除均为 **0**。原件继续保留，后续迁移以逐文件 SHA256 和实际恢复验证为门槛。

## 运行环境与实验口径

- 根 `uv.lock`、Lab 的 `src/lab/uv.lock`、前端与浏览器工具的 `package-lock.json` 各自权威；根 `requirements.lock` 是导出文件。根和 Lab 的 OR-Tools、NumPy 版本各自锁定，升级按环境验收。
- Python 3.12 的两套应用环境与 ROS Humble 的系统 Python 3.10 分开；日常前端与 CI 使用 Node 24。历史实验锁按原环境保存。
- 主 CVRP 基线为 4,505 项终态：4,316 可行、189 无有效解；53 个问题来自 51 个底本。路线质量、描述统计、同条件耗时证明是不同层次，见 [重审](../protocol/audits/20261017/README.md)。
- 当前收尾范围限于工程整理；后续研究按 [交付要求](requirements.md) 和 [研究决策图](research-map.md) 另行确定，研究结论对应原始结果与独立验算。

## 资产核验更新

2026-10-30：四份报告资产的独立副本恢复与Git内容身份核对通过，原件保留；历史补充论文包21篇单独登记。原11项无根许可证目录已分为3项代码许可确认、2项声明确认、1项占位文档和5项待取得授权依据。见 [许可核定](assets/rights-review.md) 与 [恢复记录](assets/restoration.json)。
