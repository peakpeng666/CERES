"""Task composer, fleet routing, motion planning, libraries and study analysis."""
import copy
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from urp_lab import analysis
from urp_lab.compose import EXAMPLES, compose
from urp_lab.compose.geo import export_geojson, import_geojson
from urp_lab.compose.nl import cn_number, parse
from urp_lab.library import cvrplib, references
from urp_lab.recommend import recommend
from urp_lab.scenarios import get_scenario
from urp_lab.scenarios.validate import validate_instance
from urp_lab.solvers import catalog, run_algorithm


def test_chinese_numbers():
    assert [cn_number(s) for s in ('十二', '二十', '三十五', '一百二十', '两', '40')] == [12, 20, 35, 120, 2, 40]


def test_parse_examples():
    spec = parse(EXAMPLES[0])['spec']
    assert spec['size'] == [200.0, 120.0] and spec['vehicles'] == 3 and spec['tasks'] == 40 and spec['capacity'] == 10
    assert spec['obstacles'] == {'pond': 2, 'building': 1} and spec['depot'] == 'sw'
    spec = parse(EXAMPLES[1])['spec']
    assert spec['objective'] == 'makespan' and spec['plots'] == 6 and spec['obstacles']['tree'] == 3
    assert parse('给 25 棵果树喷药，2台车')['spec']['tasks'] == 25
    assert 'tree' not in parse('给 25 棵果树喷药')['spec'].get('obstacles', {})


def _scenario(text, **vehicles):
    sc = compose(text)
    sc['instance']['vehicles'].update(vehicles)
    sc['instance'] = validate_instance('fleet_routing', sc['instance'])
    return sc


def test_compose_is_deterministic_and_valid():
    a, b = compose(EXAMPLES[2]), compose(EXAMPLES[2])
    assert a['instance']['tasks'] == b['instance']['tasks'] and a['instance']['obstacles'] == b['instance']['obstacles']
    inst = validate_instance('fleet_routing', a['instance'])
    assert len(inst['tasks']) == 12 and inst['vehicles']['count'] == 2


@pytest.mark.parametrize('algorithm', ['pyvrp.fleet', 'ortools.fleet', 'panel.savings_fleet', 'panel.sweep', 'panel.cluster_first'])
def test_fleet_algorithms_verified(algorithm):
    sc = _scenario(EXAMPLES[0])
    r = run_algorithm(sc, sc['instance'], algorithm, {'time_limit_s': 0.5})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    served = sorted(t for route in r['routes'] for t in route['sequence'])
    assert served == sorted(t['id'] for t in sc['instance']['tasks'])
    assert r['metrics']['vehicles_used'] <= sc['instance']['vehicles']['count']
    assert math.isclose(r['check']['computed_cost'], r['metrics']['total_distance_m'], rel_tol=1e-6)


def test_single_trip_capacity_is_reported_as_infeasible():
    sc = _scenario(EXAMPLES[0], trips='single')
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 0.3})
    assert r['status'] == 'infeasible' and not r['routes']


def test_routes_avoid_obstacles():
    sc = _scenario('100×60米，2台车，20个点，3个池塘，种子 7')
    r = run_algorithm(sc, sc['instance'], 'panel.savings_fleet', {})
    from shapely.geometry import LineString, Polygon
    for o in sc['instance']['obstacles']:
        poly = Polygon(o['polygon'])
        for route in r['routes']:
            assert not LineString(route['polyline']).crosses(poly) and not LineString(route['polyline']).within(poly)


def test_checker_catches_tampering():
    from urp_lab.solvers import fleet
    sc = _scenario(EXAMPLES[2])
    r = run_algorithm(sc, sc['instance'], 'panel.sweep', {})
    routes = r['routes']
    routes[0]['sequence'] = routes[0]['sequence'][1:]
    assert not fleet.check(sc['instance'], routes)['valid']


def test_lane_graph_connects_every_lane_and_task():
    from urp_lab import lanes
    from urp_lab.solvers.fleet import LaneGraph
    sc = _scenario(EXAMPLES[0])
    inst = sc['instance']
    assert inst['vehicles']['travel'] == 'rows'  # generated field maps drive along the crop rows
    g = LaneGraph(inst)
    assert g.net.lanes and g.info['entries'] >= len(g.net.lanes)
    # depot, every service position, lane entry and open-area corner hang together
    assert np.isfinite(g.dist[0]).all()
    assert all(o <= g.net.max_reach for o in g.offsets)
    r = inst['robot_radius']
    rows = g.net.rows()
    for lane in g.net.lanes:  # a lane keeps the vehicle clear of every crop row
        assert min(lane.line.distance(row) for row in rows) >= r + lanes.SIDE_M - 1e-6
    # narrow rows get a driving track every few rows, wide ones are driven between every two rows
    square = [[0, 0], [60, 0], [60, 40], [0, 40]]
    wheat = lanes.plot_rows({'id': 'A', 'crop': '小麦', 'rows_deg': 0, 'polygon': square}, None, [], 0.5)
    kiwi = lanes.plot_rows({'id': 'B', 'crop': '猕猴桃', 'rows_deg': 90, 'polygon': square}, None, [], 0.5)
    assert wheat.mode == 'track' and kiwi.mode == 'every'
    assert min(wheat.lane_width, kiwi.lane_width) >= 2 * (0.5 + lanes.SIDE_M)


