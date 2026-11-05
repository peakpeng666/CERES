# 原生仿真验收

在GitHub标准Ubuntu 22.04 runner上安装项目环境，运行无界面Gazebo与Nav2，并检查真实轨迹、任务完成和进程清理。先运行maze-inspection；通过后，在另一台全新runner上回放库房历史五任务路线。两阶段均使用ground_truth定位和follow_path执行。

## 版本与环境

- 执行源码固定为3acf840d1a0c16ab1cfcca15744e4ea6609ddb35。
- 验收工具使用另一固定提交；工作流分别检出两个完整SHA。
- 安装调用项目既有workbench流程，并跳过可选Fields2Cover组件。
- GitHub临时主机通过显式参数启用APT冲突处理：先模拟精确包替换并检查移除清单，通过后执行，最后重试原安装器一次。本地默认不启用。
- HOME、Python环境、运行库和实验输出位于本次runner临时目录；使用Xvfb软件渲染。
- 新建检出目录先还原Git属性声明的换行转换，再逐字节校验原始Git blob；安装前后均验证源码内容。

## 通过条件

完整执行流程成功，真实轨迹有运动，全部任务点到达，规划与实际路径均通过地图检查，源码与原生运行库身份一致，会话及网关正常退出，最终没有自有残留进程。取消、超时或强制清理均按失败记录。

明确选择simulation执行时钟，仅follow_path，不按观测耗时或RTF改写预算。maze保留规划器约103秒的选定时钟预算，wall watchdog为900秒，full_loop外层1800秒、runner模拟阶段3600秒、job125分钟。库房保留649.6秒的选定时钟预算，watchdog5400秒，lifecycle外层7200秒、runner模拟阶段9000秒、job220分钟。两个阶段安装上限均3600秒；预载、就绪和独立有界清理不跳过。不同作用域分别公开，7200秒不是整个job预算。

原始请求、queued及terminal的冻结执行策略必须一致，且无budget覆盖；身份、路线和全部物理验收条件不变。公开摘要将规划预算与观测耗时分开，明确不测量实时可行性。接驳仍按项目契约交由Nav2规划，但与所有路线段共享执行时钟。该协议要求已审查的execution_budget_v2应用源码，源pin已统一更新为已审查提交；本地测试不等同原生验收。

## 文件与接口

- runner/run.py：安装、Xvfb、验收与清理。
- acceptance.py：调用现有full_loop.py，汇总真实执行证据。
- public_summary.py：生成字段受限的公开摘要。
- runner/verify_worktree.py：校验源码。
- runner/verify_registry.py：核对镜像包完整性及官方tarball路径；临时安装改走官方npm源，锁文件不变。

```sh
python acceptance.py --source SOURCE --private-dir NEW_DIRECTORY --summary SUMMARY --harness-sha COMMIT --stage maze
python public_summary.py --input SUMMARY --runner-status RUNNER_STATUS
python -m unittest -v test_acceptance runner.test_runner runner.test_worktree runner.test_materialize runner.test_apt_diagnostic runner.test_apt_projection runner.test_registry runner.test_host_apt_repair test_integration
```

公开摘要包含固定状态、数值、布尔值和摘要哈希；缺测值标null。安装失败时列出经软件包缓存核对的依赖诊断，并标注核验方式。完整日志与运行数据留在临时目录，不上传artifact或缓存。工作流不发布镜像，不修改部署分支，不使用付费runner。

安装诊断另行标注supervisor的终止原因；超时、取消不再由历史APT日志猜测。主机修复前的依赖证据保留于私有日志，若公开投影则明确标为before_repair，不作为重试失败的现时原因。APT元数据异常仅追加固定API操作枚举，不公开异常文本、路径或来源地址。3600秒安装上限、原安装器、物理验收和清理条件均不变。

库房回放工具位于warehouse目录。它保留历史五任务几何和起点，重新进行路线、地图、45项资源及当前执行身份校验，并通过原有网关执行；不重复调用求解器。maze失败或清理不合格时，库房job跳过。

库房资源单独标为historical_release_exact_bytes_v1：从固定发布归档核验后，仅还原61个逐文件固定哈希的数据文件到不存在的新缓存；已有缓存（包括空目录）不覆盖。执行前后均核对61项精确文件集、必需目录集、来源与字节；额外文件或目录、链接及特殊文件一律拒绝。原45项资源比较不变，仅对四个已核对两侧字节的Jersey路径作exact_jersey_alias_bytes_v1映射。公开摘要区分raw路径清单2c3acde82c25c0372c8793225abcbc94d4e74be4b441ad130de7c3a475790d66与canonical历史清单64f4c3b93af78800aefc036c58035a8556401c82627f55409a16483d12dc7055，不把路径不同说成原始清单完全相等。该来源证明独立于npm校验，也不代表当前Fuel版本；缺少来源或物理证据均不能通过。归档、缓存及原始报告不发布。

新的 simulation_execution_v2 cohort 保持路线和选定时钟预算不变，仅将执行新鲜度协议明确为 3 sim 秒 source freshness、30 wall 秒 receipt/source-progress guards，并保留独立 wall watchdog。只有 ground_truth 允许零 stamp fallback，测量年龄仍未知。原子 trigger 相对年龄及最多 8 条固定 schema 观测可公开；旧 v1 cohort 仅保留历史标签，不视为新版策略验收通过。
