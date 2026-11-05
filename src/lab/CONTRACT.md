# URP Lab 集成契约（开发者用）

本文件固定科研面板各进程之间的数据结构与接口。实现必须遵守；如需改动，同步修改本文件、`src/frontend/src/lab/types.ts` 与测试。

## 0. 进程与端口

| 进程 | 解释器 / 环境 | 端口 | 说明 |
|---|---|---|---|
| 网关 `urp_lab` | `src/lab/.venv`（uv，CPython 3.12） | 127.0.0.1:4100 | 托管面板（`src/frontend/dist`）、`/lab/api/*`、反向代理 `/api/*` → 4180、SSE |
| 档案与数值任务后端 | 根目录 `.venv`（uv，CPython 3.12） | 127.0.0.1:4180 | 档案查询与有界 CVRP Jobs；应用数据库为 `.local-state/research.sqlite3`，保留冻结原库；由网关启动/停止 |
| 求解工作进程 | `src/lab/.venv` 子进程 | — | 每次求解独立子进程，带墙钟预算；项目模块通过 `sys.path` 引入 `src/research/src`，不修改研究源码 |
| 仿真会话 `urp_sim_session.py` | 系统 `/usr/bin/python3`（3.10）+ ROS 2 Humble rclpy | 127.0.0.1:4191 | 官方 TB4 + Gazebo Fortress + Nav2 + 可选 GUI/RViz；复用 `setup/2026-10-15/deployment` 的 TF 修补、GUI 插件保护、有序桥接关闭 |

ROS 环境变量通过 `bash setup/profile.sh simulation …` 设定（CycloneDDS、`ROS_DOMAIN_ID=139`、`ROS_LOCALHOST_ONLY=1`）。科研 Python 与 ROS Python 不互相导入。

运行目录：`src/lab/runtime/`（git 忽略）——`logs/`、`lab.sqlite3`、`sim/<session_id>/`、`exports/`。

## 1. 坐标系与来源（必须在数据和界面中显式区分）

- `frame.kind = "benchmark"`：基准算例坐标，**无物理单位**（`units: "unitless"`），成本单位按算例声明（如 `synthetic_integer_cost`）。不能直接送入仿真。
- `frame.kind = "map"`：ROS `map` 坐标，单位米（`units: "m"`），附官方地图（`world`、`map_yaml`）。转弯路网成本为整数厘米当量（`cost_unit: "cm_equivalent"`：路段长度 cm + 转弯惩罚 cm 当量）。
- 来源标签 `source`：`"project"`（URP 项目算法/检查器，给出文件路径）、`"third_party"`（PyVRP、OR-Tools、Fields2Cover，给出版本）、`"panel_baseline"`（面板内置的简单基线，非项目研究成果）、`"nav2"`（Nav2 自身规划/控制）。
- 仿真执行区分：`follow_path`（Nav2 控制器跟踪 **求解器几何路径**，不调用 Nav2 全局规划器）与 `nav2_waypoints`（只给出任务顺序，**路径由 Nav2 NavFn 规划**）。机器人从当前位置到路线起点的"接驳段"总是 Nav2 规划，单独记录。
- 实际轨迹：`ground_truth`（Gazebo 真值，world 坐标；官方地图与 world 对齐）与 `amcl`（定位估计，map 坐标）分开记录。

## 2. 数据模型（JSON）

### 2.1 Scenario

```jsonc
{
  "id": "farm-rows-warehouse",
  "title": "仓储货架通道 · 作物行代理",
  "summary": "…",
  "problem_class": "turn_routing" | "euclidean_cvrp" | "coverage",
  "frame": {"kind": "map", "units": "m", "world": "warehouse", "map": {"yaml": "/opt/…/warehouse.yaml", "resolution": 0.03, "origin": [-15.1,-25.0,0], "width": 1006, "height": 1674, "image": "/lab/api/maps/warehouse.png"}}
         | {"kind": "benchmark", "units": "unitless"},
  "cost_unit": "cm_equivalent" | "synthetic_integer_cost" | "euclidean" | "m",
  "provenance": {"generator": "urp_lab.scenarios.field:warehouse_rows", "params": {...}, "seed": null, "note": "…"},
  "sim": {"supported": true, "world": "warehouse", "spawn": {"x":0,"y":0,"yaw":0}, "reason": null},
  "instance": { /* problem-class payload, §2.2 */ },
  "editable": {"tasks": true, "depot": true, "capacity": true, "demands": true},
  "layers": { /* 仅用于绘制的附加几何，可选：row_polygons, headland, obstacles, field_polygon … */ }
}
```

`GET /lab/api/scenarios` 返回不含 `instance` 的摘要列表（含 `id,title,summary,problem_class,frame,sim.supported,task_count,node_count`）。

### 2.2 Instance payloads

**turn_routing**（与 `src/research/src/urp_research/turn_modes` 输入格式一致，另加几何）：
```jsonc
{"depot": 0, "capacity": 6,
 "customers": [{"id": 0, "node": 12, "demand": 1, "label": "A3"}],
 "arcs": [[u, v, cost_int]], "forbidden_turns": [[u, v, w]], "turn_penalties": [[u, v, w, penalty_int]],
 "coordinates": {"0": [x, y]},            // 键为节点 id 字符串
 "node_kinds": {"0": "depot"|"headland"|"row"|"grid"},   // 可选
 "units": "cm_equivalent", "service_heading_reset": false, "depot_reset": "between_routes_only", "fleet_limit": null}
```
**euclidean_cvrp**（与 `cvrp_selection` 一致：下标 0 为仓库）：
```jsonc
{"coordinates": [[x,y], …], "demands": [0, d1, …], "capacity": 30, "depot": 0,
 "fleet": "unlimited_identical_vehicles", "objective": "sum_unrounded_euclidean_distance", "labels": ["D", "1", …]}
```
**coverage**：
```jsonc
{"field": [[x,y], …],            // 外边界多边形（米），逆时针，不闭合
 "obstacles": [[[x,y],…]],         // 可选内部障碍
 "headland_width": 0.8, "swath_width": 0.5, "turn_radius": 0.3, "robot_width": 0.35,
 "start": [x, y], "route_pattern": "boustrophedon"|"snake"|"spiral", "angle_deg": null}
```

### 2.3 Algorithm catalog entry (`GET /lab/api/algorithms`)
```jsonc
{"id": "urp.turn_mode_dp", "name": "转弯模式分层 DP（精确）", "problem_classes": ["turn_routing"],
 "family": "exact"|"metaheuristic"|"heuristic"|"baseline"|"coverage"|"relaxation",
 "source": "project"|"third_party"|"panel_baseline", "origin": "src/research/src/urp_research/turn_modes/solver.py" | "PyVRP 0.14.0",
 "turn_aware": true, "guarantee": "optimal"|"optimal_for_relaxation"|"none",
 "limits": {"max_tasks": 12}, "available": true, "unavailable_reason": null,
 "params": [{"name": "time_limit_s", "type": "number", "default": 5, "min": 0.5, "max": 120, "label": "时间预算 (s)"}],
 "description": "…"}
```
固定 id（`pyvrp.hgs` 为历史 API 标识；已安装的 PyVRP 0.14.0 实际运行 IteratedLocalSearch，显示名和来源使用 ILS，不把它归类为 HGS）：
- turn_routing：`urp.turn_mode_dp`、`urp.reset_dp`（忽略进入方向的松弛，可能违反转弯约束）、`urp.raw_oracle`（独立原图精确搜索）、`pyvrp.hgs+urp.layered`、`ortools.gls+urp.layered`（第三方元启发式在松弛距离上给出分配与顺序，再由项目 `turn_modes/evaluate_candidate.py` 分层 DP 实现转弯可行路线）。
- euclidean_cvrp：`pyvrp.hgs`、`ortools.gls`。旧 `urp.js.*` 尚未纳入此网关目录。
- coverage：`f2c.boustrophedon`、`f2c.snake`（可选 Fields2Cover；版本和 native 指纹必须匹配 vendor 清单，否则 available=false 并说明原因）、`panel.boustrophedon`（面板基线，Shapely 实现，允许原地转向）。F2C 支持单个凸内缩作业区，采用原生 Dubins 转弯、0.02 m 离散；独立检查完整足迹、采样曲率及作业区覆盖。拒绝的几何保留在 raw_result，不能进入仿真。

### 2.4 Solve request / Solution
```jsonc
// POST /lab/api/solve
{"scenario_id": "…", "instance": {…可选：用户编辑后的 instance；省略则用场景原始 instance…},
 "algorithms": [{"id": "urp.turn_mode_dp", "params": {"time_limit_s": 10}}], "label": "可选备注"}
// → 202 {"job_id": "job_…", "runs": ["run_…", …]}
```
Solution（`GET /lab/api/runs/{run_id}`）：
```jsonc
{"run_id": "run_20261015T…_ab12", "job_id": "job_…", "scenario_id": "…", "problem_class": "…",
 "instance_sha256": "…", "frame": {…同 Scenario.frame…}, "cost_unit": "…",
 "algorithm": {"id": "…", "name": "…", "source": "…", "origin": "…", "version": "…"}, "params": {…},
 "state": "queued"|"running"|"done"|"error"|"timeout"|"canceled",
 "status": "optimal"|"feasible"|"infeasible"|"no_solution"|null,
 "objective": {"value": 1234, "unit": "cm_equivalent", "lower_bound": null},
 "routes": [{"vehicle": 0, "nodes": [0, 3, …] | null, "sequence": [task ids], "services": [{"task": 0, "position": 3}],
             "polyline": [[x,y], …], "cost": 512, "load": 4, "mode_chain": […]|null}],
 "check": {"checker": "src/research/src/urp_research/turn_modes/checker.py", "source": "project",
           "valid": true, "errors": [], "computed_cost": 1234, "details": {…}},
 "map_check": null | {"checked": true, "collision_free": true, "min_clearance_m": 0.71, "robot_radius_m": 0.175,
                      "violations": [{"route": 0, "segment": 5, "point": [x,y], "clearance_m": 0.1}]},
 "metrics": {"cost": 1234, "length": 52.3, "length_unit": "m"|"unitless", "n_routes": 2, "n_tasks": 6,
             "turns": 7, "turn_penalty_total": 300, "forbidden_turn_violations": 0, "wall_s": 0.21, "cpu_s": 0.2},
 "log": "最后 4 KB 标准错误/输出", "error": null,
 "created_utc": "…", "finished_utc": "…", "label": null}
```
规则：不可行/无解时 `routes: []`、`objective.value: null`，绝不编造路线；`check` 必须由独立检查器给出（未检查则 `check: null` 并在界面显示"未检查"）。地图坐标场景必须给出 `map_check`。

