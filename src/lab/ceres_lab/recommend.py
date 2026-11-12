"""Algorithm recommendation: instance features → ranked algorithms with plain-language reasons and a comparison set.
Rules come from the algorithm literature (see each catalog entry's refs); a learned selector is used where trained."""
from __future__ import annotations

import math
import statistics as st

from . import learned, paths, time_windows
from .solvers import catalog


# Development-side audit evidence (experiments/runs/evolve-audit-20261023, Dutch fields, 3 s, seed 42); quoted with
# its scope so the reason is not mistaken for a guarantee on a new field.
FLEET_EVIDENCE = {
    'total_distance': '开发侧审计（真实田块 274 个可解实例，3 s）：PyVRP 首选可行 271/274，可行时 261 例为逐实例最好、其余与最好差距可忽略（平均 1.000 倍）；'
                      '该历史批次被拒的 3 条与障碍有毫米级擦碰（接口需求第 18 项）；当次 OR-Tools 重试通过，不保证新任务也可求解',
    'makespan': '开发侧审计（真实田块 68 个可解实例，3 s）：最晚完成目标下 OR-Tools 在 65 个实例上最好，'
                'PyVRP 只优化里程，平均最晚完成长 19%',
    'balanced': '物理预演实测（36 个合成任务，2 s，并行执行）：比 OR-Tools 工期短 1.4%、里程短 3.1%，24 个任务上最快；OR-Tools 在其余 12 个最快',
}


def _feature(name, key, value, unit=None, hint=None):
    return {'name': name, 'key': key, 'value': value, 'unit': unit, 'hint': hint}


def _clark_evans(points, area):
    """Nearest-neighbour index R: <1 clustered, ≈1 random, >1 regular (Clark & Evans 1954)."""
    if len(points) < 3 or area <= 0:
        return None
    nn = [min(math.dist(p, q) for j, q in enumerate(points) if j != i) for i, p in enumerate(points)]
    return st.mean(nn) / (0.5 * math.sqrt(area / len(points)))


def _field_operation(inst):
    """Line count and the two ratios that decide what kind of problem this is: tank size and turning radius."""
    rig, n = inst['rig'], len(inst['lines'])
    pitch, r = inst['pitch_m'], rig['min_turn_radius_m']
    res = rig['resource']
    demand = sum(x.get('demand', 0) for x in inst['lines'])
    fills = math.ceil(demand / res['capacity']) if res.get('kind') != 'none' and res.get('capacity') else 0
    ratio = 2 * r / pitch
    feats = [_feature('作业行', 'lines', n), _feature('机组', 'machines', inst['machines']),
             _feature('至少加注', 'fills', fills, '次', '总用量 / 容量'), _feature('掉头比 2r/w', 'turn_ratio', round(ratio, 2), None, '大于 1 时相邻行需要回绕'),
             _feature('地头', 'headland', inst['headland_m'], 'm'), _feature('目标', 'objective', '最晚完成' if inst.get('objective') == 'makespan' else '非作业里程')]
    score = {'agri.split_anneal': 3.0, 'ortools.field': 2.5, 'agri.skip': 1.0, 'agri.sequential': .8, 'agri.exact': -1.0}
    why = {k: [] for k in score}
    why['agri.split_anneal'].append('同时决定行序、方向与补给点')
    why['ortools.field'].append('独立的搜索实现，用来交叉检查')
    why['agri.sequential'].append('现场习惯做法，作为比较基准')
    if ratio > 1:
        score['agri.skip'] += 1.2
        why['agri.skip'].append(f'相邻行要回绕（2r/w={ratio:.1f}），跳 {math.ceil(ratio)} 行可全用 P 型转弯')
    else:
        score['agri.sequential'] += .6
        why['agri.skip'].append(f'相邻行可直接转入（2r/w={ratio:.1f}），跳行只增加里程')
    if fills > 1:
        score['agri.split_anneal'] += .5
        why['agri.split_anneal'].append(f'至少加注 {fills} 次，补给点位置影响里程')
    if n <= 12 and inst['machines'] == 1 and len(inst.get('stations') or []) == 1 and list(inst['stations'][0]['xy']) == list(inst['depot']):
        score['agri.exact'] = 2.8
        why['agri.exact'].append('行数少，可以求到最优，用来核对启发式')
    else:
        why['agri.exact'].append('只用于 12 行以内的单机组实例')
    return feats, score, why


