# 资料与第三方源码

当前派生索引覆盖 **2,443 个文件、75 个第三方源码目录**，来源为已合并 main `4ee2c6dd8c2b455351812d3c312a8215627fce1f`。2026-11-01 修正分类器漏掉 `.tar.xz` 的问题，补入 `experiments/baselines/cvrp/raw/` 四包，共 **19,983,780 字节**；没有新增或改写原始包。逐包路径、blob、大小和与原清单的差异见 [current/review.json](current/review.json)。

原 **2,439 项**清单及其 `85a7048b` 生成基线保留原样；2026-10-30 的 [review.json](review.json) 仅证明所列 2,439 项的路径和 blob 对齐，不证明分类没有漏项。当前逐项复核仍为 2,439 项原路径/blob 匹配、0 移除、0 内容变更。树中存在与本地已下载分开记录。

现行数量为 559 份论文 PDF、16 份教材 PDF、25 份其他 PDF、**294 个实验压缩包**、5 个其他压缩包、1 份数据库、1,543 个数据或模型文件。当前已有 **4 项报告资产**通过独立副本恢复验证（Space 4、Drive 0），迁移数为 **0**，详见 [restoration.json](restoration.json)。

- [current/assets.csv](current/assets.csv)、[current/inventory.json](current/inventory.json)：当前派生路径、用途、Git blob、证据引用与分提供方统计。
- [assets.csv](assets.csv)：保留的历史逐文件清单；[inventory.json](inventory.json)、[review.json](review.json) 保留原生成和核验口径。
- [third-party.csv](third-party.csv)：75 个外部代码目录的根许可证路径、文本类型线索、嵌套许可证数量和处置。
- [copies.json](copies.json)：实际副本、权限、上传与恢复状态；只有 `restore-verified` 记录进入已验证副本字段。
- [rights-review.md](rights-review.md)：按许可范围分组的提取与发布队列，以及11个无根许可目录的逐项处理。
- [文献目录](../../literature/index.csv)、[外部仓库来源](../../src/repos/REPOSITORIES.md)、[既有存储说明](../../storage.json)：对应书目、代码来源和历史存储位置。

## 文件分工

| 内容 | 工程中的位置 | 处理方式 |
|---|---|---|
| 源码、配置、小型用例和接口 | `src/`、`tools/`、`setup/`、`deploy/` | 日常开发与回归 |
| 冻结协议、统计表、摘要和失败 | `protocol/`、`experiments/baselines/`、`experiments/reports/` | 保留原版本；新实验另建记录 |
| 压缩结果、数据集与历史数据库 | 见 `current/assets.csv`，历史身份仍见 `assets.csv` | 保留原路径，先建立可验证副本再迁移 |
| 论文与教材 | `papers/`、`literature/教材/` | 逐项核对版本、来源和分发授权，优先维护书目与原文链接 |
| 外部源码 | `src/repos/` | 保留许可证；运行环境用已锁定依赖，后续按实际引用提取补丁与下载清单 |
| 新运行、缓存与依赖 | `.local-state/`、`src/lab/runtime/`、虚拟环境 | 本地写入，定点忽略 |

## 许可核验

仓库尚无根 LICENSE，本轮未代选。第三方文本仍各自适用；`third-party.csv` 的类型来自文件文本识别。

已确认的具体条款线索：`src/repos/ACCEL/LICENSE`、`src/repos/PLR/LICENSE`、`src/repos/OptiMUS-v0.1/LICENSE` 为 CC BY-NC 4.0；`src/repos/InstanceSpace/LICENSE` 为 PolyForm Noncommercial 1.0.0。HGS-CARP、HGS-TDCARP 等目录有 GPL 文本，版本和覆盖范围见其原文件。

11个无根LICENSE目录已核为：**3项代码许可确认、2项项目许可声明确认、1项README占位、5项授权依据待补**。AutoML-Zero的67个源码/脚本文件头为Apache-2.0且69个文件与固定上游逐blob匹配；EoH-S为MIT；SISR七算法为Apache-2.0、12份lib许可为MIT；ASlib README声明GPLv3；QD-LHD README与pyproject声明MIT。QUBE仅有一份README，不列入运行代码许可队列。

具体范围与剩余五项见 [rights-review.md](rights-review.md)，固定提交与逐文件SHA见 [license-evidence.json](license-evidence.json)。`third-party.csv` 及 `current/third-party.csv` 仍按原文件名规则生成，其中 `no-root-license-found` 只表示文件位置；当前许可结论以这两份核定记录为准。SISR共19份许可路径，生成索引只数7份，是文件名匹配规则漏计12份后缀型许可，并非授权缺失。

PDF 的 `rights_status` 逐项记为 `review-required`。文件名、论文 DOI 或代码许可证不能说明某个 PDF 版本的分发权限；清单未将它们一概判为可分发或不可分发。

## 迁移步骤

