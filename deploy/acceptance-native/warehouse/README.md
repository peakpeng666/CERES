# 库房固定路线回放

执行源码固定为3acf840d1a0c16ab1cfcca15744e4ea6609ddb35。原始五任务路线来自既有公开Release，按压缩包及成员文件哈希读取；不重新求解、不改变任务顺序。

本地harness已对齐已审核3acf源码的simulation_execution_v2 / execution_budget_v2协议。所需固定策略回执缺失或不符时必须失败，不能静默回退为墙钟执行。本次只完成本地测试，没有运行ROS/Gazebo或GitHub原生验收，也不构成物理通过证据。

## 执行流程

1. 校验当前源码和历史输入；从固定归档还原61项历史资源文件到不存在的新缓存，核对精确文件与目录集，再作原45项场景资源比较。
2. 调用当前独立检查器与地图检查器，确认路线可行。
3. 在新运行目录创建回放记录和当前执行身份。
4. 通过原有网关，以ground_truth定位和唯一follow_path模式执行，显式请求time_basis=simulation及wall_watchdog_s=5400，不传入budget_s覆盖值。
5. 核验提交和终态的完整固定预算策略、关联身份，以及真实轨迹、五个任务到达情况、实际路径碰撞及正常退出。

工作流仍要求maze job通过后，本阶段才在另一台全新标准Ubuntu 22.04 runner运行。规划器按原公式选择的649.6秒不变，在本协议中明确为模拟时钟预算；既不以历史/本次测得时长替换，也不按RTF自动延长。历史0/5与5/5表示单条路线的任务完成数。

墙钟界限分层设置：生命周期7200秒；启动就绪上限1200秒；执行轮询5400秒看门狗加90秒终态处理。启动前及派发前均要求剩余时间至少覆盖30秒提交与5490秒轮询；迟于轮询截止的响应不能通过。正常会话关闭另有DELETE 180秒和状态确认240秒的独立有限预算，网关退出另限240秒。上层runner的验收阶段为9000秒，覆盖历史Fuel资源还原、网关启动和关闭等开销；安装阶段3600秒不变，warehouse job总上限220分钟。

资源来源独立标为historical_release_exact_bytes_v1，不代表当前Fuel版本，也不等同npm校验。执行前后均重新核对固定字节和完整缓存条目，额外目录或文件、链接及特殊文件不能通过。仅四个已核验两侧字节的Jersey路径允许exact_jersey_alias_bytes_v1映射；raw清单与canonical历史清单分别报告，不混同原始路径相等。

## 结果

输出固定字段的有界数值、枚举、布尔值和哈希摘要。新增execution_protocol明确模拟时钟、原规划预算、5400/7200墙钟边界以及提交/终态策略是否核验；策略缺失、变更或预算与原规划不一致时不能PASS。原有路线、碰撞、任务和清理判据保持不变。实时可行性未测得，不能将此协议与默认墙钟实验混同。

原有physical_budget_origin仍表示源码公式重建来源，新策略回执另行标明API实际返回的策略是否吻合。当前地图绑定YAML及图像字节哈希；历史材料没有记录原地图字节哈希。完整历史输入、运行日志和Fuel模型留在临时目录。

## 本地测试

配置SOURCE、MAPS和HISTORY目录后，从本目录运行：

```sh
PYTHONPATH="$SOURCE/src/lab:$SOURCE/src/research/src" URP_ROS_MAPS="$MAPS" URP_HISTORICAL_JSON="$HISTORY" "$SOURCE/src/lab/.venv/bin/python" -m unittest -v test_recheck test_lifecycle
```

上述测试检查数据、独立校验及模拟的接口生命周期；实际仿真由工作流执行。
## 资源记录与后段采样

`startup_diagnostics.process_resources` 提取会话已有的进程组采样，覆盖启动至清理。
CPU 累计值统计当前存活进程，进程退出后可下降。UTC 相对时间保持原记录顺序，
时间回退单独标记；超过 128 条时等距选取，并保留首尾记录。

`URP_WAREHOUSE_STARTUP_OBSERVER_DELAY_S=30` 将独立观测推迟至会话绑定后约 30 秒；
默认仍为 5 秒。两种设置均使用既有 8 秒观测窗口、阶段检查、身份检查和看门狗。
非法延迟配置仅关闭该项诊断，启动、超时、路线及验收条件保持原样。

