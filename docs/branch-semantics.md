# 保留分支的语义取舍

> 以下是 2026-11-01 的历史语义核对，不是当前分支存续要求或当前 main 的文件计数。2026-11-06 历史核对基线为 `028bae0e`，目标改为 main 单分支；第一阶段 `3481c414` 已通过工程与镜像发布验收，本次切换安装默认值，仍未删除引用。当前来源、更新后的文件计数和恢复限制见 [迁移证据](main-migration-20261106.json)及[当前索引](current-index.md)。不改写历史证据，也不把旧实现覆盖新实现。

核对基线：已合并 main `9324ba1a71eb4b85a60fddbf4635900471b0e3f8`。本表针对十个“没有 main 缺失路径、但不是 main 祖先”的分支；**全部继续保留**。七条已授权引用的实际删除另见 [退役回执](branch-retirement-20261101.json)，不由本表扩大删除范围。

逐支先取其与基线的 merge base，再比较该分支实际引入的文件；“完全匹配”要求文件模式和 Git blob 同时相同，不是仅检查路径存在。Python 另比较去掉位置和 docstring 的 AST 符号，并人工核对关键行为差异。没有执行历史分支代码、跑研究实验或启用新功能。逐文件身份及符号结果见 [机器可读证据](branch-semantics.json)。数量对应核对基线，不能当作任意未来主线的结论。

| 保留分支 | 该分支引入文件 / 与基线完全匹配 | 语义结论及具体取舍 |
|---|---:|---|
| `codex/preserve-20261026-happy-editor` | 81 / 56 | 编辑、选择、草稿与预览的多数交付已同 blob。主线额外保留生成参数聚焦、后端默认值规范化、请求身份和分析合同；不整支覆盖。 |
| `codex/preserve-20261026-labpatch` | 5 / 0 | 文件都继续演进；导入来源的十个原测试函数 AST 相同。主线使用完整 `prepare_import` 比较、保留官方地图 ID、按真实包加载 validator；旧字段子集比较和单文件加载方式不取回。 |
| `codex/preserve-20261026-luna-agri` | 82 / 62 | `drafts.ts`、`manualEdits.ts`、`GeneratedFrom.tsx` 在该交付中已与基线同 blob。保留当前 Agent、错误状态、取消/迟到响应和统计分母的后续修整。 |
| `codex/preserve-20261026-mamba-results` | 2 / 2 | 独占提交只改两个根目录 BAT，均与主线同模式同 blob；没有新增研究结果需要提取。可列下一轮引用归档候选，本轮保留。 |
| `codex/preserve-20261026-mamba-studio` | 84 / 65 | 是前端 `7fa18cbf` 头的祖先。保留主线在规划页打开农业目录的修正和默认值规范化；后续代码来源可集中到较新的 workbench 头，无须重复合并。 |
| `codex/preserve-20261026-verify` | 127 / 109 | 编辑器、合成农业夹具与多数交付已完全匹配；其余关键差异对应主线更完整的导入校验、状态生命周期及统计合同。保留这些主线行为，不恢复旧验证入口。 |
| `frontend/grokbot-20261022` | 84 / 68 | 与下一行完全同 SHA `7fa18cbf72317b9572c3da6c0bcd41d59a3550e0`，是同一来源的两个命名引用。保留主线新增的 Agent、请求代次、错误呈现、分析分母与任务 CSV；本轮不删任一引用。 |
| `frontend/workbench-20261022` | 84 / 68 | 同上。未来需要收敛命名时，可选它作为规范来源名；须另行决定引用归档，不把同 SHA 误报成两份独立功能。 |
| `integrate/evolve-contracts-20261024` | 50 / 46 | 旧 `serve.py` 的自声明身份及模型评分入口已被更严格的离线 guard 取代；保留主线 `ModelApproval`、robot width 和学习 serving 关闭门禁。旧专用 CI 和 conftest 不覆盖现行全量 Lab/必需地图配置。 |
| `integrate/evolve-serving-guards-20261024` | 51 / 49 | 除共享 CI 与 conftest 外的交付已与基线完全同模式同 blob，含离线 guard 源码及测试。两处配置差异是主线的全量 Lab 与必需地图整合，不是缺失的 serving 功能；八项在线整合测试仍明确 deferred。 |

## 人工核对的关键行为

- [导入入口](../src/lab/urp_lab/gateway.py) 的 `_same_import` 复用 [prepare_import](../src/lab/urp_lab/scenarios/__init__.py)，比较完整归一化场景。旧 labpatch 只比较若干字段，会遗漏 CRS、单位、summary、layers 或来源变化；当前 [幂等测试](../src/lab/tests/test_scenario_idempotency.py) 逐项覆盖这些变化，非法 frame 返回 400。[来源测试](../src/lab/tests/test_agri_import_provenance.py) 还保证缺少地图时也不能占用官方场景 ID。旧 bridge 的单文件动态加载会破坏 validator 的相对导入，当前使用真实包导入。
- [农业手工编辑检测](../src/frontend/src/studio/agri/manualEdits.ts) 规范化后端补入的默认值、键序和数值精度；旧 happy-editor / mamba-studio 直接 JSON 比较会把这些变化误标为手工编辑。[GeneratedFrom](../src/frontend/src/studio/agri/GeneratedFrom.tsx) 保留当前规划页并打开目录卡片，旧 mamba-studio 会跳去 `#/archive`。对应 [测试](../src/frontend/src/studio/agri/manualEdits.test.ts) 保留。
- [Studio 状态](../src/frontend/src/studio/store.ts) 已按场景代次隔离求解、取消与轮询，呈现读取失败，避免迟到结果写回新场景；[现有状态测试](../src/frontend/src/studio/store.test.ts) 覆盖这些反例。当前 [类型合同](../src/frontend/src/studio/types.ts) 与图表读取完整比较块、gap 分母及逐时刻观测数量，不能用旧分支接口覆盖。当前计划页也保留任务/趟次 CSV。
- [离线 serve](../src/lab/urp_lab/evolve/serve.py) 要求外部提供的审查身份，并保持 learned activation 关闭；[guard 测试](../src/lab/tests/test_evolve_serving_guards.py) 检查身份、成员范围、机宽、内容替换与缓存不可变性。旧 `_scores` 的移除是明确的准入边界，不是待补回功能。八项真实推荐器接入测试仍在 [deferred_tests](../src/lab/deferred_tests/README.md)，未冒充已通过。

## 本轮实际整理

内部 Studio 调用统一直接导入 `lab/pendingWrites`，移除只转发该模块的旧 Studio 入口；未改实现、sessionStorage 键、版本或未完成写入恢复规则。evolve 说明改为指向当前全量 Lab 门禁，不重新添加历史专用 CI 作业。以上是入口与配置说明整理，不激活历史模型或择取新研究功能。

本轮静态和回归证据不构成所有历史行为的形式化等价证明，也不评价保留研究结果的科学价值。五个有独有路径的非祖先分支继续保护原 SHA 和路径；workbench 的敏感候选文件仍未打开或提取。
