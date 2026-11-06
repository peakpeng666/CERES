"""Vehicles moving at once on frozen paths: who waits where, and that nothing collides or deadlocks however they are delayed."""
import math
import random

import pytest

from urp_lab import field_coordination as fc
from urp_lab import mujoco_field
from urp_lab.field_traffic import PASSAGE_POLICY, PassageQueue, separation
from urp_lab.gateway import Config, Lab
from urp_lab.physics import PhysicsError

from test_physics import plan_of

RADIUS = 0.49


def vehicle(name, path, tasks=()):
    return {'name': name, 'path': [list(map(float, p)) for p in path], 'tasks': list(tasks)}


def gates(*vehicles, speed=1.0):
    result = fc.compile_gates(list(vehicles), RADIUS, speed)
    assert result['feasible'], result
    return result


def test_paths_that_never_meet_have_no_gates():
    g = gates(vehicle('a', [(0, 0), (30, 0)]), vehicle('b', [(0, 10), (30, 10)]))
    assert g['meetings'] == 0 and not any(g['gates'].values())
    assert g['predicted_makespan_s'] == pytest.approx(30, abs=.5)


def test_at_a_crossing_the_vehicle_that_gets_there_first_goes_first():
    g = gates(vehicle('near', [(0, 0), (40, 0)]), vehicle('far', [(25, -30), (25, 30)]))
    assert g['meetings'] == 1
    assert not g['gates']['near'] and len(g['gates']['far']) == 1
    gate = g['gates']['far'][0]
    assert gate['s'] < 30 and gate['after'][0][0] == 'near' and gate['after'][0][1] > 25


def test_vehicles_meeting_head_on_in_a_lane_take_it_in_turn():
    a = vehicle('a', [(0, 0), (30, 0), (30, 20)])
    b = vehicle('b', [(30, -20), (30, 0), (0, 0)])
    g = gates(a, b)
    orders = {name for rows in g['gates'].values() for gate in rows for name, _ in gate['after']}
    assert len(orders) == 1, 'one vehicle holds the whole shared lane'
    waiting = 'a' if orders == {'b'} else 'b'
    assert g['gates'][waiting][0]['after'][0][1] >= 28, 'the other vehicle leaves the lane before this one enters'


def test_a_follower_trails_the_leader_instead_of_waiting_for_the_whole_stretch():
    lead = vehicle('lead', [(5, 0), (105, 0)])
    follow = vehicle('follow', [(0, 0), (100, 0)])
    g = gates(lead, follow)
    rows = g['gates']['follow']
    assert len(rows) > 10 and rows[0]['after'][0][1] < 15
    assert g['predicted_makespan_s'] < 120, 'a whole-stretch lock would take about 200 s'


def test_three_vehicles_crossing_in_a_triangle_cannot_wait_on_each_other_in_a_circle():
    g = gates(vehicle('a', [(-10, 0), (30, 0)]), vehicle('b', [(25, -10), (5, 30)]), vehicle('c', [(20, 30), (-5, -12)]))
    assert g['meetings'] == 3
    assert g['predicted_makespan_s'] < 60


def test_two_vehicles_swapping_ends_of_one_lane_with_nowhere_to_pass_have_no_order():
    result = fc.compile_gates([vehicle('a', [(0, 0), (20, 0)]), vehicle('b', [(20, 0), (0, 0)])], RADIUS, 1.0)
    assert not result['feasible']


def test_a_vehicle_parked_on_a_route_leaves_before_the_other_vehicle_arrives():
    parked = vehicle('parked', [(20, 0), (20, 30)])
    passing = vehicle('passing', [(0, 0), (40, 0)])
    g = gates(parked, passing)
    assert g['gates']['passing'] and g['gates']['passing'][0]['after'][0][0] == 'parked'
    assert not g['gates']['parked']


def test_a_vehicle_that_ends_on_a_route_arrives_after_the_other_has_passed():
    ending = vehicle('ending', [(20, 30), (20, 0)])
    passing = vehicle('passing', [(0, 0), (40, 0)])
    g = gates(ending, passing)
    assert g['gates']['ending'] and g['gates']['ending'][0]['after'][0][0] == 'passing'
    assert not g['gates']['passing']


def test_two_vehicles_parked_on_each_others_routes_have_no_order():
    a = vehicle('a', [(20, 0), (20, 30), (10, 30), (10, 0)])
    b = vehicle('b', [(20, 0.5), (40, 0.5)])
    result = fc.compile_gates([a, b], RADIUS, 1.0)
    assert not result['feasible'] and result['reason']


def test_the_timeline_counts_turns_and_dwell_not_only_distance():
    path = [(0, 0), (10, 0), (10, 10)]
    s, t = fc.timeline(vehicle('a', path, [{'path_s': 5.0, 'service_s': 3.0}]), 1.0)
    assert s[-1] == pytest.approx(20)
    assert t[-1] > 20 + 3 + 1, 'a square corner is stopped at and turned on the spot'