### 2.5 Recommendation（`POST /lab/api/recommend {scenario_id, instance?}`）
```jsonc
{"problem_class": "turn_routing", "policy": "rules-v1",
 "features": [{"name": "任务数", "key": "n_tasks", "value": 6, "unit": null}],
 "learned_selector": {"id": "urp.cvrp_selection.knn_crc"|"lab.cvrp.knn_crc.v1", "status": "unavailable"|"available"|"not_applicable"|"out_of_scope",
                      "reason": "训练数据未恢复：缺少 data/research/cvrp-selection-crc/data/raw.jsonl", "required": ["…路径…"], "prediction": null},
 "ranking": [{"algorithm_id": "urp.turn_mode_dp", "rank": 1, "applicable": true, "suitability": "推荐"|"可用"|"不适用",
              "reasons": ["任务数 6 ≤ 12，精确 DP 可在预算内求得最优", "…"], "suggested_params": {"time_limit_s": 10}}],
 "recommended": "urp.turn_mode_dp", "explanation": "…一句话…"}
```
默认按实例特征与算法条件使用 `rules-v1`。本地 CVRP 原型 `lab.cvrp.knn_crc.v1` 有独立的 60/30/30 训练/校准/测试记录，模型状态为 evaluated 且指纹正确时，在已评估的规模/容量/需求范围内使用 `knn-crc-local-v1`；额外返回 prediction、model_sha256、budget_s、evaluation 和 scope。范围外回退规则。测试集全部使用固定 PyVRP，零平均增益，不能声称学习推荐优于基线。原历史研究数据仍未恢复。每次 Solution 的 `recommendation_at_submission` 固化推荐快照、实际提交算法及预算匹配状态；不推断用户是否采纳。

### 2.6 Services（`GET /lab/api/services`）
```jsonc
{"services": [
  {"id": "gateway"|"archive"|"solver"|"ros"|"sim"|"rviz"|"gazebo_gui"|"fields2cover",
   "name": "档案后端", "kind": "process"|"check",
   "state": "running"|"stopped"|"starting"|"stopping"|"ready"|"degraded"|"failed"|"unavailable",
   "detail": "…", "pid": 123|null, "port": 4180|null, "url": "…"|null, "started_utc": "…"|null,
   "controls": ["start","stop","restart"], "log": "archive"|null,
   "facts": [{"label": "Python", "value": "3.12.13"}], "known_issues": ["…"]}]}
```
`POST /lab/api/services/{id}/{start|stop|restart}`（JSON body 可选，如 sim：`{"world":"warehouse","gui":true,"rviz":true}`）。
`GET /lab/api/logs/{log}?tail=400` → `{"log": "archive", "path": "…", "lines": ["…"]}`；`GET /lab/api/logs` → 可用日志列表。

### 2.7 仿真
- `GET /lab/api/sim/state` → SimState：
```jsonc
{"session_id": "sim_…"|null, "phase": "stopped"|"starting"|"undocking"|"localizing"|"nav2_starting"|"ready"|"executing"|"stopping"|"failed",
 "world": "warehouse"|null, "spawn": {…}|null, "components": {"sim": "running", "gui": "off"|"running", "rviz": "off"|"running", "localization": "active", "nav2": "active"},
 "robot": {"t_sim": 123.4, "ground_truth": [x,y,yaw]|null, "amcl": [x,y,yaw]|null, "speed": 0.2} | null,
 "execution": SimRun|null, "errors": ["…"], "warnings": ["…"], "stages": [{"name":"…","wall_s":12.3}],
 "known_issues": [ /* 当前会话实际适用的问题；不从历史报告硬编码成功或失败 */ ]}
```
- `POST /lab/api/sim/session {"world": "warehouse", "spawn": {...}?, "gui": false, "rviz": true, "localization":"amcl"}` 启动；localization 默认为 amcl，可选 ground_truth（显式的理想定位实验，AMCL 仅旁路观测，不能用于证明传感器定位能力）。`DELETE /lab/api/sim/session` 有序关闭；`POST /lab/api/sim/gui {"on": true}`，`POST /lab/api/sim/rviz {"on": true}`。
- `POST /lab/api/sim/runs {"run_id": "run_…", "mode": "follow_path"|"nav2_waypoints"}` → 202 SimRun；`GET /lab/api/sim/runs`、`GET /lab/api/sim/runs/{sim_run_id}`、`POST /lab/api/sim/runs/{id}/cancel`。不适用（基准坐标、世界不一致、地图检查不通过且模式为 follow_path）返回 422 `{"error":"not_simulatable","message":"…"}`；已运行世界不一致为 409 `world_mismatch`。
- SimRun：
```jsonc
{"sim_run_id": "sim_run_…", "run_id": "run_…", "session_id": "…", "world": "warehouse", "mode": "follow_path",
 "state": "queued"|"approach"|"executing"|"succeeded"|"failed"|"canceled",
 "localization": "amcl"|"ground_truth", "navigation_profile": {…参数文件、来源 SHA256、覆盖值与实测参数…},
 "controller": "FollowPath"|"FollowCoverage", "progress": {"leg":1,"legs_total":5},
 "sources": {"route_plan": "URP · 转弯模式分层 DP (src/research/…)", "approach": "Nav2 NavigateToPose (NavFn+RPP)", "tracking": "Nav2 FollowPath (RPP 控制器)"},
 "planned": {"path": [[x,y],…], "length_m": 52.1},
 "tasks": [{"task": 0, "label": "A3", "xy": [x,y], "state": "pending"|"reached"|"missed", "t_sim": null, "distance_m": null}],
 "actual": {"ground_truth": [[t_sim,x,y,yaw],…], "amcl": [[t_sim,x,y,yaw],…]},
 "nav2": [{"action": "follow_path", "status": 4, "status_text": "SUCCEEDED", "t_sim": 12.3}],
 "metrics": {"duration_sim_s": 210.4, "duration_wall_s": 212.0, "actual_length_m": 53.0, "cross_track_mean_m": 0.06,
             "cross_track_max_m": 0.21, "cross_track_p95_m": 0.15, "final_error_m": 0.12, "tasks_reached": 6, "tasks_total": 6},
 "error": null, "report": "src/lab/runtime/sim/<session>/runs/<id>.json", "created_utc": "…", "finished_utc": "…"}
```
任务"到达"判定：真值位置进入任务点 `task_radius_m`（默认 0.35 m）且按计划顺序；缺失数据填 null，不插值伪造。终态必须同时含 `finished_utc` 和最终指标才写入数据库，取消状态过渡期间继续轮询。metrics 额外包含 actual_map_check 和 planned_to_actual_max_m（计划几何到实际轨迹的最大距离）；覆盖任务含 actual_working_coverage_fraction、actual_field_coverage_fraction、actual_footprint_in_field、coverage_required_fraction（0.90），不以 0/0 访问任务作为成功依据。

点到点任务使用其声明的 `goal_tolerance`（米），而非上述默认任务半径。终态独立从跟踪段末条真值复算 `independent_goal_error_m`，给出 `goal_tolerance_m`、`goal_reached_within_tolerance` 和检查依据；超差不允许 succeeded。当前 TB4 适配全向 XY 几何路径，拒绝 Dubins/Reeds–Shepp 执行，避免丢失 SE(2)/倒车语义。覆盖执行指标另带 `actual_coverage_kind: "trajectory_swath_proxy"`、`operation_state_observed: false`，代表作业具连续开启假设下的轨迹扫掠，不能证明真实农业作业质量。

### 2.10 规划身份、编辑与回放（2026-10-18）

- 新 Solution 的 `planning` 使用 `urp.planning/1`，固定完整输入 SHA256、米制/基准坐标、目标规则与单位、任务类型、松弛约束及 `comparison_key`。原生地图还固定栅格、自由空间、分辨率、原点和净空规则的 `environment_sha256`；读取历史记录不拿当前地图补写未知环境。不同模型、单位、预算或约束不得混合选优。统计比较另按算法版本与非预算/种子参数区分配置。
- 覆盖规划固定 `validation_policy: "urp.coverage-interior90/2"`：至少覆盖 90% 的内缩作业区，作业行须包含于导出路径，检查完整足迹与采样曲率。全流程适配 `raw_result.adapter_contract: "urp.f2c-full/2"` 包含最后 PathState 的实际终点，不对受检/执行几何抽稀；全田块覆盖率与内缩作业区覆盖率分列。旧全流程结果不改写，但不再作为通过结果排序或执行，必须重求解。倒车切换朝向尚未独立适配的尖点拒绝通过；含倒车段的覆盖结果拒绝 TB4 执行。这些采样检查不构成连续 SE(2) 可行性证明。
- `scenario_snapshot.instance` 固定实际提交输入。编辑后原 BKS 不再适用时，移至 `provenance.reference_invalidated`，不能继续计算原算例差距。目录 `algorithm.randomness` 与运行 `reproducibility.seed_usage` 明确种子是否实际生效；改变被忽略的种子不等于独立随机重复。
- `trace_contract` 使用 `urp.search-trace/1`。只有明确声明 `reported_objective` 的轨迹可画最终目标收敛曲线；内部量化、方向松弛或加权成本标为 `solver_search_proxy`，不拿它证明最终目标增益。每个样本未经独立检查这一限制保留在记录中。
- `POST /lab/api/planning/validate {scenario_id, instance}` 返回规范化 instance 与 planning，不保存。现有求解器未实现的时间窗、优先关系、混合线/面任务、动态障碍和能量约束显式拒绝，不能保存后静默忽略。Fleet、覆盖与点到点几何规划必须声明米制；当前点到点需要已知仿真地图。
- 用户场景 PUT 支持 `base_revision`；保存成功返回递增 `revision`，过期为 409 `scenario_conflict`。前端保留未保存草稿，提供重试与另存副本，不覆盖并发新版本。锁适用于当前单网关进程，旧客户端不传版本的兼容接口不构成跨主机事务保证。
- 田间结果增加 `replay_contract`（`urp.replay/1`），声明源仿真时钟、真值位置、米/弧度及最大插值间隔 0.25 s。前端不跨缺流插值或累计跨缺口里程；不单调时间戳拒绝单轴重放。回放里程/偏差随选定时刻计算，完整终态证据另列；任务到达与作业停留完成分开。显示抽稀不改原始遥测和实验导出。

