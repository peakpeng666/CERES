# URP 使用与开发

工作台统一监听 `http://127.0.0.1:4100/`。任务规划、Agent、算法实验、仿真和回放在同一页面；`#/jobs` 提供 CVRP 数值任务，`#/bpp` 提供历史装箱结果。

## 获取工程

完整获取可直接克隆仓库。只开发系统时，使用源码检出清单，按需保留大资料：

```sh
git clone --filter=blob:none --no-checkout --branch main https://github.com/peakpeng666/URP.git
cd URP
git show HEAD:setup/source-checkout.txt | git sparse-checkout set --no-cone --stdin
git checkout
```

[源码检出清单](source-checkout.txt) 省去论文、教材、第三方源码、压缩包与恢复数据，保留 BPP 只读回归所需的完整记录；[资产索引](../docs/assets/README.md) 记录原提交中的资料路径。

## 安装依赖

源码开发使用 Python 3.12、Node 24、npm 和 uv。在仓库根执行：

```sh
uv sync --locked --extra solvers
(cd src/lab && uv sync --locked --inexact)
npm ci --prefix src/frontend --engine-strict --no-audit --no-fund
```

| 环境 | 权威锁文件 | 用途 |
|---|---|---|
| 根 `.venv`，Python 3.12 | `uv.lock` | 档案、CVRP 数值任务和工程检查 |
| `src/lab/.venv`，Python 3.12 | `src/lab/uv.lock` | 网关、Agent、规划和离线 evolve |
| 前端，Node 24 | `src/frontend/package-lock.json` | 页面、类型检查和界面测试 |
| 浏览器验收工具 | `src/frontend/tools/browser/package-lock.json` | Playwright 验收 |
| ROS 2 Humble，系统 Python 3.10 | [部署依赖](../deploy/DEPENDENCIES.md) | Gazebo、ROS 节点与原生组件 |

根 `requirements.lock` 是 `uv.lock` 的 pip 导出，由 CI 检查一致性。更新后用以下命令重新生成：

```sh
uv export --locked --extra solvers --no-dev --no-hashes --no-annotate --output-file requirements.lock
```

`src/frontend/requirements.lock` 属于独立的历史 BPP 数据转换环境；`runtime/cvrplib/requirements.lock` 和实验快照中的锁文件对应原实验。它们不参与上述两套 Python 环境安装。

## 启动与停止

```sh
bash setup/run.sh lab-code --open
curl -fsS http://127.0.0.1:4100/lab/api/health
bash setup/run.sh lab-stop
```

`lab-code` 启动规划、Agent、验算和记录服务。`lab` / `lab-code` 每次先构建前端，包含锁文件、配置、工具与公共资产的变化；构建失败则停止启动。启动会增加一次构建耗时。在启动终端按 Ctrl+C 也会正常关闭。

完整 ROS 2 / Gazebo 安装使用 [DEPLOY.md](../DEPLOY.md)。已配置的机器运行 `bash setup/run.sh lab --open`，该命令还会准备仿真修补库。安装器部署后的命令为 `urp up`、`urp status`、`urp down`。

## Agent

在启动终端配置 OpenAI 兼容接口：

```sh
export URP_LLM_API_KEY='<接口密钥>'
export URP_LLM_MODEL='glm-5.3'
# URP_LLM_BASE_URL 可指定兼容接口地址
bash setup/run.sh lab-code --open
```

默认接口为智谱，密钥也可用 `ZHIPU_API_KEY`。在工具栏「智能体」输入任务，例如“生成果园地图，比较 PyVRP 和 OR-Tools，各给 3 秒”。时间线可展开工具参数、求解和验算结果；运行编号链接到场景与方案对比。每次启动仿真需在面板批准。会话和用量随运行记录保存，密钥只从环境读取。

