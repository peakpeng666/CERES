import math
from xml.etree import ElementTree as ET

import pytest

from ceres_lab import field_physics as p, fieldworld
from ceres_lab.solvers import fleet
from test_backend_integrity import instance


def test_mass_inertia_and_collision_envelope():
    m = p.profile()
    assert m['body']['inertia_kgm2'] == pytest.approx([20*(.56**2+.22**2)/12,
        20*(.8**2+.22**2)/12, 20*(.8**2+.56**2)/12])
    assert m['caster']['inertia_kgm2'] == pytest.approx([.00072]*3)
    assert m['total_mass_kg'] == 25
    from shapely.geometry import Polygon, Point
    hull = Polygon(p.FOOTPRINT)
    # Analytic polygon-edge distance bounds the complete disks, not just sampled corners.
    for side in (-1, 1):
        center = Point(0, side * p.WHEEL_SEPARATION / 2)
        assert hull.covers(center) and hull.exterior.distance(center) >= p.WHEEL_RADIUS - 1e-12
    assert m['wheel_contact'] == 'sphere' and m['wheel']['width_m'] == .30
    # the hull must contain every projected rigid-ring tyre rectangle up to the widest supported tyre
    for side in (-1, 1):
        for cx in (-p.WHEEL_RADIUS, p.WHEEL_RADIUS):
            for cy in (-p.TYRE_WIDTH_MAX/2, p.TYRE_WIDTH_MAX/2):
                corner = Point(cx, side*p.WHEEL_SEPARATION/2 + cy)
                assert hull.covers(corner) and hull.covers(Point(0, side*p.WHEEL_SEPARATION/2 + cy))
    assert max(math.hypot(*v) for v in p.FOOTPRINT) == pytest.approx(p.RADIUS)
    loaded = p.profile({'payload_kg': 20})
    assert loaded['total_mass_kg'] == 45 and loaded['body']['inertia_kgm2'][0] == 2*m['body']['inertia_kgm2'][0]
    assert loaded['sha256'] != m['sha256']


def test_tyre_model_and_legacy_sphere_profile():
    m = p.profile({'wheel_contact': 'tyre'})
    assert m['schema'] == 'urp.field-diffdrive.rigid/6'
    assert m['wheel'] == {'geometry': 'cylinder', 'radius_m': p.WHEEL_RADIUS, 'width_m': p.TYRE_WIDTH,
                          'separation_m': p.WHEEL_SEPARATION, 'mass_kg': 2,
                          'inertia_kgm2': pytest.approx(p.cylinder_inertia(2, p.WHEEL_RADIUS, p.TYRE_WIDTH))}
    assert 'diagnostic' in m['assumptions']['wheel_contact'] and 'post-hoc' in m['assumptions']['actuator']
    sphere = p.profile({'wheel_contact': 'sphere'})
    assert sphere['wheel']['geometry'] == 'sphere'
    assert sphere['wheel']['inertia_kgm2'] == pytest.approx([.4*2*p.WHEEL_RADIUS**2]*3)
    assert sphere['sha256'] != m['sha256']
    narrow = p.profile({'wheel_contact': 'tyre', 'tyre_width_m': p.TYRE_WIDTH_MAX})
    assert narrow['wheel']['width_m'] == p.TYRE_WIDTH_MAX and narrow['collision_hull_m'] == m['collision_hull_m']


def test_energy_accounting_matches_closed_form():
    import numpy as np
    from ceres_lab.field_energy import account
    physics = p.profile({'payload_kg': 5})            # 30 kg total
    crr, eta, mass, g = .06, .8, 30, 9.80665
    dt, v_cruise, a = .02, 1.0, .5
    ramp = [(i*dt, .5*a*(i*dt)**2) for i in range(0, 101)]               # 0 → 1 m/s over 2 s, 1 m
    cruise = [(2 + i*dt, 1.0 + v_cruise*i*dt) for i in range(1, 151)]    # 3 s at 1 m/s, 3 m
    samples = [[t, x, 0, 0, 0, 0, 0] for t, x in ramp + cruise]
    out = account(samples, physics)
    inertial = mass*a*a*2**2/2/eta                                      # ∫m·a·v dt over the ramp
    rolling = crr*mass*g*(1.0 + 3.0)/eta                                # rolling resistance over 4 m
    assert out['energy_j'] == pytest.approx(inertial + rolling, rel=.03)
    assert out['distance_m'] == pytest.approx(4.0, rel=.01)
    assert out['peak_wheel_torque_nm'] == pytest.approx((mass*a + crr*mass*g)*p.WHEEL_RADIUS/2, rel=.05)
    assert out['model']['recuperation'] is False
    assert account([[0, 0, 0, 0, 0, 0, 0]], physics) is None


