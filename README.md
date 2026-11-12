# CERES（灵畴）：农业机器人可验证规划调度与自主智能体工作台

> **CERES** (*Composable Evolutionary Routing & Execution Sandbox* / 可组合演化路由与执行沙盒) 
> 面向农业机器人自主作业的神经符号任务规划、多机协同调度与独立物理验证闭环系统。（学术工程兼容标识：*CERES-Harness*）

CERES 将自然语言农艺任务建模、算法组合推荐、多机运筹求解、独立几何/物理验算及跨引擎仿真无缝集成于同一工作台。
本地 Web 交互入口：`http://127.0.0.1:4100/`

---

## 快速导航

- **[部署与环境搭建](setup/README.md)** · **[工程验证与验收报告](setup/2026-10-30/engineering/REPORT.md)**
- **[系统演进与后续规划](docs/系统演进与后续规划.md)** · **[科研证据与技术总纲](docs/科研证据与技术总纲.md)**
- **[模块导引与索引](docs/模块导引与索引.md)** · **[资产与许可证索引](docs/assets/README.md)**

---

## 核心子系统架构

| 模块目录 | 角色定位 | 核心职责 |
|---|---|---|
| [`src/frontend/`](src/frontend/) | **交互控制台 (CERES Workbench)** | 任务编辑、田间模板、Agent 对话流、方案对比、图表呈现与历史回放 |
| [`src/lab/`](src/lab/CONTRACT.md) | **网关与调度大脑 (CERES Core)** | HTTP/SSE 网关、Agent 决策循环、求解器适配、独立检查器与仿真会话管理 |
| [`src/lab/ceres_lab/agri/`](src/lab/ceres_lab/agri/) | **农机与农艺模型 (Agri Engine)** | 环境-作物-机具三元组建模、ASAE D497 牵引核验、Bochtis 掉头与田间行规划 |
| [`src/lab/ceres_lab/evolve/`](src/lab/ceres_lab/evolve/) | **演化求解内核 (Evolve Sandbox)** | 互补算子挖掘、圆角检查 (fillet)、边界内缩修复 (repair) 与停-转-行工期模型 |
| [`src/backend/`](src/backend/README.md) | **历史实验档案 (Archive Server)** | 离线实验包归档、SQLite 记录与有界 CVRP 数值计算（`127.0.0.1:4180`） |
| [`src/research/`](src/research/README.md) | **算法研究库 (Research Lib)** | 纯欧氏 CVRP 推荐、装箱优化、转弯模式与基准 CLI |
| [`src/robotics/`](src/robotics/README.md) | **跨引擎物理仿真 (Robotics Sim)** | ROS 2 (Humble) / Gazebo Fortress / MuJoCo 动力学验证与时序遥测 |
| [`independent/`](independent/) | **数学形式化验算 (Formal Verifier)** | 独立复算脚本、离线数据冻结协议与真值对账 |

---

## 运行与操作

### 1. 启动 CERES 规划工作台
环境依赖配置完成后，运行以下指令启动网关与交互前端：

```sh
# 源码模式启动并自动打开浏览器
bash setup/run.sh lab-code --open
```

已安装 ROS 2 / Gazebo 环境的计算节点，运行集成仿真启动：
```sh
bash setup/run.sh lab --open
```

### 2. 关闭工作台
```sh
bash setup/run.sh lab-stop
```

完整依赖锁、模型连接（Claude / Qwen / DeepSeek）、地图获取及验证命令请查阅 **[系统部署说明](setup/README.md)**。

---

## 实验基准与技术档案

- **[科研证据与技术总纲](docs/科研证据与技术总纲.md)**
- **[实验报告与失败用例记录](experiments/目录.md)**
- **[算法清单与能力注册表](algorithm_inventory.md)**
- **[CVRP 基准测试档案](experiments/baselines/cvrp/README.md)**

历史实验严格保留对应配置快照、计算预算与物理约束环境。CERES 遵循零信任验证纪律：语言模型仅负责意图规约与参数候选提议，方案可行性与边界合规完全由独立几何检查器及动力学仿真裁定。