配置密钥、启动网关和打开面板只读取状态；点击「发送」或「交给智能体」后才调用模型。停止在当前调用返回或超时后生效，迟到答复不再作为完成结果；`URP_LLM_TIMEOUT_S` 默认 120 秒。网关关闭会结束运行中和等待批准的轮次，重启后需重新提问。

当前 Agent 回归使用脚本模型和回环替身；真实模型效果待接入后评估。工具、调用预算和会话恢复规则见 [接口契约 §9](../src/lab/CONTRACT.md)。

## 工作流程

1. 选择农业模板、导入地图，或输入任务描述；在编辑器检查边界、障碍、任务、车辆和单位。模板参数会保存为草稿，生成后的手工修改可追踪。
2. 查看推荐，选择算法、时间预算和种子，提交求解。方案对比同时展示进程状态、独立验算、地图净空和输入版本。
3. 在算法实验中批量运行与比较；查看性能剖面、ECDF、收敛、显著性和历史记录。相同 ID 的相同导入重试返回原场景，内容变更返回冲突。
4. 已适配的米制几何路径可送入仿真，结束后导出完整记录并回放。当前执行车辆为同构差速车，多车使用串行预约；Dubins / Reeds–Shepp 用于规划比较。

ROS 视图使用 Foxglove Bridge，Gazebo 场景使用 gzweb。执行时钟、覆盖验收、非理想地面和原生组件配置见 [CONTRACT](../src/lab/CONTRACT.md) 与 [历次仿真验收](2026-10-15/integration/REPORT.md)。

## 开发与测试

```sh
uv run --no-sync python tools/check_project.py
python3 tools/fetch_ros_maps.py
(cd src/lab && URP_ROS_MAPS="$PWD/../../.local-state/ros-maps" \
  .venv/bin/python -m pytest -q --require-maps)
```

取图命令按 [ros-maps.json](ros-maps.json) 下载固定上游提交的六个 YAML/PGM 文件，并检查大小与 SHA256；已有文件先校验。默认写 `.local-state/ros-maps/`，可用 `--destination` 指定目录。

不安装地图时，直接在 `src/lab` 运行 `.venv/bin/python -m pytest -q`；仅依赖官方地图的用例跳过。指定 `URP_ROS_MAPS` 或 `--require-maps` 后，缺少必需地图会失败；损坏的已安装地图始终按错误处理。

启动工作台后，运行完整 HTTP 小任务：

```sh
python3 tools/smoke_workbench.py
```

它会实际生成任务，运行 PyVRP 与 savings，等待独立验算，核对 JSON 导出并读取比较结果。输出保存在新的 `.local-state/smoke/<时间>/` 目录。[本轮报告](2026-10-30/engineering/REPORT.md) 记录当前检查；CI 将全量 Lab、农业构建器和工程检查作为回归入口。

从仓库根执行已有的双服务验收入口，可自行启动并关闭独立网关与档案后端：

```sh
python3 tools/smoke_dual_service.py \
  --output "$PWD/.local-state/dual-service-$(date -u +%Y%m%dT%H%M%SZ)-$$" \
  --port 4186 --archive-port 4187
```

先按上文准备根、Lab 两套 Python 环境与前端依赖，确保指定端口空闲，输出目录必须是新目录。脚本为失败注入与健康流程分别设置绝对 `URP_API_DB` 和 `URP_LAB_RUNTIME`，在输出目录内新建测试库；不要改为历史库或个人运行库。它检查坏解释器的明确失败、真实 Jobs、工作台求解/验算/导出、关闭与端口释放，并比较检出的冻结数据库前后字节。未检出的原始库记为 `missing`，不当作已验证；这是 HTTP 与生命周期验收，浏览器、容器及 ROS 结果单独记录。

## 数据与恢复

