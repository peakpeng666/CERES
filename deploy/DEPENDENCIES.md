# 依赖清单

这份清单对应 [统一部署入口](../DEPLOY.md)。Python 和前端使用仓库锁文件；系统包按部署清单从 Ubuntu / ROS 仓库安装。旧平台快照及其历史环境见 [工作台使用说明](../setup/README.md)。

源码环境以根与 `src/lab` 的 `uv.lock` 为准，根 `requirements.lock` 是经 CI 核对的导出。前端及浏览器验收各用自己的 npm 锁文件，下载地址统一为 npm 官方源。历史 BPP 数据转换和冻结实验使用各自原锁文件。完整环境映射与更新命令见 [开发说明](../setup/README.md)。

工作台有两套互不替换的 Python 环境，解释器同为 3.12，依赖版本不同，安装时必须分开：

| 环境 | 解释器 | 锁文件 | 用途 |
|---|---|---|---|
| 仓库根 `.venv` | CPython 3.12.13 | `uv.lock` | 档案 / CVRP 数值任务（`src/start.sh backend`） |
| `src/lab/.venv` | CPython 3.12.13 | `src/lab/uv.lock` | 网关、求解、前端不负责的算法 |

Ubuntu 22.04 x86_64（WSL2 或原生）的完整安装：

```sh
bash setup/ubuntu/setup.sh --components full
```

`--components` 与安装器的 `URP_COMPONENTS` 对应：

| 组件 | 安装内容 |
|---|---|
| `workbench` | 两套 Python 环境、Node / 前端、ROS 2 Humble / Gazebo Fortress；Fields2Cover 仅在本地已有轮子时校验并安装 |
| `research` | 两套 Python 环境、Node / 前端、JDK 17、AILS 与 SISR；不安装 ROS |
| `full`（默认） | 合并上面两组，并下载、校验和安装 Fields2Cover 及其系统库 |

`research` 是当前 CVRP 数值基线环境，不包含历史 NAS 训练栈。Docker 的 `urp-sim` 包含完整运行栈，`urp-bench` 包含规划与 CVRP 基线，不含 ROS / Fields2Cover。

手动准备 Python 与前端时：

```sh
uv python install 3.12.13
uv sync --locked --extra solvers --python 3.12.13
(cd src/lab && uv sync --locked --inexact --python 3.12.13)
(cd src/frontend && npm ci --engine-strict --no-audit --no-fund && npm run build)
```

`uv` 固定为 **0.12.23**。Node 固定为 **24.22.0**（官方 `linux-x64` 包 SHA256 `28ea0a438de9a13bd7891fd758eee6c7819ee8af5442dfd8d45bd65e56d4228c`，Windows x64 包 SHA256 `f9683a19877e12a9b167305c8db71040d812876359de21f0d2e97c3f321f80c3`）。仓库没有 `.nvmrc`。`src/frontend/package.json` 的 `engines.node` 是 `>=22.12.0`，CI 只写了大版本 24。部署统一使用 24.22.0，npm 使用该官方包自带的 npm，`package-lock.json` 为 lockfileVersion 3。

## 工作台直接依赖

这些包由工作台或仿真脚本直接使用，已在 `src/lab/pyproject.toml` 中声明，并与锁文件保持一致：

| import | 分发名 | 版本 | 原先从哪来 |
|---|---|---|---|
| `yaml` | PyYAML | 6.0.3 | `uvicorn[standard]` |
| `vrplib` | vrplib | 2.2.0 | `pyvrp` |
| `websockets` | websockets | 17.2 | `uvicorn[standard]`；`e2e/field_backend.py` 直接使用 |
| `starlette` | starlette | 1.8.0 | `fastapi` |

同时把原来的范围约束收成锁文件里的版本：`httpx==0.28.1`、`pillow==12.3.0`、`pyproj==3.8.0`、`python-multipart==0.0.32`、`defusedxml==0.7.1`、`opencv-python-headless==5.0.0.93`、开发依赖 `pytest==9.1.1`。

## 锁文件之外的依赖

