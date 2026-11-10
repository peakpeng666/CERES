# CERES 任务智能体：架构、接口与可验证执行

本模块把自然语言作业需求转换为**经用户确认的任务合同**，再通过受校验的工具调用现有的建图、推荐、求解、独立验算与田间仿真服务。设计遵循 LLM-Modulo 原则（Kambhampati et al., ICML 2024）：模型负责理解、规划与解释，可行性由外部可靠检查器判定；执行流程采用状态驱动的分阶段工作流（StateFlow, Wu et al., 2024），工具调用循环沿用 ReAct 范式（Yao et al., ICLR 2023）。

## 1. 数据模型

| 对象 | 模式 | 位置 | 要点 |
|---|---|---|---|
| 任务合同 | `urp.task-contract/1` | `contract.py` | 22 个字段，统一使用米、秒、平方米；每个值记录来源（原话 `user_text`、模型理解 `llm`、人工修改 `user_edit`、默认值 `default`、批准的修复 `repair`）与依据原文；无法表达的要求原样列入 `unsupported`；修改后自动回到草稿，须重新确认 |
| 会话 | `urp.agent-session/1` | `session.py` | 对话、合同、计划、工具调用、模型调用、用量、任务地图与运行编号、待批准项；存于 `runtime/agent.sqlite3`（与 `lab.sqlite3` 分离） |
| 理解评测集 | `urp.agent-nlu/1` | `evals/dataset.py` | 合成（模板生成）与人工编写分开记录；按底本（family）划分开发集与留出测试集 |

合同字段的约束（类型、范围、单位、选项）只在 `contract.FIELDS` 定义一次：后端校验、模型工具的 JSON Schema 与前端编辑器均由此派生或与之对应。用户已明确给出的值，模型在没有新原话依据时不能覆盖。

## 2. 执行流程

```
理解需求 ──► 确认合同 ──► 建立任务地图 ──► 推荐算法 ──► 求解对比 ──► 独立复核 ──► 物理预演 ──► (诊断与修复) ──► (田间仿真) ──► 结果报告
  规则解析      用户确认     build_scenario   recommend     solve        verify_run     physics_check  diagnose_run       start_simulation
  + 模型理解    或自动确认                                                (强制)          (强制)         propose_task_change  (须批准，守到结束)
```

- **分阶段工具集**：理解阶段只开放 `update_task_contract / ask_user / find_real_fields`；执行阶段才开放建图、求解与仿真工具。越阶段的调用被拒绝并记录。
- **强制复核**：报告前，运行器对本会话所有报告可行的方案再次运行独立检查器（`diagnose.recheck`），与存档判定比对；模型是否调用 `verify_run` 不影响这一步。
- **物理预演**：每个通过复核的方案都在 MuJoCo 中用同一路径跟踪器和车辆参数执行一遍（`physics_check`，数秒，不启动 ROS，无需批准）。模型遗漏时由运行器补上，结果进入报告依据。物理预演是另一个引擎的物理仿真，不等于实车结果。
- **守着仿真跑完**：批准后运行器留在 `simulate` 步骤，直到 Gazebo 会话结束，报告写的是实际结果而不是“已启动”。成功时用会话冻结的 `plan.json` 在 MuJoCo 重放，把两个引擎的轨迹差写进报告。取消会停止会话；暂停只是离开，仿真继续，继续后接着等它结束。
- **原因分类**（`diagnose.py`）：任务本身不可行（不连通、单点需求超容量、单趟总需求超车队容量）、方法不适用、预算不足、软件实现错误（求解器异常、检查器拒绝“可行”结果、复核与存档不一致）。前两类允许自主换方法或加预算；改变任务本身的修复（多趟、加车、提高容量）须经用户批准。
- **数值核对**：模型撰写的报告逐个提取数值，与执行依据比对；依据中不存在的数值在界面上单独标出。
- **无模型时**：同一流程由确定性规则完成（规则解析、推荐集合求解、规则修复），会话与报告明确标注“规则流程”。