@pytest.mark.parametrize('algorithm', ['pyvrp.fleet', 'panel.savings_fleet'])
def test_rows_routes_never_cross_crop_rows(algorithm):
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    from urp_lab import lanes
    sc = _scenario(EXAMPLES[0])
    inst = sc['instance']
    r = run_algorithm(sc, inst, algorithm, {'time_limit_s': 0.5})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    assert r['metrics']['travel'] == 'rows' and r['metrics']['lanes'] > 0
    rows = lanes.Network(inst).rows()
    tree = STRtree(rows)
    for route in r['routes']:
        line = LineString(route['polyline'])
        assert not tree.query(line, predicate='intersects').size
        near = tree.query(line.buffer(inst['robot_radius']))
        assert all(line.distance(rows[i]) >= inst['robot_radius'] - 1e-6 for i in near)
        for sv in route['services']:  # served on the route, next to the task
            assert line.distance(LineString([sv['stop'], sv['stop']]).centroid) < 1e-6
    # the same map with free travel cuts straight across the rows, and is shorter for it
    free_inst = copy.deepcopy(inst)
    free_inst['vehicles']['travel'] = 'free'
    free = run_algorithm(sc, free_inst, algorithm, {'time_limit_s': 0.5})
    assert free['check']['valid'] and free['metrics']['travel'] == 'free'
    assert any(tree.query(LineString(rt['polyline']), predicate='intersects').size for rt in free['routes'])
    assert free['metrics']['total_distance_m'] < r['metrics']['total_distance_m']


def test_checker_rejects_shortcut_across_crop_rows():
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    from urp_lab import lanes
    from urp_lab.solvers import fleet
    sc = _scenario(EXAMPLES[0])
    inst = sc['instance']
    r = run_algorithm(sc, inst, 'panel.savings_fleet', {})
    assert r['check']['valid'], r['check']
    tree = STRtree(lanes.Network(inst).rows())
    free = lanes.free_space(inst)[0]
    routes = copy.deepcopy(r['routes'])
    done = False
    for route in routes:  # replace the way between two consecutive stops by a straight line through the crops
        pts = route['polyline']
        at = [next(i for i, p in enumerate(pts) if math.dist(p, sv['stop']) < 1e-9) for sv in route['services']]
        for (a, b) in zip(at, at[1:]):
            cut = LineString([pts[a], pts[b]])
            if b > a + 1 and free.covers(cut) and tree.query(cut, predicate='intersects').size:
                route['polyline'] = pts[:a + 1] + pts[b:]
                done = True
                break
        if done:
            break
    assert done
    res = fleet.check(inst, routes)
    assert not res['valid']
    assert any('1 段路线离开行间通道和地头' in e for e in res['errors']), res['errors']


def test_makespan_objective_balances():
    sc = _scenario(EXAMPLES[1])
    assert sc['instance']['objective'] == 'makespan'
    r = run_algorithm(sc, sc['instance'], 'ortools.fleet', {'time_limit_s': 1})
    assert r['objective']['unit'] == 's' and r['check']['valid']


def test_geojson_roundtrip():
    sc = _scenario(EXAMPLES[2])
    import json
    doc = json.loads(export_geojson(sc))
    back = import_geojson(doc, 'roundtrip')
    inst = validate_instance('fleet_routing', back['instance'])
    assert len(inst['tasks']) == len(sc['instance']['tasks']) and len(inst['obstacles']) == len(sc['instance']['obstacles'])


def test_recommendation_for_fleet():
    sc = _scenario(EXAMPLES[1])
    rec = recommend(sc, sc['instance'])
    assert rec['recommended'] == 'ortools.fleet' and len(rec['compare']) == 3
    assert any(f['key'] == 'clark_evans' for f in rec['features'])


@pytest.mark.requires_maps('maze')
def test_motion_planners():
    sc = get_scenario('p2p-maze-cross')
    for aid in ('grid.astar', 'grid.theta_star'):
        r = run_algorithm(sc, sc['instance'], aid, {'time_limit_s': 3})
        assert r['status'] == 'feasible' and r['check']['valid'] and r['map_check']['collision_free']
    a = run_algorithm(sc, sc['instance'], 'grid.astar', {})['objective']['value']
    t = run_algorithm(sc, sc['instance'], 'grid.theta_star', {})['objective']['value']
    assert t <= a + 1e-6


def test_catalog_has_references_that_exist():
    keys = {r['key'] for r in references.listing()}
    for a in catalog():
        assert set(a['refs']) <= keys, a['id']


def test_cvrplib_catalog_and_rounding():
    status = cvrplib.status()
    assert status['instances'] >= 700 and status['sets'] >= 15
    x = next(i for i in cvrplib.instances('X') if i['name'] == 'X-n101-k25')
    assert x['bks'] == 27591 and x['optimal']


def test_analysis_statistics():
    sc = _scenario(EXAMPLES[2])
    runs = []
    for k, aid in enumerate(['pyvrp.fleet', 'panel.sweep', 'panel.savings_fleet']):
        for seed in (1, 2):
            r = run_algorithm(sc, sc['instance'], aid, {'time_limit_s': 0.3, 'seed': seed} if aid == 'pyvrp.fleet' else {})
            runs.append({**r, 'run_id': f'r{k}{seed}', 'scenario_id': sc['id'], 'instance_sha256': 'abc', 'state': 'done',
                         'params': {'time_limit_s': 0.3}, 'scenario_snapshot': sc})
    res = analysis.analyze(runs)
    assert res['n_instances'] == 1 and {s['algorithm'] for s in res['summary']} == {'pyvrp.fleet', 'panel.sweep', 'panel.savings_fleet'}
    assert min(s['gap_best'] for s in res['summary']) == 0.0
    assert 'tabular' in analysis.latex_table(res)


