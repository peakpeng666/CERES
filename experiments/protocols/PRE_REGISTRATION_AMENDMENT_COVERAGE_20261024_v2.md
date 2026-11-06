# 预注册修订提案 v2：覆盖配置尝试顺序（H1c/H2c/H3c）

状态：**提案，待导师确认**。本文件取代 [v1](PRE_REGISTRATION_AMENDMENT_COVERAGE_20261023.md)，是对 `paper` 分支 `paper/experiments/PRE_REGISTRATION.md`（草案 v0.9）的第二版修订提案。导师确认后，在 `paper` 分支修订并冻结原协议；**冻结前不运行任何封存田块求解器**。日期：2026-10-24（数据截至 2026-10-23/24，开发侧）。

与 v1 的差异：主判定由「官方检查」改为「倒圆判定」（§1）；登记运行间非确定性的处理方式（§2）；H2c 依据扩展库结果重新定义（§3）；H3c 依据修补 v2 结果选择实现并冻结其提交与源文件哈希（§3）；新增功效分析与一次性执行环境（§5、§6）。

## 1. 主判定：倒圆判定为主，官方与严格为敏感性

第 2 轮复核对主审计 1728 条官方可行覆盖路径做了三种判定（`experiments/runs/evolve-audit-20261023/verdict_comparison.json`、`fillet/summary.json`）：

| 机具 | 官方 | 倒圆 | 严格 |
|---|---:|---:|---:|
| 小型机器人 | 712 | 566（79.5%） | 177（24.9%） |
| 中型农机 | 552 | 509（92.2%） | 423（76.6%） |
| 大型喷杆 | 464 | 397（85.6%） | 361（77.8%） |
| 合计 | 1728 | 1472（85.2%） | 961（55.6%） |

- **官方判定**只在原始采样点计算三点曲率；作业行与地头段是单条长弦，长弦之间的拐角会被大外接圆放过。按倒圆判定，官方判定放过了 **14.8%** 实际不可执行的路径（小型机器人 20.5%），因此不宜作主判定。
- **严格判定**把每条弦加密到 2 cm，会把控制器能吸收的数值折角也算作失败（小型机器人只剩 24.9%），对判定阈值过于敏感。
- **倒圆判定**问的是物理问题：每个拐角能否在相邻长直段内用最小转弯半径 r 的圆弧倒圆；偏离量 r(1/cos(δ/2) − 1) ≤ 1 cm 的数值折角视为可吸收；倒圆后的路径（作业行按切线长截短并重新声明）必须通过**未修改的**官方检查。倒圆判定在 0.5、1、2 cm 三种偏离容差下给出完全相同的 1472 条，说明对容差不敏感。
- 14 条路径在 5 次重跑内无法再现记录哈希，标为无法核验并按不可行计入（§2）。

**建议**：封存测试以倒圆判定为主判定，官方与严格判定作敏感性（同一批记录、同一统计方法）。倒圆判定仍只是几何可执行性检查，不等同于实车跟踪验收。主判定的最终选择由导师确定；若导师选择官方判定，H1c–H3c 的方向与统计方法不变，只替换可行性标签。

## 2. 运行间非确定性

`evolve-determinism-20261024` 对 143 个源任务各重复 5 次（全部哈希不一致任务、分解 × 机具分层样本、无解样本），共 715 项（715/715 终态）。分析代码 `urp_lab/evolve/determinism.py`；结果 `analysis.json`。5 次路径哈希全同 88/143 = 61.5%；可行性稳定 128/143 = 89.5%；可行↔无解翻转 3/143 = **2.10%**（Wilson 95% [0.72%, 5.99%]）；含超时翻转 9/143 = 6.29%。按分解：不分解 85.7% / 100% / 0 翻转；牛耕 46.7% / 86.7% / 2 翻转（3.3%）；梯形 62.5% / 85.4% / 1 翻转（2.1%）。3 个可行↔无解翻转与 9 个含超时翻转全部在小型机器人；中型农机与大型喷杆无翻转。分层可行样本 0/90 翻转，mismatch 加富层 13.0% [4.5%, 32.1%]——基准率低，但一旦发生，单次标签不可复现。可行重复间的成本 CV 中位 0、最大 1.45%。不分解配置的路径并非完全确定（85.7% 哈希全同），但可行性 100% 稳定、成本在机器精度内相同。

登记规则：

- 主结果：每个「实例 × 配置」的**单次**终态（冻结计划的第一次运行），与开发侧一致；不因重复运行择优。
- 稳定性次要指标：同一任务重复 K = 5 次的路径哈希一致率、可行性翻转率与成本 CV，按配置、分解方式与机具报告。
- **多数票敏感性**：主判定另以「K 次中多数可行」作敏感性；不把多数票当主结果，因为它改变「一次运行即可交付」的部署语义。依据：代表性可行样本 0/90 翻转，而刻意加富的 mismatch 层 13.0%；多数票会掩盖单次运行的风险。
- 路由求解器预算：探针显示 `genRoute` 的 `time_limit_seconds` 默认 1 s 是时间预算而非固定种子；把预算固定到 10 s 可在两个探针任务上得到 10/10 相同路径，但在牛耕任务上该确定性路径 0/10 通过官方检查（默认预算 8/10 可行）。因此**不把固定预算作为协议处理**；若适配器将来暴露该参数（接口需求第 22 项），须先在开发侧重跑审计并登记，再决定是否用于封存测试。

## 3. 假设