1. 核对文件用途、权利和保存位置。私有研究资料使用保留原权限的私有存储。
2. 写入独立副本，核对逐文件 SHA256，再实际下载恢复到新目录。
3. 在索引填入可恢复位置、SHA256 和验证记录；完成后，才在后续提交中移出跟踪。

`assets.csv` 的外部位置字段来自 Drive 候选登记；独立 Space 副本的恢复结果见 [restoration.json](restoration.json)。Git blob SHA1 对齐仓库内容，SHA256 核对外部副本。历史重写另行决定。

## Drive 候选副本记录

已检查用户指定的私有「实验项目」文件夹，并创建 项目私有归档目录。目录权限沿用 owner / writer 两个用户条目，没有公开或域条目，权限未更改。已上传一份 826 字节的资产基线 JSON，文件名、大小、父目录和权限已读回确认；原始内容尚未完成下载恢复校验。

原文件夹中另发现 19,629,355 字节的 `URP-private-papers-20261023.zip` 和四份研究报告 / LaTeX 包。读取该 ZIP 的完整文件引用成功，但云端下载返回 `URLError: Tunnel connection failed: 403 Forbidden`；当次未恢复文件。独立副本的后续核验结果见下节，资产移出跟踪数为 **0**。

| 类别 | 当前方案 | 本轮处置 |
|---|---|---|
| 559 份论文、16 份教材 | 私有保存并逐文件核对来源、版本和权限 | 历史补充包已核验21篇，与当前559篇清单无同路径或同blob匹配；原件保留 |
| 294 个实验压缩包 | 按实验包保存 SHA256、报告与恢复位置，先校验内部路径再解包 | 含本次补计四个 `.tar.xz`；现有 Drive 证据包不自动视为同一批次，未移出 |
| BPP 输入与 NPZ 记录 | 作为只读回归数据保存，支持恢复后运行原验算 | 完整保留，源码检出清单继续包含必需 NPZ |
| CIFAR 五个训练批次 | 以 `data/cifar-training.SOURCE.json` 的五个 SHA256 核对独立副本 | 共 153,650,000 字节，未上传或移出 |
| 历史 SQLite | 独立副本恢复后检查字节与 SQLite 完整性、行数 | 本轮未上传数据库，保持只读档案 |
| 项目报告 / LaTeX | 优先核对已有的四份 Drive 文件，避免重复上传 | 独立Space副本4/4匹配当前Git blob；两PDF解析及两ZIP完整性通过 |
| 第三方源码与原生资产 | 按上游提交、许可、补丁和现有平台清单分别恢复 | 保留当前来源与许可证，不打包未知授权资料到公开发布 |

### 独立副本恢复结果

四份报告资产从已有 Space 副本恢复，与当前仓库的精确路径和 Git blob 全部匹配：项目报告 PDF 为110页、简报26页；两份 LaTeX ZIP 的300和48项清单哈希全部通过，CRC及成员路径检查通过。文件 SHA256 和逐项结果见 [restoration.json](restoration.json)。恢复位置标识留在私有交接记录，公开索引仅保存内容身份与验证结果。

19,629,355字节的历史补充论文包也已从独立副本恢复：23成员，包括21篇PDF和2份清单，逐篇大小及SHA256全部匹配。原恢复记录与当时2,439项清单的路径和blob匹配均为0；新补计的四项为实验压缩包，不改变该历史补充包的独立归类。

原Drive下载隧道403记录保留在 `copies.json`；独立副本恢复不改写原Drive下载状态。本轮资产移出数为0。

分支状态由 [当前工程索引](../current-index.md) 统一维护；这里仅维护资产身份、权利和恢复状态。

更新索引时输出到新目录：

```sh
python3 tools/index_assets.py --ref HEAD --output .local-state/asset-index
```

重新生成时会读取 `copies.json`：未恢复副本保持候选状态，已验证副本必须包含 URL、Git blob、SHA256 和恢复记录，且 Git blob 与所索引版本一致。

派生视图联合读取 `copies.json` 与 `restoration.json`，按当前树的精确路径、Git blob 与实际字节 SHA256 对齐；当前联合已验证副本为4（Drive 0、Space 4），历史补充包不计入现行2,443项资产。新生成的 `external_copy` 为登记文件内的证据引用，不复制私有恢复地址；`verified_providers` 与 `verified_external_copies_by_provider` 区分提供方，原Drive状态不变。恢复副本不等于迁移：`migrated_assets=0` 与 `known_migration_events=0` 仅表示这两份登记中已知迁移事件数，`migration_event_schema_supported=false` 明示当前schema尚无迁移事件契约，不是对所有历史移动的全面审计；路径已不在树中也不能推断已迁移。既有核验原文和生成清单保持不动，新输出须写入独立目录。

`tools/restore_materials.py` 的历史完整 ZIP 契约另见 [使用说明](../../setup/README.md#历史完整-zip-资料入口)与 [materials-archive.json](../../setup/materials-archive.json)。旧 ZIP 的历史指纹、平台 Release 目录、Drive 候选和 Space 恢复证据分别维护，不能相互替代。
