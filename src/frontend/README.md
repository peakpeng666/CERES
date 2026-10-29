# URP 科研工作台

React/Vite工作台连接本地后端，提供真实CVRP任务提交、排队、独立验算、路线查看、记录导出和完整状态分页。`#/jobs`是路径任务入口，`#/jobs?tab=archives`查看导入的实验及版本化路线。科研工作台首页为 `#/`（智能体工作区，`#/agent/<会话>` 可直接恢复会话）；原有`#/bpp` BPP结果、逐步装箱回放、选择解释、同前缀风险和原始证据保持可用。

完整启动、停止和恢复统一按 [本机使用说明](../../setup/README.md)。下列命令仅用于独立前端开发。

从本目录运行：

```sh
bash scripts/console.sh build
bash scripts/console.sh serve      # 127.0.0.1:4173
bash scripts/console.sh dev
bash scripts/console.sh test
```

| 内容 | 位置 |
|---|---|
| 页面与交互 | `src/` |
| 智能体工作区（对话、任务合同、执行计划、调用记录） | `src/agent/`，接口见 [agent/README](../lab/urp_lab/agent/README.md) |
| 装箱回放与数据读取 | `core/` |
| 路线检查与文件适配 | [core/routing](core/routing/README.md) |
| 原始输入 | `inputs/` → `../../data/bpp/inputs` |
| 实验记录 | `research/` → `../../data/bpp/records` |
| 展示数据 | `public/data/` → `../../../data/bpp/exports` |

路径任务调用[Jobs API](../backend/docs/JOBS_API.md)，每次读取可变任务状态都绕过缓存。统一入口 4100 由网关代理；独立生产预览和开发服务通过同源 `/api/` 连接 `127.0.0.1:4180`，通过 `/lab/api/` 连接田间网关 4100（可设 `URP_LAB_PORT`）；更换后端端口时给两边设置相同`URP_API_PORT`。代理校验真实服务端口、loopback Host和相同Origin，后端保留自己的边界校验。

选择任务、实验或run后会核对响应身份，归档查询固定revision。失败和中断保留原状态；没有可行解不显示虚构的零成本或路线。图形只表示XY访问顺序，EUC_2D整数成本来自后端checker，不能用画面长度替代。

## 验收

统一工程回归从仓库根执行`bash src/start.sh check`。它使用根`.venv`，保留每次新的日志和输出。`scripts/console.sh setup/test/validate`仍是原BPP独立交付的环境与重建入口，不必为了启动URP任务而重新转换历史数据。

新增浏览器验收脚本为`tools/browser/workspace-acceptance.mjs`，使用该目录原有锁定的Playwright。它要求已运行的**临时测试后端数据库**与生产预览，真实提交两个六节点任务，检查API、导出、刷新、无障碍控件和390px布局；最后一项分页/终态检查使用明确的拦截响应，不向后端写入伪造记录。

```sh
npm ci --prefix tools/browser
node tools/browser/node_modules/playwright/cli.js install chromium
URP_BROWSER_OUTPUT=/absolute/path/to/new-report-dir node tools/browser/workspace-acceptance.mjs
```

可用`URP_BASE_URL`指定本机预览地址，`URP_BROWSER_EXECUTABLE`指定实际安装的兼容Chrome二进制。报告目录的父目录须已存在，输出目录必须为新目录。设置`URP_ROUTE_FIXTURE_ID`可追加已导入`backend/tests/route_fixture.py`合成归档的四类路线检查；未设置时报告会明确列出遗漏项。本轮真实浏览器记录及复验步骤见[系统审查](../../docs/20261017-system-review.md)。

[设计与交互](docs/FRONTEND.md) · [静态数据接口](docs/DATA_API.md) · [安装与重建](docs/MIGRATION.md) · [验收](docs/ACCEPTANCE.md)。
