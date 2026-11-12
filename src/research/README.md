# 研究实现

本目录包含父代选择、CVRP 选择与 CRC、转弯服务 mode 合成检查、数据恢复、结果重放及 NAS 模块。研究进展见 [2026-10-19 导师查看](../../docs/导师查看.md)，各实验结果按对应日期与版本查阅。

在本目录执行：

```sh
python3 scripts/ceres.py --help
python3 scripts/test.py
```

核心模块使用 Python 标准库；NAS 推断与训练需要配置中列出的 PyTorch / NumPy 依赖。

| 入口 | 内容 |
|---|---|
| `src/ceres_research/` | 研究模块与 CLI |
| [数据恢复](docs/DATA_RESTORE.md) | 本地实验包与校验命令 |
| [NAS](docs/NAS_FIDELITY.md) | 训练保真度、重算与训练接口 |
| [生成器](generator/README.md) | 装箱输入与模型执行器 |
| [进程监督](execution/README.md) | 子进程回收、资源与退出状态记录 |
| [研究结果验收](docs/RESEARCH_ACCEPTANCE.md) | 路线验算、计划分母、推荐划分与失败记账 |
| `configs/` | 输入清单、哈希和依赖 |

完整结果见 [experiments](../../experiments/README.md)。既有实验输入位于 `../../data/research`，新运行输出写入 `../../experiments/runs/<run_id>`。多问题生成循环的后续研究设计见 [下一阶段](../../docs/下一阶段.md)。

## CVRPLIB 实验入口

`src/ceres_research/cvrplib/` 包含真实 CVRPLIB 输入、统一端到端预算、恢复的经典基线及 PyVRP 0.14.0 ILS。运行 `python3 scripts/cvrplib.py --help` 查看命令，复现步骤见 [验收和复现说明](docs/CVRPLIB_READINESS.md)。

正式主批次已完成 **17 配置 × 53 问题 × 5 种子/重复 = 4505 项**。其中 4316 项独立验算可行，189 项无有效解，详见 [基线报告](../../experiments/baselines/cvrp/README.md)和 [归档重审](../../protocol/audits/20261017/README.md)。

53 个问题来自 51 个底本，后续推荐实验按底本分组。vendor 120 项、接手验收 742 项及两批各 48 项按各自实验独立记账。

## CVRP 选择器

最新选择器实验采用 train60/cal30/test30 划分，与 [较早的合成 CRC 回放](docs/RESEARCH_ACCEPTANCE.md)分别归档。

按严格成本比较，PyVRP 在训练集为 51 胜 9 平、校准集为 23 胜 7 平、测试集为 27 胜 3 平；OR-Tools 严格更优 0 项。当前测试尚未显示选择策略相对固定 PyVRP 的收益。

[冻结模型](../lab/models/cvrp-knn-v1.json)已可运行，评估合同为每算法 0.25 秒、两次重复。工作台默认配置为 3 秒、种子 42，属于另一组运行条件；其他预算或分布下的选择表现由对应实验评价。