### 2.8 SSE `GET /lab/api/events`
`event:` 类型与 `data:` JSON：`services`（完整 services 列表，状态变化时）、`job`（`{job_id, run_id, state}`）、`run`（完整 Solution，完成时）、`sim_state`（SimState，≤ 4 Hz，当前为完整状态与轨迹快照）、`sim_run`（SimRun 完成或状态变化）、`log`（`{log, lines}`；当前前端通过日志接口轮询）。心跳 `event: ping` 每 15 s。

### 2.9 其他
- `GET /lab/api/health` → `{"status":"ok","version":"0.1.0","executes_solvers":true}`
- `GET /lab/api/maps/{world}` → `{"world","resolution","origin","width","height","image":"/lab/api/maps/{world}.png"}`；`.png` 为灰度栅格（行 0 在图像顶部，对应 map y 最大处）。
- `GET /lab/api/runs?scenario_id=&limit=` → `{"runs":[Solution 摘要…]}`；`DELETE` 不提供（历史保留）。
- 导出：`GET /lab/api/runs/{run_id}/export?format=json|csv|geojson`、`GET /lab/api/sim/runs/{id}/export?format=json|csv`、`POST /lab/api/compare/export {"run_ids":[…], "format":"csv"|"json"}`。文件名带 run id 与 UTC 时间。
- 错误格式统一 `{"error": "code", "message": "中文说明"}`。网关只监听 127.0.0.1；POST 只接受 `application/json`，拒绝跨源写入（Origin 与 Host 不一致 → 403）。

## 3. Python 模块接口（网关 ↔ 核心）

```python
# urp_lab.paths
REPO, LAB, RUNTIME, RESEARCH_SRC, FRONTEND_DIST, DEPLOYMENT  # pathlib.Path
# urp_lab.scenarios
list_scenarios() -> list[dict]           # 摘要
get_scenario(scenario_id: str) -> dict   # 完整 Scenario；KeyError 若不存在
validate_instance(problem_class: str, instance: dict) -> dict   # 返回规范化 instance；ValueError(中文) 若非法
world_map(world: str) -> dict            # §2.9 元数据；map_png(world) -> bytes
# urp_lab.solvers
catalog() -> list[dict]                  # §2.3
run_algorithm(scenario: dict, instance: dict, algorithm_id: str, params: dict) -> dict
    # 在当前进程内求解+检查+地图检查，返回 Solution 中的 status/objective/routes/check/map_check/metrics/algorithm 字段
# urp_lab.worker  (python -m urp_lab.worker < request.json > result.json)，网关用子进程+墙钟预算调用 run_algorithm
# urp_lab.recommend
recommend(scenario: dict, instance: dict) -> dict   # §2.5
# urp_lab.export
export_run(solution: dict, fmt: str) -> tuple[bytes, str, str]  # (data, media_type, filename)
export_sim_run(sim_run: dict, fmt: str) -> tuple[bytes, str, str]
export_compare(solutions: list[dict], fmt: str) -> tuple[bytes, str, str]
# urp_lab.simplan
to_sim_plan(solution: dict, scenario: dict, mode: str) -> dict
    # 生成会话 /execute 负载（§4），不适用时 raise NotSimulatable(中文原因)
```

## 4. 仿真会话控制接口（网关 ↔ ROS，127.0.0.1:4191，仅本机）

启动：`bash setup/profile.sh simulation /usr/bin/python3 src/lab/ros/urp_sim_session.py --session-id ID --world warehouse --spawn X,Y,YAW --runtime DIR --port 4191 [--gui] [--rviz]`（先 `source /opt/ros/humble/setup.bash`）。默认启用 TF、GUI 兼容覆盖与桥接源码修复，按依赖顺序关闭；`URP_TF_FIX=0`、`URP_GUI_FIX=0`、`URP_BRIDGE_FIX=0` 分别选择对应的原系统实现，最后一项不关闭有序回收。

- `GET /state` → SimState（§2.7，`execution` 为当前/最近一次运行）。
- `POST /execute`：
```jsonc
{"sim_run_id": "…", "run_id": "…", "mode": "follow_path"|"nav2_waypoints",
 "path": [[x,y],…],                      // follow_path：URP 规划折线（map 米）；网关已按 0.05 m 加密并去重
 "waypoints": [{"task": 0, "label": "A3", "x": 1.0, "y": 2.0, "yaw": null}],  // 有序任务点
 "tasks": [{"task":0,"label":"A3","xy":[x,y]}], "task_radius_m": 0.35,
 "sources": {…同 SimRun.sources…}, "planned_length_m": 52.1, "budget_s": 900}
```
→ 202 `{"accepted": true}`；正在执行时 409。
- `POST /cancel`、`POST /gui {"on":bool}`、`POST /rviz {"on":bool}`、`POST /shutdown`（有序关闭后进程退出，写 `session-report.json`）。
- `GET /runs/{sim_run_id}` → SimRun（含完整轨迹）。
- ROS 话题（供 RViz）：`/workbench/planned_path`（nav_msgs/Path，transient local）、`/workbench/actual_path`（真值轨迹，nav_msgs/Path）、`/workbench/tasks`（visualization_msgs/MarkerArray：待访/已达/未达三色 + 标签）、`/workbench/status_text`（Marker 文本）。Nav2 自身路径仍为 `/plan`，在 RViz 中用不同颜色。


## 5. 已实现的验收约束（2026-10-15）

- 默认 `maze-inspection`：官方 maze 世界，两点巡检，4.30 m 路线，490 cm_equivalent 成本。是集成验收场景，不是农业环境结论。仓库货架仅模拟静态行间通道。
- 仿真实验 JSON 导出在 SimRun 顶层增加 `planning_solution`，包含完整原始规划；CSV 仍只含实测轨迹。
- Solution 同时保留 `instance`、`scenario_snapshot`（深拷贝）、`raw_result`、`reproducibility`（Python/包版本、源文件 SHA256、种子用途、时间停止准则）、`process`（真实退出码、墙钟预算、原始日志路径）。PyVRP 使用随机种子；精确算法无随机性；此版 OR-Tools GLS 不设置随机种子。墙钟终止的启发式结果未承诺逐位复现。
- 精确求解进程有 SIGALRM 时间上限；主进程另有预算 + 2 s 墙钟看门狗，超时/取消终止其拥有的进程组。非零退出不能因 stdout 中有 JSON 而变成成功。
- 地图未知区与边界外均占据；EDT 对单元格尺寸作保守扣减，按半个栅格采样所有路径段并扣减采样间误差界。要求机器人半径 0.175 m + 0.05 m 余量，不只检查离散节点。该圆足迹用于官方 TB4；其他机器人需新的足迹及控制器适配。
- `/execute` 的 `legs` 为加密原始几何边（0.05 m），F2C 则为连续的整条作业行及随后的 Dubins 转弯（0.02 m）。拼接必须与独立检查过的 polyline 相同，禁止断开的多路线隐式跳跃。闭环不能整条直接交给 FollowPath，否则起终点相同可能立即结束。每段端点真值误差、实际到计划及计划到实际的最大距离均须 ≤0.35 m，访问任务还须按序全部到达。覆盖任务还须实际内缩作业区覆盖 ≥90%、足迹在田块内；所有类型均检查实际整段足迹与地图。允许 TB4 原地转向，不把规划曲率约束当作实机运动学验证。
- 会话复制官方导航 YAML 后施加仿真参数，不写系统目录。默认 AMCL 使用 180 束激光及低噪声里程计配置；关键参数通过 ROS 服务读取核实后才就绪。访问与覆盖路径使用已安装的 RPP（FollowPath / FollowCoverage，0.18 m lookahead，碰撞检测开启），支持面向相反方向的新目标。实际参数文件及哈希随会话归档。
- `approach_trajectory` 与 `tracking_trajectory` 分别保存，跟踪指标排除接驳段。`nav2_plans` 保存 Nav2 `/plan` 及阶段，逐点导航的成功不能解读为执行原算法几何路径。真值 Pose_V 桥接头时间为 0 时，样本取最新 `/clock`；不声称严格同步采样。AMCL 保留自身时间戳。
- `localization`（2026-10-17 第四轮）是后验对比记录：AMCL 估计在自身源时间戳上与插值真值配对，给出位置 RMSE/均值/p50/p95/最大/最终误差与航向 RMSE；`replans` 汇总 Nav2 全局规划消息（次数、首末 t_sim、首末路径长度；重规划原因 BT 不发布、不做推断）。纯记录不参与成败判定；amcl 模式下估算质量是仿真激光 + AMCL 默认参数的证据，不是标定结论。实测对照（maze-inspection）：follow_path 0 次重规划（纯追踪不经规划器），nav2_waypoints 42 s 内 43 次重规划、AMCL 位置 RMSE ≈ 0.04 m。
- SimState 有 `telemetry` 消息计数、数据年龄和 map→base_link TF 年龄；执行中时钟、scan、odom 或真值中断会失败。必需启动器退出、必需子节点提前退出（含 rc=0）、Nav2 心跳故障均导致会话失败及清理；模型 create/controller spawner 的正常一次性退出除外。窗口单独退出会告警。
- 每会话预审官方 SDF、Fuel 模型、碰撞网格与当前 Fortress 使用的材质贴图，记录文件哈希。Fuel 模型数量按规范化 owner/name 去重，mesh URL 不能重复计数。Classic 的不适用材质脚本单列 `inactive_classic_materials`，不跳过活动 PBR 贴图。实际地图/碰撞一致性仍需传感器证据，清单完整不等于地图已验收。
- 正常关闭：取消活动动作、观察停车、在时钟仍运行时关闭 Nav2/定位生命周期及 TB4 节点，再关闭桥接、调用 Fortress `/server_control` 停止物理服务器，回收本会话进程，保存 `session-report.json`。必须核验实际退出与残留，原生停止接口接受请求本身不代表关闭通过。强杀、异常退出、生命周期未关闭或残留进程不得报告 PASS。桥接在 main 返回前完成 ROS shutdown，保留正常静态析构与真实状态码，不使用 `_exit`；源码/依赖指纹不符则拒绝使用。
- `POST /lab/api/shutdown {}` 为整体有序关闭；API 请求不伪造 SIGINT。Ctrl+C 使用正常信号语义，终端返回 130 表示用户中断，不代表所有子组件返回 0；以清理报告逐项判断。
- 覆盖基线分别报告内缩作业区面积、扫掠覆盖面积/比例及 `turn_radius_enforced=false`。检查器复核扫掠和连接段；路线跑完不能声称全田块覆盖。所有输出的 `.geojson` 是带本地坐标说明的几何数据，**不是 WGS84/RFC7946 经纬度文件**，不得直接用于地理定位。
- 用户入口及证据索引只维护在 `setup/README.md` 和 `setup/2026-10-15/integration/REPORT.md`；本契约不是第二份操作指南。

