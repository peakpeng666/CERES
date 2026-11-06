"""Algorithm library: catalog metadata plus one dispatch entry point. Every witness is re-scored independently."""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import math
import time

from .. import paths
from ..algorithm_ids import canonical_algorithm_id
from ..scenarios import maps
from ..planning import context, trace_context, without_stale_reference
from . import f2c_coverage, fleet, motion, routing

TIME = {'name': 'time_limit_s', 'type': 'number', 'default': 3, 'min': 0.05, 'max': 600, 'label': '时间预算 (s)'}
SEED = {'name': 'seed', 'type': 'integer', 'default': 42, 'min': 0, 'max': 2147483647, 'label': '随机种子'}
PARAMS = [TIME, SEED]
SELECT = lambda name, label, default, options: {'name': name, 'type': 'select', 'default': default, 'label': label,
                                                'options': [{'value': v, 'label': t} for v, t in options]}
F2C_FULL_PARAMS = [
    dict(TIME, default=30),
    SELECT('decomposition', '单元分解', 'boustrophedon', [('boustrophedon', 'Boustrophedon 分解'), ('trapezoidal', '梯形分解'), ('none', '不分解')]),
    SELECT('swath_objective', '作业行方向目标', 'n_swath', [('n_swath', '作业行最少'), ('swath_length', '作业行总长最短'), ('field_coverage', '覆盖面积最大')]),
    SELECT('turn_model', '转弯曲线', 'dubins', [('dubins', 'Dubins'), ('dubins_cc', 'Dubins 连续曲率'), ('reeds_shepp', 'Reeds–Shepp（可倒车）')]),
]
GRID_PARAMS = [dict(TIME, default=5), {'name': 'grid_cell_m', 'type': 'number', 'default': 0.09, 'min': 0.03, 'max': 0.5, 'label': '栅格尺寸 (m)'}]
OMPL_PARAMS = [dict(TIME, default=2), SEED, {'name': 'simplify', 'type': 'boolean', 'default': True, 'label': '求得后简化路径（非渐近最优规划器）'}]