方向均在封存运行前登记；主判定下检验，官方与严格判定重复同一组检验作敏感性。失败、超时、无解、无法核验均计入分母（首选失败）。

- **H1c（主假设）**：在封存田块（爱沙尼亚 96、立陶宛 113，几何准入规则同开发侧：内缩作业区 ≥ 田块面积 20%）上，由**开发侧数据拟合的静态尝试顺序**的首选可行率高于现行默认配置（牛耕分解·最少作业行，单发）。
- **H2c（学习增量，描述性）**：开发侧三种判定下没有任何学习策略同时通过首选可行率与停止成本非劣效两项判据：官方判定 logit·农业首选 +2.14 pp（Holm p = 0.026）但停止成本 +2.53 pp（超出 +1% 界）；倒圆判定该策略 −2.96 pp、最好的学习策略（logit·规模）−1.77 pp；严格判定全部更低。因此 H2c 不作方向性确认检验，只描述扩展库上学习策略与静态顺序的首选可行率、停止成本比与先测后学曲线；不设拒绝阈值，不进入 Holm 族。
- **H3c（算法库进化）**：向库中加入局部修补配置后，静态顺序的首选可行率提升（主判定：倒圆）。实现选择规则（看封存数据前登记）：若修补 v2 的倒圆可行数 ≥ 修补 v1且官方可行数不低于 v1，则 H3c 的实现为 v2；否则为 v1。冻结点记录所选实现的 git 提交与 `repair2.py` 或 `repair.py` 的源文件 SHA-256。**<<R6 结果与最终选择待填>>**

若 H1c 不成立，H2c/H3c 只作描述性报告，不再检验。

## 4. 处理与结果变量

- **处理**：尝试顺序策略 ∈ {现行默认（单发，对照）、静态顺序（H1c）、开发侧晋升的学习策略或静态顺序（H2c 的处理臂按 §3 唯一确定）、含修补库的静态顺序（H3c）}。执行语义与开发侧一致：按序尝试，独立验算通过即停；失败、超时、无解、无法核验都在分母；全程墙钟计入成本。
- **主要结果**：逐实例（田块 × 机具配置）**首选可行**（0/1），主判定为倒圆判定。
- **次要结果**：停止时的惩罚比值 ρ（不可行 = 惩罚值 P，登记于运行目录 `audit.json` 的惩罚值敏感性）、尝试次数、含全部尝试的墙钟。
- **VBS/oracle 只作诊断上界**，不进入结论。

## 5. 统计方法

- **主检验**：逐实例配对 **McNemar 精确检验**（二项版本），单侧，方向已登记；同一田块的 3 个机具实例相关，**按田块聚类的自助法**（重抽田块，B = 10 000）给出可行率差的 95% 区间。
- **多重比较**：H1c、H2c、H3c 三个主检验用 **Holm** 步降法族内校正（族大小 3，若 H2c 改为描述性则族大小 2；α = 0.05，单侧）。
- **非劣效**：停止成本比（处理 − 对照）的按田块自助法 95% 区间上界 ≤ **+0.01**；该界在开发侧第 1、2 轮均使用，未因结果调整。
- **次要结果**：按田块取均值后做配对 Wilcoxon（单侧）+ Hodges–Lehmann 效应量，仅描述性，不进入拒绝决策。
- **敏感性**：官方与严格判定替换可行性后的上述全部检验；结果两者都报。严格判定的无法核验路径按不可行计入，并另做「剔除含无法核验单元的实例」的敏感性。

## 6. 功效分析

脚本 `urp_lab/evolve/power.py`，结果 `experiments/runs/evolve-policy-ext-20261024/power.json`。方法：用开发侧逐实例的首选成败（主判定下 static / default / 学习策略 / 修补库），按田块有放回抽样到封存规模 **209 块田**；实例数按开发侧准入比例 388/420 换算，约 **579 个**（209 × 3 × 388/420，每块田 3 个机具实例，几何准入后约 2.77 个）。模拟 **10 000** 次，每次做 McNemar + Holm，报告 H1c–H3c 各自功效，以及 H2c 在实际效应量下达到 80% 功效所需的田块数。配对结果按独立伯努利抽样生成，丢弃了处理臂之间的正相关，因而**功效是下界、所需田块数是上界**。

主判定（倒圆）下的开发侧逐实例首选可行率：现行默认 0.485、旧库静态 0.768、扩展库静态（含修补 v1）0.930、logit·农业 0.901。按 209 块田 / 约 579 个实例模拟 10 000 次：**H1c 功效 1.00**（达到 80% 的网格下界为 50 块田；实际效应 +28.3 pp，配对不一致率 32.5%）、**H3c 功效 1.00**（下界 50 块田；效应 +16.3 pp，不一致率 24.0%）。H2c 为描述性，不计算确认性功效；其方向性检验在实际效应 −3.0 pp 下功效为 0（不会在错误方向拒绝）。**官方判定敏感性**：H1c 1.00、H3c 1.00（效应 +8.0 pp），方向性 H2c 0.87（效应 +2.3 pp，达 80% 约需 175 块田）。模拟按独立伯努利抽样生成配对结果，丢弃处理臂之间的正相关，因此**功效是下界、所需田块数是上界**。结果：`power.json`（倒圆）与 `power_official.json`（官方）。

## 7. 执行环境与一次性规则