| 内容 | 位置 |
|---|---|
| Lab 运行与 Agent 会话 | `src/lab/runtime/lab.sqlite3` |
| 用户场景 | `src/lab/runtime/scenarios/` |
| 田间仿真与日志 | `src/lab/runtime/fieldsim/<会话>/` |
| TurtleBot4 会话 | `src/lab/runtime/sim/<会话>/` |
| 档案 / Jobs 可写库 | `.local-state/research.sqlite3` |
| 新研究 CLI 记录 | `experiments/runs/replay/`、`experiments/runs/cvrplib-run-registry/` |
| 冻结协议、结果与失败 | `protocol/`、`experiments/`，按原版本保留 |

`URP_LAB_RUNTIME` 可指定独立运行目录。`GET /lab/api/runs/<run_id>/record` 下载输入、版本、原始结果、仿真及完整性字段。新运行目录已定点忽略；冻结实验目录继续受 Git 跟踪。

旧平台使用 [2026-10-18 Release](https://github.com/peakpeng666/URP/releases/tag/platform-snapshot-20261018-ddb5113e)，清单见 [platform-snapshot.json](platform-snapshot.json)。Linux x86_64、本地 Docker 和 Python 3 环境可运行：

```sh
bash setup/run.sh reproduce --acceptance
bash setup/run.sh reproduce status
bash setup/run.sh reproduce stop
```

它下载并校验约 7.2 GB 的固定镜像与补充包。单独恢复资料时，先下载、验证，再解包到新目录：

```sh
bash setup/run.sh reproduce download --catalog setup/platform-snapshot.json --assets-dir ../urp-snapshot-assets
python3 tools/platform_snapshot.py verify --catalog setup/platform-snapshot.json --assets-dir ../urp-snapshot-assets
python3 tools/platform_snapshot.py restore --catalog setup/platform-snapshot.json --assets-dir ../urp-snapshot-assets --destination ../urp-snapshot-restored
```

旧镜像基于 `ddb5113e`；使用命名卷保存数据，停止不会删除。原生、容器及 Windows/WSL 的执行结果与已知故障见 [历史验收记录](2026-10-15/integration/REPORT.md)。

### 历史完整 ZIP 资料入口

`tools/restore_materials.py` 仅用于旧的完整 `URP.zip`。其 [独立清单](materials-archive.json) 保留 `66b2b8c742598378898d02643d4ddc2f58f1a686:storage.json#/archive` 中的大小 **10,450,371,700 字节**和 SHA256，也保留原记录“Windows 报告已验证、当次任务不可读”的状态；本轮没有取得或重验这份 ZIP。现行 `storage.json` 登记位置与副本，不含旧 `archive` 键。

持有对应原件时先检查，确认后再去掉 `--list` 恢复所选目录：

```sh
python3 tools/restore_materials.py /absolute/path/to/URP.zip --list --include papers
```

列出与恢复均先严格核对大小和 SHA256；仅恢复允许目录内未受 Git 跟踪的文件，遇到内容不同的本地文件即失败。`--manifest` 可显式指定同 schema 的历史指纹清单；缺失或不合法时明确报错。此入口不接受平台补充包，也不下载 Drive 文件或登记迁移；平台 Release 使用上面的 `platform_snapshot.py` 流程。

写入前完整预检所选材料：归档须有 `URP/` 根目录及匹配文件；重复规范化目标、文件/子路径冲突、归档符号链接和本地目标任一符号链接组件均拒绝。已有材料逐字节比对；只包含已存在材料时状态为 `already-present`，只包含 Git 管理文件时为 `tracked-only`，均不报告“已恢复”。`--list` 的非空计划为 `planned`；只有实际完成全部计划写入才报告 `restored`。

恢复使用同一个已校验的打开文件读取 ZIP，新目标以排他方式创建，预检后出现的文件也不会被覆盖。请串行使用，不要同时修改 ZIP、恢复目录或其链接。解压、权限或存储错误会非零退出，并给出已完成文件数及可能残留的新文件；它不回滚此前完成的新材料，也不自动删除失败残留。检查后再处理残留，不能把部分写入当作整批成功。`--list` 不读取尚未恢复成员的全部解压内容，CRC/解压错误仍可能在实际恢复时发现。