物理预演对多车方案跑两遍：逐车执行和全部车辆同时运行。两组工期并列写进报告，目标是时间时按 `predicted_makespan_s` 排序，它含转向、作业和路线相遇等待。

## 3. 控制与恢复

| 操作 | 行为 |
|---|---|
| 暂停 / 继续 | 当前工具调用结束后暂停；继续时从保存的阶段与上下文恢复 |
| 取消 | 放弃进行中的模型调用并终止求解进程组；已有记录保留 |
| 重试 | 对失败、取消或中断的会话重新执行当前阶段 |
| 修改合同 | 表单修改或自然语言修改；合同回到草稿，重新确认后执行；只改车队、容量、目标时原地更新任务地图，改场地、任务点或障碍时另存新地图 |
| 批准 / 拒绝 | 任务修改与 Gazebo 仿真均需批准；拒绝会记录并告知模型不得重复提出 |
| 送入仿真 | 报告之后用户可点「送入 Gazebo 仿真」，这一下就是批准，调用记录记为 `user_approved`；待确认或待批准时不可用 |
| 接管 | 用户可用地图编辑器中的地图替代“建立任务地图”，或把规划工作区中的求解结果接入本会话 |
| 网关重启 | 执行中的会话标为“已中断”，可继续 |

每轮的模型调用（20 次）与工具调用（30 次）有上限；超过即停止并报告。

## 4. 模型接入

