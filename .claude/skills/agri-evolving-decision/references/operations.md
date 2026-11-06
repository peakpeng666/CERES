# 操作：环境、命令与长任务

## 目录
1. 环境与测试
2. 算力分配
3. 冻结计划扫描
4. 检验与分析
5. 动态调度与触发器
6. 账本、看板与插图
7. 后台任务链与恢复
8. 当天踩过的坑

## 1. 环境与测试

- 两套 uv 环境：仓库根（后端与求解器，`uv sync --locked --extra solvers`）与 `src/lab`（田间模块，`cd src/lab && uv sync --locked --inexact`）。不替换系统 Python，不改 `/opt/ros`，不用 sudo。
- evolve 代码都用 `src/lab/.venv/bin/python` 运行，工作目录 `src/lab`。
- 测试：
  ```bash
  cd src/lab && .venv/bin/python -m pytest -q tests/test_evolve_*.py
  ```
  仓库级检查：`uv run --no-sync python tools/check_project.py`（仓库根）。CI 作业 `evolve-research-contracts` 跑 evolve 的契约测试。
- 依赖本机 ROS/Gazebo 的真实会话测试（`src/lab/e2e/field_backend.py`）需要全新的绝对输出路径，且不能与其他物理验收并发。

## 2. 算力分配

Intel Core Ultra 9 275HX，24 个核：性能核 0–7、能效核 8–23。

- 扫描、圆角检验、复核：能效核 `--cores 8-23`（或 `taskset -c 8-23`），16 进程。
- 分析、交叉验证、动态研究、出图：性能核 `taskset -c 0-7` 或其子集；与其他分析并行时分开（如动态研究用 0–3、扩展库评估用 4–7）。
- 负载很高时（load average 50+）出图会变慢数倍，命令放后台，不要以为卡死。

## 3. 冻结计划扫描

```bash
cd src/lab
# 生成冻结计划（只含开发田块；会写 plan.json、plan.sha256）
.venv/bin/python -m urp_lab.evolve plan --out ../../experiments/runs/<run-id> --run-id <run-id> \
    --families coverage,fleet --note "<目的>"
# families 可选：coverage、fleet、repair、candidates、determinism（repair2 由第 2 轮加入）
# 运行（可重入：已有记录的任务会跳过）
.venv/bin/python -m urp_lab.evolve run --out ../../experiments/runs/<run-id> --workers 16 --cores 8-23
```

- 计划写入代码身份（`git_commit`、源码哈希、`src_lab_dirty`）。扫描运行期间不要修改 `plans.py`、`sweep.py`、`instances.py` 及被调用的求解模块；新功能放新模块，扫描结束后再改。
- `--limit N` 只取前 N 块田，用于冒烟测试。

## 4. 检验与分析

```bash
cd src/lab
.venv/bin/python -m urp_lab.evolve reverify --out ../../experiments/runs/<run-id> --workers 16 --cores 8-23   # 加密曲率
.venv/bin/python -m urp_lab.evolve fillet   --out ../../experiments/runs/<run-id> --workers 16 --cores 8-23   # 圆角检验
.venv/bin/python -m urp_lab.evolve audit    --out ../../experiments/runs/evolve-audit-20261023
.venv/bin/python -m urp_lab.evolve policy   --out ../../experiments/runs/evolve-audit-20261023
.venv/bin/python -m urp_lab.evolve experience --out ../../experiments/runs/evolve-audit-20261023
```

扩展库 × 三种检验的策略评估（约 10–30 分钟，性能核 4–7）：

```bash
cd src/lab && taskset -c 4-7 .venv/bin/python -c "from urp_lab.evolve import verdicts as V; from pathlib import Path; \
R=Path('../../experiments/runs'); print(V.write(R/'evolve-audit-20261023', R/'evolve-repair-20261023', R/'evolve-policy-ext-20261024', workers=4, cores=[4,5,6,7]))"
```

其他分析入口（参数见各模块 docstring）：