| 名字 | 原因 |
|---|---|
| `torch` | 只出现在 `src/research` 的 NAS 试验代码。网关、档案后端和仿真会话不 import 它。 |
| `fields2cover` | 由固定 Release 轮子安装，不在 PyPI 锁里。`full` 必须安装成功；`workbench` / `research` 默认只检查本地轮子，缺少时使用覆盖往复基线。下载和 ABI 依赖见下节。 |
| `rclpy`、`sensor_msgs`、`geometry_msgs`、`nav_msgs`、`nav2_msgs`、`tf2_ros`、`tf2_msgs`、`rosgraph_msgs`、`visualization_msgs`、`std_msgs`、`irobot_create_msgs`、`rcl_interfaces`、`ros_gz_interfaces` | ROS 2 Humble 的系统包，给 `/usr/bin/python3`（Ubuntu 22.04 上的 3.10）用，不进 3.12 虚拟环境。 |
| 档案后端 `src/backend` | 标准库。CVRP 任务再 import 研究目录里的 `urp_research.cvrplib`，求解包来自根环境的 `solvers` extra。 |
| `urp_research.cvrp_selection` | 网关的学习特征模块 import 它，该包本身只用标准库。 |

## CVRP 原生基线与 Fields2Cover

`research` / `full` 安装完整 `openjdk-17-jdk-headless` 和支持 C++20 的 `g++`，通过 `bash deploy/build-cohort.sh` 构建 `cohort_b/ails` 与 `cohort_b/sisr` 的适配源码。AILS 编译使用 `--release 17`；仅有 JRE 不够。构建脚本由源码安装和 Docker 共用，不运行正式实验，也不改写已有结果。

`full` 通过 `python3 deploy/fetch-fields2cover.py` 获取 `setup/platform-snapshot.json` 中固定 Release 的 `compiled-and-wheels.tar.zst`，先核对压缩包大小与 SHA256，再只取出 manifest 指定的 wheel 并复核其 SHA256。已存在的 wheel 也先校验；错误文件不会被覆盖。下载缓存保存在 `.local-state/deploy/downloads`。

Fields2Cover 2.1.0 使用 CPython 3.12 / Linux x86_64 wheel，运行库见 `deploy/fields2cover-packages.txt`：`libgdal30`、`libgeos-c1v5`、`libtinyxml2-9`、`libjpeg8`、`libtiff5`、`libpng16-16`。下载解包还需要 `zstd`。这些是 Ubuntu 22.04 的 ABI 依赖，不能用其他发行版的同名环境直接代替。

源码安装支持 `--fields2cover auto|required|skip`。`workbench` / `research` 默认 `auto`，仅安装本地已有且校验通过的轮子；`required` 会下载并安装；`skip` 跳过。`full` 固定为 `required`，缺少资源或校验失败时停止。

