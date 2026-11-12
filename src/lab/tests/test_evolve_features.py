"""Invariance and correctness checks for the agricultural feature library (ceres_lab.evolve.features)."""
import math
import random

import pytest

from ceres_lab.evolve import features as F
from ceres_lab.evolve.geometry import dubins_length, uturn_length

L_SHAPE = [[0, 0], [200, 0], [200, 60], [80, 60], [80, 150], [0, 150]]
IRREGULAR = [[0, 0], [130, -12], [260, 8], [300, 90], [250, 170], [170, 150], [120, 210], [40, 180], [-10, 90]]
HOLE = [[150, 30], [175, 30], [175, 45], [150, 45]]
MACHINE = dict(swath_width=6.0, turn_radius=4.5, headland_width=12.0)
SCALE_FREE = F.SHAPE + ('turn_radius_over_swath', 'headland_over_turn_diameter', 'inner_area_share',
                        'inner_components', 'inner_solidity', 'inner_reflex_s', 'swaths_est',
                        'swath_len_over_turn_diameter', 'uturn_over_swath', 'turn_share_est',
                        'field_short_over_swath', 'inner_empty')


def _transform(ring, angle=0.0, dx=0.0, dy=0.0, scale=1.0):
    c, s = math.cos(angle), math.sin(angle)
    return [[scale * (c * x - s * y) + dx, scale * (s * x + c * y) + dy] for x, y in ring]


def _close(a, b, keys, rel=1e-6, abs_=1e-6):
    for k in keys:
        assert a[k] == pytest.approx(b[k], rel=rel, abs=abs_), (k, a[k], b[k])


def test_dubins_matches_analytic_uturns():
    assert uturn_length(3.0, 1.0) == pytest.approx(math.pi + 1.0, rel=1e-12)
    assert uturn_length(2.0, 1.0) == pytest.approx(math.pi, rel=1e-12)
    # A U-turn narrower than the turning diameter needs a longer bulb turn.
    assert uturn_length(1.0, 1.0) > math.pi
    assert dubins_length((0, 0, 0), (10, 0, 0), 2.0) == pytest.approx(10.0, rel=1e-12)


def test_dubins_matches_fields2cover():
    f2c = pytest.importorskip('fields2cover')
    rng = random.Random(7)
    robot = f2c.Robot(2.0, 6.0)
    for _ in range(80):
        r = rng.uniform(0.5, 8.0)
        robot.setMinTurningRadius(r)
        q0 = (rng.uniform(-20, 20), rng.uniform(-20, 20), rng.uniform(-math.pi, math.pi))
        q1 = (rng.uniform(-20, 20), rng.uniform(-20, 20), rng.uniform(-math.pi, math.pi))
        turner = f2c.PP_DubinsCurves()
        turner.setDiscretization(0.01)
        path = turner.createTurn(robot, f2c.Point(q0[0], q0[1]), q0[2], f2c.Point(q1[0], q1[1]), q1[2])
        assert dubins_length(q0, q1, r) == pytest.approx(path.length(), rel=1e-4)


@pytest.mark.parametrize('ring,holes', [(L_SHAPE, []), (IRREGULAR, []), (L_SHAPE, [HOLE])])
def test_rigid_motion_start_index_and_orientation_invariance(ring, holes):
    base = F.coverage_features(ring, holes, **MACHINE)
    for angle, dx, dy in [(0.37, 1e4, -3e3), (math.pi / 2, 0, 0), (2.9, -55.5, 812.0)]:
        moved = F.coverage_features(_transform(ring, angle, dx, dy), [_transform(h, angle, dx, dy) for h in holes], **MACHINE)
        _close(base, moved, F.COVERAGE, rel=1e-6, abs_=1e-6)
    rolled = ring[3:] + ring[:3]
    _close(base, F.coverage_features(rolled, holes, **MACHINE), F.COVERAGE)
    _close(base, F.coverage_features(list(reversed(ring)), holes, **MACHINE), F.COVERAGE)


def test_collinear_vertices_do_not_change_simplified_counts():
    dense = []
    for (x0, y0), (x1, y1) in zip(L_SHAPE, L_SHAPE[1:] + L_SHAPE[:1]):
        dense += [[x0 + (x1 - x0) * t / 5, y0 + (y1 - y0) * t / 5] for t in range(5)]
    a, b = F.coverage_features(L_SHAPE, [], **MACHINE), F.coverage_features(dense, [], **MACHINE)
    _close(a, b, F.COVERAGE, rel=1e-6, abs_=1e-6)


def test_uniform_scaling_of_field_and_machine():
    base = F.coverage_features(IRREGULAR, [], **MACHINE)
    k = 2.5
    scaled = F.coverage_features(_transform(IRREGULAR, scale=k), [], **{m: v * k for m, v in MACHINE.items()})
    _close(base, scaled, SCALE_FREE, rel=1e-6, abs_=1e-6)
    assert scaled['area_ha'] == pytest.approx(base['area_ha'] * k * k, rel=1e-9)


def test_rectangle_reference_values():
    f = F.coverage_features([[0, 0], [300, 0], [300, 100], [0, 100]], [], swath_width=6.0, turn_radius=2.0, headland_width=12.0)
    assert f['solidity'] == pytest.approx(1.0)
    assert f['rectangularity'] == pytest.approx(1.0)
    assert f['rectilinearity'] == pytest.approx(1.0)
    assert f['elongation'] == pytest.approx(3.0)
    assert f['reflex_s'] == 0
    # inner area 276 × 76 m → 76/6 swaths along the long side; U-turn with 2r < w is π r + (w − 2r).
    assert f['swaths_est'] == pytest.approx(76 / 6)
    assert f['uturn_over_swath'] == pytest.approx((math.pi * 2 + 2) / 6)


def test_empty_inner_area_is_finite_and_flagged():
    f = F.coverage_features([[0, 0], [20, 0], [20, 15], [0, 15]], [], swath_width=6.0, turn_radius=4.5, headland_width=12.0)
    assert f['inner_empty'] == 1.0 and f['inner_area_share'] == 0.0 and f['turn_share_est'] == 0.0
    assert all(math.isfinite(v) for v in f.values())


def test_feature_order_and_groups_are_stable():
    f = F.coverage_features(L_SHAPE, [], **MACHINE)
    assert list(f) == list(F.COVERAGE)
    assert set(F.SIZE) | set(F.SHAPE) | set(F.MACHINE) == set(F.COVERAGE)
    assert F.VERSION == 'agfeat/1'
    assert F.coverage_features(L_SHAPE, [], **MACHINE) == f      # deterministic


def test_real_field_rotation_invariance():
    from ceres_lab.library import fields
    try:
        fid = next(i for i in fields.all_ids() if i.startswith('nl_'))
    except StopIteration:
        pytest.skip('Fields2Benchmark not cached')
    inst = fields.scenario(fid)['instance']
    base = F.coverage_features(inst['field'], inst['obstacles'], **MACHINE)
    moved = F.coverage_features(_transform(inst['field'], 1.1, 500, -20),
                                [_transform(h, 1.1, 500, -20) for h in inst['obstacles']], **MACHINE)
    _close(base, moved, F.COVERAGE, rel=1e-5, abs_=1e-5)