## 5. 工作台扩展（2026-10-15 第二轮）

### 5.1 新增坐标系与问题类型
- `frame.kind = "field"`：实地 / 生成农田的局部米制坐标（`units: "m"`，可带 `crs`、`offset`）；符合第 6 节准入条件的 `fleet_routing` 可进入生成的田间 Gazebo 世界，其余问题类型不能据此直接执行。
- `fleet_routing`（多车任务分配与路径）：`{boundary, plots[{id,name,crop,rows_deg,polygon}], obstacles[{id,kind,name,polygon}], depot, tasks[{id,xy,demand,label,plot,service_s}], vehicles{count,capacity,speed_mps,trips:'multi'|'single',travel:'rows'|'free',names,fleet?}, objective:'total_distance'|'makespan', robot_radius, units:'m'}`。障碍 `kind ∈ pond|building|greenhouse|tree|ditch|rock|other`。
  `vehicles.fleet`（可选，2026-10-17 第三轮）是异构车队类别清单：`[{name?, count, capacity, speed_mps?, robot_radius?, payload:'load'|'none', min_turn_radius_m?, asset?:{id,version,sha256}}]`（1–8 类，总台数 ≤ 60；给出 `fleet` 时不再单独给 `count`/`capacity`/`names`）。`min_turn_radius_m`（0–10 m，默认 0 = 可原地转向）为正的类别按 Dubins (1957) 曲线实现路径（OMPL `DubinsStateSpace`，仅前进不倒车）：航向在路段间携带，每个航向变化成为曲率受约束的转弯；路点航向按离散化集合做动态规划选取（Le Ny et al. 2007 / Cohen & Epstein 2017 的 discretised-DTSP 方案，12 向候选；启发式、非连续最优），分配在直线图距离上优化，实现的曲线长度作为执行指标（`curvature_constrained_vehicles`、逐路线 `min_turn_radius_m` / `curvature_radius_m`）。实现采用分级回退：解析实现（含拐点外移 0.6/1.2×R 两级）净空不过时，回退到 OMPL RRT-Connect 在 Dubins 空间采样（停靠航向自由、逐腿求解、5 cm 采样）——能穿解析方案穿不过的窄缝（复合 S 形机动），代价如实计入里程；全部尝试记录在 `raw_result.curvature_realization_attempts`。检查器按连续三点外接圆独立复核最小转弯半径（Dubins 弧段上采样曲率精确）并要求采样间距 ≤ 0.15 m。`travel='rows'` 暂不支持曲率约束（通道内转弯规划未实现，校验直接拒绝）；执行侧田间仿真为可原地转向差速模型，曲率约束类别求解/验算可行但启动执行返回 422（`curvature_constrained_execution`）。每条结果路线带自己的类别字段 `fleet_class, capacity, speed_mps, robot_radius`；`pyvrp.fleet` 与 `ortools.fleet`（两种目标）按类别容量 / 车速求解，makespan 时为每类车速建独立时间矩阵与每车容量；构造基线（sweep / cluster_first / savings）只支持同构车队，异构输入直接报错。通行图与障碍膨胀按车队最大足迹半径（`robot_radius` 归一为最大值），独立检查器按各车自身足迹、容量与车速复算并核对路线声明的类别字段；类别可声明 `footprint`（body 系多边形，外接半径不得超过该类规划圆，2026-10-17 第八轮）：检查器按路径切线朝向后验采样多边形净空并记录 `oriented_clearance_m`（含作物行）——量化外接圆假设的保守差值，只记录不门控；朝向感知规划未实现；`payload:'none'` 的类别容量取总需求（不构成约束）。按类 `asset` 引用进入提交快照与一致性校验；执行侧田间仿真当前只生成同构内置差速机器人，多类别方案可求解 / 验算，但启动执行返回 422（`heterogeneous_fleet_execution`）。旧实例无 `fleet` 时行为与历史完全一致（单类别路径）。
  实例可含 `depots[[x,y],…]`（2026-10-17 第六轮，第 7 项）：1–8 个车库，车辆按表序轮询分配到家库（出发与结束回家库；无补给点时每趟也返家库）；pyvrp.fleet / ortools.fleet 总里程目标支持多车库（pyvrp 每库建 depot、OR-Tools 每车 start/end），路线带 `depot`（家库下标）与 `depot_xy`，检查器按家库复核起终点与分趟返库，生成世界时各库独立安排停车位。构造基线拒绝多车库输入（makespan 目标已支持：每车 start/end 家库、按库补货节点经 VehicleVar 成员限制只服务本库车辆，多库时首解策略切换为 PARALLEL_CHEAPEST_INSERTION——PATH_CHEAPEST_ARC 在补货必访时无法完成首解）。实例可含 `unload_points[[x,y],…]`（≤16，2026-10-17 第九轮）：补给点进入图节点与 makespan 补货模型（任意车辆可用，区别于家库限定），分趟可在补给点卸货后继续，路线以 `reload_stops` 声明趟间补给停靠，检查器验证停靠 ∈ 补给点 ∪ 家库且折线按序经过；任务始终回家库起返。GeoJSON 的 unload/supply 点要素导入为 `unload_points` 并原样导出。距离模式（总里程目标）不建模中途补给（趟次仍以家库为界）。旧实例无 `depots` 时等价单库，行为不变。
  实例可含 `corridors[{id,name,polygon}]`（2026-10-17 第五轮，第 8 项）：GeoJSON 里 role/type/name 含 road/track/headland/道路/机耕道/地头 等词的面要素成为通行走廊，线要素按 `width_m`（1–8 m，默认 2.5）缓冲成面；行间通行（`rows`）下走廊不种植、自动并入开阔区（地头 / 机耕路的通行网络直接吃这些要素），`free` 通行下它们不构成约束。导出 GeoJSON 原样保留走廊要素（role=corridor）。
  `vehicles.travel` 决定距离模型（生成的农田地图默认 `rows`，导入 / 旧场景缺省为 `free`）：
  - `free`：机器人半径膨胀障碍上的可视图最短路，地块可任意穿行。
  - `rows`：田间通行图（`urp_lab/lanes.py`）。每个地块按 `rows_deg` 与作物行距（作物表 `lanes.CROPS`，未知作物用默认行距；单块超过 160 行时等比放宽）种植作物行，地块边缘留 0.8 m、地块内障碍周围留 `2r + 1` m 不种；行间净宽 ≥ 2(r + 0.1) m 时每两行之间是一条通道，否则每两行留出一条作业道（空出 1 个或多个行位）。通道中线向外延伸到开阔区（地头、机耕路、障碍周围的空地）处为出入口；开阔区内是可视图，通道内只能沿中线行驶，不得穿过作物行。每个任务在离它最近的通道点或开阔区点作业（超过 `max(3 m, 最大通道间隔)` 报错），车库须在开阔区。
  结果 route 额外字段：`vehicle_name, color, trips[[task ids]], duration_s`，`services[i].position` 为有序 polyline 的顶点下标（旧记录可为空），`services[i].stop` 为作业位置（`free` 时即任务点）；metrics：`total_distance_m, makespan_s, longest_route_m, vehicles_used, fleet_size, trips, balance_cv, capacity_utilization, detour_ratio, travel, per_vehicle[]`，`rows` 时另有 `lanes, service_offset_mean_m, service_offset_max_m`；`raw_result.travel_network` 给出通道数、出入口、开阔区拐点、各地块行距与通道宽。目标为 makespan 时 `objective.unit = 's'`。当前 `raw_result.model_contract.schema = urp.fleet-model/2`；图节点统一为车库、任务、补给点。补给副本可选，模型时间按 0.1 s 离散，提取后用米制几何重新计时（允许边权取整误差）。旧模型补给记录必须重求解，执行入口返回 422 `obsolete_resupply_model`，不能仅靠旧 `check.valid` 放行。
  验算（`fleet.check`）另核对：每个任务的作业位置在路线上，且是离任务最近的可达处（`free` 时必须是任务点本身）；`rows` 时由地块重新生成作物行与通道，每段路线都须落在开阔区或通道中线上，否则报“离开行间通道和地头，斜穿了作物”。
- `point_to_point`：`{start:[x,y,yaw], goal:[x,y,yaw], motion_model:'holonomic'|'dubins'|'reeds_shepp', turning_radius, robot_radius, clearance_margin, goal_tolerance}`，地图坐标，可仿真（单条路线，终点为唯一任务点）。
- `euclidean_cvrp.objective` 可为 `sum_rounded_euclidean_distance`（TSPLIB EUC_2D，CVRPLIB 用）；场景可带 `reference{bks, optimal, routes}`，结果给出 `metrics.gap_to_bks_pct`。
- 所有结果可带 `trace: [[秒, 当前最好目标值], …]`（PyVRP、OR-Tools、OMPL 渐近最优规划器）。

