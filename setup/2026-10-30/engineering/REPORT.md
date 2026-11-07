# URP 工程基线整合 · 2026-10-30

[PR #15（已合并）](https://github.com/peakpeng666/URP/pull/15) · [使用与开发](../../README.md) · [29 个分支处置](../../../docs/branches.csv) · [资产索引](../../../docs/assets/README.md)

工程基线以 main `5ddbd22` 为基础，整合 Agent、工作台与离线 evolve。第一轮生产代码验证于 `b2f79af`，入口与 CI 验证于 `cf96ed9`；第一轮最终提交为 `aab3b42`。下面先记录在同一 PR 继续推进的第二轮结果。

## 2026-11-07 田间作业模型与 Lab 工作流

基线为 main `fd057e0b`。农业语义此前只到前端目录：后端收到的是匿名点集或覆盖多边形，机具不是实体，行作加容量的问题被校验器拒绝，目录入口要经 5 步才能打开。本轮把环境、作物、作业、车辆、机具组成机组，统一编译成作业行问题 `field_operation`，并新增「作业」入口。说明见[田间作业模型](../../../docs/agri/model.md)，实验见[研究报告](../../../experiments/runs/field-ops-20261107/REPORT.md)。

- **模型**：14 类环境、15 种作物、30 项作业、15 台平台、24 种机具，每个参数带来源与证据等级；环境声明这里成立哪些作业。覆盖矩阵从 142 个可规划格修正为 63 格，修正的是畜禽舍耕地这类由连接方式推出、农艺上不成立的组合。
- **决策**：P/U/T/原地转弯，线性时间补给切分，四种行序算法和 ≤ 12 行的枚举最优，独立检查器不导入求解侧代码。
- **执行**：只有参考差速机器人可进入仿真，方案不改几何，经原有多车检查器复核；其他机组显示只到规划层，接口按原因拒绝。
- **界面**：作业设计、农机、场景与作业三个页面；方案面板改为时间构成、一句结论、紧凑对比表，研究图表收入折叠区；12 处防御性长句改为短句，内容迁入文档。

实际 Electron（源码模式，隔离数据目录）通过界面完成：设计 → 生成并求解 → 验算 → 启动 Gazebo → 关闭 → 点击 MuJoCo 对比。场地 20×12 m，两台参考机器人，7 条作业行，4 倍速。规划层机组没有 Gazebo 入口。

| 交通方式 | Gazebo 会话 | 任务 | Gazebo / MuJoCo 工期 | 里程 | 轨迹平均 / 最大差 | 任务同钟 RMSE |
|---|---|---|---|---|---|---|
| 串行 | `field_20261107T073005_3ae65c` | 7/7 | 223.59 / 223.86 s | 181.95 / 182.20 m | 0.0047 / 0.0306 m | 0.129 m |
| 并行协调 | `field_20261107T073114_44e514` | 7/7 | 120.95 / 121.06 s | 181.97 / 182.27 m | 0.0045 / 0.0353 m | 0.076 m |

这是同一平地、同一未标定刚体模型、无机具动力学下的 sim2sim 工程验收，不是实车结论。更早一次 10 倍速、Gazebo 限 8 核且实验占满其余核的运行失败：真值位姿延迟约 2 s，控制器按 `MAX_CONTROL_POSE_AGE_S` 停车，第二台车 60 s 无进展。摘要保留在 `gazebo-failed-under-load.json`，改用 4 倍速、空闲机器后通过。

| 检查范围 | 实际结果 |
|---|---|
| Lab 全量（冻结提交 `23197311`） | **1669 passed、5 skipped、36 subtests passed**，625.25 s |
| 其中田间作业 | 57 项通过，含转弯闭式与几何互校、验算器防篡改、枚举最优、执行桥接 |
| 前端全量、类型与生产构建 | **801 passed**，类型与构建通过 |
| 桌面 Node 回归 | **38 passed** |
| 工程门禁 | **0/4 passed**，含生产构建 |
| 实际 Electron 全部路由 | **30/30** 无渲染错误、无脚本错误 |
| 实际 Electron 界面流程 | 受限机组、地头不足、返回修改、真实田块、1024 / 1280 / 1360 / 1440 / 1680 宽窗口 |

验收脚本 `src/desktop/test/field_operation_e2e.py`，摘要和截图在 [`setup/2026-11-07/field-operations/`](../../2026-11-07/field-operations/)。

未覆盖与缺口：智能体还不能建立 `field_operation`，一句话任务仍走点任务；多田块、机组混编、接粮协同、坡度与土壤力学未建模；大量平台与机具参数是假设，部分厂商参数只核对到检索摘要；Windows、安装包和容器后端未验证，本轮没有重新打包；真实付费模型未调用；没有真机。

## 2026-11-06 Lab 双引擎一体化与软件体检

基线为 main `0e3ddeb8d63f03196aeab6ce9e3ed377411c94ab`；开发直接在现有 `~/Projects/Lab/code`，沿用 Electron 工程。数据与研究保留在原目录，不创建另一个工程或长期分支。本节是下方历史“待整合、待物理验收”记录之后的新验收。

- 源码 Electron 依据实际打包状态识别本机 ROS 环境；安装版仍要求已验收的容器后端。Gazebo 现有正式链路接入显式串行/并行协调、任务时间窗、完成核验和结果摘要；两个引擎共用包含早到等待的执行时限。
- 比较前核验冻结执行条件、车辆、实际物理参数和连续真值轨迹；缺失身份、轨迹缺口和任务到达记录不补成成功。保留各车发车对齐 RMSE，并新增保留排队的任务同钟 RMSE。修复停车进场线与其他停车位的双向净空检查。
- 旧 Agent 会话不能停止新仿真或挪用其结果。MuJoCo 子进程启动/取消/关闭通过生命周期屏障串行化；异常退出即使写出成功 JSON，也不能计入批次成功率，原始 JSON 与退出码保留。
- 指标分成 Gazebo / MuJoCo 两列，记录带交通方式、时间和短 ID；修复说明覆盖指标。失败、估算能耗和执行状态明确显示，重复条件移入按需详情及[使用说明](../../README.md#仿真与引擎比较)。
- 桌面后端启动超时清理原进程，清理失败保留句柄并阻止重复启动。网关按消费到的实际分块限流：普通体 8 MiB、合规资产上传 768 MiB；拒绝非法/不一致长度，保留流式上传和失败文件清理。这是本机或获准访问来源的资源保护。
- 更新器只识别稳定版本且包含当前平台完整安装器及更新清单的 Release，固定到该 tag；跳过档案 Release。实际 Electron 网络查询返回 `no-release`，没有把“没有发布包”显示为“已经最新”。桌面发布 CI 新增 Linux 真包窗口、IPC、回环模型、JS 求解、备份恢复及重开流程；本机执行的是源码模式，尚未代表远端真包通过。

实际 Electron 通过界面启动两车、两个采样点的 Gazebo 仿真，观察 ROS 遥测及三维画面，再点击 MuJoCo 对比。任务最早开始 90 s，确实等待超过 20 s；两种交通模式均 2/2 完成、时间窗与物理核验通过。停止后会话报告与进程清理均 PASS，无所属残留进程。

| 交通方式 | Gazebo 会话 | Gazebo 工期 | 轨迹平均差 | 轨迹最大差 | 任务同钟 RMSE |
|---|---|---:|---:|---:|---:|
| 串行 | `field_20261106T224743_d73afe` | 144.62 s | 0.007160 m | 0.107762 m | 0.040234 m |
| 并行协调 | `field_20261106T224810_8359eb` | 116.34 s | 0.007679 m | 0.107762 m | 0.044155 m |

这是同一小型平地、同构车、理想伺服任务的 sim2sim 工程验收，不是实车精度、可靠性或一般性能结论。另有真实 Gazebo 两车八任务基线通过；未运行真实付费模型、机器人、田间标定或全套仿真工况。

| 检查范围 | 实际结果 |
|---|---|
| 冻结源码 Lab 全量，含固定地图 | **1557 passed、5 skipped、36 subtests passed**，651.33 s |
| 后续网关请求体与相关 API 回归 | **75 passed**，含 43 项新增边界 |
| 后续物理生命周期、批次与架构回归 | **128 passed**，生命周期/退出码新增 11 项 |
| 前端全量、类型与生产构建 | **790 passed**；类型与最终生产构建通过 |
| 桌面 Node 回归 | **38 passed** |
| 工程门禁 | **15/15 passed**；生产构建单列，后续公共入口/结构补跑 **77 passed** |
| 实际 HTTP 生成、推荐、PyVRP / savings 求解、独立验算、导出 | 通过 |
| 实际 Electron 主要路由 | **27/27** 无渲染/脚本失败；可选档案缺失单独记录 |
| 实际源码 Electron 隔离流程 | 窗口、隔离 preload IPC、回环模型、确认前任务、JS 求解、SQLite 备份/恢复、重开通过；rendererErrors 为空 |
| 比较面板视觉检查 | 1360 / 960 CSS 像素窗口均无指标/说明重叠，双引擎列和记录身份可辨 |

原始证据位于本机 `~/Research/URP/experiments/lab-sim2sim-20261106/`：`electron/summary.json` 与截图、`lab-full-frozen.log`、`gateway-body-tests.xml`、`urp-physics-lifecycle-frozen-20261106.log`、`desktop/source-window/smoke.json`、`pages-final/pages.json`、`engineering/summary.json` 和 `workbench/summary.json`。首次全量运行因并发源码变更触发保护而中断，其 `lab-full.log` 保留；随后冻结源码重跑通过。后续两个修复按表中定向回归验收，不冒充再次全量。

未覆盖与缺口：Jobs/历史实验档案因可选历史包未安装返回 503，界面提供下载入口；Fields2Benchmark 数据未下载。页面呈现通过不代表这些数据功能可用。尚无完整桌面 Release，本次未重封装本机旧安装包、发布 Release、验证 Windows 窗口、实际升级安装重启或新容器仿真；远端新 CI 结果另按提交追踪。真实运行记录、用户数据库、原环境、安装包与 RoboCup 项目均保留。

本轮已清理结束后的 pytest 临时数据和隔离桌面迁移测试数据，保留报告及截图，实测释放约 1.19 GB；没有为清理创建备份或工程副本。具体路径与实测空间变化见本机 `cleanup.json`。

## 2026-11-06 main 接续 Electron、时间窗与扰动评估

本轮以 `42101cc4` 为主线基线，直接在 main 接续已有 Electron 界面与任务时间窗，再融合本机扰动批次及 Agent 评估。没有重建另一套应用。冲突处理保留原有可选估计的子进程预算、模型适用范围、同模型排序与当前任务版本校验，系统提示递增为 `agent-prompts/7`。

额外修正批次 factors 非法类型导致的未处理异常，以及缺失任务执行记录可能误报全部按时的问题。扰动样本的成功还要求任务按时且没有提前开始作业；统计明确区分已结束、预算截断与未运行样本。工程门禁 15/15（单独生产构建已通过）、773 项前端测试、16 项桌面测试通过；联合后端最终回归 **329 项通过**（327.19 s），覆盖物理批次、Agent 扰动评估、时间窗、估计预算、主线排序保护、协调及执行保护；首轮夹具与断言适配问题已修正，并新增跨任务版本、非法参数、缺失任务记录及时间窗批次统计回归。这是 main 上的联合定向结果，不冒充全量 Lab 套件。本次启动及正常关闭后的冷启动均使用原本机数据根，数据集身份保持不变，临时调试端口已关闭。没有执行真实付费模型请求、真实 Gazebo 会话或田间可靠性验证；既有 Linux/Windows 安装包未重封装。

## 2026-11-06 本机未提交成果整合（待上游核对）

基线为 main `1fa8c02bb87c564579e7f2807919bd1a1a2b3b25`。在独立审阅副本中合并 `agent/workbench-20261029`（`5f340c25`）的未提交并行执行、工期预测、`pyvrp.balanced`、界面与模型提示缓存成果，以及 `codex/local-github-sync-20261105`（`c38a4415`）的非默认 UID 容器、可配置资源路径与桌面迁移验收。两处原工作树的 57 个文件及 staged/unstaged 补丁先行备份，原件保持不变；未推送 main、发布、删除或修改 Dock。

求解分派同时保留 main 的 `trace_diagnostics` 和本机新增的路径图参数；协议文档保留科研对话/数据互通并为并行执行另立第 13 节。桌面 `cwd` 修复已由 main 实现，保留 main 版本；Windows 超时、科研模型适配和多工具暂停保护未覆盖。系统提示版本递增至 `agent-prompts/5`，Anthropic 缓存标记仅发往实际官方主机。

工程门禁 16/16 通过（含生产前端构建、458 项可计数测试），桌面单测 16 项通过；另行部署测试 73 项完成、1 项显式跳过。Lab 变更相关回归 **220 通过、1 跳过**（229.74 s），覆盖并行执行、balanced 求解、模型服务商、工作台、Agent 执行保护、错误协议与科研对话；[日志](../../2026-11-06/local-integration/lab-targeted.log)。[工程汇总](../../2026-11-06/local-integration/engineering/summary.json)、[部署测试](../../2026-11-06/local-integration/deploy-tests.log)。1211 项全量套件未跑完（中止时 111 通过、1 跳过），不能宣称全量通过。第一次定向运行曾因执行期间源码调整触发源码变更保护，之后冻结源码重新启动验收。

既有 `workbench-runtime.zip` 按固定 SHA256 校验后实际恢复到全新目录，4709 个文件、1193772939 字节通过；`workbench-recovered-20261106.zip` 的 4766 个文件及数据库结构、行数通过核验（31 个会话、372 次求解、40 次仿真）。这些是本机恢复证据，不代表运行原始数据已经上传云端。

本机 ordinary-file 盘点覆盖 40837 个文件、8006482201 字节，排除凭据正文、依赖环境、构建包和记录的链接；其中 29442 个文件、4359168969 字节与基线 main 的 Git blob 相同。两处运行目录同相对路径且同 SHA256 的 4333 个文件合计 1107814161 字节，仅作为重复候选，不作删除授权。完整本机路径清单留在私有交接目录，不放进公开源码。

待交付：从最终审定提交重建 Linux 与 Windows 安装器、核对镜像版本，实际验收安装后的备份恢复与启动退出；Gazebo 正式会话仍为串行，本轮只保留 `setup/2026-11-06/coordination/gazebo-patch.diff` 和历史测试记录，没有将测试镜像补丁冒充正式功能。软件显示名拟为 Lab，沿用原 Electron 工程和应用身份；工程/学术目录整理待安装与数据恢复验收后实施。

## 2026-11-06 合并整理：PR #22 审查修复与行尾

基线为已合并 main `3ad714018544f1bd8fe6db30e610eb96b8bc0320`（#21），实施分支 `ccr-81735237-3k02hy`。开放的 [#22](https://github.com/peakpeng666/URP/pull/22) 头 `c38a4415`（工程 6/6、部署 2/2 CI 通过）以快进方式并入，原 70 个提交不改写；`agent/workbench-20261029` 是其祖先，一并包含。#22 的自动审查留有 5 项未处理意见，逐项先在旧代码上复现，再修复并补回归测试。没有删除、移动或改写任何分支引用，也没有调用真实模型。

| 审查意见 | 修复前复现 | 修复 | 回归测试 |
|---|---|---|---|
| P1 安装版桌面的备份/恢复报 `No module named urp_lab` | 打包布局下从其他目录启动，子进程工作目录是启动目录（实测 `/tmp`），且 `childEnv()` 已清除 `PYTHONPATH` | `DataTransfer.command` 与 `Backend.start()` 一样传入 `layout()` 的 Lab 目录，`run()` 转发 `cwd` | `src/desktop/test/main.test.js` |
| P2 精简后端删掉 pyproj 的 `proj.db` | `slim()` 删除所有名为 `share` 的目录，包括 `pyproj/proj_dir/share/proj`；无系统 PROJ 时 GeoJSON 导入与田块投影失败 | 保留该目录，其余 `share` 照常精简 | 同上，以 python3 实际调用 `slim()` |
| P2 Windows 看门狗超时被记为 error | 去掉 `SIGALRM` 后超时 JSON 写进被重定向的 stderr，stdout 为空，网关解析失败并记为 error | 看门狗写入布防时保存的真实 stdout | `test_error_contracts.py`：子进程真实触发看门狗，`JobManager._finish` 判为 timeout |
| P1 设置中连接的服务商无法进行科研对话工具调用 | OpenAI 与 Anthropic 两类均报 `KeyError: 'name'`（工具定义被二次包装、历史调用格式不符） | `ConnectedModel` 在适配边界把 OpenAI 形工具定义与历史调用转为 `llm.chat` 的内部格式；会话记录本身不变 | `test_research_agent.py`，两类服务商 |
| P2 同一回复的多个工具调用之间暂停或取消后无法继续 | 历史中只有第一个调用的结果，严格服务商拒绝继续 | 暂停、取消、致命工具错误、预算、模型与内部错误在保存前为未答复调用补“中断/取消”结果；`RUNNER_VERSION` 改为 `agent-runner/6` | `test_agent_runner_guards.py`，严格替身服务商，暂停与取消两种 |

**行尾。** `src/repos` 有 185 个第三方文件以 CRLF blob 提交（Windows 采集），而 `*.yml`、`*.yaml`、`*.sh`、`Dockerfile`、`*.bat` 规则要求转换，全新检出会依写入时刻把其中一部分报为已修改（本次检出为 95 个）。逐个改写 blob 会动到研究记录固定的字节：`literature/资料/代码定位.json` 的 152 个 `source_sha256` 中 151 个与当前快照一致，改写后 MLEvolve、DR-ALNS 两项将不再一致。因此改为 `src/repos/** -text`：第三方快照不做任何转换，不改 blob；项目自身脚本与 YAML 规则不变。强制重算全部第三方文件哈希的全新检出中，修复前 185 个、修复后 0 个显示为已修改。另记录：改写后 ReEvo、ShinkaEvolve 各一项会与 `materials/代码评议` 清单登记的上游 blob 相同，说明 CRLF 来自采集；是否恢复上游字节留待单独决定。

**分支快照。** 远端 31 个分支：11-01 快照的 27 个非主线分支头未移动，main 前进 #21，新增 #21 来源支和 #22 的两支。[当前分支表](../../../docs/current-branches.csv)按 11-06 快照重算 Git 派生列并复核消费者，未移动分支沿用 11-01 判断；`codex/preserve-20261026-local` 经工作台分支进入 #22 头，#22 合并后成为 main 祖先。各支比 11-01 多出的 1 项独有路径为 #21 有意删除的 `src/frontend/src/studio/pendingWrites.ts` 转发入口；相对本分支头另有 `src/frontend/src/lab/shell/Sidebar.tsx`，由工作台重写 `31edb275` 改为 `desk/Sidebar` 后删除，现无引用。

| 本次验证（代码提交 `92c7983`） | 结果 | 记录 |
|---|---|---|
| 5 项修复的回归测试在旧代码上 | 均失败（逐项复现上表现象） | 本节 |
| 桌面 Node 测试 | **15/15 passed**（#22 为 13 项，新增 2 项） | [summary](../../2026-11-06/cleanup/summary.json) |
| Agent、科研对话、服务商与错误合约相关 Lab 文件（与全量有交集，不相加） | **181 passed、1 skipped** | 同上 |
| Lab 全量，固定官方地图（`--require-maps`） | **1153 passed、12 skipped、36 subtests passed**，502 s；跳过均为云端缺少的可选资源：Fields2Cover 9、Fields2Benchmark 2、ROS 1（#22 本机运行有 ROS，为 11 项） | [lab-full.log](../../2026-11-06/cleanup/lab-full.log) |
| 工程门禁、类型检查与生产构建 | **16/16 passed**：458 个 Python 用例、前端 754、回放 49、桌面 15；构建含 `tsc --noEmit` | [engineering-summary.json](../../2026-11-06/cleanup/engineering-summary.json) |
| 全新检出行尾（强制重算第三方文件哈希） | 修复前 185、修复后 **0** 个已修改 | 本节 |

本轮在云端运行，没有 ROS/Gazebo、桌面或 Windows：浏览器检查、真实模型、Windows 实机看门狗、桌面打包/安装、容器镜像与现场仿真均未运行；#22 记录的数据恢复结果及约 1.72 GB 原始数据缺口不变。分支清退须逐条授权，本轮只更新快照。

## 2026-11-01 后续整理：引用清退、Drive 与语义核对

基线为已发布 main `9324ba1a71eb4b85a60fddbf4635900471b0e3f8`。用户授权的七条分支引用已实际删除；本次文件改动保留在本地分支 `chore/reconcile-retained-branches-20261101`，尚未提交或推送为新批次。

- 七支逐条核对远端 SHA、main 祖先关系、已合并 PR 和当前安装/CI 无显式消费者后，以一次原子 push 和逐引用精确 lease 删除。实测远端 **35→28**，main 及其他头未变；删后七个 PR 页仍返回原 head SHA。原提交留在主线，未实际恢复引用或测试网页按钮；[回执](../../../docs/branch-retirement-20261101.json) 保存恢复 PR 入口。五个剩余祖先候选、六个消费分支、本次已合并 cleanup 来源及十五个非祖先支继续保留。
- 根云端交接的独立 Drive 回执确认简报 PDF、LaTeX ZIP 两项真实下载与内容验证；项目报告 PDF/ZIP 均为未完成下载的未分类 fetch 错误，错误正文未保留，不能断言 403 或权限拒绝。仅更新四条既有 `copies.json` 记录，Space 历史及旧论文包 403 不改，旧被拒路径未访问。现为 **4 个独立资产、6 份提供方副本（Space 4、Drive 2）**，原件删除及迁移 **0**；见[脱敏回执](../../../docs/assets/drive-restoration-20261101.json)。仓库任务只核对交接证据与 Git 身份，没有重复下载原件。
- 十个旧实现分叉按各自 merge base 比较实际引入文件，并核对模式/blob、Python AST 与关键行为。两个前端引用完全同头；`mamba-results` 的两项独占 BAT 改动均与主线一致；serving-guards 交付 51 项中 49 项同主线，另外两项为已被全量 Lab/必需地图替代的共享配置。保留主线完整幂等比较、请求身份、统计分母和离线准入 guard，不择取旧弱实现或启用八项 deferred 在线集成。全部十五个非祖先支继续保留，详见[语义取舍](../../../docs/branch-semantics.md)与[逐项证据](../../../docs/branch-semantics.json)。
- Studio 内部导入直接指向已有 `lab/pendingWrites`，删除仅转发该实现的旧入口；实现、持久化键及版本不变。evolve 说明指向当前全量 Lab 门禁，历史专用 CI 不重新复制。没有新增研究功能、改动模型/锁定算法 ID、部署或修改许可证。

| 本次验证 | 结果 | 原始记录 |
|---|---|---|
| 副本来源隔离与索引 | **10 passed** | `.local-state/retention-round2/asset-tests.log` |
| 待完成写入、目录持久化相关前端回归 | **46 passed** | `.local-state/retention-round2/pending-write-tests.log` |
| 导入来源、幂等、离线 serving 与隔离合约 | **50 passed** | `.local-state/retention-round2/semantic-contract-tests.log` |
| 农业构建器与真实后端 validator | **434/434 passed**（默认 69、边界 365） | `.local-state/retention-round2/builders.json` |
| 工程门禁、类型检查与生产构建 | **15/15 passed**，458 个 Python、716 个前端用例 | `.local-state/retention-round2/engineering/summary.json` |

上述定向用例与全量门禁有交集，不相加为独立用例数。历史分支代码未执行；当前全量 Lab、浏览器、Windows、容器、真实模型、ROS/Gazebo 及约 10.45 GB 旧 ZIP 原件恢复没有在这一后续批次重跑。PR #20 的远端结果保持原记录，不作为本次未提交改动的远端 CI。冻结实验/数据库、Space 恢复与许可证据不变；五项第三方授权依据和保留引用的进一步取舍仍待后续决定。

## 2026-11-01 全量整理：云端本地验证

基线为已合并 main `4ee2c6dd8c2b455351812d3c312a8215627fce1f`，实施分支为 `chore/full-cleanup-20261101`。origin 与基线已核实；本轮仅提交云端本地整理结果，没有推送、创建 PR、合并、部署或删除远端分支。

- 历史恢复命令原先在 `--list` 时即报 `KeyError: 'archive'`。现改用独立 `setup/materials-archive.json`，从历史提交 `66b2b8c` 原样保留 ZIP 大小、SHA256 与“Windows 报告已验证、当次任务不可读”的状态；不再读取现行位置登记中的缺失键。列出与恢复均校验完整 ZIP，清单错误、字节不符或本地冲突明确失败；原始约 10.45 GB ZIP 本轮未取得，实际原件恢复未运行。
- 资产分类补识别 `.tar.xz`，新派生视图为 2,443 项、294 个实验包。补计四包共 19,983,780 字节，旧 2,439 项路径/blob 全匹配，0 移除、0 内容变更。原清单、Drive/Space 登记和原始证据未改写，详见 [差异核对](../../../docs/assets/current/review.json)。既有恢复证据与 Git 字节的联合验证仍为 Space 4、Drive 0，迁移 0；本轮没有重取外部副本。
- 34 个远端分支中，33 个为非主线；18 个是 main 祖先，其中 6 个存在安装或 CI 消费者，12 个仅列归档候选。15 个非祖先支保留，独有路径逐项登记；其中 10 个无独有路径的分支仍有内容差异，语义核验未完成。敏感候选 `GATEWAY.txt` 仅记录路径和 Git 对象，未打开或恢复。见 [当前清单](../../../docs/current-branches.csv) 与 [保留索引](../../../docs/branch-retention.json)。
- 当前导航补记 PR #19 完成状态；区分 main 与 deploy、当前工程检查与历史 BPP/浏览器验收，补充已有双服务验收及隔离库要求。五项第三方授权依据仍待补，源码包 `export-ignore` 仅记录建议，未实施分发变更或选择根 LICENSE。

| 首次整理验证（实现提交 `d8c9097`） | 结果 | 云端原始记录 |
|---|---|---|
| 恢复、资产和启动闭包定向测试 | **22 passed** | `.local-state/full-cleanup/focused.log` |
| 工程检查、前端类型与生产构建 | **15/15 passed**，446 个 Python 用例，716 个前端用例 | `.local-state/full-cleanup/engineering/summary.json` |
| Lab 全量，固定官方地图 | **968 passed、11 skipped、36 subtests passed** | `.local-state/full-cleanup/lab.log`、`lab.xml` |
| 隔离双服务失败注入、真实 Jobs、求解/验算/导出与关闭 | **passed** | `.local-state/full-cleanup/dual-service/summary.json` |
| 实现提交 `d8c9097` 按原 CI cone 清单独立检出 | **65 public_cli passed**，恢复清单与夹具均存在 | `.local-state/full-cleanup/ci-sparse-public-cli.log` |
| 根 pip 导出与 uv 权威锁一致性 | **passed** | `.local-state/full-cleanup/requirements.lock` |
| 当前入口文档本地链接 | **187 个路径通过** | `.local-state/full-cleanup/doc-links.json` |

双服务使用新运行库，未检出的 `data/backend/research.sqlite3` 在前后清单中均为 `missing`，不能声称核验了其哈希。Git 差异检查确认冻结数据、实验、协议、模型和算法登记没有变化。缺少 ZIP 的负向调用现在返回明确的文件错误（退出码 2），原 KeyError 复现保留在 `.local-state/full-cleanup/restore-before.log`。浏览器、真实模型、容器、Windows、ROS/Gazebo、原生长跑及历史完整 ZIP 原件恢复本轮未运行；过去的 CI 与现场记录按原版本保留。本次检查摘要见 [cleanup-20261101.json](cleanup-20261101.json)。

### 恢复入口边界复核与修正

复核提交 `5460093` 发现旧解压循环仍有三项问题：本地符号链接可越过所选材料目录，重复成员可覆盖本轮先写入的文件，错误 ZIP 根目录可返回零文件成功。这三项最初由代码检查和内存反例确认，随后在真实临时目录、Git 夹具及 ZIP 上复现；没有读取私有原件或修改冻结结果。

修正后，所有所选成员先完成根目录、规范化目标重复、文件/子路径冲突、符号链接及本地冲突预检。已有材料逐字节对齐；空计划明确区分 `already-present`、`tracked-only` 与格式/选取错误。新文件排他创建，预检后出现的文件不会被覆盖；校验与解包保持同一打开文件，归档路径被替换时也不会改读未经校验的新文件。CRC、权限或存储失败会非零退出，注明已完成数和可能残留的新文件，不报告整批成功；已完成的新材料不自动回滚，恢复目录及 ZIP 须串行使用。

| 本次修正验证 | 结果 | 云端原始记录 |
|---|---|---|
| 修复前真实 ZIP / 目录反例 | 17 个测试方法，15 个失败、6 个错误（包含多项 subtest） | `.local-state/full-cleanup/restore-boundaries-before.log` |
| 最终恢复入口测试 | **17 passed** | `.local-state/full-cleanup/restore-boundaries-final.log` |
| 恢复、资产与启动闭包定向回归 | **33 passed** | `.local-state/full-cleanup/focused-boundaries-final.log` |
| 最终工程门禁、前端类型及生产构建 | **15/15 passed**，457 个 Python 用例、716 个前端用例 | `.local-state/full-cleanup/engineering-boundaries-final/summary.json` |
| 修复提交 `28e781c` 按原 CI cone 清单本地独立检出 | **76 public_cli passed** | `.local-state/full-cleanup/ci-sparse-boundaries.log` |
| 当前文档与远端只读复核 | **187 个链接路径通过，34 个分支 SHA 未变** | `.local-state/full-cleanup/doc-links-boundaries.json`、`remote-heads-boundaries.txt` |

真实负向用例覆盖 `.git`、目录外及目录内链接、归档链接、重复及别名成员、文件/目录冲突、错误根目录、空归档与无匹配材料、并发出现的证据文件、损坏 CRC 及校验后替换归档路径。原 22 项/446 个 Python 用例的首次验证、原失败日志与原提交均保留。此次只修改恢复工具、其测试和现有说明，Lab、双服务、模型和研究实现未变；上表首次 Lab 与双服务结果没有冒充本次重跑。

## 第二轮 refresh：入口与数据契约

基线为已合并 PR #18 的 `d3f0c03a`（tree `34aff046caa8c41815bf6b9e10535700bc329f46`）。本批修改统计、Agent、启动、交付检查、导出和当前导航；冻结研究记录与原始资产不变。

- 分析以完整比较身份合并场景别名；推断资格依据实际完整比较块的来源分组。未知独立单位保留描述统计并说明所缺声明，不生成显著性结论。
- 条件质量曲线列出有效轨迹、终态成功率、逐时刻观测分母与排除原因；首次遥测前未知，不补值。实例空间按任务特征 schema、预算、目标与版本分面，面板与图表导出采用同一合同。
- Agent 复用场景类型，保留参数枚举与随机性元数据；Study 依实际种子支持展开，界面计数和 Agent 分析摘要同步。
- 启动与状态诊断共用解释器解析；`lab` / `lab-code` 每次构建前端。镜像 PR 检查覆盖所有提交，双服务回归包含坏解释器、真实任务、导出、停止重启及端口释放；显式 Lab-only 模式单独解释健康状态。
- 原几何 CSV 保持兼容，新增任务/趟次 CSV。资产恢复联合视图按内容验证为 Space 4、Drive 0；未将缺失路径推断为迁移事件。

最终稳定源码验证（2026-10-30 20:01 UTC）：

| 检查 | 结果 | 云端原始记录 |
|---|---|---|
| Lab 全量，含固定地图 | **968 passed、11 skipped、36 subtests passed** | `.local-state/refresh2/lab-full.log` |
| 前端全量、类型、生产构建 | **716 passed**；类型与构建通过 | `.local-state/refresh2/frontend-{full,typecheck,build}.log` |
| 工程门禁 | **15/15 passed**，438 个 Python 用例 | `.local-state/refresh2/engineering/summary.json` |
| 双服务 HTTP、启动失败注入、真实任务与生命周期 | **passed** | `.local-state/refresh2/dual-service-final/summary.json` |
| 当前入口本地链接 | **131 个路径通过** | `.local-state/refresh2/doc-links.json` |

独立审查的完整块过度禁用、历史摘要地图语义误合并、非法 incumbent 轨迹与旧图表消费者问题均已加反例修复。并发修改期间一次 Agent 扩大回归触发源码身份保护，该失败日志保留；稳定源码全量回归通过，未绕过保护。

此次双服务使用新运行库；云端未检出原 `data/backend/research.sqlite3`，清单记录为缺失，不声称已验证该文件哈希。浏览器、ROS 和容器未在本机重跑；容器验证由本批 PR 工作流执行。测试中的模型为脚本或 mock，本批没有科研实验或真实模型调用。

### PR #19 检出闭包修正

首个远端头 `78cb4ca` 的六项 CI 为五项通过、一项失败；容器和源码双服务均通过。工程失败日志确认稀疏检出缺少启动脚本及资产恢复记录，导致 public_cli 的四个 FileNotFoundError 和一项恢复数量断言失败。原始证据见 [engineering artifact](https://github.com/peakpeng666/URP/actions/runs/41124856005/artifacts/13009594257)。

真实本地稀疏检出重现相同 57 用例中的 1 失败、4 错误；在 cone 清单增加 `setup/ubuntu`（同时包含 setup 顶层脚本，不检出日期归档）及 `docs/assets`，并加入检出闭包断言后，58 项 public_cli 测试全部通过。日志保留在 `urp_refresh/ci-sparse-before-2044.log` 和 `ci-sparse-after-2044.log`。该次本地验证仅覆盖上述闭包修正，首次失败记录保留。

PR #19 已完成并合并：最终头为 `4c5d383a13b02022fa73923fccf8ed2824bd4782`，合并 main 为 `4ee2c6dd8c2b455351812d3c312a8215627fce1f`。已核验的 [PR 工程 CI](https://github.com/peakpeng666/URP/actions/runs/41125771960) 与 [PR deploy CI](https://github.com/peakpeng666/URP/actions/runs/41125771951) 合计 **6/6**，随后 [main CI](https://github.com/peakpeng666/URP/actions/runs/41126537526) **4/4**。这些是对应提交的既有远端记录，本次文档补记没有重跑过去的测试，也没有发布 deploy 通道或镜像。

## 全量整理与错误处理修订

以已合并的 `8885ef6` 为基线，统一当前入口、31 分支快照、16 模块职责、五项交付、研究地图和资产核验队列。PR #12、#13 的改动已由 #15 整合，旧 PR 已关闭，来源分支保留。

- Lab：模型响应按契约解析；空结果不记成功；显式 null 参数与非法道路宽度报错；种子 0 原样使用；几何与 RNG 初始化错误直接暴露。
- 场景生成：保留请求，卡片显示实际数量，未满足项以 partial 状态及摘要列出。
- 求解轨迹：读取失败的 makespan 样本单独记账，不用代理目标替代；有效解保持。
- 前端：修复跨场景、会话与研究课题的迟到响应覆盖；显式呈现轮询及部分加载错误；提交锁按场景代次隔离。
- 档案与验算：非法求解器结果仅终止对应尝试；审计输入和终态一致性明确校验；审计输出采用新文件，避免覆盖历史。

验证：Lab **932 passed / 11 skipped / 36 subtests passed**；前端 **709 passed**、类型检查及生产构建通过；工程 **15/15**，其中 Python 用例 **422**；当前文档 **116** 个本地链接路径通过。HTTP 端到端完成生成、推荐、PyVRP 与节约法求解、独立验算、导出和比较。模型、原始实验和冻结研究定义保持不变。

保留的失败记录：第一轮 Lab 915 项通过；扩展修订后一次 931 通过、1 项模块行数门禁失败，随后将 makespan 遥测提取到 `fleet_trace.py`，门禁不变，最终全量通过。浏览器检查在 Chromium 启动阶段被进程 socket 权限阻塞；标准权限提升路径又被执行环境只读目录阻塞，没有完成视觉检查。原日志保存在 `.local-state/refresh/`。

资产核验覆盖 2,439 项路径和 Git blob；外部恢复验证及大文件迁移均为 0。根许可证与具体分发范围见 [资产处理队列](../../../docs/assets/rights-review.md)。

## 第二轮收尾

开始时远端与本地均为 `aab3b42`，工作区干净，没有并行提交。重新核对 29 个来源分支的 SHA，均与分支表一致。

- 修复取消时迟到的最终答复或接口错误覆盖 `canceled` 状态；请求尝试计入模型调用次数。
- 关闭网关会结束运行中及等待批准的轮次，取消旧批准，迟到结果不再写入已关闭的数据库；重启读取状态不自动请求模型。
- 继续会话时记录模型配置变化；接口错误摘要剔除已知密钥。新增配置后只读访问的反例测试，确认没有模型请求。
- 系统提示升级为 `urp.agent.system/2`，直述结果与依据，取消固定“局限”模板。旧会话保留原提示快照。
- 修正 DEPLOY 的失效章节链接、前后端 README 和后续计划中的旧入口，补齐实际停止语义。
- 资产索引读取副本登记，重新生成不会把未恢复副本当作已验证，也不会丢掉已验证副本字段；来源 Git blob 不匹配时拒绝登记。

| 本轮检查 | 结果 | 记录 |
|---|---|---|
| 全量 Lab，含 21 项 Agent 测试 | **868 通过、11 跳过**，36 个 subtests 通过 | [lab-round2.log](lab-round2.log) |
| 工程门禁、类型检查、前端与生产构建 | **15/15 通过**；410 个 Python 用例、690 个前端用例 | [engineering-round2.json](engineering-round2.json) |
| Agent 浏览器，脚本模型 | **15/15 通过** | [browser-round2.json](browser-round2.json) |
| 副本登记、源版本与元数据验证测试 | **3/3 通过**，已包含在工程门禁 | `tools/tests/test_index_assets.py` |

修复前新增反例为 5 失败、1 通过，见 [agent-before-round2.log](agent-before-round2.log)。浏览器首次发生 `Target crashed`，独立复跑通过；[首次日志](browser-crash-round2.log)保留，未将原因归为已证实的代码或资源问题。当前提交的远端检查见 [PR #15](https://github.com/peakpeng666/URP/pull/15)。

资产核验的实际结果：私有 Drive 文件夹可读写，已上传一份 826 字节的基线 JSON，并确认文件大小、父目录与原权限；已登记五份既有候选。下载既有私有 papers ZIP 到云端时，代理返回 `URLError: Tunnel connection failed: 403 Forbidden`，未完成内容恢复验证。因此 **已验证大文件迁移 0、移出跟踪 0**。具体文件 ID、状态、错误与逐类方案见 [副本登记](../../../docs/assets/copies.json)和[资产说明](../../../docs/assets/README.md)。

本轮不发起真实收费模型请求或物理仿真长跑。测试中的模型为脚本、回环服务和 `MockTransport`。

## 第一轮整合记录

## 整合结果

| 来源 | 纳入内容 | 处理依据 |
|---|---|---|
| main | 算法、独立验算、统计、执行身份、ROS/Gazebo 管理 | 保留新版实现及其回归 |
| Agent 分支 `85a7048`（#14） | 模型接口、17 个工具、会话、批准、前端面板和换行归一 | 保留三个原提交，组合测试与浏览器复核 |
| evolve `d36a7b3`、`9b020d0`（#12/#13） | 30 个离线模块、候选准入、审计、修复、动态实验及模型读取检查 | 逐提交整合，合并地图测试与数据隔离 fixture |
| workbench `c9c4bc6`、导入 `e7a6170` | 编辑器选择、校验、草稿、农业预览、幂等导入、结果追踪与图表 | 选择功能补丁；保留 main 的统计字段、执行检查与 Agent |
| editor / agriculture / results / verify 保全分支 | 独有工作由较新的 workbench 实现覆盖 | 逐分支记录 SHA 与处置，旧快照继续保留 |
| local、snapshot、paper | 研究模型、结果、失败与论文计划 | 索引原提交；已提取有效离线模块，研究结论按原版本保留 |

各分支的完整依据、与 main 的提交差异计数见 `docs/branches.csv`。算法 ID 和历史数据格式保持兼容。`analysis.py`、`jobs.py`、执行时钟和求解器继续使用 main 的实现。

## 工程整理

- 根 `requirements.lock` 从 `uv.lock` 导出，CI 核对一致性。前端与浏览器工具共 92 个下载地址统一为 npm 官方源，版本和 integrity 不变；历史实验锁文件按原环境保留。
- 新增 `lab-code` 启动入口、固定提交与 SHA256 校验的官方地图下载、真实 HTTP 小任务和源码检出清单。
- 必需地图缺失会失败；真正可选的地图用例明确跳过。进程失败、取消和普通导入测试改用不依赖 ROS 资产的数值场景。
- 相同 ID 的导入重试比较完整归一化内容，坐标系、单位、图层和来源变化均返回冲突；网关拒绝与档案后端同端口造成的自代理循环。
- 新研究 CLI 目录定点忽略，冻结实验继续跟踪。移除工作台快照中的旧 PID / 构建现场说明。
- CI 收拢为全量 Lab、工程、部署、Windows 四组；原有非理想模型、执行、Agent 和 evolve 回归进入全量 Lab。保留各原生与镜像专用工作流。
- 重写 README 和统一使用入口，建立 75 个外部源码目录的许可证据表与逐文件资料索引。核对 2439 个索引资产，Git blob 均与整理前一致。

## 本轮验证

环境：Linux、Python 3.12.14、Node 24.19.0；浏览器为 Chromium 151。以下均为本轮执行：

| 检查 | 结果 | 记录 |
|---|---|---|
| Lab 全量，固定官方地图，`--require-maps` | **858 通过，11 跳过**，36 个 subtests 通过 | [lab.log](lab.log) |
| 前端类型检查、Vitest、生产构建 | **690 通过**，类型与构建通过 | [frontend.log](frontend.log)、[工程汇总](engineering.json) |
| `tools/check_project.py` | **15/15 通过**，407 个 Python 用例 | [engineering.json](engineering.json) |
| Agent 浏览器流程，回环脚本模型 | **15/15 通过**，11 次模型请求，0 个协议错误 | [agent-browser.json](agent-browser.json) |
| 实际 HTTP 生成、推荐、求解、验算、导出、比较和关闭 | **通过**；PyVRP 与 savings 均完成且独立检查有效 | [http-smoke.json](http-smoke.json) |
| GitHub 全新稀疏源码检出，按文档安装启动 | **通过**；HTTP 流程与单例 CVRP 完成，运行前后 Git 均干净 | [clean-checkout.json](clean-checkout.json) |
| 必需地图缺失的反向检查 | **按预期失败**，退出码 1 | [required-map-negative.log](required-map-negative.log) |
| GitHub CI，提交 `cf96ed9` | **6/6 作业通过**，含 Windows、原生适配器与容器构建/服务验证 | [ci.json](ci.json) |

11 项跳过涉及可选 Fields2Cover、本机田块缓存及尚无输入的审计结果。地图依赖用例已在实际官方文件上运行。部署合约本地运行 50 项、跳过 1 项；安装器本地运行 40 项、跳过 5 项。Windows 启动与引用合约另由 GitHub Windows 作业通过。

修复前的失败记录保留：Agent 基线无地图时 **736 通过、17 失败、7 跳过**，见 [原始日志](lab-missing-maps-before.log)；后续一次组合回归暴露进程 fixture 在 exec 完成前检查身份的竞态，见 [失败日志](lab-process-race-before.log)，现已等待子进程就绪。首次工程检查漏取 BPP 的 NPZ，修正检出清单后 15 道检查全部通过。

## 接续入口

日常开发从 [setup/README.md](../../README.md) 开始。前端、Agent、求解、验算、记录与离线 evolve 在同一工程内继续推进。

真实大模型质量和 ROS/Gazebo 物理执行本轮未重跑。大文件尚未迁移，根 LICENSE 尚未代选；逐项文件、许可证文本和迁移步骤见资产索引。既有实验和失败记录保留原版本。