def _fleet(inst):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from .solvers.fleet import fleet_classes
    n, K = len(inst['tasks']), inst['vehicles']['count']
    classes = fleet_classes(inst)
    cap, demand = inst['vehicles']['capacity'], sum(t['demand'] for t in inst['tasks'])
    area = Polygon(inst['boundary']).area
    obs = unary_union([Polygon(o['polygon']) for o in inst['obstacles']]).area if inst['obstacles'] else 0.0
    R = _clark_evans([tuple(t['xy']) for t in inst['tasks']], area)
    trips = math.ceil(demand / cap)
    feats = [_feature('任务点', 'n_tasks', n), _feature('车辆', 'vehicles', K), _feature('至少需要的趟数', 'min_trips', trips),
             _feature('车型类别', 'fleet_classes', len(classes), None, '同构=1；异构车队按类别容量/车速/足迹求解'),
             _feature('障碍占地', 'obstacle_ratio', round(100 * obs / area, 2), '%'),
             _feature('聚集度 R', 'clark_evans', None if R is None else round(R, 2), None, '<1 成片聚集，≈1 随机，>1 均匀散开'),
             _feature('目标', 'objective', '均衡完成时间' if inst['objective'] == 'makespan' else '总里程')]
    windowed = len(time_windows.by_task(inst))
    if windowed:
        feats.append(_feature('时间窗', 'time_windows', windowed, '个任务', '只有原生支持时间窗或用时间维度的算法才参与'))
    score = {'pyvrp.fleet': 0.0, 'pyvrp.balanced': 0.0, 'ortools.fleet': 0.0, 'panel.savings_fleet': 0.0, 'panel.sweep': 0.0, 'panel.cluster_first': 0.0}
    why = {k: [] for k in score}
    if inst['objective'] == 'makespan':
        score['pyvrp.balanced'] += 3.2
        why['pyvrp.balanced'].append('规则建议：按车辆实际行驶用时（含转向停车）收紧班次上限，直接压缩最晚完成时间')
        why['pyvrp.balanced'].append(FLEET_EVIDENCE['balanced'])
        score['ortools.fleet'] += 3
        why['ortools.fleet'].append('规则建议：OR-Tools 使用路线跨度与里程的加权目标；时间为匀速代理，不含转向或交通排队')
        why['ortools.fleet'].append(FLEET_EVIDENCE['makespan'])
        score['pyvrp.fleet'] += 1.5
        why['pyvrp.fleet'].append('以总里程为搜索目标，不直接均衡各车工作量；是否更优需同实例比较')
    else:
        score['pyvrp.balanced'] += .5
        why['pyvrp.balanced'].append('面向最晚完成时间；目标是总里程时里程会偏长')
        score['pyvrp.fleet'] += 3
        why['pyvrp.fleet'].append('规则建议：目标是总里程，优先试用 PyVRP 迭代局部搜索；当前场景的收益尚未评估')
        why['pyvrp.fleet'].append(FLEET_EVIDENCE['total_distance'])
        score['ortools.fleet'] += 2
        why['ortools.fleet'].append('采用不同搜索实现的对照，需用相同实例和预算比较')
    if R is not None and R < 0.8:
        score['panel.cluster_first'] += 1.8
        why['panel.cluster_first'].append(f'任务成片聚集（R={R:.2f}），可尝试按空间聚类分配；交叉通行仍需交通调度')
    else:
        why['panel.cluster_first'].append('按区域划分各车负责范围，便于现场分工')
    score['panel.savings_fleet'] += 1.2
    why['panel.savings_fleet'].append('确定性构造基线，可作为改进幅度的参照；耗时以实测为准')
    if inst['tasks'] and _depot_central(inst):
        score['panel.sweep'] += 1.0
        why['panel.sweep'].append('车库位于任务中间，可尝试按方位扫描切分；质量需同实例比较')
    else:
        why['panel.sweep'].append('按车库方位进行扫描切分的构造基线')
    if obs / area > 0.03:
        for k in why:
            why[k].append('所有算法都在绕障最短路距离上求解，障碍不会被直线穿越')
    if trips > K:
        for k in ('pyvrp.fleet', 'pyvrp.balanced', 'ortools.fleet'):
            why[k].append(f'总量需要至少 {trips} 趟，多趟往返会分配到 {K} 台车上')
    if len(classes) > 1:
        for k in ('panel.savings_fleet', 'panel.sweep', 'panel.cluster_first'):
            score[k] = 0.0
            why[k].append('构造基线只支持同构车队；异构车队由 PyVRP / OR-Tools 按类别容量、车速与足迹求解')
        why['pyvrp.fleet'].append(f'异构车队（{len(classes)} 类）按各类容量与车速分配任务与趟次')
        why['ortools.fleet'].append('OR-Tools 可为每类车使用不同车速的时间矩阵，均衡最晚完成')
    if windowed:
        score['pyvrp.balanced'] += 3
        why['pyvrp.balanced'].insert(0, '规则建议：时间窗直接进入搜索（tw_early / tw_late），用时按车辆实际行驶估计；到达时间由独立检查器复算')
        why['ortools.fleet'].insert(0, '时间窗作用在 OR-Tools 时间维度上，用时按车辆实际行驶估计；到达时间由独立检查器复算')
    budget = 2 if n <= 30 else 5 if n <= 120 else 15
    return feats, score, why, {'time_limit_s': budget}