### 5.2 新增接口（`/lab/api`）
| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `compose/examples` | 示例任务描述 |
| POST | `compose/parse {text}` | 解析结果 `{spec, found, defaults, cards}` |
| POST | `compose {text, seed?, overrides?, field_id?}` | 生成并保存多车任务地图（201，返回场景） |
| PUT | `scenarios/{id} {instance?, title?, summary?}` | 更新我的场景（内置 / 资源库 403） |
| POST | `scenarios/{id}/fork {instance?, title?}` | 另存为我的场景 |
| POST | `scenarios/import/geojson {geojson, title?, vehicles?}` | GeoJSON 导入 |
| POST | `fleet/lanes {instance}` | 任务地图（可未保存）的通道中线 `lanes[[x,y],…]`、`lane_count`、`closed_lanes` 与各地块 `plots[{id,crop,spacing_m,row_width_m,mode:'every'\|'track',lane_width_m,pitch_m,rows,lanes}]`，供地图编辑器叠加显示 |
| GET | `scenarios/{id}/export?format=json\|geojson` | 场景导出 |
| GET | `library`、`library/cvrplib/sets`、`library/cvrplib/instances?set=&q=` | CVRPLIB 目录 |
| GET / POST | `library/fields?country=&q=`、`library/fields/fetch {images}`、`library/fields/{id}/image`、`library/fields/{id}/outline.svg` | Fields2Benchmark |
| GET | `references?topic=&q=`、`references.bib?keys=` | 文献与 BibTeX |
| GET | `source?path=` | 算法源码（白名单目录） |
| POST / GET | `studies`、`studies/{id}`、`studies/{id}/cancel`、`studies/{id}/analysis`、`studies/{id}/instance-space`、`studies/{id}/export?format=csv\|json\|latex` | 研究课题与统计分析 |
| POST | `analysis {run_ids}` | 任意运行集合的统计分析 |

场景 id：CVRPLIB 为 `cvrplib-<算例名小写>`，真实田块为 `field-<国家>-field-<编号>`，打开后记入“最近打开”。

## 6. 田间多车仿真与浏览器里的 ROS / Gazebo 视图（2026-10-16）

### 6.1 接口（`/lab/api`）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `fieldsim` | 当前田间仿真状态：`phase`（idle / starting / running / finished / failed / stopped）、`running`、`sim_time`、`real_time_factor`、`vehicles[]`（每车状态、位姿、速度、完成度、到达任务数、里程、横向偏差、规划里程 / 用时）、`metrics`（结束后：规划与执行的最晚完成时间、总里程、到达任务数、横向偏差）、`components`、`web`、`errors`、`warnings` |
| POST | `fieldsim {run_id, real_time_factor?=4, gui?, rviz?, vehicle_asset?:{id,version,sha256}, physics?:{...}, execution?:{ground_preset?, ground_zones?, localization_preset?, seed?, slip_protection?}}` | 用已验算的 `fleet_routing` 运行生成世界；服务端核对绑定车型；与 TurtleBot4 会话互斥；`execution` 为非理想执行配置（见 6.6），省略或全默认即理想参考模式 |
| POST | `fieldsim/stop` | 有序关闭（会话写 `session-report.json`，含各组件返回码与残留进程检查） |
| POST | `fieldsim/window {kind: gui\|rviz, on}` | 打开 / 关闭原生 Gazebo GUI 或 RViz2 窗口 |
| GET | `fieldsim/results?run_id=`、`fieldsim/results/{session_id}` | 历次执行摘要 / 完整结果（含各车轨迹与任务到达时间） |
| GET | `ros/robots/{model}.urdf` | 用系统 ROS 的 xacro 展开官方 TurtleBot4 描述（`turtlebot4`、`turtlebot4_lite`），缓存在 `runtime/library/robots/` |
| GET | `ros/package/{package}/{path}` | `turtlebot4_description`、`irobot_create_description` 包内的网格文件，供 URDF 的 `package://` 引用 |

### 6.2 会话与世界
- 世界生成：`urp_lab/fieldworld.py`。世界坐标 = 任务地图坐标（米）。田块、地块、作物行（与规划同一套 `lanes` 行布局）、障碍为二进制 STL 网格（Gazebo 与 gzweb 都能读；障碍碰撞用 SDF `polyline`），车库、任务标记、每车一台差速机器人（官方 `DiffDrive` 系统，车体 0.8 × 0.56 m、含球面轮总宽 0.96 m，驱动轮在车体中线上、前后各一个万向轮，可绕中心原地转向；各车碰撞掩码均为 0xffff，车间碰撞开启；每车独立停车位和交通预约见 6.5）。方案为 `travel = rows` 时作物行带碰撞（静态模型 `crop_rows`：矩形行用 box，被裁过的行用 `polyline`，高度至少 0.5 m），停车位避开作物行；`free` 时作物行只做显示。
- 会话进程：`src/lab/ros/field_sim_session.py`（系统 Python 3.10，控制端口 127.0.0.1:4192）。组件：`ign gazebo -s`、一个 `ros_gz_bridge parameter_bridge`（`/clock`、`/model/vehicle_i/pose → /ground_truth/poses`（各车 PosePublisher）、每车 `/vehicle_i/cmd_vel`、`/vehicle_i/odom`、`/model/vehicle_i/contacts`）、Foxglove Bridge（127.0.0.1:8765）、Gazebo WebSocket 服务（9002，每会话随机授权密钥，资源路径限定为会话目录与 ROS share）、可选 Gazebo GUI 与 RViz2。
- 控制：每车纯追踪，前视 1.4 m，不越过下一个未到达任务的作业位置（`services[i].stop`，旧记录用任务点）与下一个急弯（转角 > 0.6 rad：通道出入口、行内掉头）；到急弯处先原地转到对准下一段再走；进度只沿路线向前投影，路线原路折返时按返程计。进入作业位置 0.45 m（路线在此急转时 0.2 m）记为到达，有 `service_s` 时原地停留；60 s 无进展记为受阻；总时限按全部车辆的规划时长之和计算，见 6.5。结果另记 `travel`、`row_collision_shapes`（世界几何数量）、`row_collisions`（观测接触计数，未知为 null）与每个任务的 `stop`。
- ROS 话题（`map` 坐标系）：`/workbench/field`、`/workbench/routes`、`/workbench/tasks`（transient local）、`/workbench/trails`、`/workbench/vehicles`（MarkerArray）、`/workbench/fleet_status`（std_msgs/String JSON，约 5 Hz）、TF `map → vehicle_i/base_link`。
- TurtleBot4 会话也同时启动 Foxglove Bridge 与 Gazebo WebSocket 服务（`--no-web` 关闭），话题改名为 `/workbench/planned_path`、`/workbench/actual_path`、`/workbench/tasks`、`/workbench/status_text`，真值 `/ground_truth/poses`，RViz 配置 `src/lab/ros/workbench.rviz`。

### 6.3 后端验收与故障语义
- 准入：`state=done`、`status=feasible|optimal`、`check.valid=true` 只是前置条件；启动必须用保存的 instance/routes 重新调用 `fleet.check`。`real_time_factor` 为有限数字 0.5–20，非法值返回 400；未检查/重验失败返回 422。`web:false` 可关闭可选网页桥，默认开启。两类会话共用生命周期互斥锁。
- `fleet.check` 核对 sequence、trips、services 一致且覆盖所有任务；按有序折线查验任务与趟间返库，比较里程/载重/用时，按原始多边形距离复算连续圆足迹。候选未通过时 `status=no_solution, routes=[]`，候选保留在 `raw_result.rejected_routes`。距离是构造出的候选图上的最短路，不保证连续空间全局最短。
- 当前田间执行模型外接圆半径 `hypot(0.4, 0.28)` m、至多 15 辆，树木碰撞与地图多边形一致，接驳段验证净空。通用规划器的半径/车辆数范围不因此缩小。
- 控制器的纯 Python 实现在 `urp_lab.field_tracking`；任务携带执行路径弧长 `path_s` 和完成停留的仿真时刻 `serviced_at`。重合位置/重复经过不能提前服务；服务停留期间不启动下一任务、不累积卡住时限。
- `result.state = succeeded|failed|canceled`。成功必须通过 `urp_lab.fieldcheck` 对实测轨迹的独立检查（圆足迹、双向路径偏差及终点、停留时间）。偏差按 0.1 m 采样并加入 0.05 m 保守误差界；圆足迹检查每条实测线段。缺少数据或检查器失败不能产生 succeeded。取消/异常也保存结果。
- 时钟进展、每车真值、里程计与接触消息有 3 s 墙钟看门狗；必需进程提前退出即会话故障，不以子进程 rc=0 当成功。`state.json` 保存最终状态，GET fieldsim 在进程退出后补读它并返回真实 `returncode`。非零码不会因用户请求停止而被隐藏。
- `session-report.json` 的 `execution_state` 为任务结果，`cleanup_status` 为进程回收结果，`status` 为会话整体结果；执行失败或关闭异常返回非零退出码。正常取消可以 rc=0，但结果保持 canceled。
- 导出额外包含 `planning_solution`、`execution_plan`、`execution_provenance`（执行源码与世界 SHA256、坐标/定位/控制/比较口径）、`session_report`（未关闭时为 null）。田间使用 URP 跟踪器，不使用 Nav2 重规划；参考模式用 Gazebo 真值，非理想定位模式用轮式输入与合成位置观测的估计（见 §7）。actual 含往返停车接驳、转向和排队，planned 从车库算起。
- 网页桥只在端口可连接后发布地址；可选服务失败告警。窗口开关在执行中处理，关闭异步回收窗口进程，不暂停跟踪；每次启动使用独立日志名。

### 6.4 前端
- `studio/ros/foxglove.ts`：唯一的 Foxglove WebSocket 连接（子协议 `foxglove.sdk.v1`），用 `@foxglove/rosmsg` + `@foxglove/rosmsg2-serialization` 解码 CDR，按话题引用计数订阅。
- `studio/ros/RosScene.tsx`（R3F）：地图、代价地图、激光、Nav2 路径、MarkerArray、TF、`urdf-loader` 载入的 TurtleBot4。`studio/ros/GazeboView.tsx`：gzweb `SceneManager`。`studio/ros/Telemetry.tsx`：uPlot。`studio/panels/FieldSimPanel.tsx`：田间仿真控制与结果。