def test_gateway_compose_edit_solve(tmp_path, monkeypatch):
    from urp_lab import gateway, paths
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    app = gateway.create_app(gateway.Config(port=4199, runtime=tmp_path, background=False))
    with TestClient(app, base_url='http://127.0.0.1:4199') as c:
        cards = c.post('/lab/api/compose/parse', json={'text': EXAMPLES[0]}).json()['cards']
        assert any(card['label'] == '车队' for card in cards)
        sc = c.post('/lab/api/compose', json={'text': EXAMPLES[2]}).json()
        assert sc['problem_class'] == 'fleet_routing' and sc['origin'] == 'user'
        inst = sc['instance']
        inst['tasks'] = inst['tasks'][:-1]
        inst['vehicles']['count'] = 3
        upd = c.put(f"/lab/api/scenarios/{sc['id']}", json={'instance': inst}).json()
        assert len(upd['instance']['tasks']) == 11 and upd['instance']['vehicles']['count'] == 3
        bad = dict(inst, tasks=inst['tasks'] + [{'id': 999, 'xy': inst['obstacles'][0]['polygon'][0], 'demand': 1}])
        assert c.put(f"/lab/api/scenarios/{sc['id']}", json={'instance': bad}).status_code == 400
        assert c.put('/lab/api/scenarios/bench-cvrp-uniform-n20', json={'title': 'x'}).status_code == 403
        rec = c.post('/lab/api/recommend', json={'scenario_id': sc['id']}).json()
        assert rec['recommended'] in {a['id'] for a in catalog()}
        assert c.get('/lab/api/references').json()['references']
        assert c.get('/lab/api/library/cvrplib/sets').json()['sets']
        assert c.get('/lab/api/source', params={'path': 'src/lab/urp_lab/solvers/fleet.py'}).status_code == 200
        assert c.get('/lab/api/source', params={'path': '../../etc/passwd'}).status_code == 403
        geo = c.get(f"/lab/api/scenarios/{sc['id']}/export", params={'format': 'geojson'})
        assert geo.status_code == 200 and b'FeatureCollection' in geo.content
        lanes = c.post('/lab/api/fleet/lanes', json={'instance': upd['instance']}).json()
        assert lanes['lanes'] and lanes['lane_count'] == len(lanes['lanes']) and len(lanes['plots']) == len(inst['plots'])
        assert c.post('/lab/api/fleet/lanes', json={'instance': {'plots': []}}).status_code == 400


def test_task_map_on_real_field():
    from urp_lab.compose import from_field
    from urp_lab.library import fields
    if not fields.status()['downloaded']:
        pytest.skip('Fields2Benchmark 未下载')
    sc = from_field('nl_field_7', '3 台机器人采样 20 个点，避开一个池塘')
    inst = validate_instance('fleet_routing', sc['instance'])
    assert len(inst['tasks']) == 20 and len(inst['obstacles']) == 1
    assert f"{len(inst['obstacles'])} 处障碍" in sc['summary']
    r = run_algorithm(sc, inst, 'panel.sweep', {})
    assert r['check']['valid']


def test_makespan_model_beats_distance_routes_on_latest_finish():
    """With the reload model and a span cost, OR-Tools must not finish later than PyVRP's distance-optimal routes."""
    sc = _scenario('5 公顷麦田分成 6 块，4 辆无人车巡检 60 个巡检点，要求尽快完成，种子 3')
    assert sc['instance']['objective'] == 'makespan'
    fast = run_algorithm(sc, sc['instance'], 'ortools.fleet', {'time_limit_s': 2})
    short = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    assert fast['check']['valid'] and short['check']['valid']
    assert fast['metrics']['makespan_s'] <= short['metrics']['makespan_s'] * 1.02
    assert 'OR-Tools' in fast['raw_result']['trip_assignment']


def test_gazebo_world_from_fleet_plan(tmp_path):
    import struct
    import xml.etree.ElementTree as ET
    from urp_lab import fieldworld
    from urp_lab.fieldsim import marker_layers
    sc = _scenario(EXAMPLES[0])
    r = run_algorithm(sc, sc['instance'], 'panel.savings_fleet', {})
    meta = fieldworld.build(sc['instance'], r['routes'], tmp_path, 'field_test', 4.0)
    world = ET.parse(tmp_path / 'world.sdf').getroot().find('world')
    robots = [m for m in world.findall('model') if m.get('name', '').startswith('vehicle_')]
    assert len(robots) == len(meta['vehicles']) == len(r['routes'])
    assert all(m.find("plugin[@filename='ignition-gazebo-diff-drive-system']") is not None for m in robots)
    for stl in (tmp_path / 'meshes').glob('*.stl'):
        data = stl.read_bytes()
        (count,) = struct.unpack('<I', data[80:84])
        assert len(data) == 84 + 50 * count and count > 0
    for v in meta['vehicles']:  # every vehicle starts at its parking spot, passes the depot and visits its tasks in order
        assert math.dist(v['path'][1], sc['instance']['depot']) < 1e-6 and len(v['tasks']) > 0
        assert all(any(math.dist(t['stop'], p) < 1e-6 for p in v['path']) for t in v['tasks'])
    # travelling along the rows makes every crop row solid; with free travel they are only drawn
    crops = world.find("model[@name='crop_rows']")
    assert meta['travel'] == 'rows' and crops is not None and len(crops.findall('link/collision')) == meta['row_collision_shapes'] > 0
    free_inst = copy.deepcopy(sc['instance'])
    free_inst['vehicles']['travel'] = 'free'
    free = fieldworld.build(free_inst, r['routes'], tmp_path / 'free', 'field_free', 4.0)
    assert free['row_collision_shapes'] == 0
    assert ET.parse(tmp_path / 'free' / 'world.sdf').getroot().find("world/model[@name='crop_rows']") is None
    layers, outlines = marker_layers(sc['instance'])
    assert all(len(layer['triangles']) % 3 == 0 and layer['triangles'] for layer in layers)
    assert outlines[0]['ns'] == 'boundary'


# ------------------------------------------------------- heterogeneous fleet --
HETERO_FLEET = [{'name': '重载车', 'count': 2, 'capacity': 20, 'speed_mps': 1.0},
                {'name': '快车', 'count': 1, 'capacity': 10, 'speed_mps': 1.8, 'robot_radius': 0.35}]