def _coverage_order(scenario, instance, score, why, params_hint):
    """Report the admission status; offline inspection never rewrites live rankings or parameters."""
    from .evolve import serve
    return serve.recommend(scenario, instance), params_hint


def _depot_central(inst):
    xs, ys = [t['xy'][0] for t in inst['tasks']], [t['xy'][1] for t in inst['tasks']]
    dx, dy = inst['depot']
    return min(xs) < dx < max(xs) and min(ys) < dy < max(ys)


def recommend(scenario, instance, budget_s=None):
    """Rank algorithms; an omitted budget cannot borrow the learned study's profile."""
    pc = scenario['problem_class']
    algs = [a for a in catalog() if pc in a['problem_classes']]
    model = None
    params_hint: dict = {}
    blocked: dict[str, str] = {}
    if pc == 'fleet_routing':
        feats, score, why, params_hint = _fleet(instance)
        if len(instance['vehicles'].get('fleet') or []) > 1 or len(instance.get('depots') or [instance['depot']]) > 1:
            for aid in ('panel.savings_fleet', 'panel.sweep', 'panel.cluster_first'):
                blocked[aid] = '构造基线仅支持同构单车库；请使用 PyVRP 或 OR-Tools'
        if time_windows.present(instance):
            for a in algs:
                if a['limits'].get('time_windows') == 'unsupported':
                    blocked[a['id']] = '不支持时间窗：不跟踪到达时刻，含时间窗的任务会被拒绝；请用 pyvrp.balanced 或 ortools.fleet'
    elif pc == 'field_operation':
        feats, score, why = _field_operation(instance)
    else:
        feats, score, why = [], {a['id']: 0.0 for a in algs}, {a['id']: [] for a in algs}
        n = len(instance.get('customers', [])) if pc == 'turn_routing' else max(0, len(instance.get('demands', [])) - 1)
        if pc == 'turn_routing':
            feats = [_feature('任务数', 'n_tasks', n), _feature('禁止转弯', 'forbidden', len(instance['forbidden_turns'])),
                     _feature('转弯惩罚', 'penalties', len(instance['turn_penalties']))]
            if n <= 12:
                score['urp.turn_mode_dp'] += 3
                why['urp.turn_mode_dp'].append(f'任务数 {n} ≤ 12：优先尝试精确分层 DP，是否证最优以终态和预算为准')
            score['pyvrp.ils+urp.layered'] += 2
            why['pyvrp.ils+urp.layered'].append('规模较大时的首选：PyVRP 定顺序，项目分层 DP 补全转弯可行路径')
            score['ortools.gls+urp.layered'] += 1.5
            why['ortools.gls+urp.layered'].append('与 PyVRP 方案互为对照')
            if n <= 8:
                score['urp.raw_oracle'] += 1
                why['urp.raw_oracle'].append('小规模时可用独立精确搜索交叉验证最优值')
            why['urp.reset_dp'].append('只作下界参考：忽略进入方向，路线可能违反转弯规则')
            score['urp.reset_dp'] -= 5
        elif pc == 'euclidean_cvrp':
            feats = [_feature('客户数', 'n_tasks', n), _feature('容量', 'capacity', instance['capacity']),
                     _feature('最少车辆', 'min_routes', math.ceil(sum(instance['demands']) / instance['capacity']))]
            score['pyvrp.ils'] += 3
            why['pyvrp.ils'].append('CVRP 迭代局部搜索对照；相对最优已知解的差距由本次求解实测，不预设性能数字')
            score['ortools.gls'] += 2
            why['ortools.gls'].append('稳健的对照求解器')
            score['urp.js.savings'] += 1.5
            why['urp.js.savings'].append('节约算法构造基线（≤ 100 客户），耗时以实测为准')
            why['urp.js.nearest_neighbor'].append('最近邻构造基线，成本经独立验算')
            model = learned.recommend(scenario, instance, budget_s=budget_s)
            if model and model['status'] == 'available':
                chosen = model['prediction']['selected']
                score[chosen] = score.get(chosen, 0) + 5
                why[chosen].insert(0, '学习型选择器（kNN + 风险门控）对本实例的选择')
            if scenario.get('reference', {}).get('bks'):
                feats.append(_feature('最优已知解', 'bks', scenario['reference']['bks'], None,
                                      '已证最优' if scenario['reference'].get('optimal') else 'CVRPLIB 当前最好'))
        elif pc == 'coverage':
            frame = scenario['frame']['kind']
            feats = [_feature('作业幅宽', 'swath', instance['swath_width'], 'm'), _feature('转弯半径', 'turn', instance['turn_radius'], 'm')]
            from shapely.geometry import Polygon
            poly = Polygon(instance['field'])
            convexity = poly.area / poly.convex_hull.area
            feats.append(_feature('凸性', 'convexity', round(convexity, 3), None, '1 为凸多边形'))
            if frame != 'map' or convexity < 0.97:
                score['f2c.full'] += 3
                why['f2c.full'].append('真实或凹形田块：自动分解后逐块规划，并统计作业/非作业距离')
            if convexity < 0.97:
                for aid in ('f2c.boustrophedon', 'f2c.snake'):
                    blocked[aid] = f'田块凸性 {convexity:.2f}：此变体只处理凸作业区，请用全流程'
            score['f2c.boustrophedon'] += 2 if frame == 'map' else 0.5
            why['f2c.boustrophedon'].append('凸作业区的往复作业候选；须通过足迹、覆盖率与采样曲率检查，仿真适配另检')
            score['f2c.snake'] += 1
            why['f2c.snake'].append('隔行跳跃减少急转弯，适合转弯半径较大的机具')
            why['panel.boustrophedon'].append('允许原地转向的几何覆盖对照基线')
        elif pc == 'point_to_point':
            model_kind = instance['motion_model']
            feats = [_feature('运动模型', 'motion', {'holonomic': '全向', 'dubins': 'Dubins（只前进）', 'reeds_shepp': 'Reeds–Shepp（可倒车）'}[model_kind]),
                     _feature('直线距离', 'euclid', round(math.dist(instance['start'][:2], instance['goal'][:2]), 2), 'm')]
            if model_kind == 'holonomic':
                score['grid.theta_star'] += 3
                why['grid.theta_star'].append('全向机器人：任意角度 Theta* 路径更短、拐点更少')
                score['grid.astar'] += 2
                why['grid.astar'].append('栅格分辨率下最优，结果可复现')
                score['ompl.bit_star'] += 1.5
                why['ompl.bit_star'].append('渐近最优的采样规划器，可与栅格结果对照')
            else:
                score['ompl.bit_star'] += 3
                why['ompl.bit_star'].append('非全向运动模型：BIT* 在曲线约束下收敛快，路径随时间持续变短')
                score['ompl.informed_rrt_star'] += 2
                why['ompl.informed_rrt_star'].append('求得首条路径后在椭圆区域内集中采样')
                score['ompl.rrt_star'] += 1.5
                why['ompl.rrt_star'].append('经典渐近最优基线')
                for gid in ('grid.astar', 'grid.dijkstra', 'grid.theta_star'):
                    score[gid] -= 5
                    why[gid].append('栅格搜索不考虑最小转弯半径，仅作几何参照')
            why['ompl.rrt_connect'].append('优先搜索可行路径，不优化路径长度；耗时以实测为准')
    if pc == 'coverage':
        model, params_hint = _coverage_order(scenario, instance, score, why, params_hint)
    by_id = {a['id']: a for a in algs}
    rows = []
    for aid in sorted(score, key=lambda k: (-score[k], k)):
        a = by_id.get(aid)
        if a is None:
            continue
        n_tasks = len(instance.get('customers', [])) if pc == 'turn_routing' else \
            len(instance.get('coordinates', [0])) - 1 if pc == 'euclidean_cvrp' else \
            len(instance.get('lines', [])) if pc == 'field_operation' else 0
        limit = a['limits'].get('max_tasks')
        ok = a['available'] and (not limit or n_tasks <= limit) and aid not in blocked
        reasons = why.get(aid) or [a['summary']]
        if aid in blocked:
            reasons = [blocked[aid]]
        elif not a['available']:
            reasons = [a['unavailable_reason'] or '当前不可用']
        elif limit and n_tasks > limit:
            reasons = [f'任务数 {n_tasks} 超过该算法上限 {limit}']
        defaults = {p['name']: p['default'] for p in a['params']}
        defaults.update({k: v for k, v in params_hint.items() if k in defaults})
        rows.append(dict(algorithm_id=aid, rank=0, applicable=ok, score=round(score[aid], 2),
                         suitability='推荐' if ok and not rows else '可用' if ok else '不适用', reasons=reasons, suggested_params=defaults))
    rows.sort(key=lambda r: (not r['applicable'], -r['score']))
    for i, r in enumerate(rows):
        r['rank'] = i + 1
        r['suitability'] = '推荐' if i == 0 and r['applicable'] else ('可用' if r['applicable'] else '不适用')
    recommended = rows[0]['algorithm_id'] if rows and rows[0]['applicable'] else None
    compare = [r['algorithm_id'] for r in rows if r['applicable']][:3]
    learned_selector = model or dict(id='urp.cvrp_selection.knn_crc', status='not_applicable' if pc != 'euclidean_cvrp' else 'unavailable',
                                     reason=None if pc != 'euclidean_cvrp' else '历史训练数据未恢复',
                                     required=[str(paths.CVRP_SELECTION_DATA / 'data/raw.jsonl')], prediction=None)
    first = rows[0]['reasons'][0] if rows else ''
    learned_policy = {'lab.coverage.order.v1': 'coverage-order-v1'}.get((model or {}).get('id'), 'knn-crc-local-v1')
    return dict(problem_class=pc, policy=learned_policy if model and model.get('status') == 'available' else 'rules-v2',
                features=feats, learned_selector=learned_selector, ranking=rows, recommended=recommended, compare=compare,
                explanation=(f"推荐 {by_id[recommended]['name']}：{first}" if recommended else '没有适用的算法'))
