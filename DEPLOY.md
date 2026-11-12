# 工作台统一部署

源码开发与新安装入口统一为 `main`。Docker 默认使用通过镜像验收后发布的 `:main` 通道；原生安装检出 main 的实际提交。可复现安装优先使用已经发布镜像的完整 40 位 Git SHA，或经过发布验证的 `v*` 标签。`deploy` 与 `redo` 不再作为新安装入口。

迁移补丁基线是 `028bae0e967894c9370460a0301cdf7c27b10965`。第一阶段 `3481c414` 已通过[工程 CI](https://github.com/peakpeng666/URP/actions/runs/42170504143)及[镜像发布流程](https://github.com/peakpeng666/URP/actions/runs/42170504225)，包含两镜像 smoke、匿名 digest 拉取、OCI revision 与 `/opt/ceres/REVISION` 检查，两个 main 通道也已独立匿名复核。首次证据见 [当前索引](docs/current-index.md)。本次切换提交的 CI 仍须单独检查，真实 Windows/WSL 首装和旧客户端迁移验收仍待完成。Git SHA 与 registry digest `sha256:…` 是不同身份。旧分支与数据在覆盖及恢复验证完成前继续保留。

默认 `full` 安装工作台、ROS 2 Humble / Gazebo Fortress、CVRP 基线和 Fields2Cover。日常使用见 [工作台使用说明](setup/README.md)，具体版本见 [依赖清单](deploy/DEPENDENCIES.md)。

## 安装

| 环境 | 安装方式 |
|---|---|
| Windows x86_64 + Docker Desktop（WSL2 后端） | 直接拉取 `ghcr.io/peakpeng666/urp-sim`，不克隆仓库 |
| Linux x86_64 + 可用的 Docker Engine | 使用同一镜像和 Compose 配置 |
| WSL2 Ubuntu 22.04 x86_64，无 Docker | 浅克隆所选版本，安装系统依赖并构建 |
| 原生 Ubuntu 22.04 x86_64，无 Docker | 与 WSL 使用同一套 `setup/ubuntu/setup.sh` |

Linux 或 WSL Ubuntu：

```sh
curl -fsSL https://raw.githubusercontent.com/peakpeng666/URP/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/peakpeng666/URP/main/install.ps1 | iex
```

安装器优先使用已启动的 Docker。没有 Docker 时，Windows 入口检查 WSL2 与 Ubuntu 22.04；首次安装发行版后，按提示创建 Linux 用户，再运行同一命令。源码安装会预检 Ubuntu 版本、x86_64 架构及 WSL2；系统依赖通过 `sudo apt` 安装，Python 和 Node 使用独立目录，不替换系统 Python。用普通用户运行安装器，不要给整个脚本加 `sudo`。

PowerShell 的 WSL 路径与已下载的 Bash 脚本发现缺少基础工具时，会在交互终端提示安装；非交互运行需预先装好。Ubuntu 若还不能执行上面的 `curl` 命令，先运行 `sudo apt-get update && sudo apt-get install -y curl ca-certificates git`。Docker 必须能执行 `docker info`，并提供 `docker compose`。

## 首次切换必须分两阶段

先仅上线发布与 digest 验收流程，保持原安装默认值，成功产出并匿名验证可用的 main 镜像。记录两镜像 digest、对应源码提交和真实 Actions 作业结果后，再切换安装器/Compose 默认值。不能把“工作流文件存在、静态条件检查通过”当成真实调度或镜像已可用；若任一构建/验收失败，第二阶段不执行。

## 版本与组件

新安装默认跟随 `main` 的已验收镜像通道，可能落后于 main 最新源码提交；不会把最新源码的部署文件与旧镜像混用。固定某次发布时，将 `CERES_REF` 设为已发布的 `v*` 标签或完整 40 位提交号；镜像也使用该标签。完整 SHA 会与镜像的 OCI revision 核对，Compose 和 Fuel 脚本从镜像对应的源码提交下载。

```sh
# 将 <完整提交号> 换成已发布镜像的 40 位 SHA
curl -fsSL https://raw.githubusercontent.com/peakpeng666/URP/main/install.sh \
  | CERES_REF='<完整提交号>' bash

# 无 Docker 的 Ubuntu 源码部署从 main 检出实际提交
curl -fsSL https://raw.githubusercontent.com/peakpeng666/URP/main/install.sh \
  | CERES_REF=main bash
```

已安装后可用 `ceres --ref <版本> update`。PowerShell 用 `$env:CERES_REF = '<版本>'` 后执行安装命令，或给脚本传 `-Ref <版本>`。安装器保存选择，后续启动和更新沿用它；不会在找不到某个版本时切换到其他通道。已保存 `deploy` / `redo` 的安装不会静默切换：先从 main 重新取得安装器，明确选择 `main` 或已验证发布的完整 SHA 后更新。旧入口及旧命令仍可能引用旧分支，不能仅修改文档就删除这些分支。

`--components` 与安装器的 `CERES_COMPONENTS` 对应：

| 组件 | 安装内容 |
|---|---|
| `workbench` | 两套 Python 环境、Node / 前端、ROS 2 Humble / Gazebo Fortress；Fields2Cover 仅在本地已有轮子时校验并安装 |
| `research` | 两套 Python 环境、Node / 前端、JDK 17、AILS 与 SISR；不安装 ROS |
| `full`（默认） | 合并上面两组，并下载、校验和安装 Fields2Cover 及其系统库 |

`research` 是当前 CVRP 数值基线环境。Docker 的 `ceres-sim` 包含完整运行栈，`ceres-bench` 包含规划与 CVRP 基线，不含 ROS / Fields2Cover。

源码安装通过 `CERES_COMPONENTS` 选择，默认 `full`。Docker 入口使用完整 `ceres-sim` 镜像，不按组件裁剪：

```sh
curl -fsSL https://raw.githubusercontent.com/peakpeng666/URP/main/install.sh \
  | CERES_COMPONENTS=full bash
```

已检出的源码直接运行 `bash setup/ubuntu/setup.sh --components full`。PowerShell 用 `$env:CERES_COMPONENTS = 'full'`，该值会传入 WSL 安装器。源码规划与 Agent 开发使用 [lab-code 入口](setup/README.md#启动与停止)，历史 NAS 数据与整机快照见 [数据与恢复](setup/README.md#数据与恢复)。

## 启动、更新与停止

```sh
ceres up       # 启动
ceres status   # 健康检查
ceres update   # 更新所选发布；源码方式会重新准备依赖
ceres down     # 停止，保留数据
ceres fuel     # 按需下载 warehouse / depot 的上游模型
```

如果找不到 `ceres`，使用 `~/.local/bin/ceres`，或将 `~/.local/bin` 加入 PATH。PowerShell 可在当前窗口保存命令入口后调用：

```powershell
$ceres = [scriptblock]::Create((irm https://raw.githubusercontent.com/peakpeng666/URP/main/install.ps1))
& $ceres status
& $ceres down
& $ceres update
```

浏览器打开 http://localhost:4100/ 。http://localhost:4100/lab/api/health 应返回 `"status":"ok"`；仿真运行时，Foxglove 和 Gazebo WebSocket 分别使用 `127.0.0.1:8765` 和 `127.0.0.1:9002`。

Docker 的运行数据保存在 `ceres-lab_lab-runtime` 和 `ceres-lab_archive-data` 卷里，`ceres down` 不删除它们。源码方式的场景和日志在 `src/lab/runtime`，可写档案库在 `.local-state`；`data/backend` 中的原始库保留。

无 Docker 时，检出目录默认为 `~/.local/share/ceres/src`；稀疏检出保留工作台和所需 CVRP 基线，排除大型原始仓库、训练数据及历史实验目录。Docker 只下载部署文件到 `~/.local/share/ceres`，Windows 为 `%LOCALAPPDATA%\ceres`。

## 安装验载

源码安装结束会自动检查所选组件，报告保存在 `.local-state/deploy/runtime-<时间>-<进程号>/runtime.json`。需要单独复核时，在源码根目录运行：

```sh
python3 deploy/check-runtime.py --components full \
  --output "$PWD/.local-state/deploy/verify-$(date -u +%Y%m%dT%H%M%SZ)-$$"
```

检查覆盖 Python / Node 版本与模块加载、前端、Fields2Cover 绑定、AILS / SISR 合成算例，以及所选 ROS / Gazebo 命令。`research` / `full` 的基线结果另保存在报告目录的 `cohort/`。这是运行依赖验载；真实 Gazebo 任务按下方仿真流程检查。

## 镜像发布与自建

`.github/workflows/images.yml` 先运行工程检查，再为 `main` 和 `v*` 标签构建镜像（标签由 desktop 工作流复用该工作流）；每个镜像先通过本地 smoke，推送提交 SHA 形式的查询标签，并从本次推送回执捕获真正的 manifest digest。匿名验收和 `promote-channels` 全程使用同一 `repository@sha256:…`，不重新解析 SHA 标签。两个镜像均通过后才提升 `:main` 或版本标签；所有调用共享提升锁，旧 main 运行必须通过当前远端 main 头检查，否则跳过。桌面手动构建默认不提升通道。为避免无统一版本排序的 `v*` 乱序覆盖，不再自动更新 `:latest`，已有别名不删除；使用者应明确选择 main 或已核验的发布版本。注册表的多标签写入不是原子事务，提升中断须按记录核对两个镜像；提交 SHA 标签也不是 registry 的不可变标识，精确镜像身份以已验收 digest 为准。

首次发布后，维护者需检查两个包的可见性与仓库关联：

- [ceres-sim 设置](https://github.com/users/peakpeng666/packages/container/urp-sim/settings)
- [ceres-bench 设置](https://github.com/users/peakpeng666/packages/container/urp-bench/settings)

在 **Package settings → Change package visibility → Public** 设置公开，并关联 `peakpeng666/CERES`。工作流会检查匿名拉取；使用者不需要 GitHub 或 Docker 登录。拉取失败也可能是所选标签尚未发布，先核对标签和构建状态。

需要修改镜像时，在完整检出中构建：

```sh
docker compose -f deploy/docker-compose.sim.yml -f deploy/docker-compose.sim-build.yml build
```

只运行规划与数值工作台、不启动 ROS 时，可使用 `deploy/docker-compose.yml` 中的 `ceres-bench` 镜像；同样通过 `CERES_IMAGE_TAG` 选择已发布标签。

## 资源与许可

`maze` 不依赖 Fuel。`warehouse` 和 `depot` 先执行 `ceres fuel`，再开启新会话；模型从 https://fuel.gazebosim.org 下载到使用者本机，安装与镜像构建不会自动下载它们。

Fields2Cover 从固定 Release 资源获取，校验 SHA256 后安装；原生安装与镜像使用同一份清单。其 BSD 声明随资源保留。Fuel 的非商业和禁止衍生条款、ROS/Foxglove 等依赖的许可见 [第三方说明](deploy/THIRD_PARTY.md)。旧平台快照的模型和运行数据仍按原清单单独管理。

维护者单独打包可再分发的 Fields2Cover 资源时使用 `bash deploy/upload-assets.sh vYYYYMMDD`；没有轮子时不会上传。

## 平台与验收范围

本安装面向 Linux x86_64；Windows 通过 Docker Desktop 的 WSL2 后端或 WSL2 Ubuntu 22.04 使用。新部署的 WSL2 / Windows 端到端实测与新镜像发布状态，以对应提交的测试记录和 Actions 结果为准。已有云端检查见 [测试报告](setup/2026-10-19/delivery-review/REPORT.md)，历史仿真记录不替代新机器验收。Fields2Cover 的既有构建来源记录保留在 manifest；原始编译命令尚未恢复。

## 网络

Docker 只向宿主机回环地址发布端口，不使用 `network_mode: host`。`ROS_LOCALHOST_ONLY=1` 配合 `setup/cyclonedds.xml` 使用回环单播，不需要 `NET_ADMIN` 或宿主机组播路由。XML 只设置参与者序号上限，不重复指定网卡或 Peer。

WSL 启动器设置 `0.0.0.0` 监听与软件渲染，供 Windows 的 localhost 转发访问。原生 Ubuntu 仍只监听 `127.0.0.1`，不强制软件渲染。直接运行 `bash setup/run.sh lab` 的本机行为保持不变。

## GPU

无界面的 Gazebo 仍会为传感器初始化 GLX。容器入口在没有 `DISPLAY` 时启动 `Xvfb :99`。已有桌面或 WSLg 显示时不替换。镜像默认 `LIBGL_ALWAYS_SOFTWARE=1`。要用 NVIDIA：

```sh
CERES_GPU=1 ceres up
```

Windows 使用支持 CUDA on WSL 的驱动，不要在 WSL 里再装 Linux 驱动。没有 NVIDIA 设备时不要加 `CERES_GPU=1`。原生 Ubuntu 不走这个变量。

## 指纹

笔记本验收文件不改。系统库与验收记录一致时，只接受原来的二进制。镜像和别的机器在 `CERES_SIM_PROFILE=image` 时使用编译出来的 `profile.json`。`CERES_BRIDGE_FIX=0`、`CERES_TF_FIX=0`、`CERES_GUI_FIX=0` 只是对照开关。

发布出去的 `ceres-sim` 在构建时运行 `deploy/record-sim-profiles.py`，所以镜像里的指纹就是那次构建的产物。本地重新编译的二进制使用独立构建指纹。

## 关闭接口

`POST /lab/api/shutdown` 默认只接受回环地址。从 Windows 经 Docker 转发进来的连接会得到 403。关容器用 `ceres down` 或 `docker compose stop`。关 WSL 或本机进程要在那个 Linux 里执行 `ceres down`，这样连接才是回环。设置 `CERES_ADMIN_TOKEN` 之后，所有关闭请求都要带 `X-CERES-Admin-Token` 或 `Authorization: Bearer`。

Windows 上也可以双击 `启动实验台.bat`。它只调用 Docker 或 `wsl`，不在 Windows 里安装 Python。

## 排错

| 现象 | 处理 |
|---|---|
| `docker pull` 要求登录或 403 | 核对标签是否已发布及包是否公开；使用者无需登录 |
| 浏览器打不开 localhost:4100 | Docker 引擎是否已启动；WSL 里 `ceres status` |
| 页面 403，提示拒绝的 Host | 用 http://localhost:4100/ |
| `sh` 脚本报 `$'\r'` | 项目自身的 `*.sh` 在 `.gitattributes` 里是 LF；`src/repos` 第三方快照按采集原字节保存（`-text`），其中个别脚本仍是 CRLF，运行前先转换副本 |
| WSL 里仿真被杀掉 | 把 `setup/ubuntu/wslconfig.example` 复制为 Windows 用户目录的 `.wslconfig`，然后 `wsl --shutdown` |
| warehouse 或 depot 报世界资源不完整 | `ceres fuel`。maze 不需要 |
| 指纹不符 | 核对源码、系统库和构建 profile 是否来自同一版本；重新按所选版本构建 |
| 平台预检未通过 | 无 Docker 时需要 Ubuntu 22.04 x86_64；Windows 内需 WSL2，不能使用 WSL1 |
| full 安装停在 Fields2Cover | 查看网络、固定 Release 资源和哈希检查日志；不要跳过后继续当作完整安装 |

## RViz 和 Gazebo 窗口

两条会话都会在你打开窗口时启动本机程序，浏览器里的 Foxglove 不代替它们：

| 会话 | 命令 |
|---|---|
| TB4 | `rviz2 -d src/lab/ros/workbench.rviz`。配置使用 `rviz_default_plugins`（地图、激光、路径、机器人模型、TF）和 `nav2_rviz_plugins`（Navigation 2、粒子云、目标点）。Gazebo 窗口是 `ign gazebo -g --gui-config` 指向 TurtleBot4 的 `gui/standard/gui.config`，并加上 `turtlebot4_ignition_gui_plugins` 与 `irobot_create_ignition_plugins`。 |
| 田间 | `rviz2` 读取当场生成的 `field.rviz`（`rviz_default_plugins` 的俯视、标记和 TF）。Gazebo 窗口是 `ign gazebo -g`。 |

默认的一条命令不带 `rviz`。面板或 `POST /lab/api/sim/rviz`（田间是 `POST /lab/api/fieldsim/window`）才会拉起窗口。历史验收 `setup/2026-10-15/deployment/nav-smoke.py --rviz` 打开的是 `ros2 launch turtlebot4_viz view_robot.launch.py`，配置 `robot.rviz` 用的也是上面两组插件。仓库里的启动脚本不调用 `rqt`。镜像里有 `rqt` 可执行文件和 `rqt_gui`、`rqt_robot_monitor`，因为 ROS 包依赖它们，但没有入口去打开它们。

| 路径 | RViz 会不会出现在你的屏幕上 |
|---|---|
| A. Docker，默认 | 不出现。容器用 Xvfb 只给 Gazebo 传感器提供 GL。浏览器看 Foxglove `ws://127.0.0.1:8765` 和 Gazebo `ws://127.0.0.1:9002`。 |
| A. Docker，要看见窗口 | 在有显示的 Linux 上，或在已经有 WSLg 的 WSL 发行版里执行 Docker：`CERES_DISPLAY=1 ceres up`。这把 `/tmp/.X11-unix` 挂进容器，并把 `DISPLAY` 传进去。Windows PowerShell 直接调用的 Docker 看不到 WSLg 的套接字，那种情况请在 Ubuntu 22.04 发行版里执行上面的命令。 |
| B. WSL2 + WSLg | 会打开。`setup/ubuntu/run-lab.sh` 发现已有 `DISPLAY` 就用它，不改成 Xvfb。在面板里打开 RViz 后，窗口出现在 Windows 桌面。没有 WSLg、`DISPLAY` 为空时，才用 Xvfb，窗口不可见。 |
| C. 原生 Ubuntu | 会打开。脚本不设置 `LIBGL_ALWAYS_SOFTWARE`，已安装的 GPU 驱动直接画到本机 `DISPLAY`。 |

## 仿真怎么试

工作台起来之后，TB4 用官方 `maze`（无 GUI）：

```sh
curl -sS -X POST http://127.0.0.1:4100/lab/api/services/sim/start \
  -H 'content-type: application/json' \
  -d '{"world":"maze","gui":false,"rviz":false,"localization":"ground_truth"}'
```

等到 `GET /lab/api/services` 里仿真为运行，并且 `127.0.0.1:8765` 与 `127.0.0.1:9002` 能连上。田间仿真要先有一份已验算通过的多车方案，再 `POST /lab/api/fieldsim`。