# --- a kinematic stand-in for the tracker: arc length, braking, delays ---------------------------------------------------
class Body:
    def __init__(self, v, speed, rng):
        self.name, self.failure = v['name'], None
        self.path = v['path']
        self.cum = [0.0]
        for a, b in zip(self.path, self.path[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.speed, self.rng = speed, rng
        self.x = self.s = self.v = 0.0
        self.state, self.pose, self.pause_until = 'waiting', None, 0.0
        self.last_progress = (0.0, None)

    def at(self, s):
        s = min(s, self.cum[-1])
        i = max(1, next((k for k, c in enumerate(self.cum) if c >= s), len(self.cum) - 1))
        a, b = self.path[i - 1], self.path[i]
        u = (s - self.cum[i - 1]) / max(self.cum[i] - self.cum[i - 1], 1e-9)
        return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u

    def advance(self, now, dt):
        if self.state == 'driving' and self.rng.random() < .002:
            self.pause_until = now + self.rng.uniform(1, 20)
        want = self.speed if self.state == 'driving' and now >= self.pause_until else 0.0
        self.v = want if want > self.v else max(want, self.v - 2.0 * dt)
        self.x += self.v * dt
        if self.state == 'driving':
            self.s = self.x
            if self.x >= self.cum[-1]:
                self.state = 'done'
        self.pose = (*self.at(self.x), 0.0)


def scattered(rng, count):
    """Routes through a small field, so nearly every pair meets several times."""
    def route(i):
        spot = (-3.0 - 3 * i, 2.5 * i - 5)
        stops = [(rng.uniform(0, 30), rng.uniform(0, 20)) for _ in range(rng.randint(2, 5))]
        return vehicle(f'v{i}', [spot, *stops, spot])
    return [route(i) for i in range(count)]


@pytest.mark.parametrize('seed', range(24))
def test_any_delays_on_random_routes_end_with_everyone_home_and_nobody_closer_than_the_guard(seed):
    rng = random.Random(seed)
    vehicles = scattered(rng, rng.randint(2, 5))
    plan = fc.compile_gates(vehicles, RADIUS, 1.0)
    if not plan['feasible']:
        pytest.skip(plan['reason'])
    bodies = [Body(v, rng.uniform(.3, 1.0), rng) for v in vehicles]
    for b in bodies:
        b.pose = (*b.at(0), 0.0)
    queue = PassageQueue(bodies, RADIUS, 0.0, plan['gates'])
    now = 0.0
    while any(b.state != 'done' for b in bodies):
        queue.step(now)            # raises if any pair comes inside the guard
        for b in bodies:
            b.advance(now, .05)
        now += .05
        assert now < 6000, f'stuck: {[(b.name, b.state, round(b.s, 1)) for b in bodies]}'


def test_a_failed_vehicle_stops_the_field():
    bodies = [Body(vehicle('a', [(0, 0), (9, 0)]), 1, random.Random(0)), Body(vehicle('b', [(0, 5), (9, 5)]), 1, random.Random(1))]
    queue = PassageQueue(bodies, RADIUS, 0.0, {})
    bodies[0].state, bodies[0].failure = 'failed', '测试'
    with pytest.raises(RuntimeError, match='全部停车'):
        queue.step(1.0)


# --- the real physics ------------------------------------------------------------------------------------------------------
def test_the_engine_runs_a_two_vehicle_plan_at_once_and_faster_with_the_same_paths(tmp_path):
    plan = plan_of(tmp_path, vehicles=2)
    coordinated = fc.apply(plan)
    assert coordinated['traffic']['policy'] == PASSAGE_POLICY
    assert [v['path'] for v in coordinated['vehicles']] == [v['path'] for v in plan['vehicles']]
    one_by_one, together = mujoco_field.execute(plan), mujoco_field.execute(coordinated)
    assert one_by_one['state'] == together['state'] == 'succeeded'
    assert together['metrics']['makespan_s'] < .8 * one_by_one['metrics']['makespan_s']
    assert together['metrics']['makespan_s'] == pytest.approx(coordinated['traffic']['predicted_makespan_s'], rel=.1)
    assert together['metrics']['tasks_completed'] == one_by_one['metrics']['tasks_completed']
    assert separation({n: t for n, t in together['trails'].items()}, plan['robot_radius_m'])['valid']


def test_a_gazebo_record_is_replayed_the_way_it_ran(tmp_path):
    lab = Lab(Config(runtime=tmp_path, background=False))
    try:
        with pytest.raises(PhysicsError) as raised:
            lab.physics.submit(session_id='field_x', traffic='coordinated')
        assert raised.value.code == 'frozen_traffic'
        with pytest.raises(PhysicsError) as raised:
            lab.physics.submit(run_id='run_x', traffic='sideways')
        assert raised.value.code == 'invalid_request'
    finally:
        lab.shutdown()