#  id, name, problem class, library, family, guarantee, summary, refs, origin, extra
_TABLE = [
    ('urp.turn_mode_dp', '转弯模式分层 DP', 'turn_routing', '本项目', 'exact', 'optimal',
     '把每个任务点按进入方向拆成状态，在子集动态规划上求带转弯约束的精确最优路线。', ['vidal2017turn', 'clossey2001turn'],
     'src/research/src/urp_research/turn_modes/solver.py', {'limits': {'max_tasks': 12}}),
    ('urp.raw_oracle', '原图状态精确搜索', 'turn_routing', '本项目', 'exact', 'optimal',
     '直接在（已服务集合，来向，节点）状态图上做最短路，与分层 DP 相互独立，用来交叉验证最优性。', ['dijkstra1959'],
     'src/research/src/urp_research/turn_modes/oracle.py', {'limits': {'max_tasks': 8}}),
    ('urp.reset_dp', '方向松弛 DP', 'turn_routing', '本项目', 'relaxation', 'optimal_for_relaxation',
     '忽略进入方向的松弛问题最优解，是真实成本的下界；路线可能包含被禁止的转弯。', ['vidal2017turn'],
     'src/research/src/urp_research/turn_modes/solver.py', {'limits': {'max_tasks': 12}}),
    ('pyvrp.ils+urp.layered', 'PyVRP + 转弯分层补全', 'turn_routing', 'PyVRP', 'metaheuristic', 'none',
     'PyVRP 在方向松弛距离上决定任务分配与访问顺序，再由项目分层 DP 补全满足转弯约束的路径。', ['wouda2024pyvrp', 'vidal2017turn'], 'pyvrp', {}),
    ('ortools.gls+urp.layered', 'OR-Tools GLS + 转弯分层补全', 'turn_routing', 'OR-Tools', 'metaheuristic', 'none',
     'OR-Tools 引导局部搜索给出分配与顺序，项目分层 DP 补全转弯可行路径。', ['ortools', 'voudouris1999gls', 'vidal2017turn'], 'ortools', {}),
    ('pyvrp.ils', 'PyVRP 迭代局部搜索', 'euclidean_cvrp', 'PyVRP', 'metaheuristic', 'none',
     'PyVRP 0.14 的迭代局部搜索（含 SWAP* 邻域）；改进幅度以相同输入、预算与独立复算为准。', ['wouda2024pyvrp', 'vidal2022hgs', 'lourenco2003ils'], 'pyvrp', {}),
    ('ortools.gls', 'OR-Tools 引导局部搜索', 'euclidean_cvrp', 'OR-Tools', 'metaheuristic', 'none',
     'Google OR-Tools 路由求解器：最便宜弧构造 + 引导局部搜索。', ['ortools', 'voudouris1999gls'], 'ortools', {}),
    ('urp.js.savings', 'Clarke–Wright 节约算法', 'euclidean_cvrp', '本项目', 'heuristic', 'none',
     '经典并行节约构造算法，确定性、毫秒级，常作为构造基线。', ['clarke1964savings'],
     'src/frontend/core/routing/cvrp-solver.js', {'limits': {'max_tasks': 100}, 'params': [dict(TIME, default=10)]}),
    ('urp.js.nearest_neighbor', '最近邻构造', 'euclidean_cvrp', '本项目', 'baseline', 'none',
     '每次走向最近的可装载客户，满载返回；最朴素的构造基线。', ['toth2014vrp'],
     'src/frontend/core/routing/cvrp-solver.js', {'limits': {'max_tasks': 100}, 'params': [dict(TIME, default=10)]}),
    ('f2c.full', 'Fields2Cover 全流程', 'coverage', 'Fields2Cover', 'coverage', 'none',
     '地头生成、单元分解、作业行与跨单元连接；候选路径须通过足迹、覆盖率和采样曲率检查，不保证任意田块可解。',
     ['mier2023f2c', 'choset2000boustrophedon', 'dubins1957', 'reeds1990'], 'fields2cover', {'params': F2C_FULL_PARAMS}),
    ('f2c.boustrophedon', 'Fields2Cover 往复行', 'coverage', 'Fields2Cover', 'coverage', 'none',
     '凸作业区内的往复作业行与 Dubins 连接；独立检查足迹、覆盖率与采样曲率，执行能力另行验证。', ['mier2023f2c', 'dubins1957'], 'fields2cover', {}),
    ('f2c.snake', 'Fields2Cover 蛇形行', 'coverage', 'Fields2Cover', 'coverage', 'none',
     '隔行跳跃的蛇形作业顺序，减少急转弯；凸作业区，Dubins 转弯。', ['mier2023f2c', 'dubins1957'], 'fields2cover', {}),
    ('panel.boustrophedon', '平行往复（面板实现）', 'coverage', '基线', 'baseline', 'none',
     '按固定方向切出作业行，复用可见图绕障连接，接驳段另行规划；允许原地掉头，不约束转弯半径，不是全局最优下界。', ['choset2000boustrophedon', 'galceran2013cpp'], 'src/lab/urp_lab/solvers/coverage_baseline.py', {}),
]
GROUND_POLICY = {**SELECT('ground_policy', '地面风险策略', 'ignore', [('ignore', '忽略地面风险'), ('risk_adjust', '风险加权与限速')]),
                 'description': 'risk_adjust：用 instance 声明的已知地面图（ground.zones）给低附着区加当量米风险成本并允许绕行；需自由通行与总里程目标'}
GROUND_WEIGHT = {'name': 'ground_risk_weight', 'type': 'number', 'default': 1.0, 'min': 0.0, 'max': 10.0,
                 'label': '地面风险权重（当量米/米）',
                 'description': '每米低附着 μ 缺口折算的当量米成本；risk_adjust 时参与搜索；ignore 时仅作事后同口径评分'}
FLEET_PARAMS = [dict(TIME, default=5), SEED, GROUND_POLICY, GROUND_WEIGHT]
TIME_WINDOW_NOTE = {'native': 'PyVRP 原生 tw_early / tw_late，到达时间由独立检查器复算', 'time_dimension': 'OR-Tools 时间维度：等待进松弛，截止为强罚软上界，到达时间由独立检查器复算',
                    'unsupported': '不支持；含时间窗的任务会被拒绝，不会忽略约束'}


