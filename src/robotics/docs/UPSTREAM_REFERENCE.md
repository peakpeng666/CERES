# TurtleBot 4 官方参考接入

检查日期：2026-10-17。范围是官方启动入口、来源与本地依赖检查；当前没有物理仿真运行成功记录。

## 采用的参考组合

沿用项目[路径研究与仿真方案](../../../docs/路径研究与仿真方案.md)优先复用现成项目的决定，首个可执行入口指向 TurtleBot 4 官方 Humble 仿真包。机器人和世界由上游提供，本仓库不复制或改写它们。

| 项目 | 本轮配置 |
|---|---|
| 上游仓库 | [turtlebot/turtlebot4_simulator](https://github.com/turtlebot/turtlebot4_simulator/tree/0ea4a60c51e49ed4a9e25b4f9971ff0f0c9a08fe) |
| 检查提交 | `0ea4a60c51e49ed4a9e25b4f9971ff0f0c9a08fe`，Humble 分支当时的 1.0.3 |
| 目标软件栈 | Ubuntu 22.04、ROS 2 Humble、Gazebo Fortress / Sim 6 |
| 官方启动文件 | `turtlebot4_ignition_bringup/launch/turtlebot4_ignition.launch.py` |
| 本仓库入口 | `ros2 launch ceres_route_sim turtlebot4_reference.launch.py` |
| 机器人与世界 | 官方 `lite` 模型、官方 `depot` 世界；起点 `(0, 0, 0, yaw=0)` |
| 通信范围 | ROS domain 72、namespace `ceres_tb4_reference`、独立 `ceres_tb4_*` Gazebo partition、localhost |
| 当前行为 | 启动上游世界、模型和原桥接，可选 RViz；不提交 CERES 任务或运动命令 |
| 导航设置 | `nav2=false`、`localization=false`、`slam=false`；未将任意世界与默认定位地图混配 |

官方 [Humble 使用说明](https://turtlebot.github.io/turtlebot4-user-manual/software/turtlebot4_simulator.html)和[固定提交的主 launch](https://github.com/turtlebot/turtlebot4_simulator/blob/0ea4a60c51e49ed4a9e25b4f9971ff0f0c9a08fe/turtlebot4_ignition_bringup/launch/turtlebot4_ignition.launch.py)支持上述入口和模型参数。上游 launch 会启动 Gazebo GUI；本适配没有声称提供上游的 headless 模式，关闭 RViz 也不等于关闭 Gazebo GUI。

Depot 是参考场景，不是农业场景。加载该世界和差速机器人不证明农业土壤、作物碰撞、转弯、作业行服务或阿克曼约束已建模。

## 先运行依赖和版本预检

在仓库根目录执行：

```sh
cd src/robotics
python3 scripts/check_upstream.py --output-dir evidence/tb4_preflight_new
```

命令只读取本地环境和源码；不安装依赖、不下载模型、不启动 ROS/Gazebo，也不发控制命令。输出目录必须是新路径。

- 退出 2：依赖、目标 OS/ROS、图形会话或关键源码匹配存在阻塞；查看 `preflight.json` 的 blockers。
- 退出 0：已发现所检查的启动依赖，状态为 `launch_dependencies_available`。这仍是离线预检，`runtime_executed=false`、`task_execution_verified=false`、`simulation_ready=false`。
- `reference_layout.files` 记录实际安装文件的 SHA256，并核对已审查的 Git blob 身份。版本号相同但关键文件被修改也会阻止该固定参考入口。

当前云端实际执行的预检见 [本轮结果](../evidence/20261017_hardening/upstream_preflight/preflight.json)：Ubuntu 24.04，无 Humble 环境、`ros2`、`ign`、ament 或图形会话，因此退出 2。

依赖清单和 6 个关键文件身份在 [upstream_turtlebot4_humble.json](../ceres_route_sim/config/upstream_turtlebot4_humble.json)。TurtleBot 的可选依赖没有加入本包的强制 package.xml 依赖，避免只看研究路线也必须安装整套仿真。

## 有匹配环境后启动参考

先按官方手册在目标环境准备依赖与 `ceres_route_sim` 工作空间，固定 TurtleBot 4 到上述提交或与所审查文件一致的 1.0.3 安装包，再执行：

```sh
source /opt/ros/humble/setup.bash
source ~/ceres_sim_ws/install/setup.bash
export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=72 IGN_IP=127.0.0.1
export IGN_PARTITION=ceres_tb4_reference_manual
ros2 run ceres_route_sim upstream_preflight --output-dir /absolute/path/to/new_tb4_preflight
ros2 launch ceres_route_sim turtlebot4_reference.launch.py rviz:=true
```

launch 会再次预检，随后直接 include 官方入口。它不开放任意命令、地图路径、机器人命名或 benchmark 坐标作为启动参数。上述命令是入口使用方法，没有在当前云端执行。

## 来源冻结的实际范围

主 launch、Ignition launch、spawn、bridge、Depot 世界文件和 package.xml 已核对到固定提交；这是关键入口冻结，**还不是整个运行环境冻结**。上游的 [Depot SDF](https://github.com/turtlebot/turtlebot4_simulator/blob/0ea4a60c51e49ed4a9e25b4f9971ff0f0c9a08fe/turtlebot4_ignition_bringup/worlds/depot.sdf)引用不带版本的 Fuel 模型：

```text
https://fuel.gazebosim.org/1.0/OpenRobotics/models/Depot
```

因此配置保留 `asset_versions_frozen=false` 和 `full_dependency_lock=false`。首次完整运行需要另外记录 Create 3、机器人描述、导航/桥接库和 Gazebo 插件版本，以及本地 Fuel 缓存中的实际模型版本、文件哈希和资产许可证。上游源码声明 Apache-2.0，不自动代替外部模型资产的许可证核查。预检不会悄悄下载或复制这些资产。

## 接入研究任务还缺的验收

1. 官方实例真实加载，取得持续推进的 `/clock`、传感器、odom/TF 和独立物理观测；保存完整版本、启动日志、截图或录像。
2. 明确地图和服务位姿、米制坐标与标定来源。研究访问折线仍通过 route-scene v1 展示，不能成为导航命令。
3. 加入任务执行器与取消/超时/停止逻辑，区分任务访问顺序、导航重规划路径和实际轨迹。官方机器人起点包含 dock 状态，需按上游接口处理任务开始，不能直接发送速度模拟脱离充电座。
4. 按任务定义记录服务事件、未服务与失败；独立轨迹用于评价。没有接触观测时碰撞状态记为未知，不能报告零碰撞。

这些尚未完成的环节对应项目中期的仿真执行要求。当前入口消除了“只有文档候选、没有任何上游启动适配”的缺口，但没有完成中期仿真任务验收。

## 给后端和前端的状态约定

目前提供独立 CLI JSON，没有新增 HTTP 仿真 API。后续平台列表可消费 `platform_id`、`launch_available`、`blockers` 与环境记录；preflight 通过、进程已启动、物理反馈已观测和任务成功须分别展示。

未来 SimulationRecord 应保留独立 simulation/task ID，关联实验 revision、源 run 与输入哈希、标定和地图/机器人版本；另记录仿真时间、墙钟、服务事件、规划与执行轨迹、失败原因和观测是否齐备。数学解的可行性、推荐状态和物理任务成功分别由对应证据决定，不能互相填充。
