import copy
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import LineString, Point

from ceres_lab import fieldworld
from ceres_lab.field_traffic import MissionQueue, PARKING_MARGIN_M, separation
from test_backend_integrity import instance
from ceres_lab.solvers import fleet


def test_parking_outside_mission_and_other_parking_approaches(tmp_path):
    inst = instance()
    inst['vehicles'].update(count=2, capacity=1, trips='single')
    routes, checked, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert checked['valid'] and len(routes) == 2
    plan = fieldworld.build(inst, routes, tmp_path, 'parking_test')
    for a in plan['vehicles']:
        assert a['path'][0] == a['path'][-1] == a['start'][:2]
        for b in plan['vehicles']:
            if a is not b:
                assert LineString(a['path']).distance(Point(b['start'][:2])) >= 2 * fieldworld.ROBOT_RADIUS + PARKING_MARGIN_M
    doc = ET.parse(plan['sdf'])
    assert {el.text for el in doc.findall('.//collide_bitmask')} == {'0xffff'}
    assert len(doc.findall('.//sensor[@type="contact"]')) == 5 * len(routes)



def test_later_parking_approach_clears_previously_parked_robots():
    # The third approach used to pass 1.6 m from the second parked robot even
    # though the old one-way candidate-position check passed (required > 1.77 m).
    from ceres_lab.scenarios import validate_instance
    inst = validate_instance('fleet_routing', {
        'boundary': [[0, 0], [90, 0], [90, 60], [0, 60]], 'depot': [2.75, 2.75],
        'tasks': [{'id': i + 1, 'xy': xy} for i, xy in enumerate([[82, 6.6], [64, 13.7], [84, 6.8]])],
        'vehicles': {'count': 3, 'capacity': 1, 'travel': 'free', 'speed_mps': 1, 'trips': 'single'}})
    routes, checked, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert checked['valid'] and len(routes) == 3
    vehicles = fieldworld.plan_vehicles(inst, routes)
    for moving in vehicles:
        for parked in vehicles:
            if moving is not parked:
                assert LineString(moving.path).distance(Point(parked.start[:2])) >= 2 * fieldworld.ROBOT_RADIUS + PARKING_MARGIN_M

def test_queue_preserves_wait_in_makespan_and_settles_before_release():
    ds = [SimpleNamespace(name=f'v{i}', pose=(i * 4, 0, 0), state='waiting', s=0, failure=None) for i in range(2)]
    q = MissionQueue(ds, .49, 10)
    q.step(10)
    assert [d.state for d in ds] == ['driving', 'waiting']
    ds[0].state = 'done'
    q.step(30)
    q.step(30.9)
    assert ds[1].state == 'waiting'
    q.step(31)
    assert ds[1].state == 'driving' and ds[1].t_depart - ds[1].t_start == 21
    ds[1].pose = (.1, 0, 0)
    with pytest.raises(RuntimeError, match='车间距离保护'):
        q.step(32)


def test_synchronous_checker_finds_collision_between_samples():
    trails = {'a': [[0, -2, 0, 0], [.25, 2, 0, 0]],
              'b': [[0, 2, 0, 0], [.25, -2, 0, 0]]}
    r = separation(trails, .49)
    assert not r['valid'] and r['pairs'][0]['min_center_distance_m'] == 0
    assert r['pairs'][0]['t_sim'] == pytest.approx(.125)
    for point in trails['b']:
        point[2] = 2
    assert separation(trails, .49)['valid']
    trails['b'][-1][0] = trails['a'][-1][0] = 2
    assert not separation(trails, .49)['valid']  # missing telemetry is not a clear path


def test_synchronous_checker_rejects_missing_or_reversed_time():
    with pytest.raises(ValueError):
        separation({'a': []}, .5)
    with pytest.raises(ValueError):
        separation({'a': [[1, 0, 0, 0], [0, 1, 0, 0]]}, .5)


def test_reported_speed_updates_after_done_and_rejects_invalid_truth():
    from ceres_lab.field_tracking import truth_time
    from test_backend_integrity import driver
    d = driver([])
    assert truth_time(0, None) is None
    assert truth_time(2, 3) == 2
    d.observe((0, 0, 0), truth_time(0, 1))
    d.observe((.1, 0, .01), truth_time(0, 1.1))
    assert d.speed_now == pytest.approx(1)
    assert d.distance == pytest.approx(.1)
    d.state = 'done'
    d.observe((.1, 0, .01), 1.2)
    assert d.summary(None)['speed'] == 0
    with pytest.raises(RuntimeError, match='倒退'):
        d.observe((.1, 0, 0), .9)
    with pytest.raises(RuntimeError, match='非有限'):
        d.observe((float('nan'), 0, 0), 2)


def test_saved_trajectory_cannot_certify_zero_distance_or_moving_at_finish():
    from ceres_lab.fieldcheck import verify_motion_metrics
    result = {'vehicles': [{'name': 'v', 'distance_m': 2, 'speed': 0}],
              'trails': {'v': [[0, 0, 0, 0], [1, 1, 0, 0], [2, 2, 0, 0]]},
              'metrics': {'total_distance_m': 2}}
    assert verify_motion_metrics(result)['valid']
    result['vehicles'][0]['distance_m'] = 0
    result['metrics']['total_distance_m'] = 0
    assert not verify_motion_metrics(result)['valid']
    result['vehicles'][0].update(distance_m=2, speed=.4)
    result['metrics']['total_distance_m'] = 2
    assert not verify_motion_metrics(result)['valid']