### Bounded owned-process CPU observation

With the existing startup observer enabled and its delay set to `30`, the helper
also samples gateway descendants during cached `starting` observations between
30 and 38 seconds after the same session binding. The actual gateway `Popen`
object is passed immediately after creation; API PIDs and files are not ownership
authority. The gateway must remain the original helper child and process-group
leader. Each descendant's PID/start ticks and complete ancestry are revalidated
around executable identity reads. No argv, environment, maps, ptrace, signals, packages, application changes or
acceptance changes are added. The target-only thread extension below reads task
stat entries only for its one independently revalidated selected process.

`warehouse_process_observation` is a separate fixed-schema projection, not part
of the native observer's 16 KiB result. At most nine snapshots enumerate at most
4096 numeric process entries and account for at most 128 descendants/lifetimes;
the public output retains 32 highest observed-CPU lifetime rows with omission
counts. A lifetime is an invocation-local integer backed privately by PID/start
ticks. CPU seconds sum only adjacent matched identity/executable/parent intervals,
without child CPU counters. Covered wall seconds use monotonic snapshot-start spacing, not exact per-process
read instants; individual reads occur within each bounded scan. Interval counts
are reported, not utilization or a root-cause diagnosis. Late polls do not extend
the 30–38-second window, and reads completing after its end are discarded.

Executable categories are intentionally conservative: Python and Ruby remain
interpreter categories, even if the launch is running Gazebo. An executable
identity change drops that interval and makes its lifetime category unavailable.
Same-image exec is not detectable using this interface. Reparenting, reuse,
missing permissions, exits, scan/row caps and incomplete coverage are not replaced
with guessed values. Summed RSS is not unique physical memory, and sampled process
state is not a thread-level blocking diagnosis. Sampling is bounded by read counts,
not a preemptive real-time guarantee for individual procfs reads. Phase departure,
DELETE, errors and finalization stop observation without signaling any process.

Cancellation uses the existing project predicate, including the exact
RuntimeError("interrupted") used by its SIGTERM handler, as well as BaseException
cancellations. A failed GET /sim/state is rethrown unchanged and breaks matched
interval continuity, but does not prevent a later caller-driven valid startup
poll from sampling within the original window. No retries are added. The current
warehouse lifecycle normally enters cleanup after such an API error; DELETE
still closes sampling before attempting the original cleanup exactly once.

The exact native executable catalogue reuses `LAUNCH_GRAPH_PROCESSES` from
`resource_uri_diagnostics.py`, already sourced from the public pinned Humble
launch graph, together with its exact `create`, `image_bridge`, `turtlebot4_node`
and `create3_nodes` names. It does not use that module's command-line/log aliases.
Thus native `motion_control`, `sensors_node`, and `wheel_status_publisher` can be
distinguished, while an interpreter executing a similarly named script remains
Python/Ruby. Unknown basenames are never published verbatim.


### Target-only thread discriminator

The same observer now includes a nested `thread_observation` fixed-schema
projection, leaving the existing native observer output and its 16 KiB cap alone.
After one matched process interval, it may select the unique highest positive
CPU-delta candidate among the bounded, verified descendant samples, only if that
candidate is currently Ruby and has not crossed an executable-identity change.
A tie, no positive interval or a non-Ruby leader does not select a target. Selection
freezes the actual PID/start/executable and invocation-local process lifetime ID;
no previous-run identity is used and no later process is silently substituted.
The complete private source-owned ancestry chain is frozen and checked before
and after each thread batch, with full chain checks around both exe reads.
This is not evidence that Ruby has a particular Gazebo launch role.

Only `/proc/<target>/task/<tid>/stat` is added to existing process/exe reads.
At most 128 thread entries and 128 accounting lifetimes are retained, within the
same maximum nine snapshots and unchanged 30–38 second window. Selection consumes
the first process interval, so the thread observation window can be shorter.
Each thread row is read twice and identity/name/counter consistency checked;
only adjacent matching process/TID/start/name intervals contribute CPU seconds.
A rename starts a new accounting lifetime, including across a missed snapshot.
Per-thread timing uses monotonic second-read completion. Comparable process CPU
consumption is sampled over the successful thread-batch intervals, timestamped
after validation completes; counters and timestamps are not atomic. Missing
threads and differing read times mean thread/process totals need not be equal.
These are CPU consumption and coverage measurements, not utilization.