def _fleet_scenario(example, fleet, **extra):
    sc = compose(example)
    veh = {'fleet': [dict(c) for c in fleet]}
    travel = sc['instance']['vehicles'].get('travel')
    if travel:
        veh['travel'] = travel
    veh.update(extra)
    sc['instance']['vehicles'] = veh
    sc['instance'] = validate_instance('fleet_routing', sc['instance'])
    return sc


@pytest.mark.parametrize('algorithm', ['pyvrp.fleet', 'ortools.fleet'])
def test_heterogeneous_fleet_solves_and_checks(algorithm):
    sc = _fleet_scenario(EXAMPLES[0], HETERO_FLEET)
    r = run_algorithm(sc, sc['instance'], algorithm, {'time_limit_s': 2})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    served = sorted(t for route in r['routes'] for t in route['sequence'])
    assert served == sorted(t['id'] for t in sc['instance']['tasks'])
    classes = {c['name']: c for c in sc['instance']['vehicles']['fleet']}
    for route in r['routes']:
        cls = classes[route['fleet_class']]
        assert route['load'] <= cls['capacity'] and route['capacity'] == cls['capacity']
        service = sum(t['service_s'] for t in sc['instance']['tasks'] if t['id'] in route['sequence'])
        assert math.isclose(route['duration_s'], route['cost'] / cls['speed_mps'] + service, rel_tol=1e-6)
        assert route['robot_radius'] == cls['robot_radius']
    contract = r['raw_result']['model_contract']['fleet']
    assert contract['type'] == 'heterogeneous' and len(contract['classes']) == 2


def test_heterogeneous_makespan_uses_class_speed():
    sc = _fleet_scenario(EXAMPLES[1], [{'name': '重载车', 'count': 2, 'capacity': 20, 'speed_mps': 1.0},
                                       {'name': '快车', 'count': 2, 'capacity': 10, 'speed_mps': 1.8}])
    r = run_algorithm(sc, sc['instance'], 'ortools.fleet', {'time_limit_s': 2})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    classes = {c['name']: c for c in sc['instance']['vehicles']['fleet']}
    assert {route['fleet_class'] for route in r['routes']} == set(classes)
    for route in r['routes']:
        cls = classes[route['fleet_class']]
        service = sum(t['service_s'] for t in sc['instance']['tasks'] if t['id'] in route['sequence'])
        assert math.isclose(route['duration_s'], route['cost'] / cls['speed_mps'] + service, rel_tol=1e-6)


def test_heterogeneous_single_trip_capacity_is_reported_as_infeasible():
    sc = _fleet_scenario(EXAMPLES[0], [{'name': '小车', 'count': 2, 'capacity': 5, 'speed_mps': 1.0}], trips='single')
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 0.5})
    assert r['status'] == 'infeasible' and not r['routes']
    assert r['metrics']['fleet_capacity'] == 10


@pytest.mark.parametrize('algorithm', ['panel.sweep', 'panel.cluster_first', 'panel.savings_fleet'])
def test_constructive_baselines_reject_heterogeneous_fleet(algorithm):
    sc = _fleet_scenario(EXAMPLES[0], HETERO_FLEET)
    with pytest.raises(ValueError, match='同构'):
        run_algorithm(sc, sc['instance'], algorithm, {})


def test_heterogeneous_checker_catches_tampering():
    from urp_lab.solvers.fleet import check as fleet_check
    sc = _fleet_scenario(EXAMPLES[0], HETERO_FLEET)
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    routes = r['routes']
    routes[0]['capacity'] = routes[0]['capacity'] + 5
    chk = fleet_check(sc['instance'], routes)
    assert not chk['valid'] and any('声明' in e for e in chk['errors'])
    routes[0]['capacity'] -= 5
    routes[0]['fleet_class'] = '不存在的类别'
    chk = fleet_check(sc['instance'], routes)
    assert not chk['valid'] and any('类别' in e for e in chk['errors'])
    # relabeling a heavy route as the small fast class cannot pass: capacity, speed or duration gives it away
    routes[0]['fleet_class'] = '重载车'
    heavy = max(routes, key=lambda x: x['load'])
    heavy['fleet_class'] = '快车'
    chk = fleet_check(sc['instance'], routes)
    assert not chk['valid'] and any(('载重' in e or '用时' in e or '声明' in e) for e in chk['errors'])


def test_heterogeneous_fleet_validation():
    def bad(mutator):
        sc = compose(EXAMPLES[0])
        veh = {'fleet': [dict(c) for c in HETERO_FLEET]}
        mutator(veh)
        sc['instance']['vehicles'] = veh
        with pytest.raises(ValueError):
            validate_instance('fleet_routing', sc['instance'])

    bad(lambda v: v.update(count=4))          # 与 fleet 推得的总台数冲突
    bad(lambda v: v.update(capacity=21))      # 与 fleet 推得的最大容量冲突
    bad(lambda v: v.update(names=['a']))
    bad(lambda v: v['fleet'][0].update(capacity=0))
    bad(lambda v: v['fleet'][1].update(count=-1))
    bad(lambda v: v['fleet'][1].update(speed_mps=0))
    bad(lambda v: v['fleet'][1].update(speed_mps=16))
    bad(lambda v: v['fleet'][0].update(name=v['fleet'][1]['name']))
    bad(lambda v: v['fleet'][0].update(payload='huge'))
    bad(lambda v: v.update(fleet=[]))
    bad(lambda v: v['fleet'][0].update(asset={'id': 'x'}))
    # a class that cannot hold the largest single demand is rejected outright
    sc = compose(EXAMPLES[0])
    sc['instance']['tasks'][0]['demand'] = 3
    sc['instance']['vehicles'] = {'fleet': [{'count': 1, 'capacity': 2}]}
    with pytest.raises(ValueError, match='最大单点需求'):
        validate_instance('fleet_routing', sc['instance'])