@pytest.mark.parametrize('bad', [{'engine': 'other'}, {'seed': True}, {'seed': -1},
    {'payload_kg': -1}, {'ground_friction': float('nan')}, {'max_step_size_s': .1}, {'collision_detector':'unknown'},
    {'wheel_contact': 'magic'}, {'tyre_width_m': .5}, {'tyre_width_m': .302}])
def test_invalid_physics_rejected(bad):
    with pytest.raises(ValueError):
        p.profile(bad)


def test_rest_to_rest_triangular_trapezoidal_and_route_reference():
    assert p.rest_to_rest(1, 10, 1, 1) == pytest.approx(2)
    assert p.rest_to_rest(2, 1, 1, 1) == pytest.approx(3)
    assert p.rest_to_rest(0, 1, 1, 1) == 0
    ref = p.timing_reference([[0,0],[1,0],[1,1]], 1, 4)
    assert ref['rotation_s'] > 0 and ref['duration_s'] > ref['constant_speed_service_s'] == 6


def test_emitted_sdf_audit_and_inertia_tampering(tmp_path):
    inst = instance()
    routes, chk, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert chk['valid']
    plan = fieldworld.build(inst, routes, tmp_path, 'physical', physics={'payload_kg': 10, 'max_step_size_s': .002})
    assert plan['physics_audit']['valid'] and plan['physics']['total_mass_kg'] == 35
    assert plan['execution_timing_reference']['serial_makespan_s'] > 0
    doc = ET.parse(plan['sdf'])
    doc.find('.//inertial/inertia/ixx').text = '50'
    doc.write(plan['sdf'])
    assert not p.audit_sdf(plan['sdf'], plan['physics'])['valid']


def test_wheel_odom_alignment_preserves_drift_and_checks_physical_domain():
    from ceres_lab.field_dynamics import verify
    r = {'vehicles':[{'name':'v'}], 'truth_timing':{'latest_clock_samples':0,'tf_header_samples':3},
         'rigid_body_poses':{'v':[[0,5,3,0,0,0,math.pi/2],[.1,5,4,0,0,0,math.pi/2],[.2,5,5,0,0,0,math.pi/2]]},
         'wheel_odometry':{'v':[[0,0,0,0,1,0],[.1,1,0,0,1,0],[.2,2.2,0,0,1,0]]}}
    check = verify(r)
    assert check['valid'] and check['vehicles'][0]['odom_position_final_m'] == pytest.approx(.2)
    r['trails'] = {'v':[[0,5,3,math.pi/2],[.1,5,4,math.pi/2],[.2,5,5,math.pi/2]]}
    assert verify(r)['valid']
    r['trails']['v'][-1][1] += 1
    assert any('二维轨迹' in e for e in verify(r)['errors'])
    r['trails'].pop('v')
    r['rigid_body_poses']['v'][-1][4] = .7
    assert not verify(r)['valid']
    r['truth_timing']['latest_clock_samples'] = 10
    assert any('源仿真时间' in e for e in verify(r)['errors'])
    # Equal endpoints do not prove the robot remained stationary in the unobserved interval.
    parked = {'vehicles':[{'name':'v'}], 'truth_timing':{'latest_clock_samples':0,'tf_header_samples':3},
              'rigid_body_poses':{'v':[[0,5,3,0,0,0,1],[.5,5,3,0,0,0,1],[1,5,3,0,0,0,1]]},
              'wheel_odometry':{'v':[[0,0,0,0,1,0],[.5,0,0,0,1,0],[1,0,0,0,1,0]]}}
    assert not verify(parked)['valid']
    parked['rigid_body_poses']['v'][1] = [.5,6,3,0,0,0,1]     # the robot moved across the 0.5 s gap
    assert not verify(parked)['valid']


@pytest.mark.parametrize('selector,value', [
    ('./world/gravity','0 0 -1'),
    (".//model[@name='ground']/link/collision/surface/friction/ode/mu",'0.001'),
    (".//plugin[@name='gz::sim::systems::DiffDrive']/wheel_separation",'1.4'),
    ('./world/physics/dart/collision_detector','bullet'),
    (".//link[@name='left_wheel']/pose", '0 0.28 0.15 -1.570796326795 0 0'),
    (".//link[@name='left_wheel']/collision/geometry/sphere/radius", '0.16'),
    (".//link[@name='left_wheel']/visual/geometry/sphere/radius", '0.16'),
    (".//joint[@name='left_wheel_joint']/axis/xyz", '1 0 0'),
])
def test_frozen_model_rejects_sdf_parameter_drift(tmp_path, selector, value):
    inst = instance()
    routes, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    plan = fieldworld.build(inst,routes,tmp_path,'audit_parameters')
    doc = ET.parse(plan['sdf']);doc.find(selector).text = value;doc.write(plan['sdf'])
    assert not p.audit_sdf(plan['sdf'],plan['physics'])['valid']


