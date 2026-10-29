# URP 集成验收记录

唯一使用入口为 [setup/README](../../README.md)。本报告按日期保留验收范围与原始证据；最新结论在最上方，历史通过不自动覆盖之后的模型或代码。迷宫巡检、仓库五任务、Fields2Cover 的限定闭环及 CVRP 学习原型评估见下方历史记录，不等于新车队功能、推荐增益或真实农业环境已验证。

## 任务智能体与智能体工作区（2026-10-30，分支 `agent/workbench-20261029`）

**范围**：新增 `urp_lab/agent`（任务合同 `urp.task-contract/1`、模型提供方、受校验工具、失败诊断、分阶段运行器、会话导出）与前端「智能体」工作区（首页）。工具只调用既有实现（建图、规则推荐、`JobManager`、`fleet.check`、田间仿真入口）；本轮没有执行新的 Gazebo 物理任务，仿真工具仅验证了“须批准、拒绝后不执行”的门控。

**自动化测试**：`tests/test_agent.py` 33 项通过，覆盖合同来源与校验、参数校验、预检查与原因分类、对真实求解结果的独立复核及篡改检出（检查器自身测试）、报告数值核对、密钥不出服务端、可重试错误与取消、规则流程端到端、缺项澄清、任务不可行时须批准的修复、模型流程的阶段工具集与强制复核、仿真批准门控、环境错误使会话失败、会话导出完整性、封存田块拒绝、真实田块建图。Lab 全量 495 通过、1 失败：`test_evolve_determinism::test_route_budget_probe_smoke` 读取 10-26 保全时离线归档的原始记录，与本轮改动无关，恢复方法见 setup/README。前端 115 项、类型检查与生产构建通过。

**浏览器验收（规则流程）**：[browser_e2e.mjs](../../2026-10-30/agent/browser_e2e.mjs) 驱动真实界面：描述需求 → 合同确认 → 3 个方案全部通过独立复核 → 报告 → “改成 3 台车”后原地更新同一任务地图并再次求解，累计 6/6 复核通过；截图与日志见 [browser-rule](../../2026-10-30/agent/browser-rule/log.json)。规则流程没有识别出“上午九点前送达”这一时间窗要求（未支持要求检出为 0），这是规则基线的已知局限。首次运行暴露两项问题并已修正：网关加载后改动源码导致求解被身份校验拒绝时，会话曾以“无方案”结束（现标为环境错误并提示重启）；验收脚本的异步等待条件过早返回（改为轮询会话状态）。

**任务理解评测 `urp.agent-nlu/1`**：46 条（模板生成 32、人工编写 14），按底本划分开发集 29 条 / 留出集 17 条。规则基线：开发集字段 F1 0.962、逐轮完全匹配 0.742；留出集 F1 0.947、完全匹配 0.700、多余提问率 0.211（英文“sampling points”未被识别）；两集未支持要求检出均为 0，多轮保持率 1.0。原始记录见 `experiments/agent-evals/`。

## 本地工作保全（2026-10-26，非合并或新增物理验收）

按用户要求，各本地工作区分别推送到 `codex/preserve-20261026-*`，未合并或更新远端 main。原桌面分支 `evolve/decision-loop-20261023` 的 46 个未推送提交及各工作区未提交成果均纳入对应保全分支。逐分支提交、增量附件和旧快照的内容映射见 [恢复索引](../../2026-10-26/local-preservation/recovery-index.json)，使用与恢复只维护在 [setup/README](../../README.md)。

保全源码下本地 Lab **466 passed**（11 条警告），工程 **14 门禁通过**，该次明确省略生产构建；日志与门禁范围见 [Lab 日志](../../2026-10-26/local-preservation/lab-pytest.log)和[工程汇总](../../2026-10-26/local-preservation/engineering-summary.json)。增量归档逐成员 SHA256 已核对，发布副本的认证处理记录见[审查记录](../../2026-10-26/local-preservation/publication-review.json)。本次不重做全部物理执行验收，不把归档或 CI 通过当作新的路径执行结论。本机原生运行层、数据库、场景、资产和 Fuel 保留。


本轮最终保全与整理：8 个远端保全分支，新增 Release **14 个附件、665,116,274 字节**；全部重新以无认证 HTTP 下载并核对 SHA256。按新工具实际恢复 28,885 个实验文件及一个旧快照样本，拒绝覆盖已有恢复目录。逐文件映射含 78,764 个记录，复用旧快照、去重文件与一条宿主解释器链接分别记录；绝对解释器链接不自动安装。公开副本 34 处文件变换、98 次认证替换，原科研数值结构检查和 ZIP 成员 CRC 记录保留。[公开下载](../../2026-10-26/local-preservation/public-download-verification.json)、[恢复复验](../../2026-10-26/local-preservation/public-recovery-verification.json)。

清理额外 7 个工作区、5 处验收/交接副本及已归档的离线原始数据；根目录控制文件按原 SHA256 保留，当前运行数据库、场景、资产、Fuel 和环境未移动。**46,767 个保留文件及 152 条符号链接逐一核对无变化**。生产构建另行通过；正式原生入口在隔离 Lab 状态下实际通过推荐、求解、独立验算、地图检查及三种导出，28/28 算法、Fields2Cover 和 ROS 检测可用，标准关闭请求 202、网关实际退出 0。首轮验收脚本关闭请求未声明 JSON，被正确拒绝 415，随后信号退出 -15；该失败保留，纠正请求后的复验单列。[保留层校验](../../2026-10-26/local-preservation/platform-protection-after.json)、[清理清单](../../2026-10-26/local-preservation/cleanup-summary.json)、[实际接口与关停](../../2026-10-26/local-preservation/native-platform-verification.json)。**没有执行新 Gazebo 物理任务**。

远端检查仍需区别版本：main 为 `5ddbd22f`、工程 CI 通过；两个 evolve 草稿 PR 未合并，deploy 镜像分支较 main 落后 144 个提交，最近观察到的 warehouse 生命周期诊断失败。镜像注册表可匿名读取不等于当前 main 的物理流程通过。本次保全没有消除这些集成与物理问题，细节见[远端记录](../../2026-10-26/local-preservation/remote-review.json)。

## 基线补做归档（2026-10-19）

本段只记录数值基线交接的入库与独立复算，不替代下方的部署验收，也不签收 G5。李月莹 2026-10-16 交接包已按原校验和放入 `experiments/results/路线求解/李月莹_基线补做_20261016.zip`。独立复算：53 例 5 秒端到端 742/742，16 例 1 秒搜索循环两批各 48/48。两套计时分开。工作台在 `#/jobs?tab=archives` 与算法实验面板分别显示。细节与未完成项见 [核验与接手](../../../experiments/baselines/cvrp/handoff/20261016-liyueying/核验与接手.md)。

## GitHub 完整平台恢复与容器验收（2026-10-18，当前部署交付）

