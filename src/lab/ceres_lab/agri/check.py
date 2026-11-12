"""CERES 农田行作独立几何与资源验证器

零信任独立核验：完全不依赖求解器中间状态，仅从输出折线复核里程、时间、离散曲率、
有效施作覆盖率与药箱补给积分，拦截求解器漏检或虚报。
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

CURVATURE_TOL = 1.02        # 离散圆弧采样曲率容差系数
GEOM_TOL = 1e-3             # 空间几何数值精度 (m)


def _length(pts) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _max_curvature(pts) -> float:
    worst = 0.
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        ab, bc, ac = math.dist(a, b), math.dist(b, c), math.dist(a, c)
        if ab < .02 or bc < .02:      # below the export resolution, curvature is rounding noise
            continue
        area2 = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if ab * bc * ac > 1e-12:
            worst = max(worst, 2 * area2 / (ab * bc * ac))
    return worst


def _corner_at_joint(a, b, c):
    h1 = math.atan2(b[1] - a[1], b[0] - a[0])
    h2 = math.atan2(c[1] - b[1], c[0] - b[0])
    return abs(math.remainder(h2 - h1, 2 * math.pi))


def check(inst: dict, routes: list[dict]) -> tuple[dict, dict]:
    rig = inst['rig']
    errors: list[str] = []
    warnings: list[str] = []
    r = float(rig['min_turn_radius_m'])
    width = float(rig['clearance_width_m'])
    outer = Polygon(inst['field'])
    holes = [Polygon(o) for o in inst.get('obstacles') or []]
    area = outer.difference(unary_union(holes)) if holes else outer
    lines = {x['id']: x for x in inst['lines']}
    res = rig['resource']
    consume = res['kind'] != 'none' and res['capacity']
    capacity = float(res['capacity']) if consume else None
    speed = {'work': rig['speed_work_mps'], 'turn': rig['speed_turn_mps'], 'transfer': rig['speed_transfer_mps']}
    served: dict[int, int] = {}
    total = work = turn = transfer_m = 0.
    turns: dict[str, int] = {}
    refills = 0
    reverse_m = 0.
    seconds = []
    worst_curv = 0.
    swaths = []
    walked = []
    crop_cross = 0.
    protected = None
    if inst.get('crop_protected'):
        inner = outer.buffer(-inst['headland_m'], join_style=2)
        if holes:
            inner = inner.difference(unary_union([h.buffer(inst['headland_m'], join_style=2) for h in holes]))
        protected = inner.buffer(-.05, join_style=2)
    home = inst['depot']
    stations = [s['xy'] for s in inst['stations']]
    for route in routes:
        level = 0.
        clock = 0.
        pos = None
        endurance = 0.
        segs = route.get('segments') or []
        if not segs:
            errors.append(f"{route.get('vehicle_name')} 没有分段几何")
            continue
        start = segs[0]['polyline'][0]
        if math.dist(start, home) > .05:
            errors.append(f"{route['vehicle_name']} 不是从车库出发")
        if math.dist(segs[-1]['polyline'][-1], home) > .05:
            errors.append(f"{route['vehicle_name']} 没有回到车库")
        for seg in segs:
            pts = seg['polyline']
            if pos is not None and math.dist(pos, pts[0]) > .01:
                errors.append(f"{route['vehicle_name']} 路径在 {pts[0]} 处不连续")
            pos = pts[-1]
            kind = seg['kind']
            if kind == 'service':
                if not any(math.dist(pos, s) < .05 for s in stations):
                    errors.append(f"{route['vehicle_name']} 的补给点不在声明的站点上")
                refills += 1
                level = 0.
                endurance = 0.
                clock += float(seg['duration_s'])
                continue
            length = _length(pts)
            if abs(length - float(seg['length_m'])) > .02 + 1e-4 * length:
                errors.append(f"{route['vehicle_name']} 分段长度与几何不符")
            total += length
            duration = length / speed[kind]
            if r <= 0 and kind != 'work':
                turned = sum(_corner_at_joint(a, b, c) for a, b, c in zip(pts, pts[1:], pts[2:]))
                duration += turned / rig['pivot_rate_radps']
            if float(seg['duration_s']) + 1e-3 < duration:
                errors.append(f"{route['vehicle_name']} 分段用时低于几何下限")
            clock += float(seg['duration_s'])
            endurance += float(seg['duration_s'])
            if rig.get('endurance_s') and endurance > rig['endurance_s'] + 1:
                errors.append(f"{route['vehicle_name']} 超过续航 {rig['endurance_s'] / 3600:g} h")
            if kind == 'work':
                lid = seg['line']
                line = lines.get(lid)
                if line is None:
                    errors.append(f'未知作业行 {lid}')
                    continue
                ends = (tuple(line['p1']), tuple(line['p2']))
                if {tuple(pts[0]), tuple(pts[-1])} != set(ends) and min(math.dist(pts[0], ends[0]) + math.dist(pts[-1], ends[1]),
                                                                         math.dist(pts[0], ends[1]) + math.dist(pts[-1], ends[0])) > .02:
                    errors.append(f'作业行 {lid} 的几何与实例不符')
                served[lid] = served.get(lid, 0) + 1
                work += length
                swaths.append(LineString(pts).buffer(float(rig['work_width_m']) / 2, cap_style=2, join_style=2))
                walked.append(LineString(pts))
                if consume:
                    level += float(line.get('demand', 0))
                    if level > capacity + 1e-6:
                        errors.append(f"{route['vehicle_name']} 在作业行 {lid} 超出容量 {capacity:g} {res['unit']}")
            else:
                if kind == 'turn':
                    turn += length
                    turns[seg.get('turn', '?')] = turns.get(seg.get('turn', '?'), 0) + 1
                else:
                    transfer_m += length
                if seg.get('reverse'):
                    reverse_m += length
                    if not rig.get('reverse'):
                        errors.append(f"{route['vehicle_name']} 含倒车，但机组不允许倒车")
                if r > 0:
                    worst_curv = max(worst_curv, _max_curvature(pts))
                walked.append(LineString(pts))
                if protected is not None and len(pts) > 1:
                    crop_cross += LineString(pts).intersection(protected).length
                if not inst.get('overfly') and not area.buffer(GEOM_TOL).covers(LineString(pts).buffer(width / 2 - .02, quad_segs=4)):
                    errors.append(f"{route['vehicle_name']} 的{'转弯' if kind == 'turn' else '转场'}车体越出田块或压到障碍")
        seconds.append(clock)
        route_sum = sum(float(s['duration_s']) for s in segs)
        if abs(route_sum - float(route.get('duration_s', route_sum))) > .05:
            errors.append(f"{route['vehicle_name']} 工期与分段之和不符")
    missing = [i for i in lines if i not in served]
    double = [i for i, c in served.items() if c > 1]
    if missing:
        errors.append(f'{len(missing)} 条作业行未作业：{missing[:8]}')
    if double:
        errors.append(f'{len(double)} 条作业行重复作业：{double[:8]}')
    if r > 0 and worst_curv > CURVATURE_TOL / r:
        errors.append(f'路径曲率 {worst_curv:.3f} 超过最小转弯半径 {r:g} m')
    if crop_cross > .5:
        (warnings if inst.get('allow_crop_crossing', True) else errors).append(f'转场穿过作物 {crop_cross:.1f} m')

    inner_area = outer.buffer(-inst['headland_m'], join_style=2)
    if holes:
        inner_area = inner_area.difference(unary_union([h.buffer(inst['headland_m'], join_style=2) for h in holes]))
    covered = unary_union(swaths) if swaths else Polygon()
    pattern = inst.get('pattern')
    fraction = covered.intersection(inner_area).area / inner_area.area if inner_area.area else 0.
    swept = sum(s.area for s in swaths)
    overlap = max(0., swept - covered.area) / swept if swept else 0.
    if pattern == 'swath' and fraction < inst.get('coverage_required', .9):
        errors.append(f'作业区覆盖率 {fraction:.1%} 低于 {inst.get("coverage_required", .9):.0%}')
    track = float(rig.get('track_m') or 0)
    tread = float(rig.get('tread_width_m') or 0)
    trafficked = None
    if track > 0 and walked:
        tracks = []
        for line in walked:
            if line.length > 1e-6:
                for side in (-track / 2, track / 2):
                    off = line.parallel_offset(abs(side), 'left' if side > 0 else 'right', join_style=2)
                    if not off.is_empty:
                        tracks.append(off.buffer(tread / 2, cap_style=2, join_style=2))
        trafficked = unary_union(tracks).intersection(area).area / area.area if tracks else 0.
    v = rig['speed_work_mps']
    ideal = work / v if v else 0
    machine_s = sum(seconds)
    makespan = max(seconds, default=0.)
    nonwork = total - work
    field_ha = area.area / 10000
    metrics = dict(
        total_distance_m=round(total, 2), working_distance_m=round(work, 2), nonworking_distance_m=round(nonwork, 2),
        turn_distance_m=round(turn, 2), transfer_distance_m=round(transfer_m, 2), reverse_distance_m=round(reverse_m, 2),
        nonworking_ratio=round(nonwork / total, 4) if total else None, turn_counts=turns, turn_count=sum(turns.values()),
        refills=refills, makespan_s=round(makespan, 2), machine_seconds=round(machine_s, 2),
        field_efficiency=round(ideal / machine_s, 4) if machine_s else None,
        effective_ha_h=round(work * float(inst['pitch_m']) / 10000 / (makespan / 3600), 3) if makespan else None,
        coverage_fraction=round(fraction, 4), overlap_fraction=round(overlap, 4), field_ha=round(field_ha, 3),
        trafficked_fraction=None if trafficked is None else round(trafficked, 4), crop_crossing_m=round(crop_cross, 2),
        max_curvature_per_m=round(worst_curv, 4), lines_served=len(served), lines_total=len(lines))
    result = dict(valid=not errors, errors=errors, warnings=warnings, computed_cost=round(total, 3),
                  checker='ceres_lab.agri.check / 分段几何、车体足迹、曲率、容量、续航、覆盖的独立复算', source='independent')
    return result, metrics