### 6.5 交通、资产与可追溯记录（2026-10-17）

- `traffic.policy=exclusive_mission_v1`：完整停车位→车库→原规划路线→原停车位依次预约执行；停车位须在所有其他任务及停车接驳走廊之外。`traffic.parking`、每车 `parking_xy`、`queue_wait_s`、预约事件随结果保存。计时从共同任务起点开始；总超时按各车规划时长之和的 2.5 倍加 120 s（基数至少 60 s）。这是保守基线，不承诺并行通行效率。
- 所有车体碰撞掩码为 `0xffff`。车身、驱动轮及脚轮接触经 `/model/vehicle_i/contacts`（`ros_gz_interfaces/Contacts`）送入控制进程，缺流超过 3 s 墙钟报错。地面接触保留样例；其他接触记录 `t_sim`、碰撞体、车辆、位置和深度，并停止全部车辆。碰撞状态不能以传感器缺数据的零计数代替。`row_collision_shapes` 是实体作物行几何数量，`row_collisions` 是去重的“仿真时间戳 + 碰撞体对”接触消息数，`robot_contact_events` 同理；不称为独立事故次数。历史 `row_collisions` 几何值在 API 中转为 shapes 并将接触数置 null，原始文件不改写。
- 独立检查计算共同时间轴上的分段线性轨迹最小车间距离，圆足迹半径同物理模型；最大采样间隔须 ≤0.5 s，首尾时间须一致到 0.2 s。该结论限定在记录及线性插值假设内，不证明未观测曲线运动。
- `vehicle_asset` 必须与求解时 `instance.fleet.asset` / vehicle 角色引用一致。求解提交保存 `asset_snapshot`；执行解析锁定版本、文件哈希与元数据快照，身份或参数不符返回 409，未适配车型返回 422。仅内置田间差速模型已支持；无资产绑定的历史方案显式记录 `explicit_legacy_builtin`，不静默替代已绑定的导入车型。
- `control_traces` 列为 `t_sim,path_s_m,cross_track_m,cmd_v_mps,cmd_w_radps,state,pose_age_s,controller_pose,controller_pose_stamp`（历史记录可缺少后几列）。`pose_age_s` 是该周期控制时间减实际控制姿态源时间，`controller_pose` 为当时使用的 `[x,y,yaw]`；绝对差超过 0.10 s 时只发零指令，不推进路径或确认作业。未测量误差为 null，不复用上一周期数值。ROS 实验遥测使用可靠订阅，常驻单线程执行器在控制前有界处理积压回调；这些措施不代替保存数据的 0.25 s 缺口门禁，也不是操作系统硬实时保证。任务含 `requested_service_s,actual_service_s,service_complete`；未结束停留的实际时长为 null。`tasks_reached` 是到达数，`tasks_completed` 是完成作业数；`execution_started_sim_s` 给出共同时间原点。
- 实测速度与里程随真值更新，完成状态也继续更新。当前版本通过各车 PosePublisher 直接提供源仿真时间，50 Hz 记录；零时间戳拒绝执行，`truth_timing.latest_clock_samples` 必须为 0。此前以最新 `/clock` 回退的记录属于旧版本接收时刻估计，原始记录保留。全车到终点后再观测至少 1 s 仿真时间，实测线速度 <0.02 m/s、角速度 <0.05 rad/s 才结束；5 s 内未稳定则失败。`makespan_s` 计至最后一车到达终点并发出停车命令，原始轨迹及里程另覆盖这段制动观察。独立检查复算轨迹长度，核对在线里程（误差上限 max(0.2 m, 1%)）和最终速度，防止位置持续变化却导出零里程。
- `GET /lab/api/runs/{id}/record` 返回 `{record, integrity, assembled_utc}`。schema 为 `agri-workbench.experiment-record/2`；record 含原始规划、场景/资产快照、推荐及田间/Nav2 仿真。SHA256 对 UTF-8、键排序、无多余空白、不转义非 ASCII、禁止非有限数的 record JSON 计算；是内容完整性散列，不是身份签名。未保存提交时资产快照的历史结果明确标注导出时解析。
- 结果列表按文件纳秒修改时间、大小及 inode 缓存紧凑摘要，原子替换/删除后失效，返回独立副本。图表按实际记录内容散列缓存，图像及元数据预算 64 MiB；历史图使用提交实例，拒绝混合其他场景/会话及不同输入的单实例比较。控制误差图优先读取在线控制周期，任务完成曲线以完成停留为准。


- `GET fieldsim/results/{session_id}?evidence=true` 仅返回实时面板所需的物理判据/能耗/关闭报告，省去原始采样数组；默认完整结果及回放、导出不变，均不缓存旧关闭报告。
- 田间、TB4 与物理探针在 ROS 初始化前共同持有每用户 `~/.cache/urp/physical-session.lock` 的非阻塞内核锁；并发者返回 2，不终止持有者。锁文件不删除，进程退出自动释放。
- gzweb 3.0.2 使用带双重 SHA256 检查的安装补丁：同步 ArrayBuffer 解码、连接代际回调隔离、取消旧场景资源请求、完整释放控制器/画布；按 UTF-8 字节定位帧头，支持中文资源名。绝对 STL 路径走 Gazebo 资源通道，缺资源或 15 s 未返回明确显示失败/超时，可重新连接；该超时从资源请求发出时计算。

### 6.6 模型契约与物理验证（2026-10-18；无滑移参考一致性门槛）

- `GET /lab/api/fieldsim/physics` 返回默认 `urp.field-diffdrive.rigid/5` 和可覆盖键。POST 的 `physics` 仅支持 `max_step_size_s`（0.0005–0.01 s）、`collision_detector`（ode / bullet）、`ground_friction` / `wheel_friction`（0.01–2）、`payload_kg`（0–50 kg）、`wheel_contact`（tyre / sphere）、`tyre_width_m`（0.04–默认 0.10 m，上限 0.16 m）；未知键、非有限数、错误类型拒绝。默认步长 0.004 s、ODE 接触、DART 动力学、Dantzig 约束求解。参数可配置不等于该工况已验证；复验覆盖范围见集成报告。当前 Fortress 无可用的显式引擎 seed 设置，传入 seed 拒绝，不能承诺逐位重现。`wheel_lateral_friction`（mu2，缺省=各向同性）是诊断/敏感性参数：实测（2026-10-17，mu2 0.3/0.6 × 初始朝向扫描）原地滑移对其几乎不敏感（15.8→14.6–15.8%），且 ODE 摩擦方向存在世界系锁定一脉的已知限制——不能据此宣称各向异性轮胎建模。
- 默认总质量 25 kg，20 kg 箱体、两枚 2 kg 球面驱动轮（半径 0.15 m，轮距 0.66 m；`wheel_contact:'tyre'` 为有限宽刚性圆柱接触诊断）、两枚 0.5 kg 球面脚轮；均匀实体惯量独立复算。载荷均匀分布在箱体，更新质量和惯量。足迹同时包含圆柱投影矩形（按最大支持胎宽冻结）及完整球面投影圆盘（外接多边形，边到圆心距离独立复算），外接圆仍为 0.4882623 m；旧车型绑定不会静默换成新模型，须重新绑定并求解。圆柱接触在转向中会产生与中心轮距运动学不一致的真值响应，不能以放宽滑移率上限冒充当前无滑移参考通过；球面模型也不冒充经过真实轮胎/土壤标定。
- 物理速度上限 2 m/s，线加速 1.5 / 制动 2 m/s²，角速度 2 rad/s、角加速 4 rad/s²。这是理想关节速度伺服，环内无电机力矩饱和与电池模型；`energy` 字段是后验运动学核算（滚动阻力按表面区分：田面 0.06 / 走廊与机耕道 0.03，ASAE D497/S296 口径；传动效率 0.8、制动不回收；逐车给出田面/走廊能耗拆分），给出每车 J/Wh、平均/峰值功率与峰值单轮力矩需求，不是电机电流或电池实测。`urp.field-energy.post_hoc/3` 按时间求导，采用固定 0.2 s（取最近奇数采样点）的速度滤波；停车尾段不再改变总能耗与峰值，平均功率仍按整段观测时间计算。执行来源指纹包含能耗核算源码。额外质量是常量，VRP demand 仍是抽象容量单位，不自动换成 kg 或随任务变化。
- `physics` 含配置、假设与 SHA256；`physics_audit` 检查生成 SDF 的惯量、质量、轮/脚轮位姿、关节轴、可见/碰撞几何、足迹、驱动、重力、摩擦与配置一致；`physics_runtime` 读取所属进程的实际库映射和 Gazebo 选择日志，保存库哈希。选择日志未完整出现或实际实现不符时拒绝就绪。Gazebo 以行缓冲记录日志，不依赖退出时才 flush。
- `rigid_body_poses` 列为 `t_sim,x_m,y_m,z_m,roll_rad,pitch_rad,yaw_rad`；`wheel_odometry` 为 `t_sim,x_local_m,y_local_m,yaw_local_rad,v_mps,w_radps`。轮式里程计只按首个共同姿态做 SE(2) 对齐，保存完整位置/航向漂移统计，不用全轨迹拟合消除误差。轨迹与刚体真值须一致；任意单个采样缺口超过 0.25 s 拒绝验收，端点相同不能证明中间静止；里程计须覆盖完整真值区间（端点容差 0.25 s）。v5 必须携带固定的 `urp.no-slip-consistency/1`：参考物理下全轨迹最大位置误差 ≤0.10 m、航向误差 ≤0.03 rad；只增加定位误差时仍适用。显式地面几何/摩擦或接触形状扰动按 §7 记录偏离，不能用 `mode` 标志豁免。协议缺失、修改或适用范围内超限均使独立检查失败；结果物理配置还须与冻结计划一致。历史记录保留原值并标明没有新版门槛。倾斜超过 0.35 rad 或模型原点高度绝对值超过 0.5 m 时超出当前平地模型范围并停车，异常姿态保存至关闭报告。执行侧在贴近障碍 / 作物 / 边界的路径段收紧纯追踪预瞄（1.4→0.7 m），减小切入规划裕度的切角。
- `execution_timing_reference` 为逐顶点停、原地转、再加速的解析参考，含平移、转向、停留和串行放行间隔；不是当前追踪器工期预测或上下界。求解 `raw_result.model_contract` 明确同构车队、匀速时间代理、瞬时返库卸载、访问停车代理、LPT 启发式与未证最优。任务成功表示访问与规定停留完成，不表示机械作业具够到作物或农业作业质量达标。
- 验证脚本 `e2e/physics_backend.py` 使用隔离 ROS 域与开放场地，保存实际指令、源时间真值、里程计、物理参数、原始日志和真实清理状态。primary / holdout 为预定义不同速度及转向的协议；每设置重复 3 次，独立检查直行、制动解析参考与原地转向：所有接触形状统一按 0.15 rad 理想滚动转向界，同时要求完整里程计一致性通过，不再对圆柱形状放宽到 20%。`low_grip` 是敏感性试验，偏离无滑移参考会明确记 false；`sphere_legacy` 是保留的协议案例名（当前参数相同于默认球面参考），`cylinder_reference`、`cylinder_width4`、`cylinder_width16` 为刚性圆柱宽度诊断。离线 `e2e/physics_summary.py` 输出 CSV、图和输入哈希，不拟合物理参数。

