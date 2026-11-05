# 科研记录与任务后端

Python标准库与SQLite实现本地实验包导入、结果查询、附件下载、同口径比较和路线读取，并通过受控任务队列调用研究组件中的真实CVRP runner。当前记录包为selector、budget、fleet、turns、bpp-history，以及 2026-10-16 的 `cvrplib-53-e2e-5s-20261016` 与 `cvrplib-16-searchloop-1s-20261016`。后两包的比较键不同，5 秒端到端和 1 秒搜索循环不会被比较接口合成。新提交的工程验证任务独立记账。

从仓库根目录启动独立档案服务，依赖安装与统一工作台入口见[使用说明](../../setup/README.md)：

```sh
bash src/start.sh backend
```

`backend` 会先把 `data/packages` 导入运行库再提供服务。未改动的包沿用原版本。`bash src/start.sh bootstrap` 仍可单独导入。冻结的 `data/backend/research.sqlite3` 不被这次导入改写。

默认运行库为 `.local-state/research.sqlite3`。首次启动将存在的历史库只读备份到运行库；已有运行库不会被替换。直接使用组件 CLI 时也应指定独立运行库，例如：

```sh
cd src/backend
../../.venv/bin/python -m backend --db ../../.local-state/research.sqlite3 list
```

`packages`连接到`../../data/packages`。新数据库使用`bootstrap --package-root packages`导入；相同内容重复导入保持幂等。`--db`须放在子命令前，可指定独立数据库。

[API与数据口径](api.md) · [CVRP任务契约](docs/JOBS_API.md) · [路线格式](docs/) · [源码来源](config/sources.json)。数值执行与独立验算由`src/research`承担。

`serve` 在 Linux 启动串行 CVRP 队列：支持 2–300 个节点（含仓库）、1 或 5 秒预算，以及 savings、T、TRS、TSR；安装 PyVRP 0.14.0 时启用 pyvrp。`GET /api/algorithms` 返回当前可用项。农业规划与 Agent 使用 [Lab 网关](../lab/CONTRACT.md)。

任务接收后先写入SQLite，再进入队列。输入、场景版本、执行源码和依赖版本身份随每次尝试保存；失败、无方案和重启中断均保留。已有实验记录与不可变版本不因新任务而改写。

只读 [Route-scene v1 契约](docs/ROUTE_SCENE_API.md) 为二维展示导出原始访问顺序、单位与来源。

```sh
../../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
RESEARCH_CONSOLE_PACKAGE_ROOT=packages ../../.venv/bin/python -m unittest discover -s tests -p integration_backend.py
```

历史记录中的派生数据库位于URP根目录相对路径 `data/backend/research.sqlite3`，来源与原验证记录为同目录 `SOURCE.json`，原声明为9个实验、672条路线。本轮没有将该历史数据库作为可写验收库。2026-10-17 的包集成验证使用独立临时库，覆盖5个通用实验包、690项指标和28个附件，核对导入前后的源文件哈希；记录见[本轮工程验收](../../docs/verification/20261017/engineering/summary.json)。
