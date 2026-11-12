# CVRP工程验证任务

此接口把数值输入送入`src/research/src/ceres_research/cvrplib/runner.py`的`run_payload`，返回其真实执行状态、路线、用时和独立checker证据。每次请求使用独立子进程；服务按队列串行调用已评审的runner。不会执行上传脚本，也不接受文件路径、Python解释器、worker路径、模型端点、参考最优值或预先生成的候选路线。

## 本期范围

- 仅CVRP：首节点为仓库0，其余为必须服务的客户；车辆数约束为至多K条非空路线。
- 2到300节点（包含仓库），二维有限坐标的绝对值不超过10^9；需求与容量为整数，仓库需求为0。
- 距离采用EUC_2D：每条边为`floor(Euclidean distance + 0.5)`，目标是这些整数边长之和。
- 算法入口仅注册`savings`、`T`、`TRS`、`TSR`和可选`pyvrp`。研究目录中的其他已存算法和结果保留，不据此声称都能通过Jobs API运行。
- budget_seconds仅1或5；seed为uint32。前四个算法不使用seed，更换seed不构成独立随机重复。
- PyVRP仅在当前解释器安装0.14.0时可选。Linux以外仍可查询历史记录，但当前进程监督不可用。
- 坐标单位由输入给定，没有物理地图、米制变换、车体碰撞或运动学合同。路线可用于二维访问顺序展示，不能据此启动真实机器人。

这些任务统一标记`purpose=engineering_validation`，不会自动加入正式基线实验或历史实验包。原runner结果中的`purpose=acceptance_only`原样保留。

## 创建任务

```http
POST /api/jobs
Content-Type: application/json
```

下例是明确的工程测试输入：

```json
{
  "coordinates": [[0, 0], [1, 0], [2, 0]],
  "demands": [0, 1, 1],
  "capacity": 2,
  "K": 1,
  "algorithm": "savings",
  "seed": 0,
  "budget_seconds": 1,
  "scene_id": "example",
  "scene_revision": 1
}
```

七个求解字段必填。`scene_id`、`scene_revision`可省略：此时场景ID由输入哈希生成，版本为1。只有这七个数值/算法字段进入原runner，场景身份单独记账。

同一`scene_id + scene_revision`必须对应同一组coordinates、demands、capacity、K。输入变化而版本不变会返回409 `scene_revision_conflict`；增加版本或使用新的默认输入身份后可提交。算法、预算或种子变化不改变场景输入身份。

202响应表示任务已提交到SQLite。它包含：

| 字段 | 含义 |
|---|---|
| `id`、`sequence` | 本次尝试的唯一任务ID和队列序号 |
| `status` | queued / running / completed / failed / interrupted |
| `scene_id`、`scene_revision` | 固定输入的场景身份 |
| `input_sha256` | 四个问题字段的规范JSON哈希 |
| `payload_sha256` | 七个runner字段的规范JSON哈希 |
| `source_sha256`、`source_manifest` | 当前研究源码、Jobs实现、Python版本与PyVRP版本身份 |
| `request` | 实际被接受且不会再改变的七字段输入 |
| `created_at`、`started_at`、`finished_at` | 实际接收、开始和终结时间；未发生时为null |
| `result` | 原runner结果；未得到执行结果时为null |
| `error` | 错误代码、类型或说明；无错误时为null |
| `result_status`、`feasible`、`cost` | 可快速显示的结果字段；未知不填0 |

每次成功接收都会建立新尝试记录；重复提交不会覆盖旧任务。队列最多保留32个未终结任务（含正在执行的一个），满时返回503 `job_queue_full`，未被接受的请求不算一次求解尝试。

## 状态和失败分母

| 任务状态 | 解释 |
|---|---|
| `queued` | 已持久化，尚未调用runner |
| `running` | 已记录开始时间并由串行worker领取 |
| `completed` | runner正常结束或到达预算；仍须检查result.feasible |
| `failed` | runner异常、构造失败、数值或方案校验失败，或执行接口抛出错误 |
| `interrupted` | 服务停止或重启时仍未完成；保留已接收输入，不自动重新执行 |

`completed`不等于存在合法方案。`TIMEOUT_NO_SOLUTION`、`NO_SOLUTION`保留`feasible=false`、`routes=null`、`cost=null`；没有返回方案不构成问题不可行的证明。`TIMEOUT_WITH_FEASIBLE_INCUMBENT`只保留预算截止前实际收到、独立checker通过的候选。

所有已接收任务都计入列表的`counts`与`total`，包括排队、失败和中断。每次启动在持有数据库独占执行锁后，将上次残留的queued/running记录转为interrupted；不会悄悄重新求解或复用一个新结果覆盖旧尝试。同一数据库只能有一个任务服务所有者。

正常SIGINT/SIGTERM停止时，当前有界任务完成后关闭服务，其余已接收任务保留为中断。进程被强制结束时，下一次启动执行上述恢复。数据存储故障不会被伪造为完成结果。

## 查询和回放

```http
GET /api/algorithms
GET /api/health
GET /api/jobs?limit=100&offset=0
GET /api/jobs/{id}
```

`algorithms`返回本期算法的available、reason、deterministic和版本，以及输入上限与预算限制。health只在执行模块可用时声明`executes_experiments=true`，并限定`execution_scope=bounded_cvrp_engineering_validation`。

列表按序号降序返回，limit为1到500，offset非负；状态计数始终覆盖数据库中的全部任务，不随当前页缩小分母。详情保留完整result，包括原始worker输出、各检查点验算、实际CPU/墙钟时间、截止时间和返回路线。

result.routes中的每条路线包含首尾仓库0。前端以request.coordinates按节点索引绘制折线；缺少方案时不绘制假路线。`cost`为整数EUC_2D目标，不能用连续绘图折线长度替代。现有历史`route-scene`接口继续采用其原有单位与来源合同。

## 预算与可复现性

原runner预算从请求序列化、子进程启动前开始，覆盖导入、建模、初始化、搜索和检查点接收；独立checker与进程清理时间另行保留。预算不是整个HTTP请求耗时，排队时间也不计入单次求解预算。

任务提交和实际执行前均核对源码与依赖版本身份。服务存活期间源码改变会拒绝新执行，要求重启后提交新的尝试；不会把内存中旧模块与磁盘新worker混记为一个版本。版本与源码哈希支持复验，不提供最优性或物理可执行性的证明。

## 验证

从`src/backend`运行：

```sh
../../.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

`test_jobs.py`包含实际HTTP→SQLite→数值runner→独立checker闭环、四种基础算法的真实小例、可用时的PyVRP真实小例、真实构造失败、串行时间顺序、场景版本冲突、来源变化、队列上限、失败与无返回区别，以及真实子进程强制退出后的持久化恢复。故障注入只存在于单元测试，不是HTTP参数或生产开关。所有输入均明确为合成工程测试，临时数据库不会成为正式实验记录。