def _windows(algorithm_id: str) -> dict:
    support = fleet.TIME_WINDOW_SUPPORT[algorithm_id]
    return {'time_windows': support, 'time_window_note': TIME_WINDOW_NOTE[support]}


_TABLE += [
    ('pyvrp.fleet', 'PyVRP 固定车队', 'fleet_routing', 'PyVRP', 'metaheuristic', 'none',
     '在绕障最短路距离上用 PyVRP 迭代局部搜索分配任务、安排顺序，车辆数不超过车队规模，目标总里程最短。',
     ['wouda2024pyvrp', 'vidal2022hgs', 'lozanoperez1979visibility', 'dubins1957curvature', 'leny2007dubins', 'sucan2012ompl'], 'pyvrp', {'params': FLEET_PARAMS, 'limits': _windows('pyvrp.fleet')}),
    ('pyvrp.balanced', 'PyVRP 均衡工期', 'fleet_routing', 'PyVRP', 'metaheuristic', 'none',
     '车辆可在路线中途回库装卸，由同一搜索决定各趟归属；用二分收紧班次上限，使最晚完成时间最短，再比里程。',
     ['wouda2024pyvrp', 'vidal2022hgs', 'lozanoperez1979visibility', 'dubins1957curvature', 'leny2007dubins', 'sucan2012ompl'], 'pyvrp', {'params': FLEET_PARAMS, 'limits': _windows('pyvrp.balanced')}),
    ('ortools.fleet', 'OR-Tools 车队调度', 'fleet_routing', 'OR-Tools', 'metaheuristic', 'none',
     'OR-Tools 引导局部搜索；目标为“最晚完成时间最短”时加入跨车跨度代价，使各车工作量更均衡。',
     ['ortools', 'voudouris1999gls', 'lozanoperez1979visibility', 'dubins1957curvature', 'leny2007dubins', 'sucan2012ompl'], 'ortools', {'params': FLEET_PARAMS, 'limits': _windows('ortools.fleet')}),
    ('panel.savings_fleet', '节约算法 + 2-opt', 'fleet_routing', '基线', 'heuristic', 'none',
     'Clarke–Wright 节约算法在绕障距离上合并路线，再逐车 2-opt 改进；确定性、速度快。',
     ['clarke1964savings', 'croes1958twoopt'], 'src/lab/urp_lab/solvers/fleet.py', {'params': [dict(TIME, default=5), GROUND_POLICY, GROUND_WEIGHT], 'limits': _windows('panel.savings_fleet')}),
    ('panel.sweep', '扫描算法', 'fleet_routing', '基线', 'heuristic', 'none',
     '以车库为中心按极角扫过任务点，按容量切分给各车，再逐车 2-opt；适合任务围绕车库分布的场景。',
     ['gillett1974sweep', 'croes1958twoopt'], 'src/lab/urp_lab/solvers/fleet.py', {'params': [dict(TIME, default=5), GROUND_POLICY, GROUND_WEIGHT], 'limits': _windows('panel.sweep')}),
    ('panel.cluster_first', '先聚类后排程', 'fleet_routing', '基线', 'heuristic', 'none',
     '按车辆数做容量约束 k-means 分区，再分别构造与改进单车回路；不保证最优或路线互不交叉。',
     ['fisher1981gap', 'croes1958twoopt'], 'src/lab/urp_lab/solvers/fleet.py', {'params': FLEET_PARAMS, 'limits': _windows('panel.cluster_first')}),
]
for _aid, (_name, _mode, _refs) in motion.GRID_PLANNERS.items():
    _TABLE.append((_aid, _name, 'point_to_point', '基线', 'graph_search', 'resolution_optimal' if _mode != 'theta' else 'none',
                   {'astar': '8 邻接栅格上的 A*，八方向距离启发；在当前栅格分辨率下最优。',
                    'dijkstra': '无启发的一致代价搜索，作为 A* 的对照基线。',
                    'theta': '任意角度 Theta*：扩展时检查与祖先的可视性，得到更短、更少拐点的路径。'}[_mode], _refs,
                   'src/lab/urp_lab/solvers/motion.py', {'params': GRID_PARAMS}))
