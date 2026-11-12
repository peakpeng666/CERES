# 成熟求解器档案适配与共同问题比较

## 范围

在已发布的路线读取后端上做增量，显式增加 `vrptw-mature-solver-v1` 格式。直接读取原文件名和结构，不重命名或覆盖原档，不调用 OR-Tools、模型、源 checker 或任意实验代码。只在导入时用后端自有标准库算术重新计算连续路线见证。

兼容 `vrptw-frozen-v1` 与原 GET/POST API。五个基础包保留690指标、28附件，原240条路线的fingerprint不变。本页说明两类48实例档案；Solomon公共基准格式见[公共路线接口](PUBLIC_ROUTE_API.md)。

## 显式导入与身份

从CERES实验目录中的两份48实例归档解压后，在`src/backend`执行：

```sh
python3 -m backend --db backend_data/shared.sqlite3 import-routes \
  --format vrptw-frozen-v1 "$VRPTW_SOURCE_ROOT"
python3 -m backend --db backend_data/shared.sqlite3 import-routes \
  --format vrptw-mature-solver-v1 "$MATURE_VRPTW_SOURCE_ROOT"
python3 -m backend --db backend_data/shared.sqlite3 serve --port 4180
```

旧命令 `import-vrptw` 保持可用。新默认实验 ID 是 `vrptw-mature-20261007`，原为 `vrptw-20261007`。同一格式、同一输入集合、内容完全相同会复用版本；新证据内容追加不可变版本。两个格式不能使用同一实验 ID 覆盖彼此，输入集合变化也要求使用独立 ID（409）。直接通用包导入仍是原来的声明式存储功能，不能用它绕过格式适配器的校验。

新96个run沿用 `results.jsonl` 原生 ID，例如 `G12_tight__250ms`。唯一导航身份是 `(experiment_id, revision, run_id)`；不能只拼接 `instance.method`，否则两个预算会撞号。原240个run身份不变。

两档案具有同一 `inputs.json` 原字节和 SHA-256，48个实例不被创建成不同输入版本。每个实验保留自己的不可变证据字节，比较层按共享文件及逐实例哈希只引用48个逻辑输入；这不是声称数据库已做物理文件去重。

## 状态合同

新格式分别保留以下字段：

- `source_status`：原 `outcome`
- `solver_status`：OR-Tools状态码/名称、assignment_returned和outcome
- `execution_status`：运行终态；完成不表示找到方案
- `solution_status`：feasible / partial / invalid / no_solution / unknown
- `problem_feasibility`：来源于 reference 中原连续问题 exact oracle，独立于求解器状态

本档66条返回合法全服务方案，`solution_status=feasible`；30条ROUTING_FAIL保留 `no_solution_in_budget` 与 `solution_status=no_solution`。无返回时 routes、vehicle、audit和missing均为 null或不适用，不伪造空路线，也不把no_solution当作invalid或infeasible。原oracle的15无解实例仍在旧实验保持infeasible。相同实例在新实验可同时是“本次没有返回方案”和“参考oracle判定原问题不可行”，来源必须分开。

合成反例测试另覆盖“原oracle可行，但本次没有返回方案”，其solution_status仍是no_solution。合法全服务见证与oracle不可行标签冲突会拒绝导入。参考oracle可行记录必须有合法见证，导入时回放并检查其目标；这只是核对已记录见证，不重新证明最优性或不可行性。

## 真实证据与回放

适配器读取并保留原始 `results.jsonl`、`evaluated_results.json`、`map_backend.json`、输入、请求、协议、冻结哈希、验证、checker测试、checker源字节、环境版本、两档参数textproto、编码输入及reference输入/协议/冻结/路线。

固定源格式按其真实结构解析：map.instances[*]中包含input和runs；verification使用records_checked。版本1明确支持冻结的250/1000ms两档，不猜测别名或宽松转换其他格式。

全部66返回候选从原连续坐标重算欧氏距离、覆盖/重复/未知节点、容量、到达/等待/开始/离开、闭区间开始服务窗和返仓期限；完整结果与原audit核对。数值比较容差为abs=1e-9、rel=1e-12，约束违反判定沿用1e-9边界容差。原gap还根据回放距离和参考目标复核。更改保存时刻或gap但同步map/evaluated也不能骗过回放。

`checker.recomputed_at_import`标明候选是否实际回放；源checker实现哈希与 `replay_implementation=backend.route_replay-v1` 分列，源checker文件不会被执行。`checker.audit`为回放结果，`recorded_audit`保留档案值。

