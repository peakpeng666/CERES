# URP：农业机器人规划与 Agent 工作台

URP 将农业任务建模、算法推荐、真实求解、独立验算、仿真和实验记录接入同一工作台。浏览器入口为 `http://127.0.0.1:4100/`。

**[安装、启动与开发](setup/README.md)** · **[本轮整合与测试](setup/2026-10-30/engineering/REPORT.md)** · **[当前工程与分支索引](docs/current-index.md)** · [整合时的 29 个来源分支](docs/branches.csv) · [资料与许可证索引](docs/assets/README.md)

## 工程入口

| 模块 | 职责 |
|---|---|
| [前端](src/frontend/) | 任务编辑、农业模板、Agent 面板、方案对比、研究图表和历史回放 |
| [Lab](src/lab/CONTRACT.md) | HTTP 网关、Agent 工具、算法适配、独立检查、运行记录和仿真会话 |
| [离线 evolve](src/lab/urp_lab/evolve/INTEGRATION.md) | 候选准入、互补性审计、修复、动态调度实验与受保护的模型读取 |
| [档案后端](src/backend/README.md) | 实验包、SQLite 档案与 CVRP 数值任务（`#/jobs`） |
| [研究实现](src/research/README.md) | CVRP、装箱、转弯任务及历史研究 CLI |
| [验算与协议](independent/) | 独立复算、冻结协议与原始结果核对 |
| [仿真](src/robotics/README.md) | ROS 2 / Gazebo 集成、场景与执行回归 |

整理基线保留 main 的统计和执行检查，整合 Agent、工作台编辑与结果功能，以及离线 evolve。算法 ID、接口结构和历史记录继续兼容；逐分支依据见 [branches.csv](docs/branches.csv)。

## 运行

准备依赖后，启动规划和 Agent 工作台：

```sh
bash setup/run.sh lab-code --open
```

已安装 ROS 2 / Gazebo 的机器用 `bash setup/run.sh lab --open`。关闭用 `bash setup/run.sh lab-stop`。完整步骤、模型配置、地图获取和验证命令见 [使用说明](setup/README.md)。

## 研究与资料

[冻结研究证据（截至 2026-10-19）](docs/导师查看.md) · [实验报告与失败记录](experiments/目录.md) · [CVRP 基线](experiments/baselines/cvrp/README.md) · [文献笔记](literature/README.md) · [算法登记](algorithm_inventory.md)

既有实验按各自版本、预算和数据解释。Agent 的现有验证使用脚本模型；真实大模型质量、ROS/Gazebo 执行和历史仿真结果分别记在验收报告中。

论文、教材、结果包和第三方源码的逐项路径保存在 [资产索引](docs/assets/README.md)。本轮未迁移大文件；运行数据库与新轨迹保存在本地运行目录。