- 机器与预算：能效核 8–23、16 个常驻进程、每项墙钟上限 300 s；单线程 BLAS/OpenMP；三档机具参数同开发侧（小型机器人 3/1/6 m，中型农机 6/4.5/12 m，大型喷杆 12/7/24 m；幅宽/最小转弯半径/地头）。
- 每个计划键只记一次终态；不重试、不择优、不因失败补跑。超时、崩溃、无解、无法核验都计入分母并保留原始记录。
- 封存测试只有在协议状态改为 **FROZEN** 并记录提交 SHA 后运行**一次**；结果包（逐实例记录、逐条判定、分析 JSON）的 SHA-256 回填附录 B。
- 代码身份：运行目录 `plan.json` 记录 git 提交与 `urp_lab/evolve`、`solvers`、`library`、`compose` 源文件哈希；计划文件的 SHA-256 由 `plan.sha256` 固定，运行器拒绝执行哈希不符的计划。
- **附录 A**：封存田块 ID 与原始 WKT SHA-256（只写 ID 与哈希，不生成实例、不运行求解器）——见下。
- **附录 B**：结果包哈希（冻结运行后回填）。
- **附录 C**：偏差登记（任何偏离本提案的操作、原因与影响；无偏离也如实记录）。

## 附录 A · 封存田块清单（209 块：爱沙尼亚 96、立陶宛 113）

来源：`urp_lab.evolve.instances.manifest()`；WKT 压缩包 SHA-256 `d00d0c9574212e29f7f18be775975f6b1ffd38a7dcd3501d7c05d2f63b7b26f7`。几何未处理。

