"""Local Dubins repair candidate: defect detection, swath-protecting windows and acceptance only via check_path."""
import math

import pytest

from ceres_lab.evolve import repair as R


def test_curvature_defects_detect_a_kink_but_not_a_feasible_arc():
    r = 2.0
    arc = [[r * math.sin(t / 50), r - r * math.cos(t / 50)] for t in range(60)]       # radius r, 4 cm steps
    assert R._curvature_defects(arc, r) == []
    kink = [[0, 0], [1, 0], [2, 0], [2.05, 0.5], [2.1, 1.0]]
    assert R._curvature_defects(kink, r)


def test_window_never_enters_a_swath():
    pts = [[float(i), 0.0] for i in range(12)]
    kinds = ['swath', 'swath', 'turn', 'turn', 'turn', 'turn', 'turn', 'turn', 'swath', 'swath', 'turn']
    a, b = R._window(pts, kinds, 5, half=100.0)
    assert kinds[a - 1] == 'swath' and all(k != 'swath' for k in kinds[a:b])
    assert kinds[b] == 'swath'


def test_real_field_curvature_failure_is_repaired_and_passes_official_check():
    pytest.importorskip('fields2cover')
    from ceres_lab.evolve import instances as I
    from ceres_lab.library import fields
    if not fields.status()['downloaded']:
        pytest.skip('Fields2Benchmark not cached')
    from ceres_lab.solvers import f2c_coverage as C
    _sc, inst = I.coverage_instance('nl_field_2', 'tractor_m')
    params = dict(decomposition='none', swath_objective='n_swath', turn_model='dubins')
    _r0, official, _m0, _raw0 = C.solve_full(inst, params)
    routes, check, metrics, _raw = R.solve_full_repaired(inst, params)
    assert not official['valid'] and check['valid']
    assert metrics['repairs'] >= 1 and metrics['remaining_defects'] == 0
    # the official checker, unchanged, decided validity of the repaired path
    again, _ = C.check_path(inst, routes[0]['polyline'], [])
    assert not any('曲率' in e for e in again['errors'])
