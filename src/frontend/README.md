# URP 科研工作台

React/Vite 工作台提供农业任务编辑、Agent 面板、算法实验、独立验算、仿真视图与结果回放。首页为 `#/`；`#/jobs` 提供 CVRP 数值任务，`#/jobs?tab=archives` 查看实验档案，`#/bpp` 保留装箱结果与逐步回放。

完整启动、停止和恢复统一按 [本机使用说明](../../setup/README.md)。下列命令仅用于独立前端开发。

从本目录运行：

```sh
npm run dev          # 127.0.0.1:5173
npm run typecheck
npm run test:ui
npm run build
npm run preview      # 127.0.0.1:4173
```

| 内容 | 位置 |
|---|---|
| 页面与交互 | `src/` |
| 装箱回放与数据读取 | `core/` |
| 路线检查与文件适配 | [core/routing](core/routing/README.md) |
| 原始输入 | `inputs/` → `../../data/bpp/inputs` |
| 实验记录 | `research/` → `../../data/bpp/records` |
| 展示数据 | `public/data/` → `../../../data/bpp/exports` |

路径任务调用[Jobs API](../backend/docs/JOBS_API.md)，每次读取可变任务状态都绕过缓存。统一入口 4100 由网关代理；独立生产预览和开发服务通过同源 `/api/` 连接 `127.0.0.1:4180`，通过 `/lab/api/` 连接田间网关 4100（可设 `URP_LAB_PORT`）；更换后端端口时给两边设置相同`URP_API_PORT`。代理校验真实服务端口、loopback Host和相同Origin，后端保留自己的边界校验。

选择任务、实验或 run 后核对响应身份，归档查询固定 revision，失败和中断保留原状态。`#/jobs` 绘制 XY 访问顺序，EUC_2D 成本由后端 checker 计算；任务规划页按场景 frame 绘制几何路径。Agent 的接口与脚本模型回归见 [CONTRACT §9](../lab/CONTRACT.md) 和 [本轮工程记录](../../setup/2026-10-30/engineering/REPORT.md)。

## 验收

当前工程回归从仓库根执行 `uv run --no-sync python tools/check_project.py`（`bash src/start.sh check` 调用同一入口），使用根 `.venv`，日志写入新的 `.local-state/checks/<时间>/`。当前 Lab 与双服务 HTTP 检查见 [开发与测试](../../setup/README.md#开发与测试)。

`scripts/console.sh setup/test/validate` 属于历史 BPP 独立交付流程：它使用前端目录自己的 Python 环境，包含科学数据核验与重建；日常工程回归不要求重建这套环境或重新转换历史数据。

### 历史浏览器验收入口（2026-10-17）

`tools/browser/workspace-acceptance.mjs` 是当时的浏览器验收脚本，使用该目录锁定的 Playwright。重跑时要求已运行的**临时测试后端数据库**与生产预览，真实提交两个六节点任务，检查 API、导出、刷新、无障碍控件和 390px 布局；最后一项分页/终态检查使用明确的拦截响应，不向后端写入伪造记录。其历史通过记录不替代当前提交验收。

```sh
npm ci --prefix tools/browser
node tools/browser/node_modules/playwright/cli.js install chromium
URP_BROWSER_OUTPUT=/absolute/path/to/new-report-dir node tools/browser/workspace-acceptance.mjs
```

可用`URP_BASE_URL`指定本机预览地址，`URP_BROWSER_EXECUTABLE`指定实际安装的兼容Chrome二进制。报告目录的父目录须已存在，输出目录必须为新目录。设置`URP_ROUTE_FIXTURE_ID`可追加已导入`backend/tests/route_fixture.py`合成归档的四类路线检查；未设置时报告会明确列出遗漏项。2026-10-17 的浏览器记录及复验步骤见[系统审查](../../docs/20261017-system-review.md)；后续各轮实际执行范围见[工程记录](../../setup/2026-10-30/engineering/REPORT.md)。

[设计与交互](docs/FRONTEND.md) · [静态数据接口](docs/DATA_API.md) · [安装与重建](docs/MIGRATION.md) · [验收](docs/ACCEPTANCE.md)。
