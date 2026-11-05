# 资产许可核定

核定版本：`007388d5e012f6a901945eda3b0abaf1aedfeb0d`，2026-10-30。逐文件SHA、固定上游版本和核验范围见 [license-evidence.json](license-evidence.json)。

原清单中的11个“无根LICENSE文件”已经分开处理：**3项确认具体代码许可，2项确认项目许可声明，1项确认只是占位文档，5项尚缺明确授权依据。** 无根文件是目录结构事实，不再作为“没有任何许可证据”的标签。

## 已确认的代码许可

|目录|许可和范围|版本匹配|使用时保留|
|---|---|---|---|
|`src/repos/AutoML-Zero`|67个源码/脚本文件头全部明确 **Apache-2.0**；README与.gitignore由对应上游项目许可管理|69/69个本地文件与 `google-research/google-research@d36068b845da4c2b24927fee2cea1e6ef98dadda` 的 `automl_zero/` 同路径同blob；[该提交根LICENSE](https://github.com/google-research/google-research/blob/d36068b845da4c2b24927fee2cea1e6ef98dadda/LICENSE) blob `d645695673349e3947e8e5ae42332d0ac3164cd7`|文件版权头、Apache正文及修改说明；提取包需附上对应许可，不必为本地目录缺根文件继续悬置分类|
|`src/repos/EoH-S`|`code/LICENSE` 是 **MIT**；顶层README也明确项目MIT，`code/README.md`同时标MIT|许可blob `706fe7b361c14d5b295a001a2cb2679f0da2b908` 与 [固定上游8310b056](https://github.com/FeiLiu36/EoH-S/blob/8310b056ab0d4ea83d194f1d9e8c7873c0a3b892/code/LICENSE) 完全相同|保留原MIT文本与版权行；该行只写2023未具名，不代填作者。数据/图像来源单列，不把代码声明自动替代外部资料权利|
|`src/repos/open-source-sisr-routing`|七算法子目录 `ctop/cvrp/main/pdptw/top/toptw/vrptw` 的LICENSE全部 **Apache-2.0**；六个lib目录的cxxopts与json全部 **MIT**|7份Apache文件同blob `55f9e58429ffd50a4c63ffec028500789886acbd`，与[官方仓库](https://github.com/hankarudova/open-source-sisr-routing)七分支固定SHA逐项匹配；12份lib文件仅两个独立blob，原文均已读，详JSON|按子树附Apache正文和两份MIT文本，保留版权/NOTICE/修改记录。已有基准instances的来源与数据条款独立管理|

SISR的19份许可不是19种许可证。cxxopts blob为 `9ac43d3e74413732736952be6ccda7c10619622c`，版权Jarryd Beck；json blob为 `f2c15fc47f6f8f0251dcd61cfbc818aa8b988fa5`，版权Niels Lohmann。原索引的嵌套计数7只识别文件名开头的LICENSE，漏计12份后缀型文件；不再据此增加不必要的授权询问。

## 已确认的声明与适用边界

|目录|核到的事实|后续仅处理的缺口|
|---|---|---|
|`src/repos/aslib_data`|README明确 **GPLv3**，blob `bbae808cc2f718b15b379b30ef6a9909933fc3d5`；116/116个本地文件与 [coseal/aslib_data@551b22beef8df17de59286b4822ef720e0aa4d6f](https://github.com/coseal/aslib_data/tree/551b22beef8df17de59286b4822ef720e0aa4d6f) 同路径同blob|分类改为“GPLv3项目声明”，不再称全未知；引用具体scenario时保留其readme/来源，不将ASlibScenario代码的BSD套入数据；正式分发时附GPLv3正文和项目声明|
|`src/repos/QD-LHD`|README明确MIT，`pyproject.toml`再次声明 `license={text="MIT"}`；两者blob分别 `5d2aa8aa457bc0bba9327eb652d3e0ee4169b8b9`、`eb6b2aecb319c06c56a863c0fc3be034b4e04017`|分类改为“MIT声明已核”；当前记录没有可归属的官方上游/真实作者，作者字段仅QD-LHD Authors。待原始提供者补作者和来源，不能把这个参考实现自动当已核官方论文代码|

## 无代码的占位项

`src/repos/QUBE` **只有README一个文件**，与 `zzjchen/QUBE_code@94245cdda8f21a7ceef0bea27d0312063cbbbbdf` 完全同blob。其Introduction、Usage、Requirements、Running均为TBD。因此它从“待解决运行源码许可”队列移出，保留为论文/项目线索；若将来取得实现，再按实际新文件核许可。没有因此删除原README，也没有授予该文档新许可。

## 仍需明确授权依据的五项

|目录|已完成核查|最小收口动作|
|---|---|---|
|`src/repos/DR-ALNS`|本地README和扩展setup无许可声明；来源记录固定 `RobbertReijnen/DR-ALNS@144e48f6912ad9967968561b6b0ce90cedcc14aa` 全树无license/copying/copyright命名文件；本地13/75文件同路径同blob，其余未同路径同blob匹配，不能称全树原样|原作者提供适用于该版本的许可/书面授权；代码引用范围确定后再核本地变更，不用论文发表或可clone代替授权|
|`src/repos/JAIR2026-FrugalAS`|README与 `stacs-cp/JAIR2026-FrugalAS@7a5727651a92fd2fa4960dcbbd7f6dab94130028` 同blob；当前上游根无许可；本地树也无许可命名文件|核大学数据页关联的Zenodo记录20095993许可及版本覆盖，或作者授权；本轮该记录网页不可访问，没有猜测条款|
|`src/repos/QDAIF`|17/17文件匹配 `qdaif/qdaif_scripts_data@9836aaf1a7703fdafb9560bd7a304ac1df4572fd`；完整树无许可命名文件，README无许可声明|该脚本/数据仓库作者确认许可。作者项目页“Code”链接的OpenELM为另一个MIT仓库，不能用它的MIT覆盖本地QDAIF脚本与数据|
|`src/repos/VRPAgent`|331/331文件匹配 `ai4co/vrpagent@c0e37da7dc5eb2e0ab05490ca4bc23adcd7576ce`；全树无许可命名文件；README仅说明早期生成启发式发布|作者确认这些生成启发式和样本种群的许可；“模型生成”不代替发布者授权|
|`src/repos/typesafe-router`|README与package.json均与 `TypeSafeAI/typesafe-router@4c6855ccfc92ff0e40a71661685c9ead327a3715` 同blob；package无license字段，`private:true`；上游根无许可|项目权利人给出适用许可；npm private仅是包发布控制，既不授予也不取消代码许可|

这五项保持来源快照与研究记录；当前发布/源码提取清单不把它们列为许可已确认组件。联系人或权利人确认由项目负责人发起，本轮未外发消息、未赋予root LICENSE。

## 本轮分发隔离核查与建议（未执行）

2026-11-01 核对已合并 main `4ee2c6dd`：上表五个目录不属于根、Lab 或前端声明的运行依赖；`setup/source-checkout.txt` 与原生安装器 `install.sh` 的稀疏检出排除 `src/repos/`，`.dockerignore` 也排除该目录。`.gitattributes` 目前只有文本/换行规则，没有源码归档的 `export-ignore`，这些规则不能视为 GitHub 自动源码包已经隔离。

建议后续最小变更仅在 `.gitattributes` 对上述五个待补授权目录添加 `export-ignore`，保留当前来源、许可核定和恢复索引；先在独立输出目录用 `git archive` 核对包内路径，再核对对应 GitHub 源码包。它只约束后续支持该属性的打包流程，不能删除旧发布包或 Git 历史，也不提供访问控制或补足授权。本轮只记录方案，没有修改归档属性、重打包、上传或代选根 LICENSE；其他论文、数据及第三方分发范围仍按原队列核定。

## 资产恢复保持独立

许可核定不改变副本状态。Drive 与独立 Space 副本分别记入 `copies.json` 和 `restoration.json`，当前四项报告资产有六份已验证提供方副本（Space 4、Drive 2），迁移 0；本次 Drive 两项成功、两项未分类 fetch 错误见 [独立回执](drive-restoration-20261101.json)，不把失败推断为 403 或权限拒绝。Space 历史和旧论文包 403 记录不改，旧被拒路径本次未访问。历史补充包与当前资产匹配为 0，见 [恢复记录](restoration.json)。论文 PDF、数据、模型和教材继续依据各自版本条款分类，不能由以上代码许可批量覆盖。
