# 汇报与可视化

## 目录
1. 术语表
2. 写作要求
3. 研究看板
4. 期刊插图
5. 配色与图形规范
6. 发布与核查

## 1. 术语表

规范来源是 `src/lab/urp_lab/evolve/terms.py`；图、图注、看板、报告都用它。冻结运行记录里的旧标签（`plans.config_label`，如「修补·不分解·最少作业行」）保持不变，展示时转换。

| 概念 | 中文 | English | 不再使用 |
|---|---|---|---|
| 选择余量 | VBS–SBS 差距 | VBS–SBS gap | 选择空间、选择收益上限 |
| SBS / VBS | 单一最优配置 / 虚拟最优配置 | single best / virtual best solver | 单一最好、逐实例最好 |
| ρ | 惩罚化成本比 | penalised cost ratio | 惩罚成本比 |
| 官方检查 | 原生曲率检验 | native curvature check | 官方判定、官方检查 |
| 倒圆判定 | 圆角可执行性检验 | fillet executability check | 倒圆判定 |
| 严格判定 | 加密曲率检验（2 cm） | densified curvature check | 严格判定 |
| 首选可行率 | 首次尝试可行率 | first-try feasibility | 首选可行率 |
| 停止成本比 | 终止成本比 | cost ratio at stop | 停止成本比 |
| 现行默认 | 工作台默认配置 / 默认配置优先 | workbench default / default first | 现行默认 |
| 学习策略 | 实例感知排序 | instance-aware ordering | 学习门控（用于排序时） |
| 只插入 | 增量插入 | incremental insertion | 只插入 |
| 每次重排 | 逐事件重优化 | event-driven re-optimisation | 每次重排 |
| rollout | 前瞻采样 | rollout | — |
| 门控（调度） | 学习型重规划触发器 | learned re-planning trigger | 学习门控 |
| 匀速代理 | 恒速模型 | constant-speed model | 匀速代理 |
| 停—转—走参考 | 停—转—行解析模型 | stop–turn–go analytic model | 停—转—走参考 |
| 机型 | 小型机器人 / 中型拖拉机 / 大型喷杆喷雾机 | small robot / mid-size tractor / large boom sprayer | 中型农机、大型喷杆 |
| 修补 | 局部 Dubins 修复 | local Dubins repair | 修补 v1 |
| 账本 | 算法库演化谱系（表名「算法库进化账本」沿用） | library lineage | — |
| 台次 | 次车辆运行 | vehicle runs | 台次 |

配置名：`terms.config_label('full:boustrophedon/n_swath')` → 「牛耕式分解 · 最少作业行数」；英文为句首大写（"Boustrophedon, fewest swaths"）。策略名：`terms.policy_label('logit:agri')` → 「逻辑回归 · 农艺结构特征」。

## 2. 写作要求

用户明确要求：术语自然化、学术化，去掉防御性解释。

- 适用范围写成事实，融入句子：「开发集」「留出田块」「规划层离散事件仿真」「原生曲率检验下」。
- 不写辩解与免责：「不外推为实车」「需导师确认」「不是冻结协议」「质量对比见运行目录」「待负责方集成后才能上线」之类删去；确有待决事项时放进「开放问题」，用研究语言陈述。
- 数字只从结果文件读取，不手填；没有数据的部分不渲染或标「—」，不写长句解释。
- 负号用「−」，区间写「9.7–17.0%」或「−7.0 至 −0.9」，置信区间写「95% CI」。
- 坐标轴标题按 GB/T 3102：「量 / 单位」，如「VBS–SBS 差距 / %」。

## 3. 研究看板

- 生成器：`src/lab/urp_lab/evolve/dashboard.py`，输入见其 `SOURCES`（审计、多车回退、三检验对照、策略、扩展库评估、时间模型、动态研究、触发器）与账本；缺失的输入对应的部分自动省略，扩展库评估出现后自动加表。
- 输出：`docs/reports/持续进化研究看板.html`（独立网页）与 `.artifact.html`（发布用正文，无文档骨架）。
- 结构：页眉（数据范围）→ 四张发现卡片（链接到各节）→ 六节研究面板（选择余量、可执行性检验、尝试顺序策略、演化谱系、执行时间模型、重规划时机）→ 开放问题 → 页脚数据来源。
- 组件：`bars()`（自零起的横条，可带置信区间，超出坐标的区间画箭头）、`forest()`（点估计 + 置信区间，零线加深，多序列在一行内错开）、`legend()`、`table()`（每图一个可展开数据表）、悬停与键盘提示。
- 测试：`tests/test_evolve_dashboard.py`（刻度、比例、零线、状态映射、无输入渲染、真实结果渲染、负号）。

## 4. 期刊插图

- `figures.py`：图 1 选择余量、图 2 各配置可行率、图 3 Shapley、图 4 尝试顺序、图 5 先测后学、图 6 实例空间、图 7 执行反馈。
- `figures2.py`：图 8 三种检验、图 9 准入规则与库容量、图 10 重规划价值（另一个 AI 维护）。
- 规范（`urp_lab/figures/style.py`）：Elsevier 栏宽（单栏 90 mm、双栏 190 mm），嵌入 TrueType，Okabe–Ito 配色并以标记/线型/填充纹理冗余编码，PDF + SVG + 600 dpi PNG，中英各一套，源数据 CSV，双语图注写进 `figures/index.json`。
- 图例不得遮挡数据：标签较长时用 `fig.legend(..., loc='outside lower center')` 放到图外。

## 5. 配色与图形规范（看板）

配色已用 dataviz 校验脚本验证（两种主题、色盲安全）：

| 用途 | 浅色 | 深色 | 说明 |
|---|---|---|---|
| 策略身份（分类） | `#2a78d6` `#eb6834` `#1baf7a` | `#3987e5` `#d95926` `#199e70` | 逐事件重优化 / 前瞻采样 / 学习型触发器；颜色跟随实体，跨图一致 |
| 检验严格程度（有序） | `#7fbf98` `#3a8a5f` `#1b5a3b` | `#2f6f4c` `#4f9c72` `#8fd0ab` | 原生 → 圆角 → 加密，越严格对比度越高 |
| 强调 / 弱化 | `#3a8a5f` / `#a4afa8` | `#4f9c72` / `#4d5a52` | 单序列图只突出结论所在的一条 |
| 页面 | 底 `#f3f5f1`，面板 `#fcfdfb` | 底 `#0f1612`，面板 `#17201b` | 绿色调中性色 |

图形：条形 ≤ 24 px 厚、数据端 4 px 圆角；网格为 1 px 实线；数值与标签用文字色，不用序列色；两个以上序列必有图例；每张图都有数据表。手机宽度下标签在上、条形与数值在下，数值列固定宽度以保证各行同一比例尺。

## 6. 发布与核查

- 发布前用本机 Chrome 截全页图核查（桌面 1200 px、手机 390 px、深色模式）：
  ```bash
  google-chrome --headless=new --no-sandbox --hide-scrollbars --window-size=1200,7400 \
      --virtual-time-budget=8000 --screenshot=/tmp/<草稿目录>/dash.png "file://<网页路径（URL 编码）>"
  # 深色：加 --force-dark-mode --blink-settings=preferredColorScheme=0
  ```
- 看板私有页面：https://claude.ai/artifact/9hPZXWDTZCSTcKdv6GqFRf 。同一会话用同一文件路径重新发布；新会话发布时传 `url` 并先读取现有版本。页面默认私有，分享由用户在页面的分享菜单里操作。