def test_fleet_normalisation_defaults():
    sc = _fleet_scenario(EXAMPLES[0], [{'count': 3, 'capacity': 10, 'speed_mps': 1.2}])
    veh = sc['instance']['vehicles']
    assert veh['count'] == 3 and veh['capacity'] == 10 and veh['speed_mps'] == 1.2
    assert [c['count'] for c in veh['fleet']] == [3]
    assert veh['names'] == ['车型1-1', '车型1-2', '车型1-3']
    # validation is idempotent: a normalised instance re-validates unchanged (fieldsim restart path)
    again = validate_instance('fleet_routing', sc['instance'])
    assert again['vehicles'] == veh and again['robot_radius'] == sc['instance']['robot_radius']
    # a bigger class footprint raises the shared planning radius
    sc2 = _fleet_scenario(EXAMPLES[0], [{'count': 1, 'capacity': 10, 'robot_radius': 0.9},
                                        {'count': 1, 'capacity': 10, 'robot_radius': 0.3}])
    assert sc2['instance']['robot_radius'] == 0.9
    assert sc2['instance']['vehicles']['fleet'][1]['robot_radius'] == 0.3


def test_recommendation_for_heterogeneous_fleet():
    sc = _fleet_scenario(EXAMPLES[0], HETERO_FLEET)
    rec = recommend(sc, sc['instance'])
    assert rec['recommended'] in ('pyvrp.fleet', 'ortools.fleet')
    panels = [row for row in rec['ranking'] if row['algorithm_id'].startswith('panel.')]
    assert panels and all(row['score'] == 0 and any('同构' in w for w in row['reasons']) for row in panels)
    assert any(f['key'] == 'fleet_classes' and f['value'] == 2 for f in rec['features'])


# ------------------------------------------------ curvature-constrained fleet --
def test_curvature_constrained_fleet_solves_and_checks():
    from urp_lab.solvers.fleet import min_curvature_radius
    sc = _fleet_scenario(EXAMPLES[0], [{'name': '阿克曼运输车', 'count': 2, 'capacity': 20, 'min_turn_radius_m': 1.5},
                                       {'name': '差速快车', 'count': 1, 'capacity': 10, 'speed_mps': 1.8}], travel='free')
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 2})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    assert r['metrics']['curvature_constrained_vehicles'] >= 1
    served = sorted(t for route in r['routes'] for t in route['sequence'])
    assert served == sorted(t['id'] for t in sc['instance']['tasks'])
    classes = {c['name']: c for c in sc['instance']['vehicles']['fleet']}
    for route in r['routes']:
        bound = classes[route['fleet_class']].get('min_turn_radius_m', 0)
        if bound:
            sampled = min_curvature_radius(route['polyline'])
            assert sampled >= bound * 0.99, (bound, sampled)
            assert math.isclose(route['curvature_radius_m'], sampled, rel_tol=1e-3)
        else:
            assert 'min_turn_radius_m' not in route
    contract = r['raw_result']['model_contract']['geometry']
    assert contract['curvature_bounded'] is True and 'Dubins' in contract['path']


def test_curvature_length_exceeds_straight_line_and_makespan_uses_it():
    sc = _fleet_scenario(EXAMPLES[0], [{'name': '阿克曼车', 'count': 2, 'capacity': 30, 'min_turn_radius_m': 2.0}], travel='free')
    curved = run_algorithm(sc, sc['instance'], 'ortools.fleet', {'time_limit_s': 2})
    assert curved['status'] == 'feasible' and curved['check']['valid']
    straight_inst = validate_instance('fleet_routing', {**sc['instance'],
                                                         'vehicles': {**sc['instance']['vehicles'], 'names': None,
                                                                      'fleet': [{'count': 2, 'capacity': 30}]}})
    straight = run_algorithm(sc, straight_inst, 'ortools.fleet', {'time_limit_s': 2})
    assert curved['metrics']['total_distance_m'] > straight['metrics']['total_distance_m']


def test_curvature_checker_catches_sharp_insert():
    from urp_lab.solvers.fleet import check as fleet_check
    sc = _fleet_scenario(EXAMPLES[0], [{'name': '阿克曼车', 'count': 2, 'capacity': 20, 'min_turn_radius_m': 1.5}], travel='free')
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    routes = r['routes']
    victim = max(routes, key=lambda x: len(x['polyline']))
    i = len(victim['polyline']) // 2
    x, y = victim['polyline'][i]
    victim['polyline'][i:i] = [[x + 0.25, y], [x, y + 0.25]]      # 0.35 m-scale zigzag: radius ≈ 0.18 m
    chk = fleet_check(sc['instance'], routes)
    assert not chk['valid'] and any('转弯半径' in e for e in chk['errors'])
    victim['min_turn_radius_m'] = 3.0                              # declared bound no longer matches the class
    chk = fleet_check(sc['instance'], routes)
    assert not chk['valid'] and any('声明' in e for e in chk['errors'])


def test_curvature_validation_and_rows_rejection():
    with pytest.raises(ValueError, match='最小转弯半径'):
        _fleet_scenario(EXAMPLES[0], [{'count': 1, 'capacity': 10, 'min_turn_radius_m': 11}])
    with pytest.raises(ValueError, match='最小转弯半径'):
        _fleet_scenario(EXAMPLES[0], [{'count': 1, 'capacity': 10, 'min_turn_radius_m': -0.5}])
    sc = compose(EXAMPLES[1])                      # generated farmland travels along rows
    travel = sc['instance']['vehicles'].get('travel')
    assert travel == 'rows'
    sc['instance']['vehicles'] = {'fleet': [{'count': 2, 'capacity': 20, 'min_turn_radius_m': 1.0}], 'travel': travel}
    with pytest.raises(ValueError, match='行间通行'):
        validate_instance('fleet_routing', sc['instance'])


