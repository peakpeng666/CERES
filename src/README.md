# 源码

| 目录 | 当前内容 |
|---|---|
| [research](research/README.md) | 父代选择、CVRP选择与CRC、转弯mode及NAS研究CLI；生成执行组件 |
| [backend](backend/README.md) | 本地实验包导入、SQLite记录、附件、比较和路线API；持久化 CVRP 数值任务与独立验算 |
| [frontend](frontend/README.md) | 科研面板、规划、仿真与运行记录；BPP 入口保留在 #/bpp |
| [lab](lab/CONTRACT.md) | 统一网关、求解适配、独立检查、推荐、导出与 ROS 仿真会话 |
| [lab/urp_lab/agent](lab/urp_lab/agent/) | 大模型会话、工作台工具与执行批准 |
| [lab/urp_lab/evolve](lab/urp_lab/evolve/INTEGRATION.md) | 离线候选、准入、修复、动态调度与模型读取检查 |
| [robotics](robotics/README.md) | 官方 TurtleBot4 固定版本预检与 route-scene 研究适配，保留独立验收范围 |
| [repos](repos/README.md) | 75个外部论文代码目录及来源清单 |

科研面板、规划与仿真、CVRP 数值任务的统一启动、停止和演示步骤见 **[本机使用说明](../setup/README.md)**（网关 `127.0.0.1:4100`）。`src/start.sh` 保留单独后端、构建和 `check` 工程验证命令，供开发与 CI 使用。

数据位于[../data](../data/README.md)。前端的`inputs`、`research`、`public/data`和后端的`packages`通过相对链接访问同一份数据。根目录 `uv.lock` 锁定研究/档案工程环境；`lab/uv.lock` 锁定仿真网关求解环境；ROS 保持系统兼容环境。档案运行库位于 `.local-state/`，首次启动只读复制历史数据库；lab 运行库继续在 `lab/runtime/`，互不覆盖。

[下一阶段](../docs/下一阶段.md) · [检查记录](checks.json) · [来源记录](sources.json)。