| 田块 ID | 国家 | WKT SHA-256 |
|---|---|---|
| `ee_field_5` | ee | `3823dc4302edbf708b102022cce2c4ec38abda7027b43e728386e261c7e122d9` |
| `ee_field_6` | ee | `b4827645dbf5c3bceab4577ab592d304444125c48c809a97c9ee66d81ac73f2e` |
| `ee_field_8` | ee | `c15aa640cc3316f9178bcfde8e05b66899b777812afde89ad2e7595136c254f8` |
| `ee_field_9` | ee | `b94b6eedf271c4747815fd4914db3218c3db6ddc9db6486f37bebfdd3255360b` |
| `ee_field_10` | ee | `e38c38e38b34235960470a1383091fa5a674bf4fb0da2221148816b4cdc229f0` |
| `ee_field_14` | ee | `923c3a129eafab77bbbe60a3ac91f3daf8d025fe8f159a1caf021d3a36226647` |
| `ee_field_15` | ee | `9f3c410b825414774cd37c211788e894aafe2334f0dfde9019e04e3b24fa8f53` |
| `ee_field_19` | ee | `4ba034ce421f41c5b91a97d6ac99ce6eed4eb0e4bed1b2632dc08497d4312f8f` |
| `ee_field_22` | ee | `db324d79e3a4f736518ca946068b7fe8410845b837aabd4e04c40ba68d5ce421` |
| `ee_field_24` | ee | `e61d9a1b46b62d07d41edbf2c805fb0b6d15f9549473226239a709300814b55e` |
| `ee_field_25` | ee | `7018eeb09c25028e21942c0224540ac6d9680ee8ab73c56606a34be7e95da19e` |
| `ee_field_26` | ee | `efeac907044b49a09fd1ee679039c2854ca8b71e259a7bac479b2f417d8bdae9` |
| `ee_field_27` | ee | `1af500dd75371e61944cae511878880f9f82009042aeadae8828446b7df7c92b` |
| `ee_field_28` | ee | `4623887243b07aea31f509dc55dad4f2fe2e83429c5843ab533cbb99b113045f` |
| `ee_field_35` | ee | `ef305633a9c845b099815eed3462e7dd31e38b981b409580752d87ced8d9a7fe` |
| `ee_field_37` | ee | `d7955f1fb585b7a56ef8984fee03324cd2b5c539e1a219759e213000be1f4951` |
| `ee_field_38` | ee | `4c8305522e88aa6a9632fd76b695486016436f127dbf2983ba5fb19788e6bff3` |
| `ee_field_41` | ee | `24a8a9991dbe0a7f83e3d410c30110acf54140c292b3813ec5ed68133abd546f` |
| `ee_field_42` | ee | `492a425d84eccc3322296491fc98627a3c1176220b13a1773d19123a4f25e5c9` |
| `ee_field_43` | ee | `a9fa5f5a7940765ee6e9216927bc8c719aac60ac881050fcff36f7d9528fe0c7` |
| `ee_field_45` | ee | `0d9279eeb2db822dd82a9646883a5d33b89b4b2123a99b17fbd5aa160cfa6b6d` |
| `ee_field_46` | ee | `20616f2243d10e5ed5f61e469879ea67fa53f8bbb425d7ca2c4b96a603968c51` |
| `ee_field_52` | ee | `5eb486d40cb985b39e183ae4eb7c01f740e6155c0aa32418c3ee9d798eb07537` |
| `ee_field_59` | ee | `6c063df0b14883c50ff8e292cf05e0d513de0bf4e015682be953360e008fb82e` |
| `ee_field_61` | ee | `d4b45a4533381582c5c26bd19ce0c24af45738f1430dd07b30a97576652667da` |
| `ee_field_62` | ee | `ae6171132213cde02c64b3f4870aaa1dafc1b279ee2fa1ea886efa7b5b69a008` |
| `ee_field_63` | ee | `60e54845c4a99d490af0fe7ba560cc2052086023e38e81883ae73214b08bfbe6` |
| `ee_field_64` | ee | `1ead5711af34fc5db84ad6dddccf4961bb443294e2e738760435b345df863493` |
| `ee_field_65` | ee | `6c6d92e1afe0fd140aa6aa8cf9d32afe0a17e5328dbdfbc0b0136dc230a7be64` |
| `ee_field_69` | ee | `8a5c4fa0866470a511705f7c002b068dad34c3b01a0935dc20b34d5c720e7f3b` |
| `ee_field_71` | ee | `e5c120539eaec5cd900310219591f4df3c8f241cd80b458773c78e34429ac2a1` |
| `ee_field_73` | ee | `5842b22210cdcbd76f7d76cf415313b1666c5e039319214d1f31089a7d07c15d` |
| `ee_field_74` | ee | `0018e73a2d48112f915a167a61d141d575759e02f9ceac2895fd4b4d60d34d04` |
| `ee_field_76` | ee | `0e9be7c272bea1821aa321c403429bff7ccd633df0b880e072094cdacf2e1ea3` |
| `ee_field_77` | ee | `5c79fe8cfe659dd1ddab84b67935c6984e834b6f4aff65099f78ba7c831794f9` |
| `ee_field_79` | ee | `b047e14d7e8e4e6a49d166bb51c0be150863a1d51c07a6f28ce0966676081c7c` |
| `ee_field_82` | ee | `d7b4f65c43eeff36d657135c312b9111f951b99c6372f8edd1c2d1f228df0d96` |
| `ee_field_89` | ee | `40e29ad51bdc700e071f3e9277e739a45d8a12c7efa1a232e2df9938e74e8b7b` |
| `ee_field_90` | ee | `529f5e108b49767437da354a3777c860b5bfc07f2a7ffe6a76b3317583d944c0` |
| `ee_field_91` | ee | `3b169ea7cb9d0e8591aef5f3db2a28e81f65164a9e0127d2caa3678699219381` |
| `ee_field_92` | ee | `53fd554445afe971ddebcabf80864c933bd04c1419b5f8657f1c3c651163f076` |
| `ee_field_93` | ee | `0adf8cbfc4b3a254277691c9625387a521c243c8c5f9df1a26fbe9cf55af20a3` |
| `ee_field_95` | ee | `3e2b125fead00678ebfce77f868835950c276b2c219baceefaabf460108b442c` |
| `ee_field_97` | ee | `51af749887ce2053edf938fbc3adbf9e3dbdcae4a084021d78a935fe2a4c52b1` |
| `ee_field_103` | ee | `ce09e3496126465f23d5c33c3d3cc79b34410919aff888d4da36399f510cba67` |
| `ee_field_105` | ee | `49e9c64004db3c1a5998803f520193f3130d745567d025fb4b91727035998048` |
| `ee_field_106` | ee | `75b883e3f7ab3b0fb586681e775d4282a6ccb220890dc0e0073db9915bf1f2fd` |
| `ee_field_107` | ee | `a0930ae21f6c0291387a58a26347da393040ad8cc11f497c97af0540096e6c94` |
| `ee_field_114` | ee | `3dc2ddf99b771a684ac917ccc4bc6760770636c9de0fa8c991de239388b07757` |
| `ee_field_117` | ee | `77c20b214fdf0b230bd31d2600a59e969a6ff1daee51e31d9d144f3595199d3e` |
| `ee_field_128` | ee | `0e8b799dc51954a0db98a0edc0c8eb23654be2ed8bf7da88ae20673136574796` |
| `ee_field_132` | ee | `04e66778f700a1fb0127039fdcde4ed507c2bc5a6f18dfcb0e5e96d58a9e3bcb` |
| `ee_field_133` | ee | `1e1eda03869be6a6f014138207e440baa93da042f541013f05c9756e62692c52` |
| `ee_field_135` | ee | `13a4c3669ce97c31899e88682c8a35be7ffb795c00c9eb32ae19994245f4c183` |
| `ee_field_136` | ee | `c374c1a3c3d11f5cff1643f3527fb9f9a1acd4045e8eaad31fef674f81ab47fb` |
| `ee_field_138` | ee | `84313e200e90a303b166a98a135e77c5b5c6a4ebc855b497991fb946a8e42655` |
| `ee_field_139` | ee | `ee746bbc003e49536690905efba70abe6c7d4bc8a8c712d52db9c50e43db9d48` |
| `ee_field_140` | ee | `41ed3bdd6226b97bb4385fa49effbbfdcfbcbba5338cb8c825b3ef0c292cd5ee` |
| `ee_field_141` | ee | `d0d3beb10d1b4ff86cb0ea57393ad37124c9dc8b2bd4a383122565c5d9319ad9` |
| `ee_field_142` | ee | `f2d112d7deaef8b29207617058bc877b752bf6d61246cd3cf4cc59f8a189a7d4` |
| `ee_field_146` | ee | `8e136fdf258f5896c0d6dab2a084a3b78aa98b12aaad2cd3079f19c0289b1d7e` |
| `ee_field_147` | ee | `70a1942ed0115588da344158c6ae65367d4f833ff61ea98e92f976577123ad22` |
| `ee_field_148` | ee | `a6e926db5729ef668c0f7e59b9ad852545aebb03f30d09b92d0dad8b8ff32797` |
| `ee_field_149` | ee | `7d114e4ed77db7723d8295dc10cd1eb0711e74e4d8681e757f21ebb6c2c4a9cb` |
| `ee_field_150` | ee | `314e9cac52aabb05c77c8e75c0187e4e8f495268afc474c5a5920daa0a6fbcaa` |
| `ee_field_152` | ee | `84aa63885e507ef1ce6ce80038a16cf641aa3a0938967cc72d83345f842f3c10` |
| `ee_field_155` | ee | `9d82f68111ea1d3b29b7a533fdc71ecb44d418bb67141aff5718f370f7dd8b64` |
| `ee_field_159` | ee | `6077a38f4fef82b09c2ae15adebadf2813ab3f0f98c7ce4adbc014b0f105c5ba` |
| `ee_field_162` | ee | `cb670eccaebfef2e414b9a83b07efb1aa44822bdd2a2afdd4d1730e2089ebd45` |
| `ee_field_163` | ee | `7b96ab89cb9d379e426df088f7bd71c77a10d839d37d640f2e415a54f2265a9d` |
| `ee_field_165` | ee | `a1243b1d1a02e3fd84488ed0bd2a0d603da31c26568d06a8033c6a0686e43c13` |
| `ee_field_168` | ee | `a4237731846b738342ed8ff7e477e338d6a635b8e52ebd01078faa9f0326bae1` |
| `ee_field_169` | ee | `f974714698c3f9122e52062a3bf665aa3732f61495cf81dfc53ae06598539a8c` |
| `ee_field_170` | ee | `e09ab5b8788a821fdb371cdfae77d626f84c0d40657672b2c482091af34dd7c9` |
| `ee_field_173` | ee | `ace1dd0dd623149f6fe02c4806ff5f6c202699acbe36b28b927329f616796ace` |
| `ee_field_175` | ee | `4af073eb5a80cb33aa29df04023f7f81c6411330c6b8d8e83357792f745440f5` |
| `ee_field_176` | ee | `c3c94104fd9551bd195fa71382031bf6d34fe0e74d21ab1e4afd426adf6450e5` |
| `ee_field_178` | ee | `0078510db62d1acb1c69d22fc39aadf0b57e9f4d3c017abc5d7257c4054b4dca` |
| `ee_field_180` | ee | `2f95c814a67dbe8251ff78b670d0a8b5a94a031414dcc430828023e630216095` |
| `ee_field_183` | ee | `96a502aaaab0602d012ddedb8ed13ceeef104325518adf3230b2fee258f57a25` |
| `ee_field_184` | ee | `13e7ada75221700f666a2fb9d35a08023b35c6961c6125f308a6bb4f3ed1012f` |
| `ee_field_188` | ee | `a1cd8aa5ef576e34b92bd48bbaf1abc698f90a0ba430c8eb24c7bf78a046f3ad` |
| `ee_field_189` | ee | `4a3185e5f1e151bdba29d79f11333d625a553514a792912c36812fa900a549f5` |
| `ee_field_192` | ee | `7fa12c2f0d7a9a0642d64fbd4c7ca2082c2ef677cde7287a5e1585a0672481a2` |
| `ee_field_197` | ee | `6d3b364a5710a7c9623c6b5586a785aa7e42ca07b69c90c6f2a90462f0bce36a` |
| `ee_field_198` | ee | `c4e0b1459d5be2a42b6c2b1be496048e8ebd61189a8ec538f546fd21daa3d84d` |
| `ee_field_205` | ee | `67d9b68f6128fe1e7ad12fbf345eabb17cd0bcb9240f9fba8aba387674b40967` |
| `ee_field_206` | ee | `4163ab251d0d61be192375d5d23206d8f916be925a10db9c79e0a1f814864a36` |
| `ee_field_207` | ee | `bcf793d4661152927511d5aeb9718dd52f24885b365e8ca4604e3308fe84188f` |
| `ee_field_209` | ee | `f3cac5f717455c05177dc97c53db0882bd74dedefabe94ef47f780429f106c5d` |
| `ee_field_210` | ee | `49b76dbbebe32bcbaec45c4d4bb59add15464c8af6bd98803dd74e9dddccaf8b` |
| `ee_field_214` | ee | `5d9b084befc808356d6ac019f5f625c70edca96b4884bd4b3980f66dfd7dd7f7` |
| `ee_field_217` | ee | `71fdebb5269f2969f8ab796cd15c880c1c0122fa6af307cf3cc425375ec2aa93` |
| `ee_field_218` | ee | `61d79fee5e767f75846afae5d6990f5b781b9fcfca61253ca850ded631b43f7a` |
| `ee_field_219` | ee | `ed1e8f8fb7fdccfa7d4e228e20e53f3bb98d9c4e94437eb6aa5ac40e7e85d49a` |
| `ee_field_221` | ee | `65de266da59ad0a9b1f459e8265b2ff3cd71f7bbc81d80413fa3fd7ce6d75f8b` |
| `lt_field_1` | lt | `a06d6fb0a2d8c2acffcd988354977bec54547cc5387b31dd519798df50782a65` |
| `lt_field_2` | lt | `ce00561cdf085ffaef8e4f2ca507e13a5bf648e6f137a750ba55a600b7359b3b` |
| `lt_field_3` | lt | `3be09413616de2c762cea141bac495bb316fe8a7b9d66a9e1928ea31e41d5f21` |
| `lt_field_5` | lt | `1579cf0fb7dc306795e01d3c665557e3e94127dce6670667b5390494bba74bd7` |
| `lt_field_12` | lt | `f20dd9a3873cd000184440fff0ff381ac30f980fd15782066f7aaf715e6e5c5f` |
| `lt_field_15` | lt | `f5520ff2396e21fe1188f5679bbe96aa41b3254b327f15e09567cd947a23f5f7` |
| `lt_field_16` | lt | `322e62f3f5e97d9d75980d0c628ee363fc6ceafaad8398c7d9378df578fd68e4` |
| `lt_field_17` | lt | `ae50a06ec1a7f74934bf697eede45a4c41509440126247645bf29407cfdd2a18` |
| `lt_field_18` | lt | `edcb44f766496c1e18fef58c53bc020240244c5c3e92c253cc4be9f50d546ff9` |
| `lt_field_19` | lt | `6239b31e99481d41dfa48957a77ca29fc288a6b821d7d58b61b939c80e3ef20c` |
| `lt_field_20` | lt | `5faa5ea823db29573ab2a4c611ec43c7ed7f27eff7f20a4f24a29b7d05c5e83f` |
| `lt_field_21` | lt | `0b20167f0b42a262f1bcc6fd0fb8f55da7e3035482c5dc84274708db8b03c074` |
| `lt_field_27` | lt | `ed6afb2cc73fe419b39803de8d2e45210c5abb7276ba2af4092e67fa0c7c88bd` |
| `lt_field_35` | lt | `11cf3c15f0ec6036f0a2500b33e7fb472429c5b61a3bbba44809988d53055c7a` |
| `lt_field_38` | lt | `cf572f2a7c2065f750b9f4842484efbac8f06017d1d0c1c8195375b343e002a6` |
| `lt_field_50` | lt | `d909403b3e451666a2c5c9922b504a587e35cf456c13b100fb2a0fa0318e8fc5` |
| `lt_field_52` | lt | `9a11e981400c519e6dcec12153916a2601748da42bec3541c86eb9a6b53f7f0b` |
| `lt_field_54` | lt | `93b2a18873cab2e6d829a3f36391c31c836025bebbb030446ee9e606cd612181` |
| `lt_field_55` | lt | `38ac47be73bd0eeb22fc4124fdd287d93c6e493d03778d70f2f80d590fd1377a` |
| `lt_field_58` | lt | `c50980386a631e306d809db363dcfce353e65366c6f34d804a8eb1a7e2147b07` |
| `lt_field_60` | lt | `a9f6b1a7be792ed1dc99275cfa08bd0a69d9b0d734a3ff0a341c74682697176f` |
| `lt_field_61` | lt | `b4622df12df21d7ebe28820026231e595a4db1601cfc7a5840a391e8a16ea88e` |
| `lt_field_65` | lt | `f80133a9d8ab6ad6fadd3a69876736e45b935d5a0e3ad86c7c812cf51b5fa099` |
| `lt_field_68` | lt | `a41d0c762fce9757ebcd1c41b758772a5a432f33ebbd092bf95aa232abb3dbe5` |
| `lt_field_69` | lt | `6361cfad770f8b5cd60d58b8f1f651459ba51adc5d64fa4c6a1145bc259080c3` |
| `lt_field_70` | lt | `5a8c421252e60792aa237dbef1263038673bb100c615739f6e4065ae49386ee5` |
| `lt_field_75` | lt | `364d44bce0e4eec832a4cf18f2c1976734ced08599d713e8eed0667621cb5501` |
| `lt_field_76` | lt | `d03cc11f4903f9ba3c14279f2e6ae0bf8bc2dcf46ca7525127828217e7227b9a` |
| `lt_field_81` | lt | `d6c4c5cf1d50c6824fb28ac0bcc301796d820f5842ae3f21ff9c8f6b65e62ebf` |
| `lt_field_82` | lt | `0abab2b7d01ec296f97c9dc8b5ec7ba59c67ae3fdfae20ed5f876245c223de21` |
| `lt_field_86` | lt | `353e22527bc69ec1a5f5e5a9d1bfc2ddbde182d282d799c1ccba491ba8f9453b` |
| `lt_field_87` | lt | `6308fa34b5bc6f33c86018b2f72b54487eb0de47b33490dd80313f54bee66b4c` |
| `lt_field_90` | lt | `33a09f2b4f534d01268a0a0996bc2d577635577872a64416c64a75e99c96825c` |
| `lt_field_95` | lt | `2bc98d84901d94e1737e09b96103329c3f1f5d7baef21dc83a7c085a6cac03df` |
| `lt_field_97` | lt | `d22e847c576c3bf34ca6e87dbb0e9873c7b7b6904923facc45393553efc9ad32` |
| `lt_field_102` | lt | `f7746631f9240d2d842a6a229456b11290a95067afad9e4d5ff0a7b90f0afc51` |
| `lt_field_103` | lt | `0c5e3dbcb6ca0653f51aa7b184831e7725c56270831f329529171934885e3d10` |
| `lt_field_106` | lt | `ae3ed1def7ace8668862e561d08e939e783d32e9593d7383b8173001fcd87565` |
| `lt_field_107` | lt | `b065f23e2394ebf25bf6ddbdf6b82232eaf5cdb46cd82f84e74e29db5f86a58b` |
| `lt_field_115` | lt | `74599ac821f48817ba8b3e1e4ded23074ff341f4ce10111a91de8edb06b1d8eb` |
| `lt_field_119` | lt | `7586c7a72bf54298bb971bf1bbbba214713a0847c06789874e2f23d35148a8ce` |
| `lt_field_120` | lt | `4fa21b0320ab27a43803494d2b1c6e0c08a7faac3c1935baf1d119e001d2f57f` |
| `lt_field_121` | lt | `e646569fd1207ef39bc3621166897eac94c5f538a95e78ff7d5db01469f4882b` |
| `lt_field_123` | lt | `108411fb7edd1166aca86a502db77e88c45f1f139fdd875bc0d2e73d750bf140` |
| `lt_field_129` | lt | `0794ebe2581d6d72c6a93a4274fb382583b8dc39ac3b0813e7cded05eddfd2b3` |
| `lt_field_131` | lt | `b311ef2f1e42019bfdba226b330c3b78aff26c5f1ce2bb482668d173f2306160` |
| `lt_field_133` | lt | `bddbf71f3529036fe7b8ebe47febcb348585f834e4ab15a4a8210ecafef1afa8` |
| `lt_field_137` | lt | `c8847ab2f1392a1a327f7f1f70fcedb7e88041b27e8dc33abdd948d36bf95b94` |
| `lt_field_139` | lt | `f4efeef4d33fe821d70adc8ea18c1be57e3526ed8f0661a3502c26f57714b93d` |
| `lt_field_143` | lt | `6765346c28c73a870203baddeb3ca5de2ae5c24842843b161463486237316a28` |
| `lt_field_144` | lt | `9f8c2ffcaa9c633929fb8006bbed3e42ab8a5e50914806d12f55cc9fd9df7d5a` |
| `lt_field_152` | lt | `b12a167a468517ab4fc4710a68af0744a83bf7379e38c78177fa89f755b4c150` |
| `lt_field_157` | lt | `aa81f127ee98859c04e670acb894cc23131ff8c878135e22e3c69cb7fdb4fc06` |
| `lt_field_158` | lt | `b645c7d2059555764cd9828234adccc77fc1433708f94708553357ef0dd746f0` |
| `lt_field_160` | lt | `bdae3212a546198e01cd7700a4193930423be53917a591cdd2bb14ac8bc129d5` |
| `lt_field_162` | lt | `12dfc46a56030b0c76d7683b9110cc30dcdafe72ccc52907ece718c915a52ccf` |
| `lt_field_163` | lt | `2be6f1643766aa61640a3bd2d0868b32fc463b2fb9d684d030858b4e5ee8a1fe` |
| `lt_field_164` | lt | `f2a0ec784ee13c76dd703c4e0779a9601bcd29aa656bbc9c519316e5cbd363ba` |
| `lt_field_165` | lt | `d8d2ce5b04e6717d408f81d8c4118be6709a114b9ec83bf594d8028daa86461c` |
| `lt_field_166` | lt | `d3fa8d409742bb63cd1dc735b2c8d08a2f315a4f888d7041105bf28565a08ae0` |
| `lt_field_167` | lt | `7f06063e72f80e7e309676bbdf338b4f43b75626c71e2d926c66dd855a2b425c` |
| `lt_field_168` | lt | `c312ee63ad623435a9e024de636ad0bbc39f149a0eab181ff493e04169a655be` |
| `lt_field_169` | lt | `a1b05dcb23121317a730ceac15e235249647d359be999d4ceaf142f326baefeb` |
| `lt_field_170` | lt | `11bd9b15869743696716af2c34e47ffbc1ae75cf2024dc5cb2af14f19f625258` |
| `lt_field_171` | lt | `c368dea4016e1f679a5b8ccf56f359d9718c2a8f83e0cf68da8a37df70f1d464` |
| `lt_field_172` | lt | `98ed8b7088059bcf08964368d6c68717ad866d60e6f660491f87ba6604317eda` |
| `lt_field_178` | lt | `bb7b4d075399da4a5106cdc2ff74af7e0af9b91c2eb06a87c8e4614268bf25fe` |
| `lt_field_179` | lt | `cdd76ad941a666ef191e21f303318797ca3af31492443154155a6807a5cfe946` |
| `lt_field_182` | lt | `9581eedc00601ac3914c2063e9b6e7bd7df87e8e83d4cbf885fd20d181d1dc88` |
| `lt_field_186` | lt | `2fedbda3386c2bdb6e280e4737351de4199c45f30a1bbf8a07c9d06dcc1b8444` |
| `lt_field_188` | lt | `8995feaf0a21993e5704977607059b47fb04258658d771095ffa3d75de80cdd5` |
| `lt_field_191` | lt | `1e946b8927a72d7211d8357bb1fdde8752b4f48fbeb5a16bd826f47eb1cfc2d2` |
| `lt_field_192` | lt | `820680f77dc1c3a6ba6a84764cd97d49b6c82023ddba24b9807b5d6f155061ce` |
| `lt_field_194` | lt | `d50bdc52dfa5fd0b34b9c55ef8fa563c63ec86d3ac6f8c8663c242b78105d51f` |
| `lt_field_195` | lt | `930503b22016535a4ceab2aab0355d591ccd8e8ebe11f32fe3d8a15ae696c4c2` |
| `lt_field_197` | lt | `9a0c115b235435181aaedf8d2abd0a46be5c04d64f9967526a784d7a692da622` |
| `lt_field_201` | lt | `b65b6b5f99dec434438b65594877a8f02e6470724ea4ae4b79cd18f3c2e90a51` |
| `lt_field_202` | lt | `c29a6b2ac0db257e15f6cf93bf204dfb826f46ad22cf9c712af57c79fc2d8f9b` |
| `lt_field_203` | lt | `2daff6e01f5bdeaad394bde3ae8fc8c60691b28272306d308b482f9003063eee` |
| `lt_field_207` | lt | `90974b54c712e171151e1a192906fe396d53f7eb2aec4680cc6882059fd1cf09` |
| `lt_field_208` | lt | `2e349373ac90382eab2b9321fb3159ae0a9d410758929d9e8c3fbc99d3ca2988` |
| `lt_field_214` | lt | `7f405b33c128f703e7695ac5de8576ddc595b03d5b1fdf5c362a47dc55a3d4ed` |
| `lt_field_217` | lt | `dc00b7deeeb8c59c172c529e0bc5fffc567f56f956fa54589c9e647d0ce9f8c2` |
| `lt_field_221` | lt | `e0aab51313df5d8c5cc5244ea3a0b88cde904675ee9bcc3605ca5c9788353101` |
| `lt_field_225` | lt | `7eef37d911f769a2ef026ca86c7d36ed4d1c2a17e9ff9ee90aa990505a9aa657` |
| `lt_field_226` | lt | `bfbcda74ffb01d72331f03733b680896a7fd75f3b3e90893543c9ad5a64546ba` |
| `lt_field_229` | lt | `64d8dfcb23b2b40ab9b271f2af72a81408a40b0dc255a159bfe7eb25f505829a` |
| `lt_field_230` | lt | `54bcaf9d3be395110daa2b234d64445940a3f7d4a7a60f8b81c07353c4acadac` |
| `lt_field_231` | lt | `1ed4579db6b8baf8bfa304a41f0964200d469f3ddf8c6591763bbc53d54730a1` |
| `lt_field_233` | lt | `30238bf6fb4f6346ac2bc54fa6f83a4873d832373c78ab678c9a380e0c08b9f9` |
| `lt_field_235` | lt | `89f2a4caba2b2bfacee0f84f7de23e35999880cdafaf445b5dbeced1fc302e7f` |
| `lt_field_236` | lt | `7dc3621d1f1594a93a9aa451d41cf7d691b30405fb7a8b01a3f43427b2ad8106` |
| `lt_field_237` | lt | `d6a4dcfac61309f3c532ce8971fe6bd73841eabb272d1e72d63709245d77cd63` |
| `lt_field_238` | lt | `8943df19698a68cf4d010f341a22c5568cee6a70fe2af485013db3d23895b6fe` |
| `lt_field_239` | lt | `d2904c72ffc1a53d9563e95cc82fa599a81ec9d20a6b43e56310b8efc6d47bc9` |
| `lt_field_240` | lt | `710100a7a187cd7fb709d4272e71df4e0bc94cc3246e4ca05877513838a0ef51` |
| `lt_field_245` | lt | `d555b9a6abbfd7d4b4074e6194c314b4fc2a6dad89f249328bf6cc1df90411a8` |
| `lt_field_251` | lt | `036ecb91eb341bbcc680d45909852c80fae71057ff84ef8bf36b27212b661b2c` |
| `lt_field_255` | lt | `1f2981073028835b1d972393227c594301cbc1b5346a37d5e3034d7fa03a3424` |
| `lt_field_256` | lt | `1dfeb6bf838a941ba817f3880073a3162942604c1d29f0d524e0affbc4f118e4` |
| `lt_field_258` | lt | `d96e16444b766f16e09fab5604a8512aefeb4828c8840f87026519ba5814268b` |
| `lt_field_260` | lt | `32dfd1809711d7dc9867568671e0018a4d202ab348ba65e76433c9d7d071d037` |
| `lt_field_269` | lt | `0e69853133b95e0ff67e4b3860557e6623a767a6c9e5a7243e4cad11ccca480b` |
| `lt_field_279` | lt | `26dd87e93dbcc1de5899efaa348a2bfb45e43cc8a244567e33df45a69f0912aa` |
| `lt_field_280` | lt | `62a8889e95519646c3dda209e77ab412eb20cfaaee2d8c396b89ea121e8cedc0` |
| `lt_field_281` | lt | `2d6a5e5e259b6c63d158852f74937fd03f09eafa4dfd8480cb65d0bcbc4ba972` |
| `lt_field_286` | lt | `ec5c2f76f066618ad610c86ad70690801d109642b8353882c9a5b696428395c7` |
| `lt_field_287` | lt | `94a7e8ee889291de63a9657521f2b94e4e1ec22e98f8cbaa428605fd8097605e` |
| `lt_field_289` | lt | `d3b475428077746dec69e2b1e0aee4ce898f77667a098c7a0353b4d0dce1ac06` |
| `lt_field_290` | lt | `dd1f95fe2c65588a68c22875f0a717fddcf3daa98ef601f1dd87e74a842280a3` |
| `lt_field_291` | lt | `1f758259c1350585fdc5327b94cf9186350e85da80ed28080850ff3bb90d6af4` |
| `lt_field_292` | lt | `76c65015c43442a155e4a03a0fa2b213fe67c8564844a963e783f44f22c4879f` |
| `lt_field_293` | lt | `ebfca8a411c67df75a505698d9da8beef8e0cfb26bdd25d331a3b931cdc1df6d` |


## 附录 B · 结果包哈希

（冻结运行后填写）

## 附录 C · 偏差登记

（运行后填写；无偏差则写「无」）