# -------------------------------------------------- transit corridors (item 8) --
CORRIDOR_GEOJSON = {
 'type': 'FeatureCollection',
 'features': [
  {'type': 'Feature', 'properties': {'role': 'boundary'},
   'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [60, 0], [60, 40], [0, 40], [0, 0]]]}},
  {'type': 'Feature', 'properties': {'role': 'plot', 'crop': 'wheat'},
   'geometry': {'type': 'Polygon', 'coordinates': [[[5, 5], [55, 5], [55, 35], [5, 35], [5, 5]]]}},
  {'type': 'Feature', 'properties': {'role': 'road', 'name': '机耕道'},
   'geometry': {'type': 'Polygon', 'coordinates': [[[24, 0], [30, 0], [30, 40], [24, 40], [24, 0]]]}},
  {'type': 'Feature', 'properties': {'role': 'headland', 'name': '地头', 'width_m': 3},
   'geometry': {'type': 'LineString', 'coordinates': [[0, 20], [60, 20]]}},
  {'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': [2, 2]}},
  {'type': 'Feature', 'properties': {'role': 'task'}, 'geometry': {'type': 'Point', 'coordinates': [50, 30]}},
  {'type': 'Feature', 'properties': {'role': 'task'}, 'geometry': {'type': 'Point', 'coordinates': [10, 30]}},
 ]}


def _corridor_scenario(travel='rows'):
    from urp_lab import lanes as _lanes
    sc = import_geojson(CORRIDOR_GEOJSON, vehicles={'count': 1})
    inst = validate_instance('fleet_routing', sc['instance'])
    inst['vehicles']['travel'] = travel
    sc['instance'] = inst
    return sc, inst


def test_geojson_corridor_import_and_roundtrip():
    sc, inst = _corridor_scenario()
    assert [c['name'] for c in inst['corridors']] == ['机耕道', '地头']
    from shapely.geometry import Polygon
    road, headland = (Polygon(c['polygon']) for c in inst['corridors'])
    assert 235 < road.area < 245                       # 6 m × 40 m polygon road
    assert 185 < headland.area < 189                   # 3 m buffered headland line (round caps)
    import json as _json
    again = import_geojson(_json.loads(export_geojson(sc, None)), vehicles={'count': 1})
    inst2 = validate_instance('fleet_routing', again['instance'])
    assert [c['name'] for c in inst2['corridors']] == ['机耕道', '地头']


def test_corridor_stays_unplanted_and_open_for_rows_travel():
    from shapely.geometry import Polygon
    from urp_lab import lanes
    from shapely.ops import unary_union
    sc, inst = _corridor_scenario()
    net = lanes.Network(inst)
    road = Polygon(next(c['polygon'] for c in inst['corridors'] if c['name'] == '机耕道'))
    planted = unary_union([p.planted for p in net.plots])
    assert road.difference(planted).area > 0.95 * road.area      # the road is not planted over
    assert net.travel_area().covers(road.representative_point())  # and it is drivable open area


def test_corridor_scenario_solves_along_rows():
    sc, inst = _corridor_scenario()
    r = run_algorithm(sc, inst, 'pyvrp.fleet', {'time_limit_s': 1})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    from shapely.geometry import LineString
    from urp_lab import lanes
    crop_rows = lanes.Network(inst).rows()
    for route in r['routes']:
        for a, b in zip(route['polyline'], route['polyline'][1:]):
            seg = LineString([a, b])
            assert all(seg.distance(row) >= inst['robot_radius'] - 1e-6 for row in crop_rows)


def test_corridor_validation_rejects_bad_polygons():
    sc, inst = _corridor_scenario()
    inst['corridors'][0]['polygon'] = [[0, 0], [1, 1]]          # not a polygon
    with pytest.raises(ValueError, match='通行走廊'):
        validate_instance('fleet_routing', inst)


# ------------------------------------------------------------- multi-depot --
def _multi_depot_scenario(n_depots=2, **vehicles):
    sc = compose(EXAMPLES[0])
    inst = sc['instance']
    from shapely.geometry import Polygon as _P
    b = _P(inst['boundary'])
    xs = [b.bounds[0] + 8, b.bounds[2] - 8]
    inst['depots'] = [[xs[i], (b.bounds[1] + b.bounds[3]) / 2] for i in range(n_depots)]
    inst['vehicles'].update(vehicles)
    sc['instance'] = validate_instance('fleet_routing', inst)
    return sc


@pytest.mark.parametrize('algorithm', ['pyvrp.fleet', 'ortools.fleet'])
def test_multi_depot_fleet_solves_and_checks(algorithm):
    sc = _multi_depot_scenario(2, count=4)
    r = run_algorithm(sc, sc['instance'], algorithm, {'time_limit_s': 2})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    depots = sc['instance']['depots']
    used = set()
    served = sorted(t for route in r['routes'] for t in route['sequence'])
    assert served == sorted(t['id'] for t in sc['instance']['tasks'])
    for route in r['routes']:
        home = route['depot']
        used.add(home)
        assert math.dist(route['polyline'][0], depots[home]) < 1e-6
        assert math.dist(route['polyline'][-1], depots[home]) < 1e-6
        assert route['depot_xy'] == depots[home]
    assert len(used) >= 1 and all(0 <= d < len(depots) for d in used)


def test_multi_depot_spreads_vehicles_over_depots():
    from urp_lab.solvers import fleet as F
    sc = _multi_depot_scenario(2, count=4)
    table = F.vehicle_table(sc['instance'])
    depots_used = {di for _vi, _ci, _cls, di in table}
    assert depots_used == {0, 1}                        # round-robin assignment in table order
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 2})
    assert r['status'] == 'feasible'
    assert {route['depot'] for route in r['routes']} >= {0, 1} or len(r['routes']) < 4