- `admission_rules.write(dirs, base_cfgs, cand_cfgs, out_dir)` → `evolve-candidates-20261024/admission/admission_rules.json`
- `admission.write(main_dir, candidate_dir, library, candidates)` → 修复 v1 的准入分析
- `determinism.write(run_dir)` → 确定性研究汇总
- `exec_model.write(out_dir)` → `evolve-exec-model-20261024/exec_model.json`
- `power.write(main_dir, repair_dir, out_dir, verdict='fillet', learned='logit:agri')` → 封存修订的功效模拟

## 5. 动态调度与触发器

```bash
cd src/lab
# 180 条流：30 块开发田 × 3 种密度 × 2/4 车（可重入）
taskset -c 0-3 .venv/bin/python -c "from urp_lab.evolve import dynamic as Y, plans; \
print(Y.run_study('../../experiments/runs/evolve-dynamic-20261024', plans.dev_fields()[::4][:30], vehicles=(2,4), seed=7, workers=4, cores=[0,1,2,3]))"
# 学习型重规划触发器：先采集前瞻采样的决策，再按田块分组评估
taskset -c 0-3 .venv/bin/python -c "from urp_lab.evolve import replan_gate as G, plans; from pathlib import Path; \
R=Path('../../experiments/runs'); G.collect_all(R/'evolve-replan-gate-20261024', plans.dev_fields()[::4][:30], workers=4, cores=[0,1,2,3]); \
print(G.evaluate(R/'evolve-replan-gate-20261024', R/'evolve-dynamic-20261024', workers=4, cores=[0,1,2,3]))"
```

## 6. 账本、看板与插图

```bash
cd src/lab
.venv/bin/python -c "from urp_lab.evolve import ledger; print(ledger.write())"        # 所有检验完成后重建账本
.venv/bin/python -c "from urp_lab.evolve import dashboard; print(dashboard.write())"  # 研究看板（秒级）
.venv/bin/python -m urp_lab.evolve figures --out ../../experiments/runs/evolve-audit-20261023   # 图 1–7（数分钟）
.venv/bin/python -c "from urp_lab.evolve import figures2; from pathlib import Path; print(figures2.write(Path('../../experiments/runs/evolve-audit-20261023')))"  # 图 8–10
```

- 账本只纳入证据完整的成员（每个检验下完整率 ≥ 99.9%）；不完整的显示为「—」。
- `figures.write` 与 `figures2.write` 都只替换自己负责的 `figures/index.json` 条目。
- 看板发布：用 Artifact 工具发布 `docs/reports/持续进化研究看板.artifact.html`；同一会话内用同一路径重新发布保持链接，跨会话传 `url`。

## 7. 后台任务链与恢复

- 长命令放后台（`run_in_background`），完成时会收到通知；等待某个条件用 Monitor 的 until 循环，不用 sleep 轮询。
- 每一步写完成标志（都在运行目录下）：`evolve-repair-20261023/fillet.done`、`evolve-determinism-20261024/chain.done`、`evolve-policy-ext-20261024/t3.done`、`evolve-candidates-20261024/t4.done`；后续步骤以标志为前提。一次查看：`ls experiments/runs/*/{fillet,t3,t4,chain}.done 2>/dev/null`。
- 中断后按顺序重跑同一命令即可（跳过已完成项）；进度看 `progress.log` 与记录数（状态脚本会列出）。
- 当天的链：修复运行圆角检验 → 候选扫描（3104 项）→ 候选圆角检验 → 确定性扫描（715 项）；性能核上等待链：T3 扩展库评估、T4 准入规则。

## 8. 当天踩过的坑

- 圆角检验早期用角度阈值判定噪声折点，结论随阈值翻转；改为偏差阈值（e ≤ 1 cm）后对 0.5/1/2 cm 稳定。
- 在 2 cm 转弯采样旁边做圆角化会产生近零长度段并触发原生曲率失败：只在两侧都是长段时圆角化。
- 多车诊断脚本因一次重跑时间预算不同而缺少 `rejected_routes` 键：读取可选键要容错。
- 曾在扫描运行时改过 `sweep.py`（随即还原）：此后一律不在运行期间改被导入模块。
- Fields2Cover 多区路径规划存在运行间差异（与核速无关）；`none` 分解是确定的。重复实验要么固定配置、要么报告重复间方差。