**已发布完整环境和原始数据，并提供 `bash setup/run.sh reproduce --acceptance` 单一恢复入口。** 使用 Linux x86_64、本地 Docker Engine（本轮 29.9.0）和 Python 3，从 GitHub 拉取后自动下载、校验、载入并启动 4100；停止、重启及原始文件恢复只维护在 [setup/README](../../README.md)。[公开 Release](https://github.com/peakpeng666/URP/releases/tag/platform-snapshot-20261018-ddb5113e)、[结构化汇总](../../2026-10-18/platform-snapshot/summary.json)。源码基线为 `ddb5113e`；仅增加容器入口、归档/恢复工具和无桌面验收选项，没有改规划器、控制器或前端。

发布内容与验证：

- **固定运行环境。** 6.699 GB 镜像的 50 个分卷均从 GitHub 重新下载、SHA256 验证并成功 `docker image load`；ID 与 main 清单一致。补充包 0.493 GB 包含旧数值基线 JDK、三套兼容 Python 环境、原始批次、缓存及历史库；首次启动前写入新容器，18,967 个文件/链接在容器内逐项核对，pip、NumPy/Pandas/PyVRP、JDK 和研究命令入口实测可用。[镜像复验](../../2026-10-18/platform-snapshot/image-roundtrip.json)、[补充环境核对](../../2026-10-18/platform-snapshot/additional-verification.json)。基础镜像 ID 和补充包 SHA256 共同定义环境版本，不能只看镜像标签。
- **完整科研文件。** 独立原始快照 2.331 GB、116,543 个文件/链接，包含全部项目虚拟环境、ROS/Gazebo 文件及版本、Fuel（13 个缓存目录、12 个规范模型）、Fields2Cover wheel、原生库、自建场景、运行数据库、完整成功和失败轨迹。全部分包已从 GitHub 取回；发布后正式入口复核了 13 个已下载缓存分包，并无认证下载 4 个附件，随后在新目录还原全部 116,543 个成员并逐文件/链接核对，60 份 SQLite 快照完整性及表行数一致；应用库保留 292 个求解运行、39 个 TB4 运行、2 个研究批次，研究库保留 9 个实验和 107 个档案。追加旧基线在同一清单中，历史文件不覆盖当前可写库。[公开下载与完整恢复核对](../../2026-10-18/platform-snapshot/public-restore.json)。本次新增物理原始记录另在 Release 的 `deployment-acceptance*`，包括失败过程，[附件清单](../../2026-10-18/platform-snapshot/acceptance-catalog.json)。
- **发布副本审查。** 346 处认证字段脱敏、307 个文件变更，原项目不变；214 个变更 JSON 的数值、状态及失败语义核对不变。依赖源码保留原字节；补充包初次通用脱敏误改 pip 代码的版本在发布前被拒绝，修正后 18,967 个成员中无脱敏改写。扫描的 1,580 + 2,741 个原始提示均已分类为科学哈希/运行标识或上游公开示例，并非“扫描零提示”；未分类为 0。浏览器登录信息、个人凭证和瞬态进程文件排除；原始/发布哈希及排除清单随包。上游许可证保留，部分 Fuel 模型有 NC/ND 限制。[审查记录](../../2026-10-18/platform-snapshot/publication-review.json)。

| 本轮实际范围 | 结果与边界 |
|---|---|
| 容器田间六例 | free、web、cancel、clock_stall、bridge_exit、restart 均满足预定状态和真实退出码；3 succeeded、1 canceled、2 failed，全部清理 PASS、无所属残留。两种故障是验收负例，不算任务成功。正常任务双车 8/8；实际真值、里程计、接触和网页桥接经过检查。 |
| 正式部署入口 | 用命名卷创建、关闭和重启后，新求解记录保留；`reproduce acceptance` 的 free/web 两例各双车 8/8、独立检查通过。停止改用既有 shutdown API，正常实际退出 0；信号降级、超时和非零码仍报错。`/tmp` 使用独立 tmpfs，解决 Xauthority 临时文件阻止重启的问题。 |
| 真实前端 | 恢复自建场景、回放历史轨迹、比较三个算法并实际下载。PyVRP/节约基线可行且验算通过；OR-Tools 短预算超时如实显示。下载文件与 API 逐字节一致，27,643 字节，控制台无错误。[页面/导出核对](../../2026-10-18/platform-snapshot/ui-verification.json)、[回放截图](../../2026-10-18/platform-snapshot/restored-replay.png)。 |
| TB4 迷宫 | 真值定位 + FollowPath：2/2、37.893 仿真秒、最大横偏 0.01491 m，实际地图检查通过、正常关闭；求解访问序列、冻结几何和实际轨迹仍分别保存。 |
| 仓库与原生 GUI | **未通过容器验收**：有/无 GUI 的真值定位均 0/5，日志显示请求 TF 比最新数据超前 18 ms、控制器等待超限。无 GUI 清理 PASS；有 GUI 的 Gazebo 使用 SIGKILL，清理 FAIL，并有渲染错误。相同场景、模式和定位在本机原生对照 5/5、372.15 仿真秒、清理 PASS。此对照确认环境下表现不同，尚未定位 TF 差异根因，不能归咎于某个库或宣称仓库/原生窗口已恢复等效。 |
| 工程检查 | 15 门禁通过；归档和部署工具最终 16 项专项测试通过，覆盖 WAL、一致性、校验失败、续传、越界路径、不可变依赖、非零退出和外部容器保护。实际 Docker/Gazebo 验收与云端纯代码测试分开报告。 |

逐场数据见[物理矩阵](../../2026-10-18/platform-snapshot/physical-cases.json)。本机测试结束后恢复原生 4100 网关；验收容器已正常停止，卷与失败记录保留。软件、模型、配置和保存结果固定，不保证不同硬件/内核/调度下新运行逐位相同。容器默认软件渲染；显卡驱动、宿主操作系统与个人登录信息不打包。当前土壤、轮胎、定位、能耗及串行交通的研究限制沿用下方合同与原验收，不因部署成功扩大科学结论。

## 地面风险、力矩执行与导出链路独立验收（2026-10-18，仿真源码基线）

**结论：修复后完成当前源码的 13 场任务会话（8 succeeded、4 failed、1 canceled）及 3 场力矩响应探针；所有任务会话均按预定成功/失败语义关闭，清理 PASS、无所属残留。** 其中低力矩、时钟停滞和桥接异常退出是负例，不能算任务成功。接手 `31f614c0`；前端源码未改，本地文献及 `src/lab/.gitignore` 的原有修改按哈希保留。当前 13 场物理源码指纹均与交付一致；另保留 16 场中间任务及早期探针，不能混入最终通过计数。[结构化汇总](../../2026-10-18/ground-risk-audit/summary.json)、[逐场与版本范围](../../2026-10-18/ground-risk-audit/physical-cases.json)、[原始文件清单与 SHA256](../../2026-10-18/ground-risk-audit/raw-manifest.json)。

独立发现与修复：

- **地面输入和结果绑定。** 原 `ground-risk/1` 标签可绕过数值校验；声明权重被记录成 1，`mu_reference` 未一致参与积分，删除风险/限速证据也可能通过。[原反例](../../2026-10-18/ground-risk-audit/baseline-findings.json)。现统一为 `urp.ground-risk/2`，所有来源重验数值、真实地块包含关系、各车库净空与分区上限；足迹边距来自声明车队半径，外扩区重叠取最差 μ，避免重复惩罚。边权为 `L + w∫max(0, 1−μ/μ_ref)ds`，权重、地图哈希、暴露和限速均随路线冻结并复算。进入仿真前还核对原求解参数，不能将另一套内部自洽的策略冒充原运行；不同策略/权重不自动混合排名。几何米与风险当量米分开保留。
- **规划到物理世界。** 只改定位时继承场景地面；显式执行地面覆盖则记录两张图及差异。限速按含停车接驳的完整路径重建，生产验算检查真值线段在足迹外扩带中的平均速度，阈值为 0.35+0.06 m/s。该方法保守地限速整条相交折线边，且使用控制预瞄，并非最优速度规划或瞬时速度上界证明。不支持的行间、makespan、曲率约束风险搜索明确拒绝。
- **关节反馈缺失的实际原因。** “Gazebo 6.18 不输出 joint 字段”的原结论不成立：修正为重复的 `joint_name` 元素后可取得双轮连续关节角。积分不对角位移取模，必须具有两轮、有限数值、递增源时间；空消息不能刷新就绪，超过 0.25 s 数据间隔明确拒绝。实际轮角形成 ROS 里程计和定位输入，目标轮速不充当测量。
- **高倍速积压。** 本机安装的 6.18 发布器忽略 `update_rate`，仍按 250 Hz 物理步发送。两次双车重启实跑分别出现停滞、源时间缺口，原失败保留。现基于[官方 6.18 源码](https://github.com/gazebosim/gz-sim/tree/ignition-gazebo6_6.18.0/src/systems/joint_state_publisher)回补 50 Hz 源时间限频，使用独立 URP 类名与库；源码、Apache-2.0 许可、最小补丁、上游哈希与本机构建哈希均保留在 `src/lab/ros/native/joint_state/`。不覆盖系统 Gazebo。每场实查加载库；反馈订阅只取最新样本，未放宽失效阈值。
- **有界驱动力及隔离。** `urp.field-diffdrive.rigid/6` 的力矩模式对每车使用独立命令话题，force PID 输出和 SDF joint effort 双重限幅，审计实际 PID、上下限、关节、话题、反馈和库，禁止旁路速度伺服。缺反馈或指令过期只发有界制动目标。力矩任务同样执行严格真值停留、停止和无滑移参考门禁；`torque_traces` 是目标/实测轮速，不是实测力矩。
- **真实页面导出阻塞。** 实际点击导出曾触发 30 s 超时，健康请求也曾超过 5 s。现场历史轮询响应含约 13.5 MB 轨迹，在事件循环上递归编码。[故障与诊断](../../2026-10-18/ground-risk-audit/gateway-before.json)。现将大记录/历史响应完整序列化放到同步工作线程；六类接口的并发回归要求编码阻塞时健康请求仍可响应，逐值与哈希核对避免丢弃证据。修复后现场三对历史/导出请求为 0.11–0.87 s，健康请求最大 0.20 s；这不是受控倍数性能结论，历史结果冷索引仍有解析成本。[实际延迟](../../2026-10-18/ground-risk-audit/gateway-latency.json)。

最终地面对照：34×26 m、1 车、2 个停留任务，μ=0.12，真值控制；前两案几何完全相同，种子 42、预算 1 s。所有真值任务和限速检查通过。

| 执行策略 | 规划几何 m | 中心线区内暴露 m | 轮式里程计/真值最大发散 m | 实际完成 s |
|---|---:|---:|---:|---:|
| 忽略地面，无分区限速 | 66.722 | 25.471 | 11.461 | 82.43 |
| 同几何，风险权重 0.2 + 限速 | 66.722 | 25.471 | 1.667 | 204.82 |
| 绕行，风险权重 1 + 限速 | 69.548 | 14.273 | 1.722 | 148.55 |

限速降低了这组实验的发散，但绕行没有进一步降低发散；其主要变化为暴露和完成时间下降、几何长度增加。不能宣称滑移消失或“绕行发散 <0.3 m”。中心线暴露与足迹暴露是不同指标。[最终对照](../../2026-10-18/ground-risk-audit/ground-missions-delivery/summary.json)、[轨迹图](../../2026-10-18/ground-risk-audit/ground-comparison.png)。本轮为探索后的工程复验；运行前保存 protocol，不称独立预注册研究。

| 验收范围 | 最终结果 |
|---|---|
| 力矩/载荷响应 | 8 N·m、0.5 N·m、0.5 N·m+20 kg 的制动段 **位移**分别 0.112、1.131、1.323 m；均有真实双轮反馈和干净退出。是合成参数敏感性，不能把其位移当速度或真实电机标定。[探针](../../2026-10-18/ground-risk-audit/probe-summary.json) |
| 力矩任务 5 场 | 8 N·m 成功 2/2，最大横偏 0.0409 m；0.5 N·m 控制器报 2/2、真值仅 1/2，跟踪/停留失败；加 20 kg 后 60 s 无进展失败。双车话题隔离成功，未放行车辆轮速保持静止；μ=0.22+survey 定位+8 N·m 组合成功 2/2，控制输入因果重建与足迹限速通过。五场数据有效、退出码分别 0/1/1/0/0。[矩阵](../../2026-10-18/ground-risk-audit/torque-missions-50hz/summary.json) |
| 力矩故障恢复 4 场 | 取消 canceled/0；时钟停滞、桥接提前退出均 failed/1；随后 8× 双车重启成功、真值 8/8，266.56 s。三种异常没有伪装成到点成功。[恢复矩阵](../../2026-10-18/ground-risk-audit/torque-lifecycle-50hz/summary.json) |
| 工作台真实操作 | 从 4100 导入、规则推荐、风险选择、求解、验算、Gazebo/gzweb 执行、关闭、结果导出与回放。真值 2/2，74.33 m / 148.31 s，最大横偏 0.2023 m；gzweb 资源 1/1。下载 2,356,206 字节，record 与 API 逐值相等且 SHA256 重算一致，清理 PASS、实际退出 0；最终页面控制台无错误。[核对](../../2026-10-18/ground-risk-audit/ui-verification.json)、[执行画面](../../2026-10-18/ground-risk-audit/ui-execution.jpg)、[回放](../../2026-10-18/ground-risk-audit/ui-replay.json) |
| 软件回归 | 最终后端 **339 passed**（3 条既有弃用警告）；工程 **15 门禁通过**，包括前端类型/测试/生产构建。后者在最后的网关序列化调整前完成；之后对应网关回归 48 项及完整 339 项再次通过。云端新增地面、轮角与响应并发门禁，只是纯代码验证。[后端日志](../../2026-10-18/ground-risk-audit/unit-integrated.log)、[工程汇总](../../2026-10-18/ground-risk-audit/engineering-summary.json) |

**范围与未完成项：** 土壤仍为共面 Coulomb 摩擦代理，没有沉陷、压实、剪切位移或轮胎变形模型；μ、PID、定位误差、限速均无实测标定。当前仅同构差速车与串行交通，不能把异构/曲率规划当作已可物理执行。最终每条件一个工程重复/一个观测种子，不宣称统计优越性或完整鲁棒性评估；能耗仍为后验代理。二维编辑器没有专用地面分区图层，分区以 JSON、Gazebo 和导出证据核对。此次没有重跑原生 TB4/Fuel 世界完整任务，其历史通过保留原范围。启动、关闭、恢复及两类演示只维护在 [setup/README](../../README.md)。大型运行原始数据本地保留，云端可见摘要、脚本、补丁与原始文件哈希。

## 地面风险规划与有限驱动力验收（2026-10-18，GLM 原交付，历史）

> 下文保留原报告；关节反馈原因、风险协议、预注册表述及完成状态以本报告最上方的独立验收为准。

**结论：声明的已知地面图已进入规划与执行决策（风险边权改变路线选择、区内限速随冻结路线执行），有限驱动力成为版本化可关闭的执行配置（物理引擎级 effort 饱和、速度伺服参考不变），纵向闭环与饱和负对照均通过实跑验收。** 接手基线 `1e300a01`（其因果定位、共面分区、任务真值门禁作为回归约束保留），文献目录未提交修改原样保留。本轮证据：[风险规划纵向](../../2026-10-18/ground-risk-planning/acceptance/summary.json)、[力矩探针](../../2026-10-18/ground-risk-planning/torque-probe/summary.json)、[力矩任务执行](../../2026-10-18/ground-risk-planning/torque-mission/summary.json)；断言预注册在 `src/lab/e2e/risk_planning.py` 文档串内，三轮过程日志（含失败）保留在同目录。

**主线 A（风险进入规划，`urp_lab/ground_risk.py`，schema `urp.ground-risk/1`）**：instance 声明 `ground`（矩形分区+μ+knowledge 标签，规划器可读的已知地图条件）；求解参数 `ground_policy=risk_adjust`（默认 ignore）把可视边权重改为 `几何长度 × (1 + w·μ缺口·区内分数)`（当量米，非失败概率），绕行节点来自分区外扩足迹边距的角点；路线输出 `geo_length_m`/`risk_cost`/`ground_stats`/`speed_profile`（区内 0.35 m/s 限速剖面，随冻结路线执行），独立检查同口径复算（篡改 risk_cost 判无效）；世界生成把声明分区转为实际共面摩擦分区，`ground_knowledge` 记录规划图与世界图。仅支持自由通行+总里程目标；行间/最短工期明确拒绝。**纵向验收（单因素三案、区内任务为机动滑移源）**：穿越未限速轮-真发散 11.51 m → 同几何限速 1.67 m（7 倍削减，代价 makespan 82→204 s）→ 绕行+限速 1.66 m（几何 +2.8 m、区内暴露 25.5→14.3 m）。**过程发现并修复**：① 匀速直穿低附区几乎无滑移（0.03 m）——滑移集中在加减速/原地转向段，实验改为区内任务；② 中心线风险成本漏掉 0.96 m 足迹——返程贴边使单侧轮压区产生 0.83 rad 航向跳差与 20 m 发散，足迹感知暴露（risk_rect 外扩 0.5 m）修复后同场景降至 1.66 m（修复由物理对照验证）。

**主线 B（有限驱动力，`physics.actuator_mode`）**：`torque_limited` 移除 DiffDrive 速度伺服（audit 禁止共存），改用 gz-sim 6.18 JointController force 模式（本机实证：命令话题为 `/joint/<name>/cmd_vel` 轮速目标，引擎内 PID 产生力矩），**力矩限幅在 SDF joint `effort` 上限（物理引擎级）**；ROS 会话 20 Hz 发布轮速目标（零目标=有界制动、反馈丢失=安全零目标、命令过期 0.25 s=制动），里程计来自 JointStatePublisher 真实关节角/角速度积分（torque_traces 记录目标 vs 实测）。指令力矩不可从 ROS 观测——饱和证据来自运动响应：探针制动段速度 τ=8:0.057 / τ=0.5:0.562 / τ=0.5+载荷:0.663 m/s；任务级执行两档均 2/2 到达任务（τ=8 跟踪 0.12 m、τ=0.5 跟踪 1.20 m 超限→诚实失败，makespan 78→111 s、里程 +8 m 为饥饿档执行代价），**但均被判 failed：本机 gz-sim 6.18 的 JointStatePublisher 实测不输出 joint 字段（消息仅含模型位姿，已试两种参数格式），力矩模式下的真实关节里程计链路缺数据，独立检查按门禁如实拒绝**——该链路为**未完成项**，不以到点替代；速度伺服模式的里程计不受影响。参数为合成执行器假设，未标定。

全量复验：318 项单元测试（新增 ground/actuator 10 项，含篡改风险成本负例与双模式 SDF 审计）、`tools/check_project.py` 15 门禁。**剩余限制**：风险成本为声明偏好且仅已知地图条件（未含不确定性传播）；限速 0.35 m/s 为工程假设；绕行仅自由通行；力矩指令不可观测故饱和只有运动学证据；力矩模式关节级里程计未完成（gz 6.18 插件输出与文档不符，候选后继：自编译最小 gz→ROS 轮角桥或升级 gz）；土壤仍为摩擦代理。

## 非理想执行独立接手验收（2026-10-18，历史）

**结论：在现有 4100 工作台完成了低附着接触、合成定位估计、路径跟踪、独立真值验算和页面导出的闭环。地面摩擦与定位误差实际影响执行；土壤力学和真实轮胎标定仍未完成。** 接手基线 `ab817f30`。当前验收包括 **17 场真实会话**：最终非理想矩阵 7 场、未受最后保护修复影响的参考/故障回归 9 场、最终页面演示 1 场；合计 **12 succeeded、4 failed、1 canceled**，负例与压力任务失败不改称任务成功。另保留最后保护修复前的 22 场共面实验，不能冒充最终保护逻辑通过。启动、停止、恢复和导入示例只维护在 [setup/README](../../README.md)。[结构化汇总](../../2026-10-18/nonideal-audit/summary.json)、[分版本逐场数据](../../2026-10-18/nonideal-audit/physical-cases.json)、[图与源数据](../../2026-10-18/nonideal-audit/physical-evidence.pdf)。

本轮修复及其依据：

- **消除摩擦对照的几何混杂。** 原分区跟随求解路线布置，且抬高的 2 mm 薄板改变接触几何；相同 μ 的薄板也曾造成 3.19 m 发散。现在分区固定在地图坐标，基础支撑剔除分区后共面铺设，无重叠底层平面；审计变换、尺寸、摩擦、几何并集和真实轮/脚轮接触。五对相同几何对照中，μ=1 的最大发散 **0.0161–0.0185 m**，μ=0.12 为 **12.035–12.748 m**。这些场次均用真值控制、不调用定位保护，因此前后版本的保护修复不影响此对照。这是本刚体模型的摩擦效应证据，不是实测轮胎滑移率或土壤参数标定。运行库实查 DART 6.12.1 / ign-physics 5.4.0，选择与 SHA256 随会话保存。
- **修正观测模型与延迟融合。** 原严重偏置 `[0.8,−0.6] m` 被静默截为 `[0.5,−0.5] m`；延迟的完美观测会把当前 x=2 拉回约 x=1。[原缺陷复现](../../2026-10-18/nonideal-audit/baseline-defects.json)。现在偏置与有界随机游走分开，观测按测量源时间插值并记录实际送达时间，不早于用于插值的源样本。三状态固定滞后 EKF 在历史时刻更新后重放轮式输入，保留航向交叉协方差和 Joseph 更新；解析直线/圆周、延迟与同一区间多次更新另有测试。
- **核对实际控制输入。** 独立检查从种子观测和实际轮式 twist 重建估计，并逐周期对比控制器使用的姿态与时间；不是只看估计轨迹“与真值不完全一样”。真值 TF、估计 TF 和在线显示来源分别标明。定位单因素不能绕过无滑移参考门禁；结果不能靠更改 execution 标志选择宽松分支。
- **真值到点、停留和停止独立判定。** 轮速归零后先连续确认 1 s 再累计停留，保持期间不累计任务；检查真实位置、平移及角速度。新增两个反例复现“原地仍在旋转却被算静止”的遗漏并修复，正确处理 ±π 航向边界。任务验收半径仍为直通 0.45 m / 转角 0.20 m（5 mm 数值容差），没有为通过测试而放宽。控制器到点数和真值合格数在结果页分开显示。
- **修复保护过早恢复。** 中间带新息、陈旧观测及失锁保持曾沿用过时的低新息计时，三种反例都会过早退出降速。现在连续恢复证据在这些情况下清零，只有新鲜低新息持续达到阈值才恢复；进入降速后的 30 s 截止时间保留，保持不能重置或延长它。[修复前](../../2026-10-18/nonideal-audit/guard-reset-before.json)、[修复后](../../2026-10-18/nonideal-audit/guard-reset-after.json)。最终实跑严重误差场景触发超时失败并关闭。
- **保留失败语义和证据。** 失败/取消也检查可用数据，区分 `data_valid` 与任务合格；不吞掉控制器错误，保留真实数值退出码、异常原因和所属进程清理结果。新息告警改称定位/模型异常保护，不能据此诊断真实轮胎打滑。

| 验收范围 | 结果与证据 |
|---|---|
| 最终非理想矩阵 7 场 | 理想、相同几何对照、低附着、定位单因素、组合扰动、失锁保持恢复均 succeeded/0；严重误差 failed/1、真值 0/3，30 s 异常保护超时。全部预定数据、状态、实际接触、控制重建及清理断言通过。观测种子 72，不宣称完成最终版本多种子鲁棒性研究。[最终 7 场](../../2026-10-18/nonideal-audit/guard-release/summary.json) |
| 前序 22 场与负结果 | 最后保护修复前 18 场任务成功、4 场失败。种子 74 的 `outage_hold` 保持/恢复正常，但任务 1 **在失锁前**偏离 0.2056206 m，超出 0.20+0.005 m，最终 2/3、failed/1。早期严重误差种子 72 控制器报 3/3、真值仅 0/3；73/74 持续新息异常停车。发出零指令不等于已经证明车体停稳。[前一版 7 场](../../2026-10-18/nonideal-audit/release/summary.json)、[种子 73](../../2026-10-18/nonideal-audit/coplanar-repeat73/summary.json)、[额外摩擦对照](../../2026-10-18/nonideal-audit/coplanar-repeat74/summary.json) |
| 最终正式页面 | 导入 24×20 m、1 车 3 点场景，规则推荐、三算法求解/验算，冻结 PyVRP 方案；μ=0.22+survey、种子 72，实际 **3/3、48.91 m / 87.47 s**，最大横向偏差 **0.1139 m**，定位误差均值 **0.0463 m**。gzweb 资源 1/1，关闭 rc=0、清理 PASS；源码指纹与交付后端一致。[API/检查核对](../../2026-10-18/nonideal-audit/ui-guard-verification.json)、[页面截图](../../2026-10-18/nonideal-audit/ui-guard-running.png) |
| 实际回放与导出 | 回放从 0 任务/0 m 至 3 任务/约 49 m。页面实际下载 7,066,251 字节，保留该方案四次历史执行，不能算作最终保护版本四次重复试验；`record` 与 API 逐值一致，内容 SHA256 重算相符，envelope 的生成时间不要求相同。[导出](../../2026-10-18/nonideal-audit/ui-guard-export-verification.json)、[回放](../../2026-10-18/nonideal-audit/ui-guard-replay.json) |
| 9 场集成与故障 | free、rows、supply、restart、web 为 succeeded/0；cancel 为 canceled/0；clock_stall、bridge_exit、实体接触为 failed/1。全部清理 PASS、无所属残留。真实 Foxglove/Gazebo 消息和 RViz 重复开关通过。这些参考/故障路径不调用本次最后修复的非理想新息保护，测试发生在该修复前，各场源码指纹保留。[逐场摘要](../../2026-10-18/nonideal-audit/field-regression-summary.json)、[原始日志](../../2026-10-18/nonideal-audit/logs/) |
| 软件与云端 | 最终田间全量 **310 passed**（3 条既有弃用警告），模型/观测/几何及保护 **60 passed**。前端 **110 passed**、类型/生产构建通过，工程 **15 门禁通过**；后两组在最后后端保护修复前完成，其受测代码未变。新增云端 `nonideal-model-contracts` 运行上述 60 项纯代码回归，不冒充 ROS 验收，提交后以对应 main SHA 的 Actions 为准。[后端](../../2026-10-18/nonideal-audit/backend-guard-final.log)、[工程](../../2026-10-18/nonideal-audit/engineering-final/summary.json)、[模型回归](../../2026-10-18/nonideal-audit/guard-model-contracts.log) |

角速度检查增加后，先对 25 场已存真实记录独立复算，原成功/失败结论未变；最后保护修复后再次以最终源码实跑完整 7 场和上述页面闭环。[复算范围](../../2026-10-18/nonideal-audit/angular-stop-rechecks.json)。早期调用错误、2 mm 几何对照失败、组合场次车体未停稳、旧轻度定位超差及主动中断的回归都保留；`all_expected=true` 仅表示该轮明确断言满足，不证明其已经排除后来发现的混杂。[迭代与排除说明](../../2026-10-18/nonideal-audit/iteration-history.json)。本次是工程审计及有界重复实验，不将调试中修订的协议称为独立预注册研究。

**仍未完成的建模：** 可变形土壤、充气轮胎及实测摩擦参数、力矩受限驱动、原始 GNSS/IMU 仿真、初始定位误差、真实能耗、地面风险感知规划与并行交通。当前为球面滚动刚体、理想轮速伺服、已知出生先验和未标定 Q/R；轨迹检查不能证明采样间所有连续运动安全。温和条件成功不保证任意偏置/失锁下可执行。TB4 全部路线及历史桥接退出压力计数未在本轮重跑；本轮桥接仍使用已有 `source_shutdown_join`，`exit_guard=false`。浏览器未新增 console error，Three.js 重复导入/Clock 弃用警告仍保留。

大型原始轨迹、世界和运行目录留本机；Git 保存约束、测试、逐场摘要、原始日志副本和截图。复制的会话报告仅脱敏临时网页桥密钥，原文件与哈希保留；[原始文件索引](../../2026-10-18/nonideal-audit/raw-manifest.json)、[源码指纹](../../2026-10-18/nonideal-audit/source-fingerprints.json)。用户原有文献与 PDF 成果未覆盖或混入本轮提交；未修改系统 Python、ROS、驱动或安全设置。

## 非理想执行原提交报告（2026-10-18，历史，后续独立审计已修正）

以下保留原报告及数值，不能作为当前独立验收结论。其“预注册”“8/8 通过”、参数依据及真值停留可信度均须以上方审计与原始失败证据为准。

**结论：滑移、地面低附着分区和定位观测误差已进入真实执行链路（Gazebo 物理 + ROS 控制 + 独立门禁），预注册的方向性矩阵 8/8 通过：理想参考保持无滑移一致；物理摩擦产生米级里程计-真值发散；定位误差按声明模型进入控制器且无真值直连；失锁触发保持并恢复；极端条件不是干净成功。** 接手基线 `d53e1604`，保留 4100 入口与既有工作台。本轮[最终矩阵汇总](../../2026-10-18/nonideal-execution/matrix/summary.json)、[第一轮](../../2026-10-18/nonideal-execution/round1/summary.json)/[第二轮](../../2026-10-18/nonideal-execution/round2/summary.json)原始记录原样保留；断言预注册与修订史写在 `src/lab/e2e/nonideal_backend.py` 文档串内。

进入执行链路的三类非理想因素（实现：`urp_lab/field_nonideal.py`、`fieldworld.py` 分区 SDF、`ros/field_sim_session.py` 估计器与保护，接口契约见 `src/lab/CONTRACT.md` §7）：

- **滑移（物理，不注入显示层）**：均匀低附着（`physics.ground_friction/wheel_friction` 覆盖）与空间**摩擦分区**（静态 `ground_zones` 薄板，预设 μ≈0.22/0.12/0.05）下，滑移由 DART 库仑摩擦饱和自然产生；轮式里程计按轮角积分自动携带误差，非理想会话不加合成里程计噪声。分区是**低附着地面代理，不是土壤力学模型**（无沉陷/压实/剪切参数）。
- **定位（估计器层）**：真值 → 声明式误差模型（偏置 + 有界随机游走 + 噪声 + 延迟 + 失锁窗口，种子化）生成绝对位置观测；估计器以车体系 twist 积分里程计做预测、标量增益融合观测、0.4 s 斜坡显示。控制器与在线保护只见估计输出；真值仅生成观测与独立评价。`localization_preset=truth` 提供单因素对照。
- **在线保护（无真值）**：估计新息持续 >0.6 m 达 3 s → 疑似打滑降速爬行（30 s 不恢复停车）；观测断流 >15 s → 定位保持、恢复后续航；均记 `execution_events` 并计为干预。

门禁分支：无滑移一致性（≤0.10 m/0.03 rad）只适用于冻结参考配置；非理想执行**或物理偏离参考**（摩擦/胎型）的会话改查数据有效性（覆盖/单调/姿态域）+ 观测模型一致性（频率 ±30%、延迟不早于声明、丢弃必须有因、估计-真值逐点一致判疑似直连），任务可执行性门禁（足迹、双向跟踪 0.5 m、终点 0.35 m、停留、接触、车间距离）不变且仍按真值判定。估计误差与滑移数值是实验结果，不是门槛。

| 案例（断言预注册） | 任务 | 里程计-真值最大发散 | 估计-真值误差均值 | 干预 | 判定 |
|---|---|---|---|---|---|
| ideal（参考回归） | 8/8 完成 | ≤0.10 m 合同内 | —（真值） | 0 | 通过，无滑移门禁保持 |
| slip_uniform（μ=0.3 全场，真值定位） | 8/8 完成 | **14.79 m** | —（真值） | 0 | 通过：任务可执行，滑移真实进入物理 |
| ground_zones（μ=0.12×2 分区，真值定位） | 8/8 完成 | 跨区车 **14.57 m** / 不跨区车 0.001 m | —（真值） | 0 | 通过：单因素隔离成立，分区审计通过 |
| loc_error（轻度定位误差） | 8/8 完成 | 0.027 m | 0.155 m | 0 | 通过：估计在模型包络内，无滑移 |
| outage（10 s 失锁 < 15 s 阈值） | 8/8 到达，**终点停车超 0.35 m 容差→诚实失败** | 0.012 m | 0.338 m | 0 | 通过：无保持触发如声明；失败如实记录 |
| outage_hold（20 s 失锁 > 阈值） | 8/8 完成 | 0.015 m | 0.148 m | 1（保持+恢复） | 通过：保持触发、恢复、计为干预 |
| combined（μ=0.22 分区 + 中度漂移失锁） | 8/8 到达，终点超容差→诚实失败 | 0.62 m | 0.336 m | 0 | 通过：两类扰动均被观测，失败有因 |
| extreme（μ=0.05 + 严重漂移） | 0/8，打滑保护停车 | 0.007 m | 0.430 m | 1 | 通过：非干净成功，保护路径生效 |

实质性发现与如实声明：① 该摩擦量级下滑移主要表现为**航向滑移**——位置发散 14.5–14.8 m 而路径长度超出仅 0.13–0.34 m，方向性断言第二轮起改用发散度（与无滑移合同同量纲），修订史记录在脚本内；② 中度定位漂移（误差 ≈0.34 m）下任务点全部到达但**终点停车超出 0.35 m 容差**——这是实验结果而非缺陷，说明该容差下定位误差直接约束可执行性；③ 严重观测偏置（0.92 m）会触发新息打滑告警（保护停车）——没有更多传感器时**大偏置与滑移在原理上不可区分**，已声明为限制；④ 估计器里程计预测曾因把固定系位置增量当车体增量（44 m 发散）和信任模型过于乐观（滞后 1.4 m）两次返工，修复后以历史会话数据离线重放验证（innovation 均值 0.072 m）。

验收过程中发现并修复的集成缺陷（第一/二轮原始记录保留）：分区盒接触被 `'ground::'` 前缀误判为外部碰撞（误杀会话）；物理偏离会话仍套理想无滑移门禁；`truth_distance` 未在物理对照路径初始化；等待车辆误入爬行。修复后全量复验：`src/lab` 286 项单元测试、`tools/check_project.py` 15 项门禁、前端 typecheck+build、field_backend 全案例回归、真实浏览器操作（执行条件面板、预设联动、证据展示）与重启复现均通过。

参数依据（全部**未标定**，详见 `field_nonideal.py` 头注与 PROVENANCE）：分区 μ 取橡胶—湿/松地表文献范围（约 0.05–0.35）内的工程假设，μ≤0.08 标注为压力测试；定位噪声/偏置/漂移取消费级绝对定位量级的工程假设；估计器 2% 里程 + 0.08 m/s 滑移底噪的信任模型为工程假设。**摩擦分区不等于土壤建模，合成观测模型不等于真实传感器，仿真通过不构成真实农田结论。**

可复现：`cd src/lab && .venv/bin/python e2e/nonideal_backend.py --out /新的绝对路径`（与其它物理验收互斥）；证据：[最终矩阵](../../2026-10-18/nonideal-execution/matrix/summary.json)、[round1](../../2026-10-18/nonideal-execution/round1/summary.json)、[round2](../../2026-10-18/nonideal-execution/round2/summary.json)、[field 回归](../../2026-10-18/nonideal-execution/field-regression/)、[界面截图](../../2026-10-18/nonideal-execution/ui-execution-setup.png)。大体积逐点原始数据按 `.gitignore` 留在本机 `matrix/runtime/`。

剩余限制：驱动仍为理想关节速度伺服（无扭矩饱和环节，μ 很低时轮速仍被伺服保持）；分区为轴对齐矩形；定位观测为位置-only 合成模型（无原始 GNSS/IMU 报文级仿真）；极端条件下的保护停车由新息告警驱动，不能区分大偏置与真实滑移；能耗仍为后验代理。

## 里程计、物理一致性与 gzweb 验收（2026-10-18，历史）

**结论：默认田间模型的轮式运动学与真实 Gazebo 轨迹已通过限定范围验证；gzweb 的断连竞态、销毁和本地 STL 加载问题已修复并实际操作复验。** 接手基线 `5e333297`，保留原工作台和 4100 入口。最新步骤只维护在 [setup/README](../../README.md)，本轮[结构化汇总](../../2026-10-18/physics-consistency/summary.json)和[源码指纹](../../2026-10-18/physics-consistency/source-fingerprints.json)对应本次验收；历史数据不回写为新版通过。

旧圆柱接触模型在原地旋转中存在明显侧向擦滑及转角偏差。此次同协议开环对照中，参考转角 2.32 rad，圆柱接触实测约 2.683 rad、组合运动末端里程计偏差约 1.576 m；球面滚动参考约 2.318 rad、末端约 0.00309 m。改变圆柱接触宽度时偏差随之显著变化，原始接触点支持有限宽接触与理想差速运动学不一致这一解释，**不据此宣称已证明 DART 内部的唯一根因或完成真实轮胎标定**。[原模型对照](../../2026-10-18/physics-consistency/baseline/summary.json)、[接触宽度诊断](../../2026-10-18/physics-consistency/contact-diagnosis/summary.json)

默认执行改为 `urp.field-diffdrive.rigid/5` 的球面滚动参考，沿用已安装官方 DiffDrive 示例的抽象。圆柱轮保留为显式诊断选项，不能放宽验收门槛。修正 v4 足迹遗漏球面轮外侧投影的问题：可视几何、碰撞几何、轮轴/轮距、惯量与冻结足迹一起核验；规划外接圆半径仍为 0.4882622 m。新结果必须匹配冻结计划的物理模型与 SDF 核验，删除模型、修改轮距或删除协议均会拒绝。里程计仅对齐首个共同姿态；全程最大位置误差须 ≤0.10 m、航向误差 ≤0.03 rad，采样缺口 ≤0.25 s，且里程计须覆盖完整真值观测区间。[篡改负对照](../../2026-10-18/physics-consistency/frozen-result-negative.json)

gzweb 保持锁定 3.0.2，用仓库中的安装补丁和原始/结果 SHA256 保证可复现：改为按 WebSocket 帧顺序同步解码，旧 socket 和旧资源回调不再修改新会话；按字节解析 UTF-8 帧头；正确释放 renderer 与 controls；服务端绝对 STL 路径直接通过认证资源通道加载，并适配该版本实际返回的 Mesh/字节类型。页面显示资源数量、缺失/超时和真实连接错误，提供重新连接；没有屏蔽 console 或把失败改为成功。实时物理证据改用轻量接口，完整原始轨迹仍用于导出/回放：本次记录响应 **8,371,704 → 17,411 字节**；5 个并发证据请求与健康检查均完成，量测范围见[原始数据](../../2026-10-18/physics-consistency/ui-field-summary.json)。

| 本轮验收 | 结果与证据 |
|---|---|
| 物理参考与敏感性 | **21 个标称/载荷试验通过**：4/2/1 ms、+20 kg、独立的更高直行速度/反向旋转/圆弧协议。3 个低摩擦和 2 个有效圆柱负对照均未通过无滑移一致性，不计为成功。[primary](../../2026-10-18/physics-consistency/reference-primary/summary.json)、[串行 holdout](../../2026-10-18/physics-consistency/reference-holdout-serial/summary.json)；最终数据通路另复验标称/载荷通过、圆柱负对照失败，[3 场明细](../../2026-10-18/physics-consistency/reference-transport-summary.json)，不并入 21 次计数 |
| 正式页面相同冻结路径 | 同一 PyVRP `run_20261018T043004_829b68`：最终数据通路下 **8/8、297.81 m、363.34 s**，最大横向偏差 0.096 m；轮式里程计全轨迹最大偏差 **9.68 mm**，真值最大间隔 **0.02 s**。控制输入曾延迟，每车记录的 356 个超时效周期均发零指令；没有删除这些周期或调整实际用时。上轮同方案旧模型末端约 **27.051 m**。新会话物理、源时间、轨迹和关闭检查通过，rc=0、无所属残留。[最终结果与关闭](../../2026-10-18/physics-consistency/ui-transport-final.json)、[页面](../../2026-10-18/physics-consistency/ui-transport-completed.png)、[首次对照](../../2026-10-18/physics-consistency/ui-field-summary.json) |
| 9 场真实集成与故障 | free、rows、supply、restart、web 成功；cancel 为 canceled/0；clock_stall、bridge_exit、实体接触为 failed/1。全部清理 PASS、无所属残留。网页桥真实消息与 RViz 开关也复验。[最终数据通路逐场摘要](../../2026-10-18/physics-consistency/live-transport-summary.json)、[日志](../../2026-10-18/physics-consistency/live-transport.log)；调整前 9 场保留在同目录 `live-summary.json` |
| 正式页面 gzweb | 最终安装补丁下连续 5 次规划/仿真工作区切换，均重新连接并显示 **10/10 资源**，正常窗口无新增 console error。定向终止本会话可选网页桥，页面报连接失败；任务继续 8/8、有序关闭后重新启动恢复完整场景。网页桥进程收到 SIGTERM 后真实退出码为 0，其异常离线仍保留为警告，不宣称网页桥成功。[切换](../../2026-10-18/physics-consistency/ui-gz-delivery.json)、[故障关闭](../../2026-10-18/physics-consistency/ui-web-fault-close.json)、[恢复截图](../../2026-10-18/physics-consistency/ui-gz-recovery.png) |
| 实验导出 | 正式按钮实际下载约 56.5 MB，包含同一冻结方案的 6 次执行及其中 2 次失败；导出 `record` 与 API 逐值一致，SHA256 重算相符。原始大包保留本机。[导出核对](../../2026-10-18/physics-consistency/ui-export-verification.json) |
| 软件与工程 | 最终田间全量 **278 passed**（包含冻结模型准入和控制时效回归）。前端 **110 passed**，锁定安装、类型/生产构建通过；工程 **15 门禁通过**。[后端](../../2026-10-18/physics-consistency/backend-transport.log)、[串行验收命令与退出码](../../2026-10-18/physics-consistency/transport-verification.json)、[前端](../../2026-10-18/physics-consistency/frontend-delivery.log)、[工程](../../2026-10-18/physics-consistency/project-delivery/summary.json) |

末轮网页恢复还暴露了真实数据通路问题：`field_c5568a` 最大真值缺口 0.32 s、横向偏差 1.135 m；首次可靠订阅调整的 `field_3878e4` 仍有 0.98 s 缺口，均保留 failed/1。后者的源时间保护将偏差限制在 0.108 m，但不能因此判作通过。约 21 秒剖析中，执行器调度占约 19 秒；发现重复时钟消费、桥接默认深度 10 与订阅深度 200 不匹配。最终使用唯一的最新源时钟、显式源时间 Marker、桥接/订阅配套的可靠历史及常驻执行器；超过 0.10 s 的控制姿态时差只发零指令、不推进任务，逐周期 `pose_age_s` 随完整轨迹保存。不抽稀接触检测数据、不放宽缺口验收。[初次失败](../../2026-10-18/physics-consistency/ui-recovery-failure.json)、[中间失败](../../2026-10-18/physics-consistency/ui-reliable-intermediate.json)、[剖析](../../2026-10-18/physics-consistency/callback-profile.txt)、[最终传输复验](../../2026-10-18/physics-consistency/ui-transport-final.json)。

测试中的失败同样保留：曾提前启动第二批物理试验造成 `/clock` 串流，明确排除两条受影响记录，后续串行重跑。现在田间、TB4、开环探针在初始化 ROS 前共用内核文件锁，跨网关/运行目录也拒绝并发，进程退出自动释放；真实并发拒绝与崩溃释放已有回归。[排除说明](../../2026-10-18/physics-consistency/overlap-exclusion.json)、[真实拒绝](../../2026-10-18/physics-consistency/live-concurrent-rejected.log)。另一次工程检查与 `npm ci` 并发导致临时缺失 tsc，原失败保留于 `project-release/`，顺序重跑 `project-delivery/` 全部通过。错误工作目录和错误 npm 脚本调用的日志不计入通过。正式网关在收到 SIGINT 后有序清理，旧 shell 包装进程返回 130，不能写作 rc=0。

**验证边界：** 当前是平地刚体、理想轮速伺服、真值定位和串行任务预约；球面滚动模型解决本合成差速平台的一致性，有限宽轮胎、可变形土壤、定位估计器、执行器力矩限制、真实能耗仍未校准。0.10 m/0.03 rad 是版本化仿真验收门槛，不是农业作业精度认证；采样轨迹检查也不扩大为连续时间安全证明。未重新验收 TB4 的全部路线或历史桥接退出压力计数。Three.js 重复导入/Clock 弃用及已有构建告警保留，未作零告警承诺。无外部付费、系统 Python/ROS/驱动改动或历史证据删除；用户原未提交文献与 PDF 工作原样保留。

大型世界、完整轨迹和接触数据留在本机 `physics-consistency/{baseline,contact-diagnosis,reference-primary,reference-holdout,reference-holdout-serial,live}/` 与 `src/lab/runtime/fieldsim/`，Git 保存源码、测试、摘要、日志和截图。含临时会话密钥的原始 API 包保留于忽略目录，提交副本仅脱敏该字段；证据文件和原始大文件的散列见[证据索引](../../2026-10-18/physics-consistency/evidence-manifest.json)。

## 路径规划工作台前后端验收（2026-10-18，前一轮）

**结论：接续 KIMI 未完成的工作，现有 4100 工作台的编辑、求解比较、独立检查、真实执行、回放、导出与关闭流程已实测；保留限定模型范围。** 未另建前端或替换系统 Python。接手基线为 `4efc6771`，KIMI 的 20 个前端文件先保存为 `caf7120d`，再集成修复；原未提交文献/PDF 工作未覆盖或混入提交。后续使用仍只看 [setup/README](../../README.md)。结构化结论为 [summary.json](../../2026-10-18/planning-integration/summary.json)，源码指纹与证据索引同目录。

本轮实质修复：

- 点到点等非车队模型进入比较表时读取 `vehicles.fleet` 导致整页白屏；实验面板也曾把抽象车队字符串当资产对象读取。两处按问题类型/字段形状处理，并将面板置于错误边界；实验仿真数据按 run_id 隔离。实际错误截图与修复后浏览器证据均保留。
- 固定完整输入、坐标、单位、目标、地图内容和覆盖检查协议；冻结实际提交实例，编辑后不沿用原 BKS。比较不混合预算/模型，内部代理搜索曲线不冒充验算目标。算法种子是否实际生效随目录和记录显示。
- 通用资产检查误拒合法 CVRP 的 `unlimited_identical_vehicles` 字符串；现在按明确的问题类型区分抽象车队与资产绑定，非法资产引用仍拒绝。真实 API 复现、修复后正常求解及取消均已验证。
- Fields2Cover 全流程把 PathState 起点当作全部路径，漏掉最后约 4.4 m 的作业段。新版包含 `path.atEnd()`，保留完整几何，统一 ≥90% 内缩作业区、实际作业行归属、严格扫掠足迹和采样曲率检查。示例 **23.343 → 27.742 m**，作业行距离 **17.6 → 22.0 m**，全田块覆盖 **36.7% → 45.8%**、内缩作业区 **100%**。旧全流程记录原样保留、标为须重求解、退出统计排序及执行；倒车切换未独立适配的尖点不通过，含倒车段的覆盖结果不交给 TB4。
- 场景 revision 冲突返回 409，页面保留草稿并支持另存副本；同 ID 的迟到加载与旧历史响应不覆盖新状态。JSON 输入先校验，未支持的时间窗等约束明确拒绝。前端 HTTP 请求有边界时间，断连和失败保留原因。
- 回放按源时间计算当前时刻的指标，区分到点与完成停留；跨缺流不插值、不计跨口里程，不单调时钟拒绝单轴重放。索引缓存与显示抽稀不改原始数据。TB4 按声明终点容差独立判定成功，拒绝把点到点 Dubins/Reeds–Shepp 丢失方向语义后执行。

| 本轮验收 | 结果与证据 |
|---|---|
| 工程与软件回归 | 工程 **15 门禁通过**，田间 Python **270 passed**，前端 **101 passed**、类型检查及生产构建通过。保留 3 条既有 Python 弃用警告及既有构建 chunk/重复导入警告；不声称零警告。[工程](../../2026-10-18/planning-integration/project-delivery/summary.json)、[后端](../../2026-10-18/planning-integration/backend-delivery.log)、[前端](../../2026-10-18/planning-integration/frontend-history.log)、[构建](../../2026-10-18/planning-integration/build-history.log) |
| 正式页面田间闭环 | 页面生成 60×40 m 果园，2 车 8 点；车库 X=3 经保存、刷新及 API 核对；规则推荐、三算法求解/验算、PyVRP 行间执行。**8/8、297.81 m / 342.06 s，最大横向偏差 0.0915 m**；关闭 rc=0、清理 PASS、无所属残留。[核验](../../2026-10-18/planning-integration/ui-field-verification.json)、[页面](../../2026-10-18/planning-integration/ui-field-completed.png)、[日志](../../2026-10-18/planning-integration/ui-field-logs/) |
| 回放与完整导出 | 浏览器起点为 0 完成/0 m，终点为 8 完成/约 298 m；与冻结 PyVRP 方案叠加。下载执行结果的指标和轨迹与 API 相同；完整实验 `record` 与 API 相同，内容 SHA256 重算相符。envelope 的 `assembled_utc` 随请求变化，不要求不同请求的整文件字节相同。[导出核对](../../2026-10-18/planning-integration/ui-experiment-export.json)、[起点](../../2026-10-18/planning-integration/ui-replay-start.png)、[终点](../../2026-10-18/planning-integration/ui-replay-end.png) |
| TB4 + Nav2 实际页面执行 | 新建 1.15 m 迷宫短路径、声明容差 0.06 m、显式真值定位、follow_path。实际误差 **0.05795 m**；取消为 canceled，Nav2 明确接受；取消后重试 succeeded、误差 **0.05736 m**。会话 PASS，全部所属启动组退出码 0，无异常退出与残留。[首次](../../2026-10-18/planning-integration/tb4-success-run.json)、[取消](../../2026-10-18/planning-integration/tb4-canceled-run.json)、[恢复](../../2026-10-18/planning-integration/tb4-recovered-run.json)、[关闭](../../2026-10-18/planning-integration/tb4-session-report.json)、[页面](../../2026-10-18/planning-integration/ui-tb4-goal-tolerance.png) |
| 消息与退出范围 | 本次 TB4 的 clock、真值、scan、odom、cmd_vel 和 map→base TF 均实际收到；另读出 640 束激光及安装 TF。桥接记录为 `source_shutdown_join`，`exit_guard=false`，没有用 `_exit` 垫片把本次结果归零。仅本次一个 TB4 会话/三次执行，未重跑历史 560/400 压力计数，也不据退出检测位置证明内存安全根因全部解决。[状态计数](../../2026-10-18/planning-integration/tb4-success-state.json)、[激光快照](../../2026-10-18/planning-integration/tb4-sensor-snapshot.json)、[原始日志](../../2026-10-18/planning-integration/tb4-logs/) |
| 五场真实故障/恢复 | cancel 为 canceled/0；clock_stall、bridge_exit、实体车间接触分别 failed/1；随后 restart succeeded/0。每场清理 PASS、所属残留为空。意外桥接即使自身收到 SIGTERM 后返回 0，也由会话判作失败并返回 1。[逐场汇总](../../2026-10-18/planning-integration/field-fault-summary.json)、[原始日志](../../2026-10-18/planning-integration/field-faults.log) |
| 无解、超时与异常 | 正式 API 的不可行结果没有路线/目标，精确算法真实超时、求解取消、工作进程异常分别 timeout/canceled/error；异常工作进程真实非零退出。[结构化结果](../../2026-10-18/planning-integration/software-api-acceptance.json) |
| 四类规划页面与恢复 | 点到点 3 解；CVRP 3 解恢复正常提交；覆盖新版 3 解；转弯 DP 与原图搜索均 46，松弛 45 因 2 处违规被排除。时间窗拒绝保留输入；实际制造 409 后另存草稿，服务器新标题/0.06 与草稿副本/0.07 分别保留。[覆盖](../../2026-10-18/planning-integration/ui-coverage-runs.json)、[CVRP](../../2026-10-18/planning-integration/ui-cvrp-runs.json)、[路网](../../2026-10-18/planning-integration/ui-turn-runs.json)、[冲突恢复](../../2026-10-18/planning-integration/ui-conflict-recovery.json) |
| 网关重复启停 | 3 次真实网关进程有序退出均 0，其中 2 次保持 SSE 连接，关闭约 0.315 s；不以 HTTP 200 代替进程退出证据。[原始结果](../../2026-10-18/planning-integration/gateway-lifecycle/summary.json) |

执行与模型边界：田间仍为同构差速车、真值定位、平地刚体及串行整段交通预约。网页场次轮式里程计末端漂移 **27.051 m**，不能声称定位/轮胎校准通过；能耗仍为版本化后验代理。TB4 激光快照的原始真值 TF stamp 为 0，保留原值；本轮未把该快照称为完成真值源时间标定。Fields2Cover 全流程本轮复验规划与检查，未重跑该新版完整覆盖路径的物理执行；正向曲率/异构规划通过也不扩大为车辆可执行证明。时间窗、优先关系、混合线/面任务和动态障碍等没有实现，明确拒绝，不用 JSON 字段伪装支持。学习推荐仍是先前限定范围的小样本原型，未新增训练或证明推荐增益。

最终历史实验切换与回放复验未新增控制台 error；整轮原始控制台仍包含已修复的两处类型错误，以及一次 gzweb `Protobuf root has not been created` 诊断。第三方 SDK 的异步读包在断连后可遇到已清理的 protobuf root，源码存在这一先后顺序；本次没有据此单条日志断言唯一原因，也没有修改依赖或宣称网页 3D 嵌入视图全部稳定。原生 Gazebo/RViz 与规划执行链独立；该嵌入视图诊断列为剩余问题。[原始控制台](../../2026-10-18/planning-integration/ui-console-history.json)、[最终复验](../../2026-10-18/planning-integration/ui-console-final.json)

回放性能使用本轮真实 **34,305** 个轨迹样本做单次本地 Node 量测：冷索引约 **40 ms**，10,000 次已缓存时刻指标查询合计约 **13.6 ms**；只含数据运算，不含网络/渲染，不是统计性性能结论。[输入与测量范围](../../2026-10-18/planning-integration/replay-performance.json)

原始大型会话/世界/轨迹保留在本机 `planning-integration/live/`、`src/lab/runtime/fieldsim/field_20261018T043031_bd0849/`、`src/lab/runtime/sim/sim_20261018T043621_c2b11c/`，完整 envelope 为本机 `ui-field-record.json`；源码、摘要、日志、截图及独立回归进入 Git。KIMI 的未完成验收、首次 CVRP 409、测试夹具错误和旧覆盖短路径均保留，不改判成通过。本轮没有安装新依赖、付费、使用凭证、修改驱动/系统 Python/ROS，也没有删除历史数据。

## GitHub + KIMI 全量集成收尾（2026-10-17—18，前一轮）

**结论：现有工作台与云端数值/科研改动已整合，限定仿真闭环通过；`main` 作为后续云端 Agent 的完整工作基线。** 启动、停止与恢复仍只维护在 [setup/README](../../README.md)，云端环境和模块边界见根目录 [AGENTS](../../../AGENTS.md)。接手时先备份全部本地未提交文件，提交为 `1700deb4`，再以正常 merge `2c89150f` 纳入 GitHub 分支 `483d3d95`。原 main、cvrp-baselines、experiments-handoff、optimize 分支及本地 workbench 都在合并祖先链中；历史 backup 分支保留原指向，不作为滚动工作分支。[合并来源](../../2026-10-17/integration-closeout/integration-lineage.json)

保留 KIMI 的 macOS 工作台、编辑与输入失效处理，并接入 `#/jobs`。修复网关与新档案后端的 Host/Origin 代理冲突；根 Python 环境按锁文件准备，不替换田间或 ROS 环境。实际操作还发现 Dockview 嵌套 padding 使地图覆盖相邻标签，已取消该布局干预；底栏分别显示田间与 TB4 状态，避免田间执行时显示“未运行”。实验按钮下载服务端原始 JSON，保留浮点字面量和完整 envelope。

建模修复包括：统一车库→任务→补给点的图编号；补给副本不再误作必访任务，修正分趟边界和路线提取；执行转换使用声明补给点；识别重复点间的瞬时折返；任意超过 0.25 s 的真值/里程计缺口均拒绝验收；固定 0.2 s 能耗滤波窗；允许小容量车承担小需求；构造基线不再推荐给异构/多库输入。新方案标记 `urp.fleet-model/2`，旧补给方案在服务端也必须重求解。能耗标记 `urp.field-energy.post_hoc/3`，旧记录不改写。[独立缺陷复验](../../2026-10-17/integration-closeout/reproductions-after/reproductions.json)

| 验收 | 结果与证据 |
|---|---|
| 工程门禁本机复验 | **15 门禁通过**：档案/Jobs、研究、生成器、进程管理、独立验证器、机器人核心、前端与 BPP 历史读回等；不包含物理仿真。[汇总](../../2026-10-17/integration-closeout/engineering/summary.json) |
| 田间 Python 回归 | **242 passed**；随后仿真转换修复的 80 项及其消费者 81 项复验通过，最终建模回归通过。首次测试夹具错误与日志保留，不改判。[全量](../../2026-10-17/integration-closeout/pytest-final.log)、[转换](../../2026-10-17/integration-closeout/fieldworld-regressions-pass.log)、[消费者](../../2026-10-17/integration-closeout/fieldworld-consumers.log)、[最终模型](../../2026-10-17/integration-closeout/model-regressions-final.log) |
| 前端 | 类型检查、**78 tests**、最终生产构建通过。仍有已有大 chunk / 重复静动态导入警告。[测试](../../2026-10-17/integration-closeout/frontend-tests-release.log)、[构建](../../2026-10-17/integration-closeout/build-release.log) |
| 7 场真实 Gazebo 回归 | free、rows、restart 成功；cancel 为 canceled/0；时钟停滞、桥接退出、实体接触为 failed/1。全部清理 PASS、无所属残留；未把故障归零。[逐场结果](../../2026-10-17/integration-closeout/live-summary.json) |
| 中途补给真实执行 | 两车 8/8，**250.76 m / 293.49 s**，最大横向偏差 0.0675 m；执行检查及关闭 PASS。首次世界转换失败保留，修复后重新完整运行。[通过](../../2026-10-17/integration-closeout/supply-summary.json)、[首次失败](../../2026-10-17/integration-closeout/supply-first-failure.json) |
| 网页完整流程 | 生成 60×40 m 果园→车库 X 改为 3→保存/刷新→三算法→独立检查→PyVRP Gazebo→8/8→关闭→下载实验。**297.84 m / 342.21 s**，最大横向偏差 **0.0899 m**。浏览器下载与 API record 相同，SHA256 重算相符，清理 PASS。[核验](../../2026-10-17/integration-closeout/ui-verification.json)、[完成截图](../../2026-10-17/integration-closeout/ui-completed.png)、[会话汇总](../../2026-10-17/integration-closeout/ui-session-summary.json) |
| CVRP 数值任务网页 | 通过正式 4100 代理提交六点示例，独立成本 **123**、两条路线，导出成功，刷新及网关重启后记录保留；不混入正式基线。[原始导出](../../2026-10-17/integration-closeout/ui-jobs-export.json)、[截图](../../2026-10-17/integration-closeout/ui-jobs.png) |
| 重复启停与目录 | 正式网关多次有序关闭；可观测的网关及其档案子进程退出码均为 0，随后统一入口恢复服务，仿真保持关闭。只读盘点未删除数据，唯一断链为旧独立 JDK 的可选源码包。[关闭日志](../../2026-10-17/integration-closeout/gateway-release.log)、[盘点](../../2026-10-17/integration-closeout/folder-audit.json) |

范围限制仍然有效：规划曲率检查是采样几何检查；异构、正最小转弯半径或未适配车辆仅可做规划研究，执行如实拒绝。补给是零处理时间、抽象容量重置，不含真实卸载机构或动态载荷。执行为真值定位、平地刚体和串行交通基线；本次网页两车的轮式里程计最大末端漂移约 **26.585 m**，已在页面和原记录显示，**不能声称里程计/轮胎校准通过**。能耗仍为后验纵向代理，缺少电机/电池/转向摩擦模型。并行避碰、可变形土壤、通用导入车辆执行和学习推荐增益尚未交付；这轮未重跑旧 TB4 压力试验或证明论文级算法优越性。

证据统一在 `setup/2026-10-17/integration-closeout/`；[源码指纹](../../2026-10-17/integration-closeout/source-fingerprints.json) 对应测试字节。Git 保留代码、协议、摘要、日志和截图；大体积原始世界/遥测仍在本机 `live/`、`live-supply-fixed/` 与 `src/lab/runtime/fieldsim/field_20261017T145457_3ba96a/`，浏览器原始 envelope 为本机 `ui-browser-record.json`，按 `.gitignore` 不上传。重新运行 `src/lab/e2e/field_backend.py --out /全新绝对目录` 可复验新增 supply 在内的场景；不要与其他物理测试并发。

云端首次同步的 `133256a3` 在 main 的 [GitHub 检查](https://github.com/peakpeng666/URP/actions/runs/39043256121) 中，14 门禁通过、合成进程监督测试缺少就绪文件而失败；同一提交的另外两分支通过。原始日志未保留子进程错误，不能据此断言唯一触发原因。审查修正两处测试时序隐患：三级就绪文件改为原子发布，350 ms 故障计时改为就绪后开始，启动单独限 2 s；保留原 4 s 总限与生产监督逻辑，并在未就绪失败时附上子进程日志。新增慢启动/启动超时回归后 **27 项通过**；旧计时方式在 450 ms 启动延迟下的失败负对照、10 轮重复启停（80 次 worker 启动）的结果分别留存。云端最终状态以 [main 的 Actions](https://github.com/peakpeng666/URP/actions?query=branch%3Amain) 为准。[首次失败](../../2026-10-17/integration-closeout/supervision-ci-first-failure.log)、[修复后](../../2026-10-17/integration-closeout/supervision-ci-fix.log)、[负对照](../../2026-10-17/integration-closeout/supervision-ci-old-deadline.log)、[重复结果](../../2026-10-17/integration-closeout/supervision-ci-repeat.json)

## GLM 增量独立验收（2026-10-17，合并前快照）

**结论：基础田间闭环复验通过，新增能力不予整体通过。** 基线 `056b857c302b7a04775336630c317bf84eda0ae7`，接手时工作区干净；本轮只增加独立验收证据、前端交接提示词并修正文档，未修改应用源码。354 个后端/前端源码和配置文件的起始指纹见 [takeover.json](../../2026-10-17/glm-acceptance/takeover.json)，最终核对见 [结构化汇总](../../2026-10-17/glm-acceptance/summary.json)。正式 4100 网关保持运行；仿真测试使用隔离存储、端口和 ROS 域，不覆盖用户实验。

| 独立复验 | 结果与证据 |
|---|---|
| Python 回归 | **226 passed**，3 条已有弃用警告；[原始日志](../../2026-10-17/glm-acceptance/pytest.log) |
| 前端检查 | 类型检查、42 项 UI 测试、Vite 隔离构建通过；仍有大 chunk 和静态/动态重复导入警告。[typecheck](../../2026-10-17/glm-acceptance/typecheck.log)、[测试](../../2026-10-17/glm-acceptance/frontend-tests.log)、[构建](../../2026-10-17/glm-acceptance/build.log) |
| 自由通行，2 车 8 点 | succeeded/0，8/8，实际 216.45 m、253.24 s，最大横向偏差 0.1178 m，清理 PASS、所属残留为空 |
| 行间通行，2 车 8 点 | succeeded/0，8/8，实际 369.88 m、428.01 s，最大横向偏差 0.1278 m，清理 PASS、所属残留为空 |
| 取消、时钟故障、故障后重启 | 分别 canceled/0、failed/1、succeeded/0，故障被看门狗明确记录；3 场清理均 PASS、所属残留为空。以上 5 场的[汇总](../../2026-10-17/glm-acceptance/live/summary.json)、[原始日志](../../2026-10-17/glm-acceptance/live.log)、[会话文件](../../2026-10-17/glm-acceptance/live/runtime/fieldsim/) |
| 开环物理对照 | 当前默认轮胎接触与 sphere 对照各 1 场，真实 Gazebo、正常退出、清理 PASS；[原始结果](../../2026-10-17/glm-acceptance/physics/summary.json)、[日志](../../2026-10-17/glm-acceptance/physics.log) |
| 浏览器与导出核对 | 实际打开已有实验、回放/结果界面并点击下载，确认状态文案和导出问题；[宽窗口截图](../../2026-10-17/glm-acceptance/ui-desktop.png)、[窄窗口截图](../../2026-10-17/glm-acceptance/ui-result-state.png)、[DOM](../../2026-10-17/glm-acceptance/ui-result-state.txt)。本轮没有从浏览器新建并跑完整实验；新增 5 场闭环由隔离验收脚本执行，不能替代 KIMI 的最终浏览器全流程验收。 |

本轮检查范围内有 **8 项后端/契约问题、5 项前端对接问题**；它们是当前版本的缺口，不把每项都归因于某个作者。完整定位、复现性质与修复要求见 [findings.json](../../2026-10-17/glm-acceptance/findings.json)。

| 编号/优先级 | 问题及实证 | 验收要求 |
|---|---|---|
| B1 / P1 | `fleet_engines.py:195` 提取错补给边界；ORTools 原解工期 **133.3 s**，解码路线改为趟间返库、最后绕行补给点，变成 **203.933067 s**，仍 feasible/check.valid。`fleet.py:685` 的 reload_stops 还误用了遗留变量。见 [原解与输出](../../2026-10-17/glm-acceptance/unload-free.json)。 | 原始节点映射、补给顺序、解码路径与目标一致；测试须真正使用补给点，不能以全返库代替。 |
| B2 / P1 | `fleet.py:132` 的 rows 图按 depot→unload→task 排列，求解器按 depot→task→unload 解释；合法 8 点实例所有作业位置错配并 no_solution。[复现](../../2026-10-17/glm-acceptance/unload-rows.json)。 | 统一节点映射，复验 rows/free × 单/多库 × 有/无补给。 |
| B3 / P1 | `fleet_curvature.py:13` 把共线反向也判作无穷大半径：Rmin=2 m、前进 1 m 后原线折返，0.05 m 采样，检查仍通过。[反例](../../2026-10-17/glm-acceptance/curvature-reversal.json)。 | 航向连续性、倒车语义、退化段与 SE(2) 路径证据独立检查；三点圆拟合不足以证明前进 Dubins 可行。 |
| B4 / P1 | `field_dynamics.py:33` 用缺口两端位姿相同豁免采样缺失；t=0/10/20 s 三个位姿、轮速 1 m/s，仍 valid。[反例](../../2026-10-17/glm-acceptance/telemetry-gap.json)。 | 超阈值未知区间应拒绝或有覆盖全区间的独立静止证据。此为离线检查边界反例，本轮真实成功会话最大源采样间隔约 0.02 s，未据此声称真实会话缺流。 |
| B5 / P2 | `field_energy.py:48` 按全程样本数选滤波窗。同一约 5 m 运动添加 0/120/600 s 静止尾段，能耗 **97.6458/87.9613/66.1946 J**，最后下降 **32.2%**；峰值功率也改变。[复现汇总](../../2026-10-17/glm-acceptance/reproductions.json)。 | 固定物理时间尺度，验证停车尾段/采样率不变性；当前不可用于可靠能效排序。 |
| B6 / P2 | 容量 1/10 两车、需求 1/10 两任务有可行分配，但校验要求小车也能装最大任务，整体拒绝。 | 按每任务可服务车型集合检查；不要将小车“不服务大任务”误写成“不能服务任何任务”。 |
| B7 / P2 | 异构输入仍默认比较 `panel.cluster_first`，三种构造基线 applicable=true，求解端却明确不支持。[接口复现](../../2026-10-17/glm-acceptance/frontend-contract.json)。 | 推荐、默认选择、提交准入统一；前端临时过滤须有原因及可移除条件。 |
| B8 / P2 | 默认 API 是 rigid/4，使用说明原为球面 v3；`model_contract` 仍写多库 makespan 不支持、仅返库补给。 | 本轮已修正使用说明与历史标题；后端输出契约和其他旧接口文案仍需统一，不改写历史原始记录。 |
| F1 / P1 | 车库编辑仅改 `depot`，后端按未变的 `depots[0]` 恢复旧坐标。[往返复现](../../2026-10-17/glm-acceptance/frontend-contract.json)。 | 统一编辑模型、兼容别名和保存后归一化回写，浏览器保存/重开验证。 |
| F2 / P1 | 实际 signature 函数对 service_s、第二车库、补给点、走廊、fleet 类别的变化均不变。[五类反例](../../2026-10-17/glm-acceptance/frontend-signature.json)。 | 完整规划输入稳定指纹，当前草稿与历史输入明确分开。 |
| F3 / P1 | 当前结果有串行交通与接触检查，页面仍固定写“屏蔽车间碰撞/未验收”。 | 按每个结果显示，未知与零不同，串行基线与并行避碰不同。 |
| F4 / P1 | 实际按钮下载 [schema /1](../../2026-10-17/glm-acceptance/ui-export.json)，无 integrity；[服务端 /2](../../2026-10-17/glm-acceptance/server-record.json) 完整记录 SHA256 独立复算一致。 | 下载服务端完整 envelope，保留全部会话与关闭报告；散列不是身份签名。 |
| F5 / P2 | 619 px 窗口里按钮 x=717，页面无可达横向滚动；1440×900 同按钮正常下载。 | 宽/中/窄三档窗口均能完成核心操作。 |

**物理结论的边界**：v4 默认是轮胎接触刚体模型，仍使用理想关节速度伺服与真值定位。此次轮胎开环原地转向 2.68710 rad，解析参考 2.32 rad，偏差 **0.36710 rad / 15.82%**；组合运动末端轮式里程计漂移 **1.57769 m**。sphere 对照分别为 **0.001756 rad / 0.003084 m**。两者的 `reference_conformance=true` 使用不同门槛：轮胎采用经验滑移 ≤20%，sphere 采用理想滚动误差 ≤0.15 rad。**门槛变化不证明轮胎真实度更高或接触根因已修复**，也不能将本轮各 1 次对照说成重复统计验证。自由通行实际任务两车末端轮式里程计偏差为 **11.534/6.500 m**，真值控制仍能跟踪；跟踪成功不能替代定位/轮胎模型验证。已有 GLM 敏感性及 holdout 原始报告保留，本轮未重跑其全部样本。能耗为后验代理且有 B5；没有实测轮胎/土壤校准、电池或环内力矩模型。

**可复验命令**：在 `src/lab` 使用现有 `.venv/bin/python`；输出目录必须是新的绝对路径，两个脚本顺序运行：

```bash
.venv/bin/python e2e/field_backend.py --out /一个新的绝对路径/field --cases free,rows,cancel,clock_stall,restart
.venv/bin/python e2e/physics_backend.py --out /一个新的绝对路径/physics --cases step4ms,sphere_legacy --repeats 1
```

后端最小反例脚本为 [reproduce_findings.py](../../2026-10-17/glm-acceptance/reproduce_findings.py)，前端指纹/往返探针与全部日志一并保存在该目录。三个探针均要求 `--out /新的绝对目录`，拒绝覆盖既有目录；Python 探针使用项目 `.venv/bin/python`，JS 探针使用 `node`。修复应先用这些反例证明失败，再验证真实流程，不通过修改证据或放宽门槛获得“通过”。KIMI 的完整任务提示词见 [前端交接任务](../../2026-10-17/KIMI-前端任务提示词.md)：优先 F1–F4，随后新增字段、科研状态与窄窗口；B1–B7 修复前相关能力保留限制和解释。应用源码本轮未修复，TB4/AMCL、Fuel 依赖及桥接压力试验本轮未重新执行，仍以各自历史证据为限。

## 建模与物理后端验证（2026-10-17，早期 v3 验收）

本轮只修改后端、验证工具和既有文档；接手时的 **224 个前端文件未变**。默认田间执行现在使用 `urp.field-diffdrive.rigid/3`，完整输入、参数、解析参考、源时间轨迹与实际物理库哈希随结果保存。统一入口仍为 [setup/README](../../README.md)，证据集中于 [modeling](../../2026-10-17/modeling/)，总索引为 [summary.json](../../2026-10-17/modeling/summary.json)。

**修正了模型与证据之间的实质缺口：**

- 旧的真值反馈能把路线追踪好，却掩盖轮式运动学误差。独立开环试验发现圆柱轮版本在 0.8 rad/s、3 s 转向中比解析参考多转 **0.2826 rad（约 12.2%）**；24 s 组合运动后轮式里程计末端偏差 **1.2343 m**。减小步长到 2 / 1 ms 仍有相似偏差；改 Bullet 接触又引入制动误差。保留全部[旧模型数据](../../2026-10-17/modeling/response-pilot2/)、[步长对照](../../2026-10-17/modeling/timestep-diagnosis/)、[检测器对照](../../2026-10-17/modeling/contact-detector-diagnosis/)。
- 新模型沿用[已安装版本的官方差速示例](https://github.com/gazebosim/gz-sim/blob/ignition-gazebo6_6.18.0/examples/worlds/diff_drive.sdf)的球面滚动接触抽象，显示、碰撞、惯量和规划足迹同步更新；车体仍为 0.8×0.56×0.22 m，含轮总宽 0.96 m，外接圆仍为 0.4882623 m。没有拟合轮距或追踪增益消除误差。**这是替换不符合目标运动学的模型近似，不是宣称修复了 DART 的所有轮胎接触问题。** 新模型依然是合成的理想差速平台。
- 使用每车 PosePublisher 的源仿真时间，替代零头时间的世界动态姿态话题。新田间会话拒绝零时间戳；以 50 Hz 目标频率保存完整 3D 姿态，独立检查采样缺口和二维轨迹一致性。轮式里程计只对齐首个共同姿态，不全轨迹拟合；姿态越出平地有效域就停车并留证。TB4 的旧记录时间口径不因此改变。
- 冻结质量、惯量、摩擦、步长、驱动上限和假设，先独立检查生成 SDF，再核对所属进程实际库映射与碰撞检测/约束求解器日志。库版本及 SHA256 保留，实际实现不符就拒绝就绪；日志使用行缓冲，避免等到退出才出现选择信息。默认 DART 5.4.0 接口 / DART 6.12.1 动力学、ODE 接触、Dantzig 求解。
- 规划结果增加显式模型契约：同构车队、抽象容量、瞬时返库卸载、通道停车访问代理、匀速时间代理和 LPT 启发式；未证最优。另给逐顶点停止、原地转弯、加速的解析时间参考，明确不是追踪器工期预测或上下界。推荐仍是规则建议，删除未经本项目证实的“2 秒内距 BKS 约 1%”“路线不交叉”“最快”等承诺。

| 验证 | 实测结果与原始证据 |
|---|---|
| 开环与敏感性 | 24 个真实 Gazebo 会话，每设置重复 3 次：primary 含 4/2/1 ms、+20 kg 和低摩擦；holdout 用更高直行速度、反向转弯与不同圆弧。**21 个正常/载荷试验全部通过**预定直行 0.15 m、制动 0.10 m、转向 0.15 rad 误差门槛；实际最大分别 **0.0385 m / 0.00483 m / 0.0111 rad**，组合运动末端里程计偏差最大 **0.00549 m**。[primary](../../2026-10-17/modeling/model-v3-primary/)、[独立协议](../../2026-10-17/modeling/model-v3-holdout-final/)、[CSV](../../2026-10-17/modeling/analysis/trials.csv) |
| 低摩擦负结果 | μ=0.05 的 3 次试验均明显偏离无滑移参考，末端里程计偏差约 **3.63 m**，明确记录 reference_conformance=false；不将它们计入 21 次通过。不包含真实土壤校准。[对比图](../../2026-10-17/modeling/analysis/physics-verification.png)、[分析及输入哈希](../../2026-10-17/modeling/analysis/summary.json) |
| 验证工具负对照 | 最终工具另跑旧圆柱轮和新模型 +20 kg 各 1 场：旧模型仍未通过转向参考，新模型通过；两场执行协议完成且清理 PASS。汇总只将适用的标称/载荷试验纳入通过判据，仅选择敏感性工况时返回 null。此 2 场不并入上面 24 场研究计数。[原始结果与汇总](../../2026-10-17/modeling/harness-final-check/) |
| 后端回归 | **178 passed**，3 条既有依赖弃用警告；包含配置漂移、惯量、完整足迹、真实进程和故障状态。后续推荐口径修改又运行现有相关 6 项，全部通过。[完整日志](../../2026-10-17/modeling/pytest-final.log)、[推荐复核](../../2026-10-17/modeling/pytest-recommendation.log) |
| 完整集成 | 8 场得到预期结果：自由、实体行间、取消、时钟停滞、桥接退出、真实车间接触、重启、网页桥/RViz 开关。成功场的源时间、实际库、轨迹、接触、停留和停车检查通过；取消为 canceled/0，故障为 failed/1；全部关闭 PASS、无所属残留。行间 2 车 8 点 **369.86 m / 429.18 s**。[逐场汇总](../../2026-10-17/modeling/integrated-final/summary.json)、[原始运行](../../2026-10-17/modeling/integrated-final/runtime/fieldsim/) |
| 正式页面闭环 | 页面重新比较 3 算法，将 PyVRP 行间方案交给新版后端，**8/8，298.38 m / 343.37 s，最大横向偏差 0.0969 m**。下载 JSON 与 API 指标一致，完整记录哈希一致，页面关闭 rc=0 / PASS，无残留；两车末端里程计偏差约 9.0 / 1.0 mm。[截图](../../2026-10-17/modeling/ui-completed.png)、[下载](../../2026-10-17/modeling/ui-browser-result.json)、[完整服务端记录](../../2026-10-17/modeling/ui-final-record.json)、[核验](../../2026-10-17/modeling/ui-verification.json)、[原始日志](../../2026-10-17/modeling/ui-runtime/) |

首轮种子参数不受 Fortress 支持、测试夹具缺字段，以及运行时核验遇到库别名/日志缓冲的启动失败都保留在同一证据目录，未改判成功。现在拒绝伪造 physics seed，实际库名按 Gazebo ABI 别名解析，必需选择日志未出现时等待明确期限后报错。各验证批次有源码快照；本轮未升级系统 Python、驱动、ROS 或依赖。

页面控制台本次采集为 0 条 error、2 条既有 Three.js 警告（重复导入及 Clock 弃用），保留[原文](../../2026-10-17/modeling/ui-console.json)，没有因后端验收通过而宣称前端零警告。最终证据的文件 SHA256 索引见 [manifest.json](../../2026-10-17/modeling/manifest.json)；持续运行的网关日志另行标注，不纳入不可变文件散列。

这些是**小规模、固定输入重复的仿真验证与敏感性试验**，图中误差条是观察范围，不是置信区间，也不是论文级算法优越性结论。尚未实现有限宽真实轮胎、可变形土壤/坡地、传感器噪声与状态估计、力矩/能耗模型、随任务变化的载荷、农业作业具可达性与作业质量；当前质量载荷均匀加在箱体且执行器为理想速度伺服。路线执行成功不能替代这些验证。模型接口保留扩展空间，未将库限定为该车型。

目录仅归集本轮证据并刷新[只读盘点](../../2026-10-17/modeling/folder-audit.json)，旧失败、用户研究数据、环境与未提交前端成果均保留；[224 文件指纹](../../2026-10-17/modeling/frontend-unchanged.json)。

## 后端持续优化（2026-10-17，前一轮记录）

仅改后端、测试和现有文档。接手快照中的 **224 个前端文件全部未变**，保留 Claude 未提交成果。唯一启动/停止/恢复入口仍是 [setup/README](../../README.md)，本轮证据统一在 [2026-10-17/backend](../../2026-10-17/backend/)，索引为 [summary.json](../../2026-10-17/backend/summary.json)。系统 Python、ROS 安装及既有求解环境均未替换。

已完成：

- 车型引用在求解提交时冻结，执行前核对版本、文件哈希和元数据。身份或参数漂移返回 409，未适配车型 422；不静默替换为内置机器人。
- 共用车库改为独立停车位及完整任务串行预约；开启车间物理碰撞，接触或距离不足停全车并记失败。保存接触位置/时间/车辆对、控制周期误差与指令、实际作业停留；成功还须通过连续圆足迹、同步轨迹最小车间距和传感器完整性检查。
- 修正作物行几何数量与碰撞次数混用，分别记录 `row_collision_shapes` 与 `row_collisions`；无接触观测的旧记录返回 null，原始文件不改写。速度在完成后继续随真值更新，独立检查复算里程并要求最终停止。
- 图表使用提交时实例，拒绝混入无关场景/会话或不同输入的单实例比较；内容散列缓存预算 64 MiB。误差图使用真实控制周期，地图注释索引对应原始轨迹；完成曲线按完成停留时刻计数，取消的缺失用时不伪造为零。
- `GET /lab/api/runs/{id}/record` 组装规划、场景、资产、推荐、仿真及关闭报告，返回内容 SHA256。它是完整性散列，不是身份签名；现有前端按钮暂未接此新版端点。
- 历史结果列表缓存紧凑摘要，随文件替换/删除失效。12 个真实结果、7 次重复的中位耗时 **70.91 → 0.414 ms**，摘要逐值相同；仅代表列表缓存收益，不代表求解加速。[数据与输入哈希](../../2026-10-17/backend/performance-final.json)，复测脚本为 `src/lab/e2e/result_index_benchmark.py`。

| 实测项目 | 结果与证据 |
|---|---|
| 后端回归 | **161 passed**，3 条已有依赖弃用警告；含无可行解、超时、取消、资产篡改、冻结输入、图表与缓存失效。[日志](../../2026-10-17/backend/pytest-clock-fixed.log) |
| 真实行间、自由通行、取消与恢复 | 2 车 8 任务完成；行间实际 370.59 m / 428.34 s，最小足迹净空 1.021 m；取消 canceled/0，实际重叠两车触发接触 failed/1，随后重启成功；各场关闭 PASS、无所属残留。[汇总](../../2026-10-17/backend/clock-fixed/summary.json)、[真实接触记录](../../2026-10-17/backend/clock-fixed/experiment-contact.json) |
| 时钟停滞、桥接退出与网页桥 | 故障应 failed/1，恢复成功；Foxglove 收到车辆消息，Gazebo WebSocket 返回正确世界，执行中两次 RViz 开关。[最终版本复测](../../2026-10-17/backend/final-faults/summary.json) |
| 实际前端闭环 | 页面生成 60×40 m 果园、2 车、8 点和池塘；规则推荐、三算法求解及独立验算。PyVRP 沿行间执行 **8/8、299.21 m / 342.52 s、最大横向偏差 0.0887 m**；最小足迹净空 **0.865 m**，终点速度归零。页面下载后关闭 rc=0、无残留。[截图](../../2026-10-17/backend/ui-final-success.jpg)、[结果](../../2026-10-17/backend/ui-final-result.json)、[服务端完整记录](../../2026-10-17/backend/ui-final-record.json)、[原始目录](../../2026-10-17/backend/ui-final-runtime/) |
| 页面导出 | 实验 JSON、12 张科研图的论文图包真实下载；图包含 PDF/SVG/PNG/CSV、图注和清单，ZIP 校验通过。图包基于较早同方案执行，最终执行另有结果导出。[图包](../../2026-10-17/backend/ui-figures.zip)、[清单](../../2026-10-17/backend/ui-figures-manifest.json)、[最终页面下载](../../2026-10-17/backend/ui-final-browser-result.json) |
| 网关与依赖 | 独立空闲/保持 SSE 连接的 3 次网关启停 rc=0；正式网关再次有序退出 rc=0，统一入口启动最新代码。锁文件增加已安装的直接绘图依赖；锁定离线同步未替换环境。[生命周期](../../2026-10-17/backend/gateway-lifecycle/summary.json)、[依赖检查](../../2026-10-17/backend/dependency-check.log) |

一次真实仿真回归须保留：改用 TF 头计算速度后，当前 Fortress 转换每条头时间为零，导致里程为零却通过旧检查。`settled/` 的 succeeded **不能作为有效科研里程结果**；这些隔离数据未进入正式运行库。现按收到真值时最新 `/clock` 回退，`truth_timing` 明确记录来源，属于接收时刻估计；加上里程复算和停车验收后，`clock-fixed/` 及最终页面运行通过。最初接触话题配置错误、注入位置偏离移动目标等失败同样保留，未删除或改判。

仍有关闭异常的范围限制：早期正式网关一次 API 关闭时工具收到 143，日志仅到 `Shutting down`，未取得信号来源证据。随后 3 次隔离复测及正式关闭均为 0，未复现，故不宣称该单次异常已定位根因；保留 `gateway-release.log`。该异常留下的档案服务已按本轮启动日志、命令行及启动时间核定归属后定向 SIGINT 停止（原父进程已消失，退出码未知）；网关重新启动并管理替代进程后，补验完整关闭，网关与档案服务均 rc=0。见 [定向恢复记录](../../2026-10-17/backend/archive-recovery.json)、[完整关闭日志](../../2026-10-17/backend/gateway-final.log)。桥接源码修复沿用上一轮，本轮真实会话核验其使用，但未重复 600 次压力实验。

目录沿用既有导航，仅盘点、清理白名单可重建后端缓存、归集证据与源码快照；不移动环境或删除研究结果。JDK 可选 `lib/src.zip` 断链保留，工作台不依赖它。[盘点](../../2026-10-17/backend/folder-audit.json)、[前端指纹](../../2026-10-17/backend/frontend-unchanged.json)。

边界：串行预约增加用时；规划指标不含停车接驳、转向与排队，不能直接用差值判算法优劣。碰撞结论限于当前实体世界、圆足迹及记录的分段线性时间轨迹，不证明未观测运动。异构车队、阿克曼多车曲率、通用导入车辆执行、并行多车避碰、学习推荐增益仍未交付。前端固定旧文案与新版导出接入见 [后端对接需求](../../2026-10-16/后端对接需求.md)，本轮未擅改前端。

## 桥接根因修复、后端优化与整理（2026-10-16，后续复验）

当前桥接使用源码修复，**不再加载 `_exit` 垫片**。旧说明与失败日志保留为历史证据。原版在本机重新测试 200 个独立桥接进程，9 次 SIGABRT；Memcheck 44 个进程中 2 个报告非法访问：`rclcpp::SignalHandler::deferred_signal_handler → Context::shutdown → rcl_logging_external_shutdown → spdlog::registry::drop` 访问已被主线程 `__run_exit_handlers` 释放的注册表。定位依据是释放与访问双方的调用栈，不只是 `free()` 报错位置。[原始内存检查](../../2026-10-16/hardening/memcheck-stock-repeat/)、[对照](../../2026-10-16/hardening/stock-control/summary.json)。

`spin()` 因 context 失效返回时，异步信号线程可能还在关闭日志。修复在官方 0.244.26 的 `parameter_bridge` 主函数作用域结束前调用 `rclcpp::shutdown()`，等待 context 关闭并 join 信号线程，然后正常执行静态析构。日志、异常信号和返回码保持真实。对应 [ROS 16.0.21 shutdown 源码](https://github.com/ros2/rclcpp/blob/16.0.21/rclcpp/src/rclcpp/utilities.cpp) 与 [join 实现](https://github.com/ros2/rclcpp/blob/16.0.21/rclcpp/src/rclcpp/signal_handler.cpp) 已按版本下载留证。只通过进程级 ament 前缀替换桥接可执行文件，复用系统消息转换库，不修改 `/opt/ros` 或系统 Python；源码、产物和依赖指纹都校验。

| 验证 | 结果 |
|---|---|
| 修复版退出压力 | SIGINT 400/400、SIGTERM 200/200 为 0，无强杀、启动失败或提前退出；单位是单个桥接进程，不是完整机器人会话 |
| Memcheck 修复版 | 40/40 无非法内存访问报告；这不是完整泄漏或数据竞争证明 |
| 延长日志关闭 500 ms 的因果探针 | 原版 8/8 在关闭完成前 main 已返回；修复版 8/8 先完成日志关闭再返回；探针未跳过析构或替换状态码 |
| 非法命令 | 仍返回 255，不把失败归零 |
| Python 回归 | 108 项通过，含真实父子进程单次信号转发、会话重启身份与接管失联回归；保留三个已有依赖弃用警告。[日志](../../2026-10-16/hardening/pytest-final-108.log) |
| 真实田间闭环 | 自由/行间 2 车 8 点成功；取消 canceled/0，遥测中断与桥接提前退出 failed/1，恢复重启成功，所属进程全部回收 |
| 网页桥 | 收到真实 Foxglove 遥测，Gazebo 世界查询通过；执行中两次 RViz 开关成功，最终轨迹检查通过 |
| 实际页面闭环 | 在未修改的前端选择迷宫、推荐三算法求解、查看独立验算、AMCL 下依次执行两种模式、下载两个实验 JSON、关闭；均 2/2，源码/参数/轨迹可追溯，关闭 PASS、所有退出码 0、无残留。[验算截图](../../2026-10-16/hardening/ui-maze-check.jpg)、[执行截图](../../2026-10-16/hardening/ui-maze-success.jpg)、[下载文件](../../2026-10-16/hardening/ui-evidence/) |
| 正式网关仓库长路线 | AMCL + FollowPath，5/5、16/16 路径段；实际 58.786 m、最大偏差 0.0804 m、终点误差 0.0825 m；独立地图检查通过，净空 0.760 m。377 s 实际执行后关闭 11.7 s，全部正常退出、无残留，整场 PASS。[完整结果](../../2026-10-16/hardening/warehouse-deployed/report.json)、[页面下载](../../2026-10-16/hardening/warehouse-deployed/browser-export.json)、[关闭原始报告](../../2026-10-16/hardening/deployed-sessions/sim_20261016T072343_417e5b/session-report.json)、[截图](../../2026-10-16/hardening/ui-warehouse-deployed.jpg) |

几何建图已按性能剖析改成分批 GEOS 运算。40／200／600 点中位时间分别从 14.61／138.26／1185.91 ms 降至 4.44／39.48／475.26 ms，约 3.29／3.50／2.50 倍；三个固定场景的节点、距离矩阵、路径前驱逐位相同。仅说明建图性能，不冒充整个求解器提速。[前后数据](../../2026-10-16/hardening/performance-after.json)。

目录整理将三个根目录文档归入 `docs/`，修正相关引用并检查 506 个本地链接；运行进程管理迁入 `src/lab/ros/processes.py`，不再导入日期目录的 Nav2 验收脚本。旧垫片源码、清单和二进制归档在 `hardening/retired-exit-guard/`。删除约 1.38 GB 可重建的诊断编译缓存，另按白名单清理后端 Python 缓存；保留来源与删除清单。文献、数据、未提交前端成果、历史实验与 Git 历史均保留。[目录与保留规则](../../../docs/README.md)、[迁移清单](../../2026-10-16/hardening/document-relocations.json)、[编译缓存清单](../../2026-10-16/hardening/removed-build-cache.json)、[Python 缓存清单](../../2026-10-16/hardening/cache-cleanup.json)。盘点发现独立 `baseline-worker` 中 JDK 的可选源码压缩包链接失效，未改动该研究环境；应用不依赖它。

原版 DWB 在 AMCL 连续切换执行模式时实测 90 s 无法完成后方目标；改用已安装的 RPP，并读取有效参数后才声明就绪。最新页面闭环路径跟踪为 4.100 m、最大偏差 0.0427 m；逐点导航为 4.071 m、最大偏离 0.039 m。前者传入求解器几何，后者仍由 Nav2 重规划。只改会话参数文件，没有放宽原有验收阈值。早期 `tb4-rpp` 导出中的接驳说明还写着 DWB，但有效参数已经是 RPP；网关重启后说明已纠正，原始导出不改写。

仓库长路线首轮达到 5/5、实际 58.81 m、最大偏差 0.082 m，但关闭发生 SIGTERM，整场仍判 FAIL。核对官方启动文件发现 Gazebo 通过 `shell=True` 启动，向 launch 领导进程发信号不能可靠停止其孙进程。现用 Fortress 的 `/server_control` 请求正常停止，并让 TB4 节点在其时钟和服务依赖尚在时先退出；其后回收全部进程并核验真实退出码。第一次失败保留在 [warehouse-rpp](../../2026-10-16/hardening/warehouse-rpp/report.json)，没有用改判状态掩盖它。TB4 节点那次超时的内部阻塞点未做调用栈证明，因此不宣称另一个上游缺陷已被定位。

连续启停还复现了新会话返回旧会话 ID 的网关竞态，现按当前会话身份隔离迟到响应；过期的“会话已退出”通知不能把新任务误记为失败。接管只允许同一运行目录、环境标识及进程启动时间均匹配的会话，失联及时报错；并行隔离测试网关不会再被正式网关接管。服务说明移除 `_exit` 残留引用，选择原版对照时会明确显示未修复状态。

正式网关重启后，仓库三场连续会话分别完成取消后正常关闭、桥接故障如实失败、重启后正常关闭；每场的请求 ID 与就绪 ID 一致、23 个运行桥接均为校验后的源码修复版、无 `_exit` 映射、无残留。[完整证据](../../2026-10-16/hardening/warehouse-recovery-deployed/summary.json)。最新资源核对为 warehouse 9、depot 1、maze 0 个独立 Fuel 模型，三个世界依赖完整；缓存共 13 个模型版本，其中部分并非这三个世界依赖。[当前依赖清单](../../2026-10-16/hardening/assets-current.json)、[缓存清单](../../2026-10-16/hardening/fuel-cache-inventory.json)。

交付网关已再次通过统一入口启动并加载全部修复，五算法求解、独立检查及导出复验通过，仿真保持关闭。与接手快照相比，139 个前端源码文件哈希全部相同。目录盘点、部署状态与证据索引见 [结构化汇总](../../2026-10-16/hardening/summary.json)、[部署核对](../../2026-10-16/hardening/deployed-check.json)、[重启后 API 复验](../../2026-10-16/hardening/post-restart-api/report.json)。

本轮网页探针最初把 Foxglove 二进制 Time 帧当 JSON，TB4 验收脚本最初按默认目录寻找隔离会话报告，均保留原始失败；已分别修正帧类型处理与显式 `--runtime`。浏览器观察到现有 Three.js 弃用、重复导入及模型加载警告，未改前端代码；这不等于前端零警告验收。

边界：这是已复现的桥接关闭竞态修复，不是所有 ROS/Gazebo 缺陷均已消除的声明；上游所有权循环是另一个问题，本轮未把它误称为这次非法访问的源头。TF2/GUI 的兼容覆盖仍单独存在，学习推荐收益、实机、真实农业环境与车间避碰仍不在这次验收结论内。`URP_BRIDGE_FIX=0` 选择未修复的系统桥接，供隔离对照，不会启用旧 `_exit`。

## 后端复验与修正（2026-10-16）

本轮仅修改后端、后端测试和对应文档，保留 Claude 的全部前端成果；接手快照与交付哈希核对见 [backend 证据](../../2026-10-16/backend/summary.json)。主网关已获用户授权后有序重启，仍使用唯一入口 `bash setup/run.sh lab --open`，未重建前端或更换 Python / 依赖。

修复了实际复现的问题：

- 原检查器会接受空几何/空作业清单、错误里程和不一致的分趟清单；重合任务/车库的零距离边会被稀疏图丢弃。[修复前复现](../../2026-10-16/backend/reproduced-before.json)。现逐项复算任务/分趟/作业位置顺序、真实返库卸载、容量、里程和用时；按连续路径段与原始障碍/作物多边形复核圆足迹。拒绝候选保存在 `raw_result.rejected_routes`，不再标为 feasible。
- 仿真启动重新验算保存的输入与路线；未验算、半径小于当前模型外接圆 0.4883 m、超过 15 台执行车辆（16 位碰撞掩码限制）等明确拒绝。规划仍支持通用车队规模。停车位到车库的接驳线也检查净空；树木的碰撞几何使用地图多边形，不再用等面积圆替代。
- 修复近邻/重合作业提前服务、停留时间被覆盖和长作业误触发卡住判断；保存精确路径发生位置及 `serviced_at`。取消保留 canceled 结果，真值/里程计/时钟中断以墙钟 3 s 看门狗报错；必需组件提前退出（即使 rc=0）使会话失败。最终状态落盘，网关不再把失败或失联显示成正常停止/重新启动。
- 结果增加独立实际轨迹检查；观察到的连续线段圆足迹须留在场内且避开实体障碍/作物，双向路径偏差须 ≤0.5 m（0.1 m 采样并保守加入 0.05 m 误差界），终点误差 ≤0.35 m，逐任务停留时间必须完成。使用理想真值定位，不证明传感器定位或车间避碰；轨迹采样间的未观测运动未作连续时间证明。实际里程/用时含停车位接驳与转向，规划指标从车库算起，导出明确记录此口径。
- 修复网页数据桥过早发布地址，以及重复开关窗口复用已存在日志文件的问题。窗口关闭在后台回收所属进程，执行控制继续运行。完整导出包含原规划、实际轨迹、执行配置和源码/世界哈希。`session-report.status`、`cleanup_status`、`execution_state` 分别说明会话、清理与任务结果，不能混用。

| 实际复验 | 结果与证据 |
|---|---|
| Python 回归 | 97 项通过（原 56 + 新增 41）；覆盖篡改、零距离、作业时序、非法入参、仿真准入及轨迹检查。[日志](../../2026-10-16/backend/pytest-final.log) |
| 自由通行 · 2 车 8 点 | 8/8、2 车返库；规划 209.075 m、实际 211.18 m；独立轨迹检查通过、退出 0、清理 PASS。[完整导出](../../2026-10-16/backend/live-web/experiment-free.json) |
| 行间通行 · 同一场景 | 8/8、2 车返库；规划 363.422 m、实际 364.67 m；实体作物行净空检查通过、退出 0、清理 PASS。[完整导出](../../2026-10-16/backend/live-web/experiment-rows.json) |
| 取消、遥测中断、桥接提前退出、故障后重启 | 分别为 canceled/0、failed/1、failed/1、succeeded/0；清理均 PASS、所属残留均空。桥接 SIGTERM 后实际以 0 提前退出，仍正确判会话故障。[汇总](../../2026-10-16/backend/live-final/summary.json) |
| 网页桥及原生窗口控制 | Foxglove 收到真实 `/workbench/fleet_status` 二进制消息；Gazebo WebSocket 完成授权、协议与世界查询；执行中两次打开/关闭 RViz，独立日志保留，路径仍通过，退出 0。[会话](../../2026-10-16/backend/live-web/experiment-web.json)、[Foxglove](../../2026-10-16/backend/live-web/foxglove.json)、[Gazebo](../../2026-10-16/backend/live-web/gazebo-web.json) |
| 主网关部署 | 新进程健康、0 倍速请求正确返回 400、两类仿真均已停止、前端源码哈希与接手一致。[核对](../../2026-10-16/backend/deployed-check.json) |

先前网页桥连接过早导致的一次验收失败及其 canceled 会话完整保留在 `live-final`，没有重写为成功；修正后的网页桥结果在 `live-web`。本轮使用隔离存储、ROS domain 141 和控制端口 4193；原始日志、SDF、计划、真值轨迹及清理报告位于各批 `runtime/fieldsim/`。复验脚本：[field_backend.py](../../../src/lab/e2e/field_backend.py)（从 `src/lab` 使用现有 `.venv/bin/python` 运行，`--out` 必须是新的绝对路径）。该次复验未涉及前端重新设计或浏览器交互验收，桥接退出垫片当时仍是临时缓解；后续已完成源码根因修复及实际浏览器验收，见本文最上方记录。

## 工作台（2026-10-16）

面板改成多面板工作台：任务规划、算法实验、仿真、资料、系统五个工作区，面板可拖动、合并、拆分、浮动，⌘K 命令面板。主流程「一句话描述 → 生成地图 → 手动调整 → 推荐 → 多车分工与彩色路线 → 验算对比」逐步实测：

| 步骤 | 实测 | 截图 / 记录 |
|---|---|---|
| 描述任务 | 「在 240×160 米的猕猴桃园里，4 台机器人从西南角车库出发给 48 个点采样，每台最多装 12 个样本，避开一个池塘和一栋温室」输入时即解析成作业、场地、任务点、车队、容量、障碍、车库 | [flow-1](workbench/flow-1-describe.png)、[另一句](workbench/plan-compose.png) |
| 生成地图 | 4 块地、池塘、温室、48 个点、西南角车库；推荐 PyVRP 固定车队，至少 4 趟，聚集度 R=0.91 | [flow-2](workbench/flow-2-generated.png) |
| 多车路线与对比 | PyVRP 1 853.4 m、OR-Tools 1 904.4 m、节约 + 2-opt 1 943.8 m，均 4 车各 12 点；按车着色、方向箭头、访问序号、图例、同步回放 | [flow-3](workbench/flow-3-solved.png) |
| 验算 | 三个方案独立复算全部通过：每点恰好一次、每趟载重、车辆数、避障不出界、车库往返、里程复算一致 | [flow-4](workbench/flow-4-verify.png) |
| 手动调整 | 拖动任务点后结果标为过期并自动保存、画池塘、删点、⌘Enter 重算（39 点，PyVRP 1 021.7 m） | [plan-fleet](workbench/plan-fleet.png)、[验算](workbench/plan-fleet-verify.png) |
| 其他问题类 | CVRPLIB X-n101-k25：PyVRP 27 710，比 BKS 27 591 高 0.43%；Dubins 仓库：BIT* 31.35 m；爱沙尼亚真实田块 5：Fields2Cover 全流程 3 759.91 m | [CVRPLIB](workbench/plan-cvrplib.png)、[运动规划](workbench/plan-motion.png)、[覆盖](workbench/plan-coverage.png) |
| 算法实验 | X 集 4 例 × PyVRP / OR-Tools / 节约 × 2 种子共 17 次：PyVRP 平均差距 0.31% ± 0.41，OR-Tools 4.75% ± 1.14（节约算法超 100 客户的 3 例跳过）；性能剖面、差距分布、收敛曲线、LaTeX 表 | [课题](workbench/experiments.png)、[图表](workbench/experiments-charts.png) |
| 仿真 | 迷宫对角穿越 Theta* 11.85 m 送进 TurtleBot4：沿路径行驶成功，实际 11.77 m，横向偏差均值 0.016 m、最大 0.100 m，终点 0.105 m，仿真 55.4 s，会话 rc=0 关闭、无残留 | [e2e 记录](workbench/e2e-p2p-maze-cross/) |
| 其余工作区 | 资料（CVRPLIB 732 例、真实田块、文献、算法库与源码）、系统（服务、日志）、仿真 | [资料](workbench/library.png)、[系统](workbench/system.png)、[仿真](workbench/sim.png) |

### 多车方案在 Gazebo 田间世界里执行（ROS 2）

上表猕猴桃园任务的 PyVRP 方案（4 车 × 12 点）送进按任务地图生成的 Gazebo Fortress 世界：田块与作物行、池塘、温室、车库、按车辆着色的任务标记，每车一台差速机器人（官方 DiffDrive 系统），经一个 ros_gz 桥沿规划路线行驶、逐点到达。

| 指标 | 规划 | 执行（2× 与 5.9× 两次） |
|---|---|---|
| 到达任务 | 48 | 48 / 48，4 车全部回到车库 |
| 最晚完成时间 | 542.3 s | 552.7 s（+1.9%，两次一致） |
| 总里程 | 1 853.4 m | 1 858.3 m（+0.3%） |
| 横向偏差 | — | 均值 1.3–1.5 cm，最大 0.41–0.42 m（转角处） |

会话停止时 Gazebo、桥、Foxglove Bridge、Gazebo WebSocket 服务都在 SIGINT 后以 0 退出，`session-report.json` 为 PASS、无残留进程。浏览器里同时看到：ROS 视图（Foxglove Bridge → R3F，田块 / 规划路线 / 实际轨迹 / 任务 / 车辆）、Gazebo 场景（gzweb 直接渲染运行中的世界）、遥测（uPlot：各车速度、完成度、横向偏差）。证据：[世界、计划、结果、会话报告](workbench/fieldsim-4veh-48tasks/)。

同一句「5 公顷麦田分成 6 块，4 辆无人车巡检 60 个巡检点，要求尽快完成」：目标自动识别为尽快完成，推荐 OR-Tools；改用带车库卸载点和时间跨度代价的 OR-Tools 模型后，最晚完成 658.9 s，PyVRP 的最短里程方案为 745.5 s，各车负担的变异系数 0.101 对 0.189。

本轮实测中修掉的问题：

- 仿真修补库目录被清理后会话起不来 → 新增 `src/lab/ros/native/rebuild-ros-overlays.sh`，按原命令重建，产物与验收时逐字节一致（TF 1b530ae8…、GUI 49bd20b8…），`lab` 入口缺失时自动重建。
- 连续打开多个页面后请求全部挂起：每个页面的事件流各占一条连接，浏览器对同一主机只开 6 条 HTTP/1.1 连接。现在页面隐藏或进入往返缓存时关闭事件流，回到前台再连上并刷新。
- 地图在面板尺寸变化后不再重新适配 → 未手动平移缩放前随面板自动适配。
- 真实田块显示「无量纲」比例尺 → 实地坐标按米显示；统计图图例改用算法名、同一算法各图同色，时间轴按 ms / s 标注；课题矩阵显示场景与算法名称。

- 「尽快完成」目标下 OR-Tools 原先因多趟模式用了任务数个虚拟车而没有加时间跨度代价，结果与最短里程方案一样；现在按真实车数建模，允许回车库卸载，最小化最晚完成时间。
- gzweb 通过 WebSocket 取 OBJ 网格时把字节直接交给文本解析器，改为二进制 STL；Fortress 的 WebSocket 服务只发资源路径内的文件，会话把世界目录加进资源路径。
- 界面与日志不再带「URP」：应用名为「农业机器人路径规划工作台」，算法来源标为「本项目 / 基线 / 第三方库名」，ROS 话题改为 `/workbench/*`、`/ground_truth/poses`。

自动检查：Python 52 通过；前端 42 通过；类型检查与生产构建通过。

### 沿作物行间与地头通行（作物行带碰撞）

生成的农田地图现在默认按作物行通行（检查器「通行方式」可改回自由通行，导入与旧地图默认自由通行）。每块地按作物行距和行向种出作物行；行间净宽够车宽加两侧各 0.1 m 时每两行之间就是一条通道，否则每两行留出一条作业道。车辆只能沿通道中线行驶，经地头和机耕路换行，任务在离它最近的通道点作业。VRP 距离矩阵、路线和独立验算都基于这张通行图；地图编辑器淡显通道，地块检查器给出行距与通道宽度。Gazebo 世界里作物行带碰撞（下面的任务共 210 行），机器人开到急弯处原地转向。

上面那句猕猴桃园任务，同一张地图、同一 PyVRP（5 s），4× 倍速：

| 指标 | 沿行间与地头：规划 | 执行 | 自由通行：规划 | 执行 |
|---|---|---|---|---|
| 到达任务 | 48 | 48 / 48，4 车回库 | 48 | 48 / 48，4 车回库 |
| 最晚完成时间 | 951.0 s | 1 021.6 s（+7.4%） | 542.3 s | 558.5 s（+3.0%） |
| 总里程 | 3 291.1 m | 3 285.8 m（−0.2%） | 1 853.4 m | 1 856.0 m（+0.1%） |
| 绕行系数（路线 / 作业点间直线） | 1.54 | — | 1.00 | — |
| 急弯 / 行内掉头 | 107 / 28 | — | 33 / 0 | — |
| 横向偏差 | — | 均值 0.6 cm，最大 0.15 m | — | 均值 0.4 cm，最大 0.15 m |
| 车体（含轮 0.8 × 0.74 m）压到作物行的位姿 | — | 0 / 7 824，最小净距 0.19 m | — | 2 258 / 4 296 |

- 沿行间通行让规划里程多 78%、最晚完成多 75%：换行必须先出到地头或机耕路，作业点平均离通道 1.09 m（最远 2.19 m）。6 张示例地图上（PyVRP 1 s）行间与自由通行的里程比为 1.3–2.5，通行图建图 0.04–0.15 s。
- 执行比规划多出的时间主要来自急弯处原地转向（行间方案的急弯是自由通行的 3 倍多）；规划时长目前只按里程 / 车速 + 作业时间估算，还没有计入转向。
- 验算新增两项：路线经过每个任务的作业位置，且作业位置是离任务最近的可达处；按行间通行时每段路线都必须落在开阔区或通道中线上。把两站之间换成一条斜穿作物的直线，验算报「1 段路线离开行间通道和地头，斜穿了作物」。
- 控制器修正：路线在行内原路折返后会再经过同一个出入口，原先的进度投影会跳到之后那一段，现在只在当前进度前后 2 m 内投影；急弯先开到弯点再原地转向；机器人驱动轮移到车体中线，原地转向不再甩尾。自由通行用同一控制器重跑：558.5 s（之前的控制器为 552.7 s）。
- 浏览器里走一遍：检查器选「沿行间与地头」→ 对比三个算法 → 验算面板 7 项全部通过（含「只走行间通道和地头，不斜穿作物」「路线经过每个任务的作业位置」）→ 方案对比里点「Gazebo」。这次送进去的是节约 + 2-opt 方案：48 / 48，最晚完成 1 024.9 s（规划 963.7 s，+6.3%），里程 3 507.6 m（规划 3 513.7 m），车体与作物行 0 / 7 860 接触，最小净距 0.19 m；ROS 视图与 gzweb 里都能看到作物行。
- 会话：上表两次与节约方案那次 `session-report.json` 均为 PASS、无残留进程。同一行间 PyVRP 方案的前一次执行结果一致（1 021.3 s、3 285.4 m），但关闭时 Gazebo 服务端没有响应 SIGINT / SIGTERM，被 SIGKILL，那次报告为 FAIL；今天 7 次田间会话里出现 1 次，其余都在 SIGINT 后 0.2 s 内退出。

证据：[两种通行的路线图（行间](workbench/fieldsim-rows-vs-free/plan-rows.png)、[自由](workbench/fieldsim-rows-vs-free/plan-free.png)），[对比摘要、两次会话的世界 / 计划 / 结果 / 会话报告](workbench/fieldsim-rows-vs-free/)。自动检查：Python 56 通过；前端 42 通过；类型检查与生产构建通过。

## 交付状态

| 部分 | 结论 |
|---|---|
| 场景 → 推荐 → 求解 → 独立检查 → 二维图 | 已通过前端及 API 实测；10 个可用算法适配，来源与保证分开标注 |
| URP 路径跟踪 / Nav2 逐点导航 | 两种模式分别实测，接驳、几何计划、重规划和实际轨迹分开记录 |
| 导出与回看 | JSON 包含当时输入、版本/源文件哈希、参数、原始结果、检查；仿真实验 JSON 同时嵌入规划结果 |
| ROS 生命周期与失败恢复 | 正常关闭、移动中取消、导航失败、必需服务退出、失败后重启、重复会话均实测 |
| Gazebo / RViz2 | 原生窗口实际启动，日志确认渲染/地图加载，随会话干净关闭；未通过原生截图工具独立验收像素外观 |
| 仓库/农业代理 | 完整官方世界、默认五任务长路线在 AMCL 下通过两种模式；仍只是静态行间通道代理 |
| 覆盖 / 学习推荐 / 历史 BPP | Fields2Cover 两种顺序可用、往复式实际覆盖通过；规则及限定范围学习原型可用；缺失 BPP 归档保持空数据 |

交付时面板在 `127.0.0.1:4100`，档案后端由网关管理，仿真已关闭。当前工作区仍为未提交成果，没有覆盖既有未提交改动或创建“最终版”分支。

## 扩展验收（在首轮核心闭环之后）

| 验收 | 实测结果 | 原始证据 |
|---|---|---|
| 仓库五任务 · AMCL 路径跟踪 | 5/5，计划 58.96 m，实际 59.460 m；最大偏差 0.3175 m，终点 0.0769 m，实际地图净空 0.760 m | [完整闭环](extended/api-loops/20261015T091612Z/report.json)、[跟踪](extended/api-loops/20261015T091612Z/sim-follow_path.json) |
| 仓库五任务 · Nav2 逐点导航 | 5/5，实际 51.223 m；相对原路线最大偏离 1.691 m；保存重规划，不称为严格执行原几何 | [导航](extended/api-loops/20261015T091612Z/sim-nav2_waypoints.json)、[双向距离补查](extended/warehouse-reverse-distance.json) |
| 前端 Fields2Cover 全流程 | 地头 0.6 m 的路径因足迹越界被拒绝；恢复 0.8 m 求解、比较、启动、执行、下载、关闭 | [实际下载](extended/ui-coverage-experiment.json)、[逐项核对](extended/ui-coverage-verification.json)、[完成截图](extended/ui-coverage-success.jpg)、[重启后回看](extended/ui-delivery.jpg)、[轨迹 CSV](extended/ui-coverage-trajectory.csv)、[对比截图](extended/ui-compare.jpg)、[CSV](extended/ui-compare.csv) |
| 实际覆盖与几何跟踪 | 5/5 路径段；内缩作业区 98.52%，全田块 57.84%；实际 27.747 m，最大偏差 0.0793 m，反向距离/终点误差 0.1040 m；足迹在田块内且地图检查通过 | [会话与清理](extended/sessions/sim_20261015T093550_c82812/session-report.json)，1904 个真值点；约 207 s，包括接驳用时 |
| 理想定位隔离模式 | 独立迷宫测试 2/2、最大偏差 0.209 m；确认 AMCL tf_broadcast=false、仅真值生成定位 TF；正常关闭 PASS | [完整报告](extended/api-loops/20261015T094608Z/report.json)（与 AMCL 验收分列） |
| 限定范围学习推荐 | 120 个不同实例、480 次真实求解，60/30/30 拆分；测试集全部选择固定 PyVRP，替代次数 0、平均改善 0% | [预定协议](extended/selector-study/protocol.json)、[逐实例原始结果](extended/selector-study/)、[独立评估](extended/selector-study/evaluation.json)、[审计及种子说明](extended/selector-study/audit.json)、[前端呈现](extended/ui-learned-evidence.jpg)、[真实下载](extended/ui-learned-solution.json) |
| 故障恢复复验 | 更新控制参数后 18 项再次通过；正常/故障退出均保留真实结果，失败清理与重启均无所属残留 | [结果](extended/api-loops/edge-cases-20261015T094147180059Z/edge-cases.json)、[日志](extended/edge-cases-final.log) |
| 最终自动检查 | Python 30 通过；前端 40 通过、2 个原有跳过；类型检查与生产构建通过 | [Python](extended/pytest-final.log)、[前端](extended/frontend-tests-final.log)、[锁定恢复及最终构建](extended/setup-locked-final.log)、[最终浏览器无错误/警告](extended/ui-console-final.json) |

仓库前几次失败原样保留：旧默认 AMCL 在长通道累计漂移；第一次参数适配误用了 launch 参数名；一次 Humble 不支持的 Python 客户端导入在控制 HTTP 启动前失败。定位 launch 使用 `params`，导航 launch 使用 `params_file`，现已分别适配，并通过 ROS 服务读取有效参数后才就绪。前端此后实际切换 CVRP 发现旧实例残留造成渲染错误，现按场景/历史记录隔离编辑状态，并对 JSON 结构做渲染保护；无效结构禁用求解，不能伪装成空场景。

核对已安装源码及 [PyVRP 0.14.0 官方实现](https://raw.githubusercontent.com/PyVRP/PyVRP/v0.14.0/pyvrp/solve.py) 后，将沿用的 HGS 显示名更正为 **ILS**。`pyvrp.hgs` 仍为兼容旧记录的 API 标识，实际运行的是 IteratedLocalSearch；旧结果不改写，审计文件明确纠正名称。

新的导航参数仅写入本会话目录，不修改 `/opt/ros`。AMCL 控制为默认：180 束、仿真低噪声里程计、0.06 m 控制器目标容差；覆盖用已安装 RPP，0.18 m lookahead，碰撞检测开启。验收阈值仍为 0.35 m，未通过放宽阈值制造成功。`ground_truth` 为显式理想定位隔离实验，不能混入 AMCL 结果。

Fields2Cover 复用已有 2.1.0 wheel，两个顺序分别调用真实原生 swath 排序与 Dubins 转弯，再由 Shapely 整段足迹及三点曲率独立检查。默认计划 27.742 m、最大采样曲率 3.333332 /m、内缩作业区几何覆盖 100%。支持单个凸内缩区，不支持未分解的凹形/孔洞；默认地头 0.8 m。规划覆盖只计直作业行，实际覆盖假设作业具沿整个跟踪轨迹持续开启，因此全田块比例不能直接与规划比例混作同一指标。最小转弯半径只约束规划几何；差速 TB4 可原地旋转，不证明农业车辆运动学可行。

学习原型复用原研究的特征与校准逻辑，但使用独立命名的本地实验。模型、归一化和固定算法只在训练集拟合，校准集只设门控，测试集只报告；篡改全部测试标签后模型与门控仍完全相同。预算为每算法 0.25 s、两次重复；PyVRP 种子 11/29，OR-Tools 不使用种子。研究分布为 [0,100] 均匀/聚簇合成数据，20/50/100 客户、容量 50、需求 1–10；现有面板基准与编辑输入可能有分布差异，模型界面明确外推未验证。**本次结果没有证明智能推荐相对固定 PyVRP 的收益，也没有证明普遍零风险。** 历史研究原始数据未恢复；新模型不冒充原研究结论。

两次完整网关启动/关闭由独立父进程直接 `waitpid` 记录，返回码均为 **0**，关闭后端口释放；此前执行工具外层终端曾返回 143，不能把外层状态当作网关真实码。[直接捕获记录](extended/gateway-lifecycle.json)、[可重复脚本](extended/check-gateway-lifecycle.py)。仿真会话的清理 PASS 与路线执行成功是两个不同状态；开发诊断中路线失败而清理正常的会话全部保留。

## 首轮核心验收结果

| 验收 | 实测结果 | 原始证据 |
|---|---|---|
| 最终前端全流程 | 真实点击场景、求解、启动、执行、下载、关闭；成本 490、到达 2/2；页面/网关/下载内容逐项相符 | [核对结果](acceptance/ui-verification.json)、[下载的完整实验](acceptance/ui-final-experiment.json)、[截图](acceptance/ui-final-success.jpg) |
| 四算法 API 闭环 | DP、独立原图 Oracle、PyVRP+分层、OR-Tools+分层：均为 490、检查通过；两种执行模式成功；总计 82.2 s，清理 PASS | [报告](acceptance/api-loops/20261015T080750Z/report.json)、[完整算法结果](acceptance/api-loops/20261015T080750Z/solutions.json)、[原始控制日志](acceptance/full-loop-final.log) |
| URP 跟踪误差 | API 闭环：均值 0.0598 m，最大 0.2201 m；最终 UI 独立再跑：最大 0.195 m，终点误差 0.206 m，257 个真值点 | [API 跟踪记录](acceptance/api-loops/20261015T080750Z/sim-follow_path.json)、[UI 核对](acceptance/ui-verification.json) |
| Nav2 逐点导航 | 2/2 到达；相对 URP 几何路径最大偏离 0.1077 m；保存 `/plan` 实际重规划输出，不宣称严格跟踪 | [导航记录](acceptance/api-loops/20261015T080750Z/sim-nav2_waypoints.json) |
| 故障与恢复 | 18 项断言通过：真实无解、精确求解超时、取消、工作进程异常、不安全路径拒绝、Nav2 不可达、取消/失败后再执行、必需服务终止与重启 | [结构化结果](acceptance/edge-cases.json)、[日志](acceptance/edge-cases.log)、[脚本](../../../src/lab/e2e/edge_cases.py) |
| 仓库短程 | 完整官方世界；节点 0→8→0，计划 4 m，任务 1/1，最大偏差 0.2954 m，正常关闭 rc=0 | [记录](acceptance/warehouse-smoke.json)、[原始日志](acceptance/warehouse-smoke.log) |
| 自动检查 | Python 23 通过；前端 38 通过、2 个原有跳过；TypeScript/Vite 构建通过；最终浏览器无控制台错误 | [pytest](acceptance/pytest-final.log)、[前端测试](acceptance/frontend-tests.log)、[构建](acceptance/build-final.log)、[浏览器](acceptance/ui-console-errors.json) |

首轮六个正常完整会话（独立初测、带窗口前端、故障后重启、四算法闭环、仓库短程、最终前端）均有 PASS 清理报告；另外保留三个 FAIL 会话，分别为修复前服务监控漏报、监控将一次性 spawner 误判退出的开发诊断、以及验收中主动终止规划服务。**这些失败未改写成成功，也没有从证据中删除。** 所有已结束会话的 `remaining_owned_processes` 均为空；各组件真实返回码、关闭信号与资源采样见 [会话目录](acceptance/sessions/)。正常 SIGINT 关闭允许相应子进程报告 -2/130，不能据此声称所有子进程均返回 0。

UI 正常结束后，「未运行」与禁用的关闭按钮见 [关闭截图](acceptance/ui-stopped.jpg)；无解显示空成本/未检查见 [无解截图](acceptance/ui-infeasible.jpg)；网关关闭后的断连与操作禁用见 [断连截图](acceptance/ui-disconnected.jpg)。

## 修复了什么

- 接手时没有仍在运行的 Claude/编译/仿真任务；两次 58 文件快照无变化，保留原有 Git 差异与未跟踪成果，[接手记录](acceptance/takeover-stability.json)。现有独立环境和契约在，但核心求解适配、推荐、导出、ROS 会话和两个主视图尚未形成可运行闭环。
- 接上既有转弯 DP/Oracle/原图检查器，适配已安装 PyVRP 0.14 的新 Route/Activity API，添加真实 OR-Tools 调用及独立重算；没有改写原研究求解源码。工作进程不再把非零退出或半截 JSON 当成功。
- 添加真实 ROS 会话，复用既有进程回收、TF 与 GUI 兼容代码。将闭合路径按原几何边交给 FollowPath，避免起终点相同被控制器立即判到达。
- 修复取消终态过早缓存，保证停止观察和指标完成后才持久化；启动恢复会从会话最终文件补齐数据库。修复求解取消与创建子进程的竞争窗口。
- 实测发现 planner_server 收到 SIGTERM 后会正常 rc=0 退出，而 launch 仍存活。现在将必需子节点提前退出及 Nav2 心跳故障视为会话故障；create/spawner 一次性正常退出除外。失败后清理、再次启动与执行已验证。
- 面板恢复历史实验的输入快照；明确规则推荐、算法来源、不可用模块、未检查/无解/失败/断连；仿真结果直接显示本次实际模式，避免下拉选项切换后误读历史执行。
- 整体 API 关闭不再伪造 SIGINT；`lab-stop` 经实测使网关以 0 退出，组件失败仍保留在清理报告与真实返回码中。正常 Ctrl+C 仍遵循中断返回码语义。

## 地图、资源与研究口径

- 物理坐标为 **map 米**，官方配对世界按 `world = map` 使用，没有把 benchmark 无量纲数值直接映射为米。整数转弯成本为路径厘米数 + 厘米当量惩罚；默认路线 4.30 m，对应成本 430+60=490。第三方求解优化整数代理成本，检查器重新计算并记录真实定义的成本。
- 机器人按 0.175 m 圆足迹 + 0.05 m 安全余量验证。未知区/地图外视为障碍，检查每条线段，保守扣减栅格和采样误差；不是仅检查节点。
- 有限段扫描核验：maze 620/640 有效回波端点全部距配对地图障碍 ≤0.15 m，warehouse 466 个有效端点也全部满足。激光安装 TF 纳入计算，没有调整世界坐标以追求通过。[迷宫记录](acceptance/maze-map-consistency.json)、[仓库记录](acceptance/warehouse-map-consistency.json)、[原始扫描](acceptance/warehouse-scan.json)。仅覆盖已测静止姿态及短程路线，不证明整幅地图/所有碰撞网格完全等价。桥接真值头时间为 0，轨迹使用最新仿真时钟，未宣称硬同步。
- [资源清单](acceptance/world-assets.json) 按模型 owner/name 去重：warehouse **9** 个 Fuel 模型、depot **1** 个，当前缓存 **12** 个模型版本。之前“12/13”混合了缓存数、原始 URI 数及 mesh 文件引用，不能当成验收数量。缺失的 Casual female 模型已经从官方 Fuel 补齐，[下载日志](acceptance/fuel-missing-model.log)。
- 活动依赖检查覆盖 SDF、网格、DAE/OBJ 材质及 PBR 贴图，并保存哈希。Classic material scripts 对 Fortress 不适用，逐项保存在 `inactive_classic_materials`；Table 的实际 PBR 贴图已核验。依据 [Gazebo Fortress 官方功能说明](https://gazebosim.org/docs/fortress/comparison/)。没有删除障碍或改动官方世界来通过检查。
- 跟踪成功要求任务按序进入 0.35 m 半径、各段终点误差和实际最大横向偏差均 ≤0.35 m。扩展验收又加入计划到实际的反向最大距离 ≤0.35 m，避免跳过部分计划仍判成功；覆盖实际作业区比例须 ≥90%、足迹留在田块内。控制器容差会使实际长度小于计划长度；不等于逐点精确重放。Nav2 逐点导航仅验收任务顺序/到达，并单独记录偏离原路线的程度。
- 访问任务、几何跟踪和覆盖率是不同指标。覆盖基线只评价内缩作业区域、允许原地转向，不满足 Dubins 最小转弯半径；路线完成不代表全田块覆盖。架构的场景/求解/检查与 ROS 会话分开；当前执行适配器为 TB4，更换机器人需相应足迹与控制器适配。

## 桥接退出历史（2026-10-15）：当时保留临时缓解

本节保留旧结论与计数；当前源码修复和验收见本文顶部。旧实现已[归档](../../2026-10-16/hardening/retired-exit-guard/)。

[原始计数复核](acceptance/bridge-evidence-audit.json) 与 [恢复的原始日志](bridge-exit/recovered-raw-logs/) 对应：

| 条件 | 批次 | 单个 parameter_bridge 退出数 | 异常 |
|---|---:|---:|---:|
| baseline-a | 8 | 160 | 7 ABRT |
| trace-a | 8 | 160 | 3 ABRT + 1 SEGV |
| trace-b | 12 | 240 | 9 ABRT |
| guard-a | 20 | 400 | 0 |

因此是未加垫片 **560 次单进程退出中 20 次异常**、加垫片 **400 次未观察到异常**，不是 560/400 个完整 TB4 会话。它们是此前压力测试的原始证据，本轮未重复宣称重新跑过同样规模。

`src/lab/ros/native/bridge_exit_guard.c` 只作用于指定 parameter_bridge 可执行文件，在真实 main 返回后 flush C 标准流并调用 `_exit(main返回码)`。加载前验证源码、二进制构建清单及目标链接库指纹；不替换系统库。新的完整 TB4 会话确认了命令转发、clock/TF/scan/odom/真值、到达、取消、失败恢复和反复关闭。

**free() 检测位置不是内存破坏起源；退出绕过不是根因修复。** `_exit` 跳过析构/atexit，不能声称那些退出钩子或其日志已验证；已捕获 stdout/stderr 原样保留，非零码、强杀和残留不吞掉。`URP_BRIDGE_FIX=0` 可关闭受控对比；桥接根因追查仍是独立未完成事项。TF/GUI 保护也只在已验证指纹的本次子进程内生效。

## 尚未完成与已知限制

- 学习原型样本小、算法组合窄，独立测试未观察到优于固定 PyVRP 的收益；跨分布、跨预算与跨机器的泛化未验证。
- Fields2Cover 限定为单个凸作业区；已有 wheel 和 native 二进制均锁定哈希，但原 C++ 构建命令/源码提交未恢复，未宣称从源码逐位重建。缺失或不匹配时可回退 Shapely 基线。
- 默认仓库五任务和指定 depot 田块已通过；不同机器人、所有通道/田块、动态障碍、长期稳定性和大规模跨场景评估未验收，仓库代理不是农业实证。
- depot 五个端点扫描中，首姿态有 27/507 个远处回波与地图障碍距离 >0.15 m，主要位于作业矩形外 y≈5.7–7.1 m；4 m 内局部回波 p95 ≤0.08 m。[原始比较](extended/depot-map-consistency.json)。没有修改地图或移除障碍来通过；局部路线通过不证明整个世界地图等价，远处差异根因仍待进一步核验。
- 原生 GUI 日志仍有上游 HMI/QML/图形警告，未发现本轮窗口异常退出；不将“窗口启动”当成视觉质量保证。
- 测试使用现有环境，Starlette 对其 httpx TestClient 有弃用警告；未为消除警告擅自升级依赖。墙钟截止启发式即使固定种子，也不保证不同机器/负载逐位相同。

## 复验与证据保存

先按唯一使用说明启动面板，然后在根目录运行（每个脚本拥有独立的会话，勿同时执行）：

```bash
src/lab/.venv/bin/python src/lab/e2e/full_loop.py --scenario maze-inspection --algorithms urp.turn_mode_dp,urp.raw_oracle,pyvrp.hgs+urp.layered,ortools.gls+urp.layered --modes follow_path,nav2_waypoints --budget 240
src/lab/.venv/bin/python src/lab/e2e/edge_cases.py
src/lab/.venv/bin/python src/lab/e2e/full_loop.py --scenario farm-rows-warehouse --algorithms urp.turn_mode_dp --modes follow_path,nav2_waypoints --budget 900
src/lab/.venv/bin/python src/lab/e2e/full_loop.py --scenario field-coverage-depot --algorithms f2c.boustrophedon --modes follow_path --budget 600
```

`edge_cases.py` 会明确终止它自行启动、且带匹配 URP 归属标记的 planner_server，以验证故障处理；不会关闭未知 ROS 任务。复验脚本将新结果写入 `src/lab/runtime/e2e/<带时间戳目录>/`，不覆盖本次 acceptance 证据。首次失败的原始记录也在 acceptance 中，包括修复前监控遗漏、spawner 误判和测试输入重复节点被正确拒绝的诊断。无解、超时与取消是预期结果，不计为成功求解。

[环境及锁文件哈希](acceptance/environment.json)、[ROS 包版本](acceptance/ros-versions.txt)、[证据 SHA256 索引](acceptance/manifest-sha256.json)、[机器可读汇总](acceptance/summary.json)。求解原始请求/stdout/stderr 位于 [solver-processes](acceptance/solver-processes/)，每次会话原始启动日志、资源清单、执行数据和清理报告位于 [sessions](acceptance/sessions/)。现场完整数据库仍保留在 `src/lab/runtime/`，没有用测试桩或人工数据冒充实验成果。

扩展证据：[环境/锁文件](extended/environment.json)、[SHA256 索引](extended/manifest-sha256.json)、[机器可读汇总](extended/summary.json)、[会话原始日志](extended/sessions/)。旧 acceptance 证据保持原样；不以新结果覆盖旧失败。