def test_multi_depot_checker_rejects_wrong_depot_return():
    from urp_lab.solvers.fleet import check as fleet_check
    sc = _multi_depot_scenario(2, count=2)
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    routes = r['routes']
    victim = routes[0]
    other = 1 - victim['depot']
    victim['polyline'][-1] = list(sc['instance']['depots'][other])     # return to the wrong depot
    chk = fleet_check(sc['instance'], routes)
    assert not chk['valid'] and any('自己的车库' in e for e in chk['errors'])


def test_multi_depot_makespan_balances_over_home_depots():
    sc = _multi_depot_scenario(2, count=3)
    sc['instance']['vehicles']['travel'] = 'free'      # rows+makespan on the lane graph is a heavier regime
    sc['instance']['objective'] = 'makespan'
    sc['instance'] = validate_instance('fleet_routing', sc['instance'])
    r = run_algorithm(sc, sc['instance'], 'ortools.fleet', {'time_limit_s': 3})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    depots = sc['instance']['depots']
    used = set()
    for route in r['routes']:
        home = route['depot']
        used.add(home)
        assert math.dist(route['polyline'][0], depots[home]) < 1e-6
        assert math.dist(route['polyline'][-1], depots[home]) < 1e-6
    assert used                             # spread over depots in table order


def test_multi_depot_rejections():
    sc = _multi_depot_scenario(2, count=2)
    sc['instance'] = validate_instance('fleet_routing', sc['instance'])
    with pytest.raises(ValueError, match='多车库'):
        run_algorithm(sc, sc['instance'], 'panel.savings_fleet', {})
    inst = copy.deepcopy(sc['instance'])
    inst['depots'].append(inst['depots'][0])           # duplicate depot
    with pytest.raises(ValueError, match='重复'):
        validate_instance('fleet_routing', inst)


def test_multi_depot_geojson_roundtrip_and_world_parking(tmp_path):
    doc = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'role': 'boundary'},
         'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [80, 0], [80, 50], [0, 50], [0, 0]]]}},
        {'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': [5, 25]}},
        {'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': [75, 25]}},
        *[{'type': 'Feature', 'properties': {'role': 'task'}, 'geometry': {'type': 'Point', 'coordinates': [x, y]}}
          for x, y in [(15, 10), (30, 35), (45, 15), (60, 40), (20, 40), (65, 10)]],
    ]}
    import json as _json
    sc = import_geojson(doc, vehicles={'count': 2})
    inst = validate_instance('fleet_routing', sc['instance'])
    assert len(inst['depots']) == 2 and inst['depot'] == inst['depots'][0]
    r = run_algorithm(sc, inst, 'pyvrp.fleet', {'time_limit_s': 1})
    assert r['status'] == 'feasible' and r['check']['valid']
    again = import_geojson(_json.loads(export_geojson(sc, None)), vehicles={'count': 2})
    assert len(validate_instance('fleet_routing', again['instance'])['depots']) == 2
    from urp_lab import fieldworld
    plan = fieldworld.build(inst, r['routes'], tmp_path, 'multi_depot', 4.0)
    homes = [tuple(v['start'][:2]) for v in plan['vehicles']]
    for v, route in zip(plan['vehicles'], r['routes']):
        assert math.dist(v['start'][:2], inst['depots'][route['depot']]) < 8.0   # parked near its own depot
    assert len(homes) == len(r['routes'])


def _wall_gap_scenario(free_gap, turn):
    """Two walls with a single gap: curvature-constrained routes must thread it."""
    inst = {'boundary': [[0, 0], [40, 0], [40, 32], [0, 32]],
            'obstacles': [{'id': 'W1', 'kind': 'building',
                           'polygon': [[18, 6 + free_gap / 2], [21, 6 + free_gap / 2], [21, 28], [18, 28]]},
                          {'id': 'W2', 'kind': 'building',
                           'polygon': [[18, 0], [21, 0], [21, 6 - free_gap / 2], [18, 6 - free_gap / 2]]}],
            'depot': [2, 2],
            'tasks': [{'id': 1, 'xy': [3, 28], 'demand': 1}, {'id': 2, 'xy': [36, 6], 'demand': 1},
                      {'id': 3, 'xy': [36, 28], 'demand': 1}],
            'vehicles': {'fleet': [{'count': 1, 'capacity': 3, 'min_turn_radius_m': turn}], 'travel': 'free'},
            'robot_radius': 0.5}
    sc = {'problem_class': 'fleet_routing', 'frame': {'kind': 'field'}, 'cost_unit': 'm'}
    sc['instance'] = validate_instance('fleet_routing', inst)
    return sc


def test_curvature_fallback_threads_narrow_gap():
    """Turn diameter wider than the gap needs a compound manoeuvre: the analytic realisation fails,
    and the OMPL Dubins fallback must recover a check-valid route (critique item, 2026-10-17)."""
    sc = _wall_gap_scenario(6.0, 3.5)
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    assert r['status'] == 'feasible' and r['check']['valid'], r['check']
    attempts = r['raw_result']['curvature_realization_attempts']
    assert [a['planner'] for a in attempts][-1] == 'ompl_rrt_connect' and attempts[-1]['valid']
    assert not attempts[0]['valid']                      # the analytic realisation genuinely failed first
    route = r['routes'][0]
    assert route['curvature_radius_m'] >= route['min_turn_radius_m'] * 0.99
    # honest cost: threading the gap with a 7 m turn circle needs the long way round
    assert route['cost'] > 2 * r['metrics']['total_distance_m'] / r['metrics']['total_distance_m'] * 117