### 桥接关闭生命周期（2026-10-16）

`bridge_runtime.py` 通过会话子进程的 ament 前缀选择经指纹校验的 `parameter_bridge`。主函数退出前完成 `rclcpp::shutdown()` 并 join 异步信号线程，保留标准退出析构；旧 `_exit` 垫片已退役。`URP_BRIDGE_FIX=0` 使用原系统桥接，仅用于对照。`session-report.json.bridge_runtime` 记录可执行文件、SHA-256、源码清单及 `exit_guard:false`。运行管理由 `ros/processes.py` 提供，不依赖历史验收脚本；必需桥接即使以 0 提前退出，也判会话失败。

网关启动会话后的响应必须携带新 `session_id`，未就绪时为 `starting`；旧会话的缓存、迟到响应和退出通知不能覆盖新会话。崩溃恢复时只接管同一运行目录、环境会话标识、PID 与启动时间均匹配的监督进程；接管进程消失时进入失败状态，原始退出码未知须明确记录，不能永远显示启动中。

合并版本的验收口径：真值或里程计任意采样间隔超过 0.25 s 都使数据检查失败，端点相同不能证明缺口期间静止。曲率检查剔除重复 XY 点，并把前进路线瞬时折返的半径判为零；它仍是采样几何检查，不是完整 SE(2) 动力学可执行性证明。异构容量只要求至少一类车能承担最大单点需求，小车可承担小需求，分配可行性由求解器和独立检查决定；构造基线对异构及多库实例不参与推荐。

## 7. 非理想执行（2026-10-18，独立审计后 v2）

POST `fieldsim.execution` 采用 `urp.field-execution/2`：`ground_preset`（none/wet_patch/loose_strip/mud_extreme）、`localization_preset`（truth/survey/light/drift_outage/hold/severe）、`seed`（0–2³¹−1，默认 20261018）、`slip_protection`（默认 true）。也可用 `ground_zones:[{rect:[x0,y0,x1,y1],mu}]` 指定 1–8 个地图固定矩形，与非 none 的 ground_preset 互斥。未知字段、越界、非有限值、重叠或侵入车库保护区均拒绝；全部默认等价参考模式。种子仅控制合成观测，不控制 Fortress 求解器或墙钟限时规划器，不保证逐位重复。

- **地面与滑移**：分区采用顶面共面的埋入式静态盒，μ 范围 0.03–1，DART 6.12 的接触摩擦实际参与动力学；里程计来自轮角运动学，没有另加显示层滑移。基础地面按矩形网格剔除分区后铺设，深度 0.10 m、顶面 z=−0.01 m，无抬高台阶和重叠的底层平面；基础支撑覆盖地图包围盒外扩 30 m。审计用几何并集检查无孔洞、无重叠、同顶面及每片摩擦。仍以相同共面分块的 μ=1 对照检验接缝影响，不把“几何设计共面”当作物理已通过。预设分区由地图边界比例固定，独立于求解路线；自定义分区使用地图米制坐标，须完整位于实际多边形内。SDF 审计同时查模型/link 变换、碰撞与可视位姿/尺寸、静态属性及 μ；实际轮/脚轮接触的区域、数量和时间另记 `ground_contact_observations`。这些是**未标定的低附着代理**；没有可变形土壤、沉陷、压实、剪切强度或充气轮胎模型，不把 μ 预设解释为某种实测土壤。
- **观测时序**：GNSS 字段表示合成二维绝对位置观测，不是原始卫星信号。按固定测量时刻在相邻真值源样本间插值，不能把最新位置倒填到过去；源间隔超过 0.25 s 时记录 truth_gap。声明偏置与有界随机游走分开，±0.5 m 限制仅约束游走，不能截断严重预设的 [0.8,−0.6] m 偏置。噪声/游走按 seed+车辆序号采样。记录测量时刻、名义可用时刻、实际送达时刻、真值插值两端；失锁和执行结束时尚未送达分别为 outage / pending_at_end，不混入同一丢包统计。
- **估计与控制**：`field_localization.Estimator` 为三状态 `[x,y,yaw]` 固定滞后 EKF。先按轮式车体 twist 做 SE(2) 常转率积分，位置观测在其测量时刻更新历史状态，再重放到当前里程计时刻；历史窗 3 s。协方差用 Joseph 更新，位置—航向交叉协方差允许航向校正，不用显示斜坡掩盖延迟。Q=`diag(0.08²+0.02²|v|,0.08²+0.02²|v|,0.03²+0.04²|w|)·dt`（SI 单位），初始位置/航向标准差均 0.05，R 为观测噪声方差；这些均为未标定工程假设，偏置不是可观测的额外状态。初始地图姿态是已知出生先验，未模拟初始定位失配。`survey` 是 σ=0.02 m、偏置 [0.03,−0.02] m 的合成正对照，不宣称真实测量级定位。
- **真值隔离**：truth 预设明确使用真值作单因素对照；其余预设的跟踪、进度、作业到达与在线保护只使用估计/轮式输入，map→base_link 为估计，真值 TF 使用单独的 ground_truth 子帧。真值仍用于合成传感器、后验评价及仿真有效域监视（倾斜/高度/接触）；不得据此把仿真监视器称为真实车载安全功能。`localization_inputs` 保存启动以来的全部轮式输入、传感器真值输入及观测更新；`field_observation_check` 重建种子观测、因果估计，并核对每周期实际控制姿态/时间。重放证明接线一致性；估计数学另有直线/圆周/延迟解析测试，二者不代替真实标定。
- **保护与作业**：最新有效观测的测量时间距当前超过 15 s 时零指令保持，恢复后续航；保持期间不推进路径或累计作业停留。`slip_protection=false` 只关闭新息告警，失锁保持仍启用。新息持续 >0.6 m 达 3 s 时降速至 0.35 倍，低于 0.3 m 持续 2 s 才恢复；中间区间、观测过期或失锁保持均重置连续恢复计时；无新观测不能视为恢复。进入降速后超过 30 s 未恢复则失败停车（计时包含保持，保持期间仍发零指令）。新息可由定位偏置或模型失配引起，不能单凭它识别轮胎打滑。保持/爬行/停车各次干预与事件计数一致。非理想执行先连续 1 s 观测到轮速/控制侧角速度低于停止阈值，再开始累计作业；这段等待不是车体停止证明，仍须独立真值门禁。控制目标收紧至任务 0.05 m、回库 0.10 m；**独立真值验收半径仍是直通点 0.45 m、转角点 0.20 m**（5 mm 数值容差），作业区间要求真值平移速度 ≤0.03 m/s、角速度 ≤0.05 rad/s，末端最后 0.5 s 要求 <0.02 m/s、<0.05 rad/s；航向先解缠，原地旋转不能算静止。不能用轮速为零代替车体停止。
- **门禁**：先核对结果 execution/physics 与冻结计划及 SDF/运行库审计一致，再选择验收分支。仅改变定位不豁免无滑移参考门槛；地面几何、摩擦或接触形状确实变化时，轮式运动学偏离作为结果记录、门槛记不适用。所有模式仍检查源时间、覆盖/单调/缺口、模型姿态域、足迹、双向跟踪 0.5 m、终点 0.35 m、停留、接触与车间距离。`check.data_valid` 区分有效数据上的任务失败与证据缺失；succeeded 必须 `check.valid=true`。取消/失败也尽可能验算并保留原状态、原因和真实退出码。
- **结果**：`execution_report/2` 从完整保存序列计算均值/最大值，不先抽稀；`tasks_reached/tasks_completed` 表示控制器判定，`tasks_verified` 表示独立真值位置及停留合格数，不能混用。里程计与真值只在首个共同姿态对齐，发散还可能来自接触几何/运动学失配，不自动等同轮胎滑移率。`localization_outage_total_s` 仅计实际失锁窗口暴露；历史结果不回写。求解器及路径跟踪器不读取地面分区或误差参数，当前是原规划在扰动下的可执行性实验，未实现风险感知重规划。

当前验收入口：`.venv/bin/python e2e/nonideal_audit.py --out /全新绝对目录`，先写 protocol 再串行运行，同场景/同路线的物理及定位对照；`--seed` 可改变合成观测种子。`e2e/nonideal_backend.py` 保留旧探索协议和历轮失败，不能把迭代中修订的指标称为独立预注册。当前逐场结果、测试边界及证据统一见 setup 的集成报告最上方。

## 8. 地面风险规划与有限驱动力（2026-10-18 独立审计后）