for _aid, (_name, _cls, _anytime, _refs) in motion.OMPL_PLANNERS.items():
    _optimal = _anytime or _cls == 'FMT'
    _TABLE.append((_aid, _name, 'point_to_point', 'OMPL', 'sampling', 'asymptotically_optimal' if _optimal else 'probabilistically_complete',
                   ('批量采样的渐近最优规划器：一次在大量样本上做快速行进树，样本越多路径越短。' if _cls == 'FMT' else
                    '渐近最优的采样规划器，时间越长路径越短；支持 SE(2)、Dubins 与 Reeds–Shepp 运动模型。' if _anytime else
                    '快速求得可行路径的采样规划器（不追求最优），求得后可做路径简化。'), _refs, 'ompl', {'params': OMPL_PARAMS}))

PROBLEM_LABEL = {'turn_routing': '转弯约束任务路由', 'euclidean_cvrp': '容量约束车辆路径（CVRP）', 'coverage': '覆盖作业规划',
                 'point_to_point': '点到点运动规划', 'fleet_routing': '多车任务分配与路径'}


def _version(origin: str, library: str) -> tuple[str, str]:
    if origin.startswith('src/'):
        return origin, hashlib.sha256((paths.REPO / origin).read_bytes()).hexdigest()
    dist = {'pyvrp': 'pyvrp', 'ortools': 'ortools', 'fields2cover': 'fields2cover', 'shapely': 'shapely', 'ompl': 'ompl'}[origin]
    try:
        version = md.version(dist)
    except md.PackageNotFoundError:
        version = None
    return f'{library} {version}' if version else library, version


def catalog():
    out = []
    f2c = f2c_coverage.available()
    for aid, name, pc, library, family, guarantee, summary, refs, origin, extra in _TABLE:
        label, version = _version(origin, library)
        if pc == 'turn_routing' and not origin.startswith('src/'):
            label += ' + src/research/src/urp_research/turn_modes/evaluate_candidate.py'
        source = 'project' if library == '本项目' else 'panel_baseline' if library == '基线' else 'third_party'
        uses_seed = aid.startswith(('pyvrp.', 'ompl.')) or aid == 'panel.cluster_first'
        seed_note = ('使用 seed；墙钟终止仍可能影响跨硬件重现' if uses_seed else
                     '此适配器不使用 seed；更换 seed 不构成独立随机重复')
        available, reason = True, None
        if aid.startswith('f2c.') and not f2c['available']:
            available, reason = False, f2c['reason']
        if aid.startswith('ompl.') and version is None:
            available, reason = False, 'OMPL Python 绑定未安装'
        if aid.startswith('urp.js.') and paths.node_command() is None:
            available, reason = False, '没有可用的 Node 运行时'
        out.append(dict(id=aid, name=name, problem_classes=[pc], problem_label=PROBLEM_LABEL[pc], family=family, source=source,
                        library=library, origin=label, version=version, turn_aware=pc == 'turn_routing' and aid != 'urp.reset_dp',
                        guarantee=guarantee, limits=extra.get('limits', {}), available=available, unavailable_reason=reason,
                        params=extra.get('params', PARAMS), description=summary, summary=summary, refs=refs,
                        randomness={'uses_seed': uses_seed, 'note': seed_note}))
    return out


def map_check(world, routes):
    g = maps.load(world)
    violations, best = [], math.inf
    threshold = maps.ROBOT_RADIUS_M + maps.MAP_CHECK_MARGIN_M
    for ri, r in enumerate(routes):
        for si, (a, b) in enumerate(zip(r['polyline'], r['polyline'][1:])):
            clearance, point = maps.segment_clearance(g, a, b)
            best = min(best, clearance)
            if clearance < threshold:
                violations.append(dict(route=ri, segment=si, point=list(point), clearance_m=clearance))
    return dict(checked=bool(routes), collision_free=bool(routes) and not violations,
                min_clearance_m=best if math.isfinite(best) else None, robot_radius_m=maps.ROBOT_RADIUS_M,
                margin_m=maps.MAP_CHECK_MARGIN_M, violations=violations[:200], violation_count=len(violations),
                method='沿整条路径按半个栅格采样，距离变换给出保守净空；未知区与地图外按障碍')


