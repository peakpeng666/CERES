# 可复现性清单（Artifact Evaluation 口径）

目标：让论文的每个数字可被第三方复核。对照 ACM artifact badge（available / functional / reproduced）自评。

## Available（可获取）

- [ ] 算法库源码公开（已满足：本仓库）+ 版本 tag（建议 `paper-v1`）
- [ ] 基准实例包公开：CVRPLIB 使用清单 + 农业田块几何（注意田块来源许可，见 `deploy/THIRD_PARTY.md` 待核对项）
- [ ] 冻结协议与参考解公开（`protocol/formal_plan.json` 已在库；参考解打包待做）
- [ ] Zenodo DOI 归档

## Functional（可运行）

- [ ] 一键复现脚本：`scripts/reproduce/paper_main_table.sh`（待写），输入实例包，输出主结果表
- [ ] 独立验算器可单独调用（`check.valid` 链路已存在，需脱离全系统最小化）
- [ ] Docker 镜像与论文版本绑定；容器 warehouse 0/5 问题须在镜像说明中如实标注
- [ ] 环境证据链：4505 项中 3604 项主机证明链追回（E6），未追回部分在论文中按现有证据等级声明

## Reproduced（可复核）

- [ ] 主结果表由脚本从原始记录包重算生成，禁止手抄数字
- [ ] 统计检验脚本随包发布（Wilcoxon + 效应量，见预注册 §5）
- [ ] 每个论文数字 → (提交 SHA, 记录包, 实例 ID, 预算) 四元组索引表

## 论文写作纪律（承接仓库既有口径）

- 配置数 ≠ 算法家族数；各实验批次分别记账，分母不混用。
- "无有效解"不等于数学不可行；可行率与质量分别报告。
- VBS 是上界诊断，实际策略净收益才是结论。
- 负结果（CVRP 双候选零收益、容器执行差异）写入正文而非隐去。