Only three public categories are allowed: `leader` (TID equals process PID),
`named_llvmpipe_worker` (exact names `llvmpipe-0` through `llvmpipe-31`), and
`unknown_worker`. Three category aggregates include all accounted lifetimes;
the public top 16 lifetime rows have an explicit omission count. Raw names,
process/thread IDs, executable paths and exception text are never projected.
Name provenance is pinned in `thread_observer.py` to official Mesa GitLab sources
for `mesa-23.2.1`: `lp_rast.c`, `lp_limits.h`, and `u_thread.c`, including retrieved
SHA256 hashes. Those sources define names, the 32-worker bound, and Linux
`pthread_setname_np`; they do not establish the installed Mesa version. Arbitrary
`RenderThread` names are not classified as verified Gazebo workers. Even observed
llvmpipe-named CPU activity alone is not a rendering/physics root-cause diagnosis.

No-target/ties, scan/lifetime caps, loss, rename, read permissions, counter
regression, cutoff and continuity gaps are explicit. Reads are bounded in count,
not a hard real-time preemption guarantee. A batch finishing beyond the unchanged
cutoff is discarded. Cancellation, cleanup, phase and API-error precedence retain
the process observer's contracts: ordinary optional diagnostic failures remain
isolated, exact project cancellations propagate, and original cleanup is still
attempted once. No new packages, native run, acceptance relaxation or environment
intervention is required to test this helper-only code.

### Optional installed-runtime identity evidence

`warehouse_runtime_identity` is an observational, separately bounded projection.
Immediately before gateway creation, it queries one fixed 21-package catalogue
from `/var/lib/dpkg` and hashes 14 fixed installed Humble xacro/launch/world files.
It does not install packages, execute xacro, resolve new resource paths, inspect
process arguments/environment/maps, create graphics contexts, or add subscribers.
The package query has a 3-second execution deadline and 64 KiB stdout limit;
its sole child is killed when needed and reaped before returning. Installed files
are regular, ownership-checked, non-symlink files under an anchored directory
chain, capped at 256 KiB each, and checked for identity/content-metadata changes.

After existing application shutdown and owned-process cleanup, the helper may
read only the fresh invocation HOME `.ignition/rendering/ogre.log` (128 KiB cap).
Rendering6's `IGN_HOMEDIR` is a compile-time macro for `HOME` on Linux, not the
similarly named runtime environment variable. The known runner HOME comes from
`runner/run.py`, not from live environment inspection. A preexisting, changed,
unowned, nonregular, symlinked, conflicting, or oversized log supplies no
renderer identity claim. Output contains only finite vendor/renderer families
and validated numeric API versions, never log lines or arbitrary GPU strings.

The evidence groups explicitly distinguish installed package database records,
installed file bytes, and renderer log claims. None proves loaded shared-library
identity, which remains `unavailable`. A fresh log is invocation-associated,
not bound to the observed Ruby process. An OpenGL API version is not a Mesa
package version. Installed bytes are not proof that an xacro was expanded or a
sensor was active. Missing evidence is reported without changing acceptance.

Ordinary optional evidence faults cannot change the application/API result or
cleanup outcome. Cancellation keeps the project's exact exception identity and
is deferred until existing cleanup and result writing have run. An earlier
application/API error remains authoritative over later optional diagnostic
failures. Observer timing, sensors, scene, route, readiness and cleanup criteria
are unchanged. No `LP_NUM_THREADS` default is introduced; both LP1 and LP0 were
negative controlled trials, not resource fixes.

One fail-closed diagnostic containment exception is explicit: if the package
query's own child cannot be confirmed reaped after bounded cleanup, gateway
creation is aborted. Its actual `Popen` handle is retained for one additional
bounded final cleanup attempt. No unrelated processes are inspected or killed.
The public finite cleanup status and sticky prior-uncertainty flag distinguish
this condition from unavailable package metadata, even if the final retry reaps
successfully. Exact cancellation identity still wins over ordinary diagnostic
cleanup errors. This adds no application readiness test and cannot turn a
failed application run into a pass.