逐站load_before/load_after仍是累计服务需求；物理onboard_load保持null。`encoded_cumul_ranges`单独保留为整数模型可行范围，不能当作真实到达或服务时刻。objective_integer也不等于连续欧氏距离。

## GET读取

原端点不变，新实验同样支持：

- `/api/experiments/vrptw-mature-20261007/instances`
- `/api/experiments/vrptw-mature-20261007/runs`
- `/api/experiments/vrptw-mature-20261007/runs?solution_status=no_solution`
- `/api/experiments/vrptw-mature-20261007/runs/G12_tight__250ms`
- `/api/experiments/vrptw-mature-20261007/runs/G12_tight__250ms/vehicles/0`

run/vehicle含算法版本、参数哈希、明确的每次搜索wall-clock毫秒预算、solver状态和独立问题可行性来源。预算不是CPU毫秒，也不等于整个模型构建/提取/检查用时。revision选择和原字节附件下载保持兼容。

## 新只读比较

```text
GET /api/route-comparison?experiments=vrptw-20261007,vrptw-mature-20261007
GET /api/route-comparison?experiments=vrptw-mature-20261007
GET /api/route-comparison?experiments=vrptw-20261007,vrptw-mature-20261007&instance=G12_tight
GET /api/route-comparison?experiments=vrptw-20261007,vrptw-mature-20261007&revisions=1,1
```

支持1到8个实验/版本，省略revisions取最新；同一解析后的实验/版本不能重复选择，避免虚增计数。`instance`可选。POST拒绝405。

共同问题比较要求问题spec哈希、完整输入哈希、逐实例内容哈希和距离/时间语义一致，否则409。新的研究协议不同于原问题协议，整数编码也另有哈希；这些身份不被混成一份协议。

完整视图返回336个run、48个逻辑输入、7个算法/预算组。新算法250ms、1000ms各48运行、33全服务、15no_solution。旧启发式没有可匹配的冻结搜索预算，其budget值保持null，不能把CPU耗时当成已分配预算。

只有合法全服务路线才有 `full_service_distance` 和 `distance_minus_best_returned_full`。partial、no_solution、infeasible、invalid这些字段为null，不按0参与排名。best_returned_full仅指当前选择的同实例最短返回合法路线，不新增全局最优证明。

比较为描述性跨算法/预算对照，`same_known_budget=false`会明示预算不同或未知。结果不声称计算预算公平、统计显著或求解器在相同可行域里工作；所有距离来自共同连续问题的checker，整数编码及独立研究协议保留。旧POST `/api/compare`仍要求原八项口径完全一致，跨预算409规则没有放宽。

## 后续格式扩展接口

- 新档案必须有单独、明确的格式分支，不能偷偷修改冻结文件适配旧解析器
- 输出共同schema_version=1路线索引：instances、runs、semantics、summary、verification、sources
- 保留原生run身份、算法/预算、source_status、候选合法性和问题可行性各自的来源
- 明确problem spec / 输入 / 研究协议 / 编码身份；不同输入或语义必须隔离
- 当前连续Euclidean回放工具只适用于已声明合同，不能用于真实道路、其他距离定义或不同窗语义
- 第三类格式要加自己的显式适配与证据测试，不靠宽松别名兜底

## 验证

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
VRPTW_SOURCE_ROOT="$VRPTW_SOURCE_ROOT" \
MATURE_VRPTW_SOURCE_ROOT="$MATURE_VRPTW_SOURCE_ROOT" \
RESEARCH_CONSOLE_PACKAGE_ROOT="$PACKAGE_ROOT" \
python3 -m unittest discover -s tests -p 'integration*.py' -v
```

小型fixture仅有两输入/四个新run，明确为合成契约测试。真实集成缺少任何指定档案即失败，不生成替代研究数据。最终覆盖包括新旧并存、幂等/版本、双向ID隔离、同输入比较、输入/spec失配拒绝、重复选择拒绝、实际HTTP、来源文件不变和原五包回归。新DB、raw和测试日志不进入清洁源码包。

## 环境检查记录

该版本的41项单测、3项真实集成及独立审阅运行于Linux、CPython3.12.14。18个Python源码/测试文件另通过`ast.parse(feature_version=(3,10))`，新增标准库API未发现3.11+依赖；`Path.is_relative_to`从3.9起可用。Python3.10与Ubuntu22.04的实机执行未验证。

后端读取OR-Tools档案并用自有算术回放，无需安装OR-Tools。静态兼容证据记录为`python310_static_compatibility.json`。
