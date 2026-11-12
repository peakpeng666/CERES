"""Repair v2 targets corners the fillet verdict cannot round, and keeps validity with the official checker."""
import pytest


def test_unfilletable_corner_is_repaired_and_becomes_executable():
    pytest.importorskip('fields2cover')
    from ceres_lab.library import fields
    if not fields.status()['downloaded']:
        pytest.skip('Fields2Benchmark not cached')
    from ceres_lab.evolve import fillet as FL, instances as I, repair as R, repair2 as R2
    _sc, inst = I.coverage_instance('nl_field_53', 'sprayer_l')
    params = dict(decomposition='none', swath_objective='n_swath', turn_model='dubins')
    pts, kinds, _robot, _cells = R._native(inst, params)
    native = FL.verdict(inst, pts, kinds)
    routes, check, metrics, raw = R2.solve_full_repaired2(inst, params)
    assert not native['fillet_valid'] and native['stage'] == 'fillet'        # documented: one unfilletable corner
    assert check['valid'] and metrics['repairs'] >= 1
    assert FL.verdict(inst, routes[0]['polyline'], raw['kinds'])['fillet_valid']
