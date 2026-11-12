"""Fillet (corner-rounding) verdict: classification, analytic rounding, roundability and official re-check."""
import math

import pytest

from ceres_lab.evolve import fillet as FL
from ceres_lab.evolve.reverify import strict_curvature


def test_long_leg_corner_is_rounded_with_analytic_deviation():
    pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]
    f = FL.fillet(pts, ['turn', 'turn'], radius=1.0)
    assert f['ok'] and f['corner'] == 1 and f['rounded'] == 1
    assert f['max_deviation_m'] == pytest.approx(math.sqrt(2) - 1, rel=1e-9)
    # quarter circle replaces the corner: length = 2·(10 − 1) + π/2
    assert f["length_m"] == pytest.approx(18 + math.pi / 2, rel=1e-5)          # chords of a 2 cm-sampled arc
    assert strict_curvature(f['points'], 1.0)['strict_violations'] == 0
    assert strict_curvature(pts, 1.0)['strict_violations'] > 0          # the unrounded corner fails strictly


def test_short_leg_corner_is_not_filletable():
    f = FL.fillet([[0.0, 0.0], [0.5, 0.0], [0.5, 10.0]], ['turn', 'turn'], radius=1.0)
    assert not f['ok'] and f['n_unfilletable'] >= 1


def test_sampled_arc_of_radius_r_is_left_unchanged():
    r, step = 2.0, 0.02
    n = int(math.pi * r / step)
    arc = [[r * math.sin(k * step / r), r - r * math.cos(k * step / r)] for k in range(n)]
    f = FL.fillet(arc, ['turn'] * (len(arc) - 1), radius=r)
    assert f['ok'] and f['corner'] == 0 and f['rounded'] == 0 and f['arc'] > 0
    assert f['length_m'] == pytest.approx(sum(math.dist(p, q) for p, q in zip(arc, arc[1:])), rel=1e-12)


def test_kink_tolerance_is_a_deviation_not_an_angle():
    # 10° jump: e = r(1/cos5° − 1) is 3.8 mm at r = 1 m (tolerated) but 2.7 cm at r = 7 m (a corner)
    d = math.radians(10.0)
    pts = [[0.0, 0.0], [0.03, 0.0], [0.03 + 0.03 * math.cos(d), 0.03 * math.sin(d)]]
    assert FL.classify(pts, radius=1.0)[0]['cls'] == 'noise'
    assert FL.classify(pts, radius=7.0)[0]['cls'] == 'corner'
    assert FL.deviation(d, 1.0) == pytest.approx(1.0 * (1 / math.cos(d / 2) - 1))


def test_sub_degree_kink_is_classified_as_noise():
    d = math.radians(0.3)
    pts = [[0.0, 0.0], [10.0, 0.0], [10.0 + 10 * math.cos(d), 10 * math.sin(d)]]
    v = FL.classify(pts, radius=4.5)
    assert [x['cls'] for x in v] == ['noise']
    f = FL.fillet(pts, ['turn', 'turn'], radius=4.5)
    assert f['ok'] and f['noise'] == 1 and f['rounded'] == 1           # long legs: rounded as well


def test_swath_segments_are_trimmed_and_declared():
    pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]
    f = FL.fillet(pts, ['swath', 'turn'], radius=1.0)
    assert f['ok'] and len(f['swaths']) == 1
    (a, b), = f['swaths']
    assert a == [0.0, 0.0] and b[0] == pytest.approx(9.0)


def test_rounding_that_hits_an_obstacle_fails_the_official_recheck():
    inst = {'field': [[-5, -5], [15, -5], [15, 15], [-5, 15]], 'obstacles': [[[8.7, 1.05], [9.0, 1.05], [9.0, 1.35], [8.7, 1.35]]],
            'headland_width': 1.0, 'robot_width': 1.0, 'swath_width': 2.0, 'turn_radius': 3.0}
    out = FL.verdict(inst, [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]], ['turn', 'turn'])
    assert out['stage'] == 'official_check_after_fillet' and not out['fillet_valid']
    assert any('越出田块或穿越障碍' in e for e in out['errors_after'])


def test_real_field_long_chord_corners_are_classified_reproducibly():
    pytest.importorskip('fields2cover')
    from ceres_lab.library import fields
    if not fields.status()['downloaded']:
        pytest.skip('Fields2Benchmark not cached')
    from ceres_lab.evolve import instances as I, repair
    _sc, inst = I.coverage_instance('nl_field_1', 'robot_s')
    params = dict(decomposition='none', swath_objective='n_swath', turn_model='dubins')
    pts, kinds, _robot, _cells = repair._native(inst, params)
    assert strict_curvature(pts, inst['turn_radius'])['strict_violations'] > 0       # the documented blind spot
    a = FL.verdict(inst, pts, kinds)
    b = FL.verdict(inst, pts, kinds)
    assert a == b and a['corner'] >= 1
