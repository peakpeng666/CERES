# Paper 分支：从研究系统到可发表论文

本分支服务于一个目标：**把 URP 已有的算法库、冻结基准与系统证据，组织成一篇可投稿的学术论文**，并按顶级期刊的评审标准补齐缺口。

本分支不改代码、不动实验数据，只做研究组织与预注册：所有新增内容在 `paper/` 目录与根目录 `CITATION.cff`。

## 文档导航

| 文档 | 用途 |
|---|---|
| [PAPER_PLAN.md](PAPER_PLAN.md) | 论文主计划：选题决策、研究问题、贡献点、目标期刊、时间线 |
| [experiments/PRE_REGISTRATION.md](experiments/PRE_REGISTRATION.md) | 缺口实验的预注册协议（假设、划分、预算、统计检验、停止规则） |
| [REPRODUCIBILITY_CHECKLIST.md](REPRODUCIBILITY_CHECKLIST.md) | 面向 artifact evaluation 的可复现性清单 |
| [related_work_matrix.md](related_work_matrix.md) | 相关工作定位矩阵（对标文献 × 本项目差异） |

## 三条使用纪律

1. **预注册先于实验**：任何为论文服务的新实验，先在本分支冻结协议，再回 `main` 执行，结果与协议一同归档。
2. **负结果同等保留**：延续本仓库的证据纪律（见 `docs/导师查看.md`），论文报告实际策略净收益，不用 VBS 或消融线索替代主结论。
3. **版本绑定**：论文中每个数字必须能追溯到提交 SHA、输入、预算与原始记录包；引用格式见各协议附录。

## 分支工作流

```
main（研究系统与历史证据，保持稳定）
 └─ paper（论文组织层：计划 → 预注册 → 缺口实验设计 → 投稿材料）
      └─ 缺口实验获批后：在 main 或实验分支执行，结果回链到本分支协议
```

创建：2026-10-21，基于 `main@9eaf9eb`。