安装末尾执行 `deploy/check-runtime.py`，将版本、导入、原生绑定、合成基线与所选 ROS 命令的检查写入 `.local-state/deploy/runtime-*/runtime.json`。复核命令见 [安装验载](../DEPLOY.md#安装验载)；镜像内也提供该脚本，`urp-sim` 用 `--components full`，`urp-bench` 用 `--components research --fields2cover skip`。物理仿真与正式研究复现单独进行。

## 工作台虚拟环境（`src/lab/uv.lock`，56 个）

```
absl-py==2.5.1
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
certifi==2026.7.22
click==8.5.0
colorama==0.4.6
contourpy==1.4.0
cycler==0.12.1
defusedxml==0.7.1
fastapi==0.143.1
fonttools==4.67.0
h11==0.16.0
httpcore==1.0.9
httptools==0.9.0
httpx==0.28.1
idna==3.20
immutabledict==4.3.1
iniconfig==2.3.1
kiwisolver==1.5.1
matplotlib==3.11.2
numpy==2.5.4
ompl==2.0.1
opencv-python-headless==5.0.0.93
opentelemetry-api==1.45.1
ortools==9.15.6755
packaging==26.3
pandas==3.0.6
pillow==12.3.0
pluggy==1.6.0
protobuf==6.33.6
pydantic==2.14.1
pydantic-core==2.50.1
pygments==2.21.0
pyparsing==3.3.3
pyproj==3.8.0
pytest==9.1.1
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
python-multipart==0.0.32
pyvrp==0.14.0
pyyaml==6.0.3
scipy==1.18.1
shapely==2.2.0
six==1.17.0
starlette==1.8.0
tqdm==4.70.1
typing-extensions==4.16.0
typing-inspection==0.4.4
tzdata==2026.5
urp-lab==0.1.0
uvicorn==0.54.0
uvloop==0.23.0
vrplib==2.2.0
watchfiles==1.3.0
websockets==17.2
```

镜像和 WSL 安装使用 `--no-dev` 时不会装 `pytest` 及其只为测试服务的依赖；版本仍以锁文件为准。`uvloop` 的标记排除了 Windows。`ompl==2.0.1` 只有 Linux 与 macOS 轮子，没有 Windows 轮子，也没有 sdist。因此纯 Windows 不能 `uv sync --locked` 这套环境。

## 档案环境（根目录 `uv.lock`，28 个）

```
absl-py==2.5.1
attrs==26.1.0
colorama==0.4.6
contourpy==1.4.0
cycler==0.12.1
fonttools==4.67.0
immutabledict==4.3.1
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
kiwisolver==1.5.1
matplotlib==3.11.2
numpy==2.2.6
ortools==9.14.6206
packaging==26.3
pandas==3.0.6
pillow==13.0.0
protobuf==6.31.1
pyparsing==3.3.3
python-dateutil==2.9.0.post0
pyvrp==0.14.0
referencing==0.37.0
rpds-py==2026.9.1
six==1.17.0
tqdm==4.70.1
typing-extensions==4.16.0
tzdata==2026.5
urp-workspace==0.1.0
vrplib==2.2.0
```

注意根环境的 `numpy==2.2.6`、`ortools==9.14.6206`、`pillow==13.0.0`、`protobuf==6.31.1` 和工作台环境不同。不要合成一个环境。

`jsonschema` 由根 `pyproject.toml` 声明，档案 HTTP 服务本身不 import 它，工程检查会用到。锁文件安装它。

## 前端

直接依赖都写死在 `src/frontend/package.json`，传递依赖在 `src/frontend/package-lock.json`（284 个包记录，lockfileVersion 3）。安装只用 `npm ci`。

```
@codemirror/lang-json@6.0.2
@codemirror/lang-python@6.2.1
@fontsource/ibm-plex-mono@5.3.0
@fontsource/ibm-plex-sans@5.3.0
@foxglove/rosmsg@5.0.5
@foxglove/rosmsg2-serialization@3.1.2
@foxglove/ws-protocol@0.8.0
@noble/hashes@2.4.0
@observablehq/plot@0.6.17
@react-three/drei@10.7.9
@react-three/fiber@9.8.2
@uiw/react-codemirror@4.25.12
cmdk@1.1.1
d3-array@3.2.2
d3-scale@4.0.2
d3-shape@3.2.0
dockview-react@8.5.1
fflate@0.8.3
gltf-validator@2.0.0-dev.3.10
gzweb@3.0.2
katex@0.19.0
lucide-react@1.52.0
marked@18.1.1
react@19.3.0
react-dom@19.3.0
three@0.186.1
uplot@1.6.32
urdf-loader@0.13.1
zustand@5.0.15
```

开发依赖：`typescript@6.0.3`、`vite@8.3.3`、`vitest@5.0.3`、`@vitejs/plugin-react@6.1.2`，以及 `@types/node@24.19.1`、`@types/react@19.3.0`、`@types/react-dom@19.3.0`、`@types/three@0.186.0`、`@types/d3-array@3.2.2`、`@types/d3-scale@4.0.9`、`@types/d3-shape@3.2.0`。

`postinstall` 会跑 `node tools/patch-gzweb.mjs`，所以必须 `npm ci`，不能只拷贝 `node_modules`。运行中的 JS 基线是 `node` 执行 `src/frontend/core/routing/cvrp-solver.js`，镜像里要有 `node`，不需要再带一套 `node_modules`。

## 系统程序

工作台运行时：

| 程序 | 作用 |
|---|---|
| `bash` | `src/start.sh`、仿真启动命令 |
| CPython 3.12.13 | 网关与档案 |
| Node 24.22.0 | 构建前端；运行时执行 JS 基线 |
| 完整 JDK 17、支持 C++20 的 `g++` | `research` / `full` 构建与运行 AILS / SISR |
| `zstd` 与 `deploy/fields2cover-packages.txt` | `full` 获取 Fields2Cover wheel 并满足其系统 ABI |
| `curl` | `setup/run.sh lab-stop` |
| `google-chrome` / `chromium` / `xdg-open` | 仅 `serve --open`。容器与远程浏览器不需要 |

仿真额外需要（Ubuntu 22.04，ROS 2 Humble，Gazebo Fortress / Ignition Gazebo 6）：

| 程序或包 | 作用 |
|---|---|
| `/usr/bin/python3`（3.10） | `rclpy` 会话。不能换成 3.12 |
| `ros-humble-ros-base` | rclpy 与核心消息 |
| `ros-humble-rmw-cyclonedds-cpp` | `setup/profile.sh simulation` 默认 RMW |
| `ros-humble-turtlebot4-ignition-bringup` | TB4 + Ignition |
| `ros-humble-turtlebot4-navigation` | 官方地图与 Nav2 参数 |
| `ros-humble-nav2-bringup` | Nav2 |
| `ros-humble-ros-gz-bridge` | `parameter_bridge` |
| `ros-humble-rviz2`、`ros-humble-rviz-default-plugins`、`ros-humble-nav2-rviz-plugins`、`ros-humble-turtlebot4-viz` | 面板打开 RViz 时执行 `rviz2 -d src/lab/ros/workbench.rviz`（TB4）或会话写出的 `field.rviz`（田间）。这两份配置和官方 `turtlebot4_viz/rviz/robot.rviz` 都只用 `rviz_default_plugins` 与 `nav2_rviz_plugins`。默认启动不打开窗口。Gazebo 窗口用 `turtlebot4_ignition_gui_plugins` 与 `irobot_create_ignition_plugins`，随 `turtlebot4_ignition_bringup` 安装。 |
| `ros-humble-foxglove-bridge` | 浏览器 ROS 视图。笔记本上的路径是 `bash src/lab/ros/native/install-ros-extras.sh`，用 `apt download` 把 `ros-humble-foxglove-bridge` 和 `ros-humble-rosx-introspection` 解到仓库内，不改 `/opt/ros` |
| `ros-humble-xacro` | 车体 URDF |
| `ignition-fortress` | `ign gazebo` |
| `libignition-gazebo6-dev` | 力矩模式按需编译关节插件，`pkg-config --modversion ignition-gazebo6` |
| `cmake`、`ninja`、`g++`、`pkg-config` | 桥接、TF、GUI 覆盖和关节插件的按需编译 |
| `ip`（iproute2） | 镜像里保留，发现不再依赖组播 |

Python 轮子是 manylinux 包，数值库的 `.so` 在轮子里。仿真的共享库在 `/opt/ros/humble/lib` 和 Ignition 的系统库。GUI 覆盖还链接 `libdl`。

## DDS

`setup/profile.sh simulation` 设置 `ROS_LOCALHOST_ONLY=1` 和 `CYCLONEDDS_URI=file://setup/cyclonedds.xml`。`ROS_LOCALHOST_ONLY=1` 选择 `lo`，这条接口不能组播，发现因此是单播。XML 只把 `MaxAutoParticipantIndex` 设为 99。不要在 XML 里再写 `NetworkInterface` 或 `Peer`：再写一次 `lo` 会报 “the same interface may not be selected twice”，再写 `AllowMulticast` 加 `Peer` 会让同一台机器上的节点互相看不见。Docker Desktop 和 WSL2 都不依赖 `239.255.0.0/16`。

## 桥接与覆盖指纹

笔记本验收文件不改：

- `src/lab/ros/native/bridge/manifest.json`（可执行文件 `1a7fdb32269d384cd46eef7727e63d246256973fdaa2c664c89e400cf6a41ce0`）
- `setup/2026-10-15/deployment/tf2-overlay-manifest.json`
- `setup/2026-10-15/deployment/gui-overlay-manifest.json`

系统库与其中的哈希一致时，只接受这些验收二进制。不一致时，`python3 deploy/record-sim-profiles.py` 用同一份源码编译，并把本机哈希写到 `src/lab/runtime/native/bridge/profile.json` 和 `setup/2026-10-15/deployment/runtime/profiles/`。源码哈希变了会拒绝。本机 profile 不能替换笔记本上已经对上的验收二进制。

## GPU

工作台和仿真源码都没有 CUDA、cuDNN 或 PyTorch 设备选择。容器和 WSL 默认 `LIBGL_ALWAYS_SOFTWARE=1`。NVIDIA GPU：Windows 使用支持 CUDA on WSL 的驱动，Docker Desktop 用 WSL2 后端，再叠加 `deploy/docker-compose.gpu.yml`。原生 Ubuntu 22.04 不设置这个变量，已安装的驱动直接使用。没有 GPU 时不要加 GPU compose 文件。

## 数据与运行资源

| 路径 | 如何得到 |
|---|---|
| `data/backend/research.sqlite3` | **在 Git 里**。首次启动由 `tools/runtime_db.py` 复制到 `URP_API_DB`（默认 `.local-state/research.sqlite3`，容器里是卷 `/data/archive/research.sqlite3`）。不要覆盖这份冻结库。 |
| `data/packages/` | **在 Git 里**。档案的 `--import-root`。 |
| `data/research/` | **在 Git 里**。选择特征数据。 |
| `src/lab/runtime/` | 运行时生成：场景、`lab.sqlite3`、仿真日志。容器卷 `/data/lab`。 |
| `src/lab/vendor/wheels/fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl` | 不在 Git 里。`full` 从固定 Release 获取，SHA256 为 `0fe53e459024b282be455b2a4bdbca3d93efa029539ff87c88c1105361344c19`；路径与指纹由 `src/lab/vendor/fields2cover-manifest.json` 管理。 |
| `setup/2026-10-15/deployment/runtime/ros-extras` | 不在 Git 里。Foxglove 与 rosx 的 deb 解包。`bash src/lab/ros/native/install-ros-extras.sh` 可以用 apt 重新下载。 |
| `setup/2026-10-15/deployment/runtime/tf-repro/liburp_tf_buffer.so`、`runtime/liburp_gui_lifetime.so`、`src/lab/runtime/native/bridge/parameter_bridge` | 主人笔记本上的验收二进制。别的机器运行 `python3 deploy/record-sim-profiles.py` 生成自己的产物和 profile，不要复制这些文件来通过检查。 |
| Fuel 模型缓存 | 新镜像不包含。`maze` 无需下载；`warehouse` / `depot` 由使用者显式执行 `urp fuel`，从 `https://fuel.gazebosim.org` 下载到本机。旧快照另有 `fuel-cache` 组件，其模型许可和文件清单按原发布记录保留。 |
| CVRPLIB 实例正文 | 目录 JSON 在 `src/lab/urp_lab/library/data/cvrplib-catalog.json`。单题按需从 `https://galgos.inf.puc-rio.br/cvrplib` 下载到运行目录，无密钥。 |

不要把 `data/bpp`（约 206 MB）或 `data/cifar-training` 打进工作台镜像。历史 BPP 页在数据未恢复时显示无历史数据，这是现有行为。

## 环境变量

本机入口默认只监听 `127.0.0.1`，关闭接口只接受本机连接。

| 变量 | 默认 | 含义 |
|---|---|---|
| `URP_REF` | `deploy` | 安装来源；可用发布标签、完整提交号或源码分支，后续命令沿用保存的选择 |
| `URP_IMAGE_TAG` | 所选已发布标签 | Docker 镜像标签；完整 SHA 与 OCI revision 核对 |
| `URP_COMPONENTS` | `full` | 源码安装组件：`workbench` / `research` / `full` |
| `URP_BIND_HOST` | `127.0.0.1` | 网关监听地址。WSL 与容器设为 `0.0.0.0` |
| `URP_PORT` | `4100` | 源码网关端口；Compose 仅改宿主机发布端口，容器内固定 4100 |
| `URP_PUBLISH_HOST` | `127.0.0.1` | 仅 Compose：宿主机发布地址 |
| `URP_EXTRA_HOSTS` | 空 | 额外允许的 Host，逗号分隔 |
| `URP_ADMIN_TOKEN` | 空 | 一旦设置，关闭接口必须带 `X-URP-Admin-Token` 或 `Authorization: Bearer` |
| `URP_ARCHIVE_URL` | `http://127.0.0.1:4180` | 档案后端 |
| `URP_ARCHIVE_AUTOSTART` | `1` | `0` 时网关不拉起档案进程 |
| `URP_API_DB` | `.local-state/research.sqlite3` | 档案库 |
| `URP_API_PORT` | `4180` | 档案端口 |
| `URP_LAB_RUNTIME` | `src/lab/runtime` | 网关运行目录 |
| `URP_FOXGLOVE_BIND` | `127.0.0.1` | Foxglove 监听地址 |
| `URP_BROWSER_HOST` | `127.0.0.1` | 页面里使用的 WebSocket 主机名 |
| `URP_BRIDGE_FIX` / `URP_TF_FIX` / `URP_GUI_FIX` | `1` | `0` 表示用未打补丁的系统实现，只作对照 |
| `URP_RMW` | `rmw_cyclonedds_cpp` | 仿真 RMW |
| `URP_ROS_DOMAIN_ID` | `139`（profile） | 验收脚本会改成隔离域 |
| `LIBGL_ALWAYS_SOFTWARE` | 容器默认 `1` | 软件 OpenGL |
| `DISPLAY` / `XAUTHORITY` | 空 | 仅本机打开 Gazebo/RViz 窗口 |

## 路径、端口、外部服务

代码里的绝对路径：

- `/opt/ros/humble`：仿真与地图。不是笔记本家目录。
- `/home/peakpeng/Desktop/URP` 和 `/home/peakpeng/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu`：只出现在已发布的平台镜像入口（`setup/platform/Dockerfile`、`tools/platform_deploy.py`）。新的 Compose 镜像用 `/opt/urp`，不再依赖这个家目录。
- `~/.cache/urp/physical-session.lock`：仿真互斥锁。
- `~/.nvm`：`setup/run.sh` 在 PATH 里没有 `node` 时才查找。

端口：`4100` 网关，`4180` 档案（只在本机，不发布），`4191` TB4 控制，`4192` 田间仿真控制，`8765` Foxglove，`9002` Gazebo WebSocket。后四个由网关在同一台机器上拉起，不是独立的 Compose 服务。

外部地址：PyPI、npm、`https://astral.sh/uv/0.12.23/install.sh`、`https://nodejs.org/dist/v24.22.0/`、`http://packages.ros.org/ros2/ubuntu`、`https://galgos.inf.puc-rio.br/cvrplib`、GitHub Release 上的平台快照。工作台和仿真源码里没有 API 密钥或令牌名。

## 第三方再分发

2026-10-19 对照上游页面和 Fuel API 的结论写在 `deploy/THIRD_PARTY.md`。公开镜像可以包含 apt 安装的 ROS 2 Humble、Gazebo Fortress（Apache-2.0）以及 Foxglove bridge（MIT，https://github.com/foxglove/ros-foxglove-bridge）和 rosx_introspection（MPL-2.0，https://github.com/facontidavide/rosx_introspection）。镜像不剥离这些包里的许可文件。

本部署不打包的 Fuel 模型：OpenRobotics Warehouse 5（CC BY-NC-ND 4.0）、MovAi shelf_big 2（CC BY-NC-ND 4.0）、MovAi shelf 1（CC BY-NC 4.0）。Depot、Chair、foldable_chair、MaleVisitorOnPhone 是 CC BY 4.0，Table、Jersey Barrier、Casual female 是 CC0。只发布后一组并不能让 warehouse 或 depot 完整，所以这些模型都不烘焙。Fields2Cover 是 BSD-3-Clause；轮子不在仓库里，安装器从仓库清单指定的 Release 获取；单独更新资源可用 `deploy/upload-assets.sh` 连同 BSD 声明打包上传。

## 版本记录

Python、Node、前端与求解库版本按上述锁文件和安装脚本固定；Ubuntu / ROS 的 apt 补丁版本以安装时仓库为准。历史平台的精确包版本、运行结果及 Fields2Cover 构建来源另保存在快照与验收记录中。新部署不复制原机器的虚拟环境或验收二进制。
