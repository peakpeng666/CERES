# URP 工作约定

`main` 是已合并基线，日常获取与开发从 `main` 开始。当前工程与分支索引见 `docs/current-index.md`；`docs/branches.csv` 保留工程整合时的 29 个来源分支快照。开始前先看 Git 状态与分支差异，保留本地未提交成果；不要用 reset、强推或批量删除来完成同步。`backup/*` 保留历史快照。

## 入口与分工

- 唯一使用说明：`setup/README.md`；本轮工程验证在 `setup/2026-10-30/engineering/REPORT.md`；现场仿真记录在 `setup/2026-10-15/integration/REPORT.md`。
- 农业工作台、算法适配、网关和 ROS 会话：`src/lab/`；前端：`src/frontend/`。
- 档案与有界 CVRP 数值任务：`src/backend/`；研究算法：`src/research/`；独立验算与协议：`independent/`、`protocol/`。
- 首页是科研工作台；`#/jobs` 是数值任务，`#/bpp` 是历史 BPP。保留同一 4100 入口，不重新搭建平行系统。

## 云端环境与检查

云端通常没有 ROS/Gazebo、Fuel 缓存或桌面，不运行依赖本机 ROS 的 `lab-setup` 来准备普通代码任务。从仓库根执行：

```sh
uv sync --locked --extra solvers
npm ci --prefix src/frontend --engine-strict --no-audit --no-fund
uv run --no-sync python tools/check_project.py
```

需要 Lab 时另准备其环境：`cd src/lab && uv sync --locked --inexact`。根目录运行 `python3 tools/fetch_ros_maps.py`，再在 Lab 目录设置 `URP_ROS_MAPS="$PWD/../../.local-state/ros-maps"` 并运行 `.venv/bin/python -m pytest -q --require-maps`。地图由固定上游提交和 SHA256 校验。Fields2Cover 与真实 ROS/Gazebo 测试单列结果；真实会话测试为 `src/lab/e2e/field_backend.py`，使用全新绝对输出路径，避免并发物理验收。

源码启动用 `bash setup/run.sh lab-code`；安装 ROS 的机器用 `lab` 准备仿真组件。`tools/smoke_workbench.py` 检查已运行网关的生成、推荐、求解、验算和导出。更新 Python 依赖先改各自 `pyproject.toml` 与 `uv.lock`；根 `requirements.lock` 仅从根 uv 锁导出，命令见使用说明。

Node 版本与 CI 保持一致（24）；根与 `src/lab` 各有独立 uv 锁文件。勿替换系统 Python 或修改 `/opt/ros`。构建、依赖及大型运行原始数据按 `.gitignore` 保存在运行目录；既有平台 Release 的恢复入口见 `setup/README.md`。新资产使用 `storage.json` 中的私有项目目录，副本状态记入 `docs/assets/copies.json`；逐文件校验和恢复验证完成后再移出跟踪。日志摘要、回归脚本与源码进入 Git。

Agent 的状态读取和启动不调用模型；显式发送或交接任务才开始一轮。工程回归使用脚本模型 / MockTransport，真实付费模型与物理长跑按用户单独指定的任务执行。系统提示版本与会话保存，更新答复风格时递增版本。

## 科研结果与数据

- 区分任务访问顺序、米制可行几何路径、实际执行轨迹；未经标定的基准 XY 不能当作米。
- 区分规则推荐与学习模型、URP 路径跟踪与 Nav2 重规划；不得用空值或演示数据补成成功。
- 检查路径段及机器人占用空间，保留随机种子、模型/算法版本、原始结果、失败状态与真实退出码。
- 当前执行为内置同构差速车、串行交通基线。曲率/异构规划通过不代表这些车辆已可物理执行。能耗是版本化后验代理，不是电池实测。
- `data/`、`experiments/`、`protocol/` 的已有证据与历史失败保留；应用可写数据库用 `.local-state/` 和 `src/lab/runtime/`，不要覆盖冻结档案。
- 新说明应更新现有入口及验收报告，不创建多个“最终版”。报告验证范围、未完成项与证据路径。