def coverage(inst):
    """Compatibility entry point for the versioned polyline coverage baseline."""
    from .coverage_baseline import coverage as solve_coverage
    return solve_coverage(inst)


def run_algorithm(scenario, instance, algorithm_id, params):
    algorithm_id = canonical_algorithm_id(algorithm_id)
    scenario = without_stale_reference(scenario, instance)
    start, cpu = time.perf_counter(), time.process_time()
    alg = next(a for a in catalog() if a['id'] == algorithm_id)
    pc = scenario['problem_class']
    extra, raw, trace = {}, None, []
    if not alg['available']:
        raise ValueError(alg['unavailable_reason'])
    if pc == 'turn_routing':
        routes, check, objective, status, extra, raw, trace = routing.run_turn(instance, algorithm_id, params, alg['limits'])
    elif pc == 'euclidean_cvrp':
        n = len(instance['coordinates']) - 1
        if n > alg['limits'].get('max_tasks', 10 ** 6):
            raise ValueError(f"{alg['name']} 当前实现最多 {alg['limits']['max_tasks']} 个客户")
        routes, check, objective, status, extra, raw, trace = routing.run_cvrp(instance, algorithm_id, params)
    elif pc == 'fleet_routing':
        routes, check, extra, raw, status, trace = fleet.solve(scenario, instance, algorithm_id, params)
        objective = fleet.objective_value(instance, extra) if routes else None
    elif pc == 'point_to_point':
        routes, check, extra, raw, status = motion.solve(scenario, instance, algorithm_id, params)
        objective = routes[0]['cost'] if routes else None
        trace = (raw or {}).get('trace') or []
    else:
        if algorithm_id == 'f2c.full':
            routes, check, extra, raw = f2c_coverage.solve_full(instance, params)
        elif algorithm_id.startswith('f2c.'):
            routes, check, extra, raw = f2c_coverage.solve(instance, algorithm_id.split('.')[1])
        else:
            routes, check, extra = coverage(instance)
        objective = sum(r['cost'] for r in routes) if routes else None
        status = 'feasible' if routes and check['valid'] else 'no_solution'
        if status == 'no_solution':
            routes, objective = [], None
    frame = scenario['frame']
    mc = map_check(frame['world'], routes) if frame['kind'] == 'map' else None
    length = sum(math.dist(a, b) for r in routes for a, b in zip(r['polyline'], r['polyline'][1:]))
    reference = scenario.get('reference') or {}
    if objective is not None and reference.get('bks'):
        extra['bks'] = reference['bks']
        extra['gap_to_bks_pct'] = 100.0 * (objective - reference['bks']) / reference['bks']
    unit = scenario['cost_unit']
    if pc == 'fleet_routing':
        unit = 's' if instance.get('objective') == 'makespan' else 'm'
    n_tasks = (len(instance.get('customers', [])) if pc == 'turn_routing' else len(instance['tasks']) if pc == 'fleet_routing' else
               len(instance['coordinates']) - 1 if pc == 'euclidean_cvrp' else 1 if pc == 'point_to_point' else 0)
    result = dict(status=status, objective=dict(value=objective, unit=unit, lower_bound=objective if algorithm_id == 'urp.reset_dp' else None),
                routes=routes, check=check, map_check=mc, trace=trace,
                algorithm={k: alg[k] for k in ('id', 'name', 'source', 'origin', 'version', 'randomness')}, raw_result=raw,
                metrics=dict(cost=objective, length=length if routes else None,
                             length_unit='unitless' if frame['kind'] == 'benchmark' else 'm',
                             n_routes=len(routes), n_tasks=n_tasks, wall_s=time.perf_counter() - start,
                             cpu_s=time.process_time() - cpu, **extra))
    result['planning'] = context(scenario, instance, result)
    result['trace_contract'] = trace_context(pc, instance)
    return result