def test_no_slip_consistency_is_a_gate_not_a_suppressed_drift_statistic():
    import copy
    from ceres_lab.field_dynamics import verify
    r = {'physics':p.profile(), 'vehicles':[{'name':'v'}],
         'truth_timing':{'latest_clock_samples':0,'tf_header_samples':3},
         'rigid_body_poses':{'v':[[0,5,3,0,0,0,0],[.1,5.05,3,0,0,0,0],[.2,5.1,3,0,0,0,0]]},
         'wheel_odometry':{'v':[[0,0,0,0,.5,0],[.1,.05,0,0,.5,0],[.2,.1,0,0,.5,0]]}}
    assert verify(r)['valid']
    missing = copy.deepcopy(r); missing['physics'].pop('odometry_contract')
    assert not verify(missing)['valid']
    drift = copy.deepcopy(r); drift['wheel_odometry']['v'][1][1] += .11
    checked = verify(drift)
    assert not checked['valid'] and checked['vehicles'][0]['odom_position_final_m'] == pytest.approx(0)
    assert checked['vehicles'][0]['odom_position_max_m'] == pytest.approx(.11)
    drift = copy.deepcopy(r); drift['wheel_odometry']['v'][1][3] += .031
    assert not verify(drift)['valid']
    drift['physics']['odometry_contract']['max_yaw_error_rad'] = 1
    assert any('协议' in e for e in verify(drift)['errors'])
    short = copy.deepcopy(r)
    short['rigid_body_poses']['v'].extend([[.3,5.15,3,0,0,0,0],[.4,5.2,3,0,0,0,0],[.5,5.25,3,0,0,0,0]])
    assert any('完整' in e for e in verify(short)['errors'])


def test_fleet_outputs_explicit_model_assumptions():
    inst = instance()
    routes, chk, _, raw, *_ = fleet.solve({}, inst, 'panel.sweep', {})
    assert routes and chk['valid']
    model = raw['model_contract']
    assert model['time']['traffic_conflicts'] is False
    assert model['task']['physical_reach_verified'] is False
    assert model['optimization']['optimality_proved'] is False


def test_runtime_audit_rejects_silent_detector_fallback(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ros'))
    from physics_runtime import audit
    library = tmp_path / 'libignition-physics5-dartsim-plugin.so.5.4.0'
    library.write_bytes(b'test-library-content')
    log = tmp_path/'gazebo.log'
    log.write_text('Using [ode] collision detector\nUsing [DantzigBoxedLcpSolver] solver.\n')
    read = Path.read_text
    def read_with_maps(path, *args, **kwargs):
        if str(path) == '/proc/123456/maps':
            return f'1000-2000 r-xp 0000 00:00 1 {library}\n'
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path,'read_text',read_with_maps)
    assert audit(p.profile(), log, [123456])['valid']
    assert not audit(p.profile({'collision_detector':'bullet'}),log,[123456])['valid']
    assert not audit(p.profile(),log,[])['valid']


def test_energy_split_by_surface_uses_corridor_coefficient():
    from ceres_lab.field_energy import account
    physics = p.profile()
    model = physics['energy_model']
    assert model['rolling_resistance'] == .06 and model['rolling_resistance_corridor'] == .03
    dt = .02
    samples = [[i * dt, 5 + i * dt, 0, 0, 0, 0, 0] for i in range(201)]     # 4 m of driving
    road = [[4.5, -1], [8.5, -1], [8.5, 1], [4.5, 1]]                       # covers x∈[4.5,8.5]
    field_only = account(samples, physics)
    with_road = account(samples, physics, [road])
    assert field_only['energy_corridor_j'] == 0.0
    assert with_road['energy_corridor_j'] > 0        # most of this run rolls on the road, so it dominates
    ratio = with_road['energy_field_j'] / field_only['energy_field_j']
    assert .08 < ratio < .18         # only the truly off-road tail (x>8.5, ~12.5% of the run) stays on the field coefficient
    assert with_road['energy_j'] < .7 * field_only['energy_j']   # most of the run rolls on the cheaper surface