- **声明与校验**：`ground` 原始输入为 `zones[{rect:[x0,y0,x1,y1],mu,label?}]`（1–8、μ 0.03–1.0、实际多边形界内、互不重叠、避开所有车库 2.5 m）、`mu_reference`（0.05–2.0，默认 1）、`knowledge`（非空标签，默认 known_map_v1）。规范化为 `urp.ground-risk/2`，`risk_margin_m` 取规划最大声明半径，`risk_rect` 作保守方形外扩；它包含圆足迹，但不等于精确方向足迹。所有派生字段每次重新计算/核对，schema 不授予信任；v1 地图校验后可迁移，v1 路线须重新求解。物理摩擦区域不外扩。
- **风险口径**：`C = L + w ∫ max(0, (μ_ref − μ_worst(s))/μ_ref) ds`，积分仅在外扩分区并集内，重叠带取最小 μ，不重复叠加。`geo_length_m` 是几何米；`risk_cost` 是按该次真实 `ground_risk_weight`（0–10）计算的当量米；`ground_stats.in_zone_length_m` 是中心线在实际分区并集内的长度，勿称其为足迹暴露。`ignore` 仍记录同权重的事后风险分数，搜索只用几何距离；`risk_adjust` 用风险边权。`metrics.search_objective` 和 `raw_result.model_contract.optimization.search` 单列搜索口径，主表总里程/匀速工期保持其原单位，不称风险最优为几何最优；自动比较分组包含地面策略及生效权重，跨策略仍可人工查看各指标。
- **支持范围**：`risk_adjust` 只支持自由通行＋total_distance＋无曲率约束的候选图；行间、makespan、曲率约束明确拒绝。不是连续空间全局最优，不是不确定性规划。每条路线携带策略、权重、地图 SHA256、参考 μ、知识标签；独立检查复算成本、暴露及限速，缺字段也无效。执行前额外用原求解 params 绑定策略与权重，不能把 coherent 的 ignore 元数据替换成原 risk_adjust 请求。
- **限速与世界**：当前工程规则 μ<0.5 → min(基准速度,0.35 m/s)，任何触及外扩分区的完整折线段都限速（保守，会受路径分段影响；不是最短时间参数化）。执行世界按含双向停车接驳的完整路径重算弧长区间，控制器前视 0.8 m 减速；独立门禁用 ≤0.25 s 源时间间隔的实际刚体线段检查低附着足迹区域，线段平均速度须 ≤上限+0.06 m/s，不能证明采样间瞬时峰值。只调整定位继承 instance.ground；显式 ground_preset/ground_zones 覆盖世界图，不修改规划图或冻结限速。`ground_knowledge` 保存两图、哈希、origin、maps_match；有意未知/错配世界不得称已知地图一致试验。
- **有界力矩**：物理版本 `urp.field-diffdrive.rigid/6`；`actuator_mode=velocity_servo` 为默认，`torque_limited` 为可选合成模型。`wheel_torque_limit_nm` 0.1–40（默认 8），`wheel_torque_kp` 0.1–40（默认 4）。力矩模式禁止 DiffDrive 共存，左右轮各一 force JointController，明确每车 `/model/{name}/joint/{joint}/cmd_vel` 话题；PID `cmd_min/max` 和 SDF joint effort 同时限幅，启动审计核对所有上限、增益、话题、双轮状态发布配置。无电机效率/电流/转速—力矩曲线标定；`limits_scope` 明确 force 模式不施加理想加速度限幅，解析工期参考不能充当其动力学预测。
- **关节反馈**：关节发布器使用重复 joint_name 标签，经 gz Model→ROS JointState；本机官方 6.18 不支持 update_rate，采用 `ros/native/joint_state` 固定上游源码的项目内限频回补（唯一插件别名 `urp::systems::JointStatePublisher`、50 Hz 源仿真时间发布，原关节测量逻辑保留）。按需构建于 runtime，记录来源、补丁、源码与实际加载库 SHA256；ROS 最新样本队列与 ≤50 Hz 派生里程计发布避免积压，源时间缺口门槛不变。连续轮角按源时间差分并作 SE(2) 积分，不包装角增量至 ±π，不用目标轮速补数据。双轮缺失、重复、非有限、零/倒退时间不刷新就绪，源间隔>0.25 s 失效；反馈过期或 Twist 过期 0.25 s 时发布零目标（有界主动制动，并不保证立即静止）。结果 `wheel_feedback` 记录有效/拒收计数；发布 `/{vehicle}/odom`。`torque_traces` 记录目标与实际轮速，未观测指令或实际作用力矩，饱和影响由运动响应对照支持。
- **统一验收**：力矩模式保持无滑移参考门禁（除显式摩擦/接触扰动）、足迹、完整路径、同步交通、真值停留及静止门禁；压力场景失败保留真实原因与退出码。`ground_speed` 进入正式 fieldcheck，测试脚本不能另行扩大范围。执行源码、模型、SDF、算法输入/参数/seed 随记录导出。有限工程重复不能推出真实土壤/轮胎可信度或算法学术优越性。

## 9. 任务智能体（2026-10-30）

接口前缀 `/lab/api/agent`：`GET /providers`、`GET|POST /sessions`、`GET /sessions/{id}`、`POST /sessions/{id}/messages`、`POST /sessions/{id}/control`（`pause|resume|cancel|retry|confirm|approve|reject|takeover`）、`PATCH /sessions/{id}/contract`。SSE 事件 `agent_session` 携带 `session_id、state、phase、updated_utc`。会话存于 `runtime/agent.sqlite3`，不改写 `lab.sqlite3` 的结构。

- 任务合同 `urp.task-contract/1` 与会话 `urp.agent-session/1` 的字段、来源标签和状态机见 [agent/README](urp_lab/agent/README.md)。
- 工具只调用本契约已有接口的同一实现（`compose`、`recommend`、`JobManager.submit`、`solvers.fleet.check`、`FieldSimManager.start`）；Gazebo 会话与改变任务本身的修复须经用户批准。
- 报告前对本会话所有报告可行的运行重新执行 `fleet.check`，并与存档判定比对；不一致归类为软件实现错误。
- 模型密钥只在服务端配置中；接口与导出不返回密钥。没有可用模型时 `mode=auto` 使用规则流程并在会话中注明，`mode=llm|hybrid` 返回 409 `llm_unavailable`。

## 10. 算法库记录、物理运行与能力清单（2026-11-05）

接口前缀 `/lab/api`，全部是增量接口：

- `GET /algorithms/stats`、`GET /algorithms/{id}/history`：从 `runs` 表只读计算，不存储。同题对比只比较 `comparison_key` 和时间预算都相同、且通过独立检查的运行；`physical` 汇总该算法方案的物理运行。
- `GET /physics/engines`（`gazebo_gap` 说明这台电脑为什么不能跑 Gazebo，能跑时为空）；`POST /physics/runs {run_id | session_id}`（202）；`GET /physics/runs?run_id=`；`GET /physics/runs/{id}`；`POST /physics/runs/{id}/cancel`。SSE 事件 `physics_run`。
- `GET /agent/capabilities`（`algorithms`、`simulators`、`learners`、`transfers`，每项带 `status`）；`GET /agent/jobs`（正在运行的 `solve`、`study`、`physics`、`fieldsim`、`tb4`）。

物理运行记录 `urp.physics-run/1`：

- `run_id` 路径用 `fieldworld.build` 得到与 Gazebo 相同的计划，校验门槛与 `FieldSimManager.start` 相同；`session_id` 路径直接读取该会话冻结的 `plan.json`，并在结束后写入 `comparison`（`urp.sim2sim/1`）。
- 同一方案、`Driver`、`MissionQueue`、车辆质量惯量几何摩擦、速度限幅、控制与真值频率；不同的是物理引擎。Gazebo 的关节速度由 DART 直接满足，MuJoCo 由 `velocity` 执行器达到，增益 `SERVO_KV = 30` 固定，不按记录拟合。接触模型按引擎各自实现。
- 只覆盖理想配置。`execution` 非空、`wheel_lateral_friction` 已设或模型参数不全时，返回 422 `unsupported_plan`，不做近似。
- 独立进程运行，结果在 `runtime/physics/<id>/`：`plan.json`、`result.json`（50 Hz 真值）。接口返回 2 Hz 轨迹。`check` 复用 `fieldcheck.track_check`，另检车间同步距离与接触。网关重启时运行中的记录标为 `failed`。

`urp.sim2sim/1` 分别回答走到哪和什么时候到：`path_*` 是两条轨迹之间的双向最近距离，不受时间平移影响；`time_rmse_m` 是从各车出发起算同一时刻的位置差；`task_arrival_s` 是到点时刻差。这是两个引擎之间的差距，不是实车误差，也不设通过阈值。

## 11. 模型服务与桌面应用（2026-11-05）

- **模型**：`/lab/api/agent/providers`（目录、已连接服务、当前模型）、`/providers/{id}/models`、`/providers/{id}/connect {api_key}`、`/providers/{id}/disconnect`、`/providers/custom {name, base_url, api_key, models?}`、`/providers/refresh`、`/model {model}`。模型地址是 `服务商/模型`，会话的 `provider.id` 记这个值，旧会话记录的裸 id 解析到该服务的第一个模型。任何响应都不含 Key，写接口受现有的同源检查保护。设计见 [agent/README](urp_lab/agent/README.md) §4。不再有本地模型接口；历史评测记录保留。
- **数据包**：`GET /lab/api/packs`、`POST /packs/{id}/install|cancel|remove`，SSE 事件 `pack`。清单 `urp_lab/data/packs.json` 写明 SHA256 和解压大小；下载支持续传，校验通过后解压到新目录再移入 `runtime/packs/<id>-<version>/`，路径越界、符号链接和超出声明大小的包一律拒绝。`/data/*` 优先读构建产物，其次读 `history` 包；`URP_PACKS_BASE_URL` 可替换下载地址。
- **桌面进程**：`python -m urp_lab serve --exit-with-stdin` 在标准输入关闭时有序退出。桌面应用设置 `URP_DESKTOP=1`、`URP_NODE`（JS 基线使用的运行时）、`URP_MODELS_REFRESH=1`。`URP_DESKTOP=1` 时 `POST /fieldsim` 和 `POST /sim/session` 返回 503 `simulator_unavailable`，智能体不会发起仿真批准，报告写明原因；Gazebo 由容器后端提供，容器里没有这个变量。
- **Windows**：`urp_lab/compat.py` 在 Windows 上提供 `signal.SIGKILL` 和基于 `taskkill` 的 `os.killpg`，因此 `services.py` 等进程代码不需要分支。`worker.py` 没有 `SIGALRM` 时用看门狗线程上报同样的超时结果。
