"""Footprint-aware *polyline* connectors for the in-place-turn coverage baseline.

This reuses the fleet visibility graph; it is not Fields2Cover, cell decomposition,
or a curvature-constrained planner. Work swaths are never rerouted or credited as
covered merely because a connector passed near them. The caller independently
checks the complete swept footprint and the original working-area threshold.
"""
from __future__ import annotations

from shapely.geometry import LineString, Point, Polygon

from .. import coverage_limits

MAX_CONNECTOR_GRAPH_NODES = 512


def connect_swaths(inst: dict, swaths: list[list]) -> tuple[list, dict, str | None]:
    # Coverage excludes the execution system's separately planned approach leg
    # (CONTRACT §1). Preserve that common baseline/Fields2Cover scope.
    coverage_limits.output_budget(segments=len(swaths), points=sum(map(len, swaths)))
    start = list(swaths[0][0])
    points = [list(p) for swath in swaths for p in swath]
    area = Polygon(inst['field'], inst.get('obstacles', []))
    radius = inst['robot_width'] / 2
    metadata = {'connector_model': 'polyline_visibility_graph; in-place turns allowed',
                'start_connection_included': False,
                'approach_scope': '接驳段由执行系统另行规划与检查，不计入覆盖目标',
                'connector_detours': 0, 'connector_graph_node_limit': MAX_CONNECTOR_GRAPH_NODES}
    links, previous = [], start
    for swath in swaths:
        a, b = previous, swath[0]
        segment = Point(a) if a == list(b) else LineString([a, b])
        links.append(not footprint_valid(area, segment, radius))
        previous = list(swath[-1])
    if not any(links):
        return points, metadata, None

    # Graph builds an all-pairs distance/predecessor matrix. Bound the fallback
    # rather than turning an oversized input into an unbounded allocation.
    estimate = len(points) + 4 * sum(len(o) for o in inst.get('obstacles', [])) + 2 * len(inst['field'])
    if estimate > MAX_CONNECTOR_GRAPH_NODES:
        return points, metadata, '绕障连接可见图超出基线节点预算；请使用完整覆盖求解器或减少作业行'
    from .fleet import Graph
    graph = Graph({'boundary': inst['field'], 'obstacles': [dict(polygon=o) for o in inst.get('obstacles', [])],
                   'robot_radius': radius, 'depot': start,
                   'tasks': [dict(xy=p) for swath in swaths for p in swath]})
    connected, previous_index, first_index = [start], 0, 1
    for swath, blocked in zip(swaths, links):
        if blocked:
            try:
                segment = graph.path(previous_index, first_index)
            except ValueError as error:
                return points, metadata, f'作业行之间没有找到满足车体净空的连接：{error}'
            connected.extend(segment[1:])
            metadata['connector_detours'] += 1
        elif connected[-1] != list(swath[0]):
            connected.append(list(swath[0]))
        # Preserve every work segment exactly; do not substitute a detour while
        # retaining credit for a swath the robot did not actually traverse.
        connected.extend(list(p) for p in swath[1:])
        previous_index = first_index + len(swath) - 1
        first_index += len(swath)
        coverage_limits.output_budget(points=len(connected))
    return connected, metadata, None


def footprint_valid(area, path, radius: float) -> bool:
    """Full original footprint plus exact clearance, with 1 nm numeric tolerance.

    Shapely round buffers approximate circles by inscribed polygons. Keep that
    existing check, but do not let its chords hide a near-corner disk collision.
    Boundary includes both the field exterior and every obstacle interior ring.
    """
    return (area.covers(path.buffer(radius)) and area.covers(path)
            and path.distance(area.boundary) + 1e-9 >= radius)


def coverage(inst):
    """Panel boustrophedon baseline: parallel swaths along x, connected end to end (in-place turns allowed)."""
    from shapely.geometry import Polygon, LineString
    from shapely.ops import unary_union
    try:
        area, interior = coverage_limits.geometry(inst['field'], inst.get('obstacles', []), inst['headland_width'])
        coverage_limits.scan_budget(interior, inst['swath_width'], 0)
    except ValueError as error:
        return [], {'valid': False, 'errors': [str(error)]}, {}
    x0, y0, x1, y1 = interior.bounds
    step = inst['swath_width']
    points, strips, swaths = [], [], []
    y, _ = coverage_limits.scan_range(y0, y1, step)
    row, scans = 0, 0
    while y < y1:
        # The preflight is mathematical; keep a hard cap for rounding drift.
        if scans >= coverage_limits.MAX_SCAN_LINES:
            return [], {'valid': False, 'errors': ['覆盖扫描行数超出预算']}, {}
        scans += 1
        cut = interior.intersection(LineString([(x0 - 1, y), (x1 + 1, y)]))
        lines = [cut] if cut.geom_type == 'LineString' else list(getattr(cut, 'geoms', []))
        for line in lines:
            if line.geom_type != 'LineString' or line.is_empty:
                continue
            pair = list(line.coords)
            if row % 2:
                pair.reverse()
            try:
                coverage_limits.output_budget(segments=len(swaths) + 1, points=len(points) + len(pair))
            except ValueError as error:
                return [], {'valid': False, 'errors': [str(error)]}, {}
            points.extend(pair)
            swaths.append(pair)
            strips.append(line.buffer(step / 2, cap_style=2))
            row += 1
        next_y = y + step
        if next_y <= y:
            return [], {'valid': False, 'errors': ['覆盖扫描浮点步进不能前进']}, {}
        y = next_y
    if len(points) < 2:
        return [], {'valid': False, 'errors': ['没有作业行']}, {}
    try:
        points, connector_metrics, connector_error = connect_swaths(inst, swaths)
        coverage_limits.output_budget(points=len(points))
    except ValueError as error:
        return [], {'valid': False, 'errors': [str(error)]}, {}
    path = LineString(points)
    footprint_ok = footprint_valid(area, path, inst['robot_width'] / 2)
    covered = unary_union(strips).intersection(area).area
    working_fraction = unary_union(strips).intersection(interior).area / interior.area
    errors = ([] if footprint_ok else ['连接段或机器人足迹超出田块/穿越障碍'])
    if connector_error:
        errors.append(connector_error)
    if working_fraction < .90:
        errors.append('内缩作业区覆盖率低于 90%')
    return [dict(vehicle=0, nodes=None, sequence=[], services=[], polyline=points, cost=path.length, load=0)], \
        dict(valid=not errors, errors=errors, computed_cost=path.length,
             checker='urp_lab.solvers.coverage · Shapely 扫掠足迹', source='panel_baseline'), \
        dict(coverage_area_m2=covered, field_area_m2=area.area, coverage_fraction=covered / area.area,
             working_area_fraction=working_fraction, coverage_required_fraction=.90,
             turn_radius_enforced=False, coverage_scope='内缩作业区；地头不作业；不约束转弯半径', **connector_metrics)