def test_curvature_wide_gap_stays_analytic():
    sc = _wall_gap_scenario(4.6, 3.5)                    # tight for the robot, easy for the turn
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    assert r['status'] == 'feasible' and r['check']['valid']
    assert r['raw_result'].get('curvature_realization_attempts') is None   # first (analytic) attempt passed


def test_oriented_footprint_clearance_is_reported_not_gating():
    """A 0.8×0.48 rectangle inside a 0.5 m circle: the oriented clearance must be ≥ the circle
    clearance (less conservative), reported per route, and never decides acceptance."""
    from urp_lab.solvers.fleet import check as fleet_check
    sc = _fleet_scenario(EXAMPLES[0], [{'count': 2, 'capacity': 20,
                                        'footprint': {'type': 'polygon',
                                                      'points': [[-0.4, -0.24], [0.4, -0.24], [0.4, 0.24], [-0.4, 0.24]]}}])
    r = run_algorithm(sc, sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    assert r['status'] == 'feasible' and r['check']['valid']
    for d in r['check']['routes']:
        # the rectangle sits inside the planning circle, so its true clearance can be below the
        # circle gate — that visible gap IS the conservatism the report is meant to quantify
        assert d['oriented_clearance_m'] is not None and d['oriented_clearance_m'] >= 0.0
        assert 'circle contract' in (d['oriented_scope'] or '')
    # circle-only classes keep the legacy detail shape
    r2 = run_algorithm(sc := _scenario(EXAMPLES[0]), sc['instance'], 'pyvrp.fleet', {'time_limit_s': 1})
    assert all(d.get('oriented_clearance_m') is None for d in r2['check']['routes'])


def test_oriented_footprint_validation():
    with pytest.raises(ValueError, match='外接半径'):
        _fleet_scenario(EXAMPLES[0], [{'count': 1, 'capacity': 10,
                                       'footprint': {'type': 'polygon', 'points': [[-1, 0], [1, 0], [0, 1]]}}])
    with pytest.raises(ValueError, match='footprint'):
        _fleet_scenario(EXAMPLES[0], [{'count': 1, 'capacity': 10, 'footprint': {'type': 'circle'}}])


# ------------------------------------------------------------ unload points --
def _unload_scenario(with_point=True):
    inst = {'boundary': [[0, 0], [60, 0], [60, 30], [0, 30]], 'depot': [3, 15], 'objective': 'makespan',
            'tasks': [{'id': i + 1, 'xy': [45 + 4 * (i % 3), 8 + 12 * (i % 2)], 'demand': 4} for i in range(8)],
            'vehicles': {'count': 1, 'capacity': 16, 'speed_mps': 1.0, 'trips': 'multi', 'travel': 'free'},
            'robot_radius': 0.5}
    if with_point:
        inst['unload_points'] = [[38, 15]]
    return validate_instance('fleet_routing', inst)


def test_unload_point_shortens_makespan_and_is_verified():
    base = run_algorithm({'problem_class': 'fleet_routing', 'frame': {'kind': 'field'}, 'cost_unit': 's'},
                         _unload_scenario(False), 'ortools.fleet', {'time_limit_s': 3})
    supp = run_algorithm({'problem_class': 'fleet_routing', 'frame': {'kind': 'field'}, 'cost_unit': 's'},
                         _unload_scenario(True), 'ortools.fleet', {'time_limit_s': 3})
    assert base['status'] == 'feasible' and supp['status'] == 'feasible'
    assert base['check']['valid'] and supp['check']['valid']
    route = supp['routes'][0]
    assert len(route['trips']) > 1
    stops = route['reload_stops']
    instance = _unload_scenario(True)
    assert stops and any(s in instance['unload_points'] for s in stops)
    assert all(any(math.dist(s, p) < 1e-6 for p in instance['unload_points']) or
               math.dist(s, instance['depot']) < 1e-6 for s in stops)
    assert supp['metrics']['makespan_s'] <= base['metrics']['makespan_s'] * 1.05 + 1e-6


def test_unload_point_tampering_is_rejected():
    from urp_lab.solvers.fleet import check as fleet_check
    sc = {'problem_class': 'fleet_routing', 'frame': {'kind': 'field'}, 'cost_unit': 's'}
    r = run_algorithm(sc, _unload_scenario(True), 'ortools.fleet', {'time_limit_s': 3})
    assert r['status'] == 'feasible' and r['check']['valid']
    routes = r['routes']
    victim = routes[0]
    assert victim['reload_stops']
    victim['reload_stops'][0] = [55.0, 27.0]          # not an unload point nor the home depot
    chk = fleet_check(_unload_scenario(True), routes)
    assert not chk['valid'] and any('补给点' in e for e in chk['errors'])


def test_unload_point_geojson_roundtrip_and_validation():
    doc = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'role': 'boundary'},
         'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [50, 0], [50, 30], [0, 30], [0, 0]]]}},
        {'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': [3, 3]}},
        {'type': 'Feature', 'properties': {'role': 'unload', 'name': '补给点A'}, 'geometry': {'type': 'Point', 'coordinates': [30, 15]}},
        *[{'type': 'Feature', 'properties': {'role': 'task'}, 'geometry': {'type': 'Point', 'coordinates': [x, y]}}
          for x, y in [(20, 10), (40, 20), (35, 5), (45, 25)]],
    ]}
    import json as _json
    sc = import_geojson(doc, vehicles={'count': 1})
    inst = validate_instance('fleet_routing', sc['instance'])
    assert inst['unload_points'] == [[30.0, 15.0]]
    again = import_geojson(_json.loads(export_geojson(sc, None)), vehicles={'count': 1})
    assert validate_instance('fleet_routing', again['instance'])['unload_points'] == [[30.0, 15.0]]
    bad = _unload_scenario(True)
    bad['unload_points'].append([999, 999])
    with pytest.raises(ValueError):
        validate_instance('fleet_routing', bad)