做法和 opencode 一样：服务商和模型来自公开的 [models.dev](https://models.dev) 目录，用户选一家，粘贴 API Key，就能用。模型地址写成 `服务商/模型`，比如 `deepseek/deepseek-v4-pro`。

| 模块 | 内容 |
|---|---|
| `catalog.py` | 目录。随代码提供一份精简快照（`data/model-catalog.json`，用 `python -m urp_lab.agent.catalog` 重新生成），桌面应用每天至多刷新一次，缓存在 `~/.config/urp-workbench/model-catalog.json`。只保留一个 Key 就能走通的服务（OpenAI 兼容或 Anthropic 接口）和能调用工具的模型；本地运行时、云账号登录和限定编程工具的订阅端点不列入 |
| `providers.py` | 已连接的服务、选中的模型和自定义接口。Key 经 `llm.set_key` 写入私有文件，这里没有函数会返回 Key |
| `llm.py` | 一次具体调用：`openai` 与 `anthropic` 两种接口，超时、仅对传输错误 / 429 / 5xx 的有限重试、取消、用量与费用记录 |

- **连接**：`POST /providers/{id}/connect`。先用服务商免费的模型列表接口校验，只有明确的 401 才拒绝；服务商不支持校验或暂时连不上时保存并标为未验证，调用时再判断。第一次连接时自动选中该服务的主力模型。
- **Key 存放**：`~/.config/urp-workbench/credentials.json`（可用 `URP_LLM_CREDENTIALS` 覆盖），权限 600，原子写入。接口、日志、会话和导出都不返回 Key，掩码也不返回。服务目录里声明的环境变量（如 `DEEPSEEK_API_KEY`）也算连接，但界面粘贴的 Key 优先。
- **不会替用户花钱**：`llm.json` 里已有的端点（比如订阅端点）会列出来，但不会被自动选中；只有用户选了才用。
- **自定义接口**：任何 OpenAI 兼容服务，写入 `llm.json` 的 `providers`，Key 同样进 `credentials.json`。
- **推理模型**：目录标明需要回传推理内容的模型，工具调用过程中把它的 `reasoning_content` 按原字段带回；不接受 `temperature` 的模型不发这个参数。
- **费用**：目录里的单价按美元每百万 tokens 计入会话用量，是估算，以服务商账单为准。

## 5. HTTP 接口（`/lab/api/agent`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/providers` | 服务目录、已连接的服务及其模型、当前模型（不含 Key） |
| GET | `/providers/{id}/models` | 一家服务的模型清单 |
| POST | `/providers/{id}/connect` | `{api_key}`，校验后保存 |
| POST | `/providers/{id}/disconnect` | 删除这家服务的 Key；自定义接口连同配置一起删除 |
| POST | `/providers/custom` | `{name, base_url, api_key, models?}` |
| POST | `/providers/refresh` | 立即更新模型目录 |
| POST | `/model` | `{model: "服务商/模型"}`，设为当前模型 |
| GET | `/sessions` | 会话列表 |
| POST | `/sessions` | `{text?, mode: auto|hybrid|llm|rule, provider_id?, auto_confirm?}`；`provider_id` 是 `服务商/模型`，省略时用当前模型；模型不可用时 `auto` 退回规则流程并注明，`llm/hybrid` 返回 409 |
| GET | `/sessions/{id}` | 完整会话（不含模型内部上下文） |
| POST | `/sessions/{id}/messages` | `{text}`，启动一轮；执行中返回 409 |
| POST | `/sessions/{id}/control` | `{action: pause|resume|cancel|retry|confirm|approve|reject|takeover|simulate, step?, scenario_id?, run_ids?, run_id?}` |
| PATCH | `/sessions/{id}/contract` | `{fields: {字段: 值}}`，来源记为人工修改 |
| PATCH | `/sessions/{id}` | `{title?, pinned?, archived?}` |
| GET | `/harness` | 工具、阶段闸门与每轮上限 |
| GET | `/capabilities` | 算法、仿真器、学习模型、引擎迁移，各自说明状态；没有接入的不会显示为可用 |
| GET | `/jobs` | 正在运行的求解、课题、物理运行、田间仿真、TurtleBot4 |

状态变化通过 SSE 事件 `agent_session` 推送。

## 6. 评测

```sh
cd src/lab
.venv/bin/python -m urp_lab.agent.evals.dataset                     # 重建评测集并输出 SHA256
.venv/bin/python -m urp_lab.agent.evals.nlu --mode rule  --split dev  --out /新的绝对路径
.venv/bin/python -m urp_lab.agent.evals.nlu --mode hybrid --split dev --out /新的绝对路径
```

执行评测：`.venv/bin/python -m urp_lab.agent.evals.execution --mode hybrid --out /新的绝对路径`（5 个端到端情景）。当前结果与版本对照见集成报告最上方。

指标：字段微平均精确率 / 召回率 / F1 与逐轮完全匹配；必要澄清召回与多余提问率；未支持要求的检出率与误报率；多轮修改中未改字段的保持率；提示注入复述率；每轮模型调用、token 与延迟。开发集用于提示与流程调整，留出测试集只在定稿后运行一次。结果存于 `experiments/agent-evals/`。

## 7. 能力清单

`capabilities.py` 把 harness 能用的东西列成一份清单，智能体（`list_capabilities`、`describe_algorithm`）和界面（设置 → 能力）读同一份。新增一个仿真器、学习模型或迁移任务，在这里加一项并说明状态；长任务再进 `jobs()`，界面的后台任务列表自动显示。各后端保留自己的队列和记录，这里只是汇总。

## 8. 研究扩展接口

- **运行时选择与修复**（本模块）：规则推荐、学习型选择器与模型辅助推荐共享同一合同、预算与验算口径，可在同一任务上并列比较；修复动作由 `diagnose.repairs` 枚举，策略可替换。
- **算法本身的训练或演化**（`urp_lab/evolve`、`src/research/generator`）：与本模块分开设计和评测，效果证据不混用。
- 可替换点：提示词模板、模型适配层、工具集、诊断与自愈修复策略、基准评测集。

## 参考文献

- Kambhampati, S. et al. Position: LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks. ICML 2024.
- Wu, Y. et al. StateFlow: Enhancing LLM Task-Solving through State-Driven Workflows. COLM 2024.
- Yao, S. et al. ReAct: Synergizing Reasoning and Acting in Language Models. ICLR 2023.
- Shinn, N. et al. Reflexion: Language Agents with Verbal Reinforcement Learning. NeurIPS 2023.
- Chen, Y. et al. AutoTAMP: Autoregressive Task and Motion Planning with LLMs as Translators and Checkers. ICRA 2024.
