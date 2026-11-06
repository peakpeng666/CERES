# 当前工程、研究与分支索引

从 [安装与开发](../setup/README.md) 进入同一个工作台；从下表进入对应模块、实验和原始证据。历史结果按原版本、预算与证据读取。

**[交付要求](requirements.md)** · **[研究决策图](research-map.md)**

## 2026-11-06 集成状态

11 月工作台与 main 已在 `codex/local-github-sync-20261105` 合并验证，数据持久化与互通、历史包发布和桌面发布门槛已实现；当前提交/CI/发布的权威状态见[最新集成验收](../setup/2026-10-15/integration/REPORT.md)。原生与容器共用持久数据，原运行库保留；只读恢复会话与缺失原始数据分别登记。

[#22](https://github.com/peakpeng666/URP/pull/22) 仍开放：头 `c38a4415` 的工程与部署 CI **8/8** 通过，自动审查留有 5 项未处理意见。合并整理分支 `ccr-81735237-3k02hy` 以快进方式包含 #22 全部 70 个提交，逐项复现并修复这 5 项（各有回归测试），并让 `src/repos` 第三方快照保持原字节，全新检出不再把最多 185 个第三方文件因行尾误报为已修改；合并该分支即同时并入 #22。范围与验证见[工程记录](../setup/2026-10-30/engineering/REPORT.md#2026-11-06-合并整理pr-22-审查修复与行尾)。资产与清退记录仍是其标注日期的快照。

## 当前基线

快照：**2026-11-06 02:41 UTC**；已合并 `main` 为 [`3ad71401`](https://github.com/peakpeng666/URP/tree/3ad714018544f1bd8fe6db30e610eb96b8bc0320)（#21）。远端 **31 个分支**：11-01 快照中的 27 个非主线分支头均未移动；新增 `chore/reconcile-retained-branches-20261101`（#21 来源，已合并）、`agent/workbench-20261029` 与 `codex/local-github-sync-20261105`（均在开放的 #22 内）。本次没有删除、移动或改写任何引用；合并整理分支在快照之后发布。

- [当前逐支 SHA、祖先关系、消费者与建议](current-branches.csv)已按 11-06 快照重算 Git 派生列（对 main `3ad71401` 的祖先、合并基、独有/不同路径，消费者逐条在当前头复核）；未移动分支沿用 11-01 的判断，其[独有路径与保留索引](branch-retention.json)与[语义取舍](branch-semantics.md)仍适用。比 11-01 多出的 1 项独有路径是 #21 有意删除的 `studio/pendingWrites.ts` 转发入口。
- 2026-11-01：远端由 **35 条引用清退至 28 个分支**；七条删除的精确 SHA、已合并 PR 恢复入口和操作回执见 [清退记录](branch-retirement-20261101.json)；main 与其他引用均未移动。
- [原 29 个来源分支表](branches.csv)保留工程整合时的快照和取舍，不跟随引用清退改写原历史。
- [#15 工程整合](https://github.com/peakpeng666/URP/pull/15)已合并。当前获取入口采用 main。
- [#16 PyVRP ILS 标识兼容修复](https://github.com/peakpeng666/URP/pull/16)已合并到当前 main；修复头 `293dbf20` 的[工程四项作业](https://github.com/peakpeng666/URP/actions/runs/41074311873)和[部署两项作业](https://github.com/peakpeng666/URP/actions/runs/41074311831)已通过。当前使用 `pyvrp.ils`，保留旧 ID 请求兼容；历史模型与结果维持原件。
- [#12](https://github.com/peakpeng666/URP/pull/12)、[#13](https://github.com/peakpeng666/URP/pull/13)已关闭（未直接合并），其离线 evolve 功能已由 #15 择取整合；来源分支保留。
- [#17 全量整理与错误处理](https://github.com/peakpeng666/URP/pull/17)已合并；[主线四项CI](https://github.com/peakpeng666/URP/actions/runs/41105645794)通过，合并前精确提交代码审查无发现。
- PR **#1、#2、#3、#4、#11、#14、#15、#16、#17、#18、#19、#20、#21** 均已合并，**#22** 开放。原提交与 PR 记录保留，七条已授权来源引用的清退另有回执。

- [#18 资产与许可证据](https://github.com/peakpeng666/URP/pull/18)已合并；[合并后四项 CI](https://github.com/peakpeng666/URP/actions/runs/41112545177)通过。
- [#19 统计、Agent、启动与交付契约 refresh](https://github.com/peakpeng666/URP/pull/19)已合并；头 `4c5d383a` 的[工程 CI](https://github.com/peakpeng666/URP/actions/runs/41125771960)与[deploy CI](https://github.com/peakpeng666/URP/actions/runs/41125771951)合计 **6/6**，其合并后 [main CI](https://github.com/peakpeng666/URP/actions/runs/41126537526) **4/4**。首次失败、闭包修正及本地验证范围保留在工程记录。
- [#20 恢复边界、资产与分支索引](https://github.com/peakpeng666/URP/pull/20)已合并；精确头 `8be9b89` 的[工程 CI](https://github.com/peakpeng666/URP/actions/runs/41277109384)与[部署 CI](https://github.com/peakpeng666/URP/actions/runs/41277109314) **6/6**，合并提交 `9324ba1` 的 [main CI](https://github.com/peakpeng666/URP/actions/runs/41277554358) **4/4**。提交与远端 tree 身份一致。
- [#21 分支保留与恢复证据核对](https://github.com/peakpeng666/URP/pull/21)已合并；头 `a84190b` 的[工程 CI](https://github.com/peakpeng666/URP/actions/runs/41284207856)与[部署 CI](https://github.com/peakpeng666/URP/actions/runs/41284207915)通过，合并提交 `3ad7140` 的 [main CI](https://github.com/peakpeng666/URP/actions/runs/41284549690) 通过。
- [#22 工作台与 main 同步、可迁移数据](https://github.com/peakpeng666/URP/pull/22)开放；头 `c38a4415` 的[工程 CI](https://github.com/peakpeng666/URP/actions/runs/42062910140) **6/6** 与[部署 CI](https://github.com/peakpeng666/URP/actions/runs/42062910016) **2/2**。

现存 13 个非主线分支是 main 祖先：`deploy`、`redo` 仍用于安装，另有 4 支被 CI 分支规则引用；5 个原祖先候选暂留，另保留已合并的 #20、#21 整理来源支。只清退过 11-01 逐条授权的七支，未将祖先关系当作批量删除许可。

其余 17 支全部保留。#22 的两支（`agent/workbench-20261029` 被 CI `agent/**` 推送规则引用）在 #22 合并后成为 main 祖先；`codex/preserve-20261026-local` 经工作台分支进入 #22 头，届时也成为祖先。其余 15 支同 11-01：备份、local 保留支、无共同祖先 snapshot、paper 分别有 7、368、2,096、7 项 main 缺失路径；workbench 保留支另有 1 项敏感候选路径，仅登记路径与 Git 对象，未打开或提取。其中 10 支已完成分支引入差异、blob/AST 与关键行为审查，具体保留与整合建议见 [语义取舍](branch-semantics.md)，不把路径存在等同于功能完整整合。#12/#13 的择取整合事实与“关闭未直接合并”状态同时保留。

## 当前 refresh

统计、Agent、启动与交付契约已由 #19 完成；恢复边界、`.tar.xz` 漏计及基础索引由 #20 完成；#21 补入独立 Drive 回执、七引用实际清退、十支语义取舍，并统一内部待完成写入入口与当前 CI 说明。11-06 合并整理在 #22 之上修复其 5 项审查意见并处理第三方快照行尾；实施和验证见[工程记录](../setup/2026-10-30/engineering/REPORT.md)。

源码主线与安装镜像分别验收；当前远端 `deploy` 为 `41e6bdc221cb`，主线合并不代表镜像已发布。

[研究决策图](research-map.md)保留候选问题，不作为本轮 refresh 的执行队列；[研究报告](导师查看.md)保留截至 2026-10-19 的冻结证据。

## 16 个模块入口

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

本次核对的已合并 main 完整树共 **29,450 个文件**，其中 `src/repos` **17,432** 个是外部源码快照；自研与适配能力按模块登记。沿用 [75 个外部目录许可证索引](assets/third-party.csv)，根 LICENSE 尚未选择；11 个无根文件目录的许可证据已分项核定，尚有 5 项授权依据待补。

[原 2,439 项清单](assets/assets.csv)保留历史，现行[派生索引](assets/current/assets.csv)为 **2,443 项**：补入 4 个 `.tar.xz` 实验包，共 **19,983,780 字节**，原路径和 blob 均不变。[外部副本登记](assets/copies.json)仅依独立验证更新对应 Drive 记录；[本次回执](assets/drive-restoration-20261101.json)加入两项成功、两项未分类 fetch 错误，联合为 **Space 4、Drive 2，覆盖 4 个独立资产**，迁移与原件删除均为 **0**。原件继续保留，后续迁移以逐文件 SHA256 和实际恢复验证为门槛。

## 运行环境与实验口径

- 根 `uv.lock`、Lab 的 `src/lab/uv.lock`、前端与浏览器工具的 `package-lock.json` 各自权威；根 `requirements.lock` 是导出文件。根和 Lab 的 OR-Tools、NumPy 版本各自锁定，升级按环境验收。
- Python 3.12 的两套应用环境与 ROS Humble 的系统 Python 3.10 分开；日常前端与 CI 使用 Node 24。历史实验锁按原环境保存。
- 主 CVRP 基线为 4,505 项终态：4,316 可行、189 无有效解；53 个问题来自 51 个底本。路线质量、描述统计、同条件耗时证明是不同层次，见 [重审](../protocol/audits/20261017/README.md)。
- 当前收尾范围限于工程整理；后续研究按 [交付要求](requirements.md) 和 [研究决策图](research-map.md) 另行确定，研究结论对应原始结果与独立验算。

## 资产核验更新

2026-10-30：四份报告资产的独立副本恢复与Git内容身份核对通过，原件保留；历史补充论文包21篇单独登记。原11项无根许可证目录已分为3项代码许可确认、2项声明确认、1项占位文档和5项待取得授权依据。见 [许可核定](assets/rights-review.md) 与 [恢复记录](assets/restoration.json)。
