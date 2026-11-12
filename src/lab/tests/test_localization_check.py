"""AMCL-vs-truth comparison and replan summaries from recorded timelines."""
import math

from ceres_lab.localization_check import compare


def _exec(const_offset=(0.3, -0.2), yaw_bias=0.05, n=60):
    dt = 0.1
    truth = [[i * dt, i * dt * 0.5, math.sin(i * dt * 0.25), 0.4 * i * dt] for i in range(n)]
    amcl = [[t, x + const_offset[0], y + const_offset[1], yaw + yaw_bias] for t, x, y, yaw in truth[::2]]
    plans = [{'t_sim': 1.0, 'phase': 'executing', 'path': [[0, 0], [1, 0]]},
             {'t_sim': 2.5, 'phase': 'executing', 'path': [[0, 0], [0.8, 0.6]]}]
    return {'actual': {'ground_truth': truth, 'amcl': amcl}, 'nav2_plans': plans}


def test_constant_estimate_offset_is_recovered():
    out = compare(_exec(), 'amcl')
    err = out['estimate_error']
    assert err['paired_samples'] == 30
    assert math.isclose(err['position_rmse_m'], math.hypot(0.3, -0.2), rel_tol=1e-9)
    assert math.isclose(err['position_mean_m'], math.hypot(0.3, -0.2), rel_tol=1e-9)
    assert math.isclose(err['position_max_m'], math.hypot(0.3, -0.2), rel_tol=1e-9)
    assert math.isclose(err['yaw_rmse_rad'], 0.05, rel_tol=1e-9)
    assert out['replans']['count'] == 2 and out['replans']['last_t_sim'] == 2.5
    assert out['replans']['path_length_first_m'] == 1.0
    assert 'not inferred' in out['replans']['note']


def test_interpolated_truth_paired_at_estimate_stamps():
    ex = _exec()
    ex['actual']['amcl'] = ex['actual']['amcl'][::3]          # sparse, off-grid stamps fall between truth samples
    out = compare(ex, 'amcl')
    assert out['estimate_error']['paired_samples'] == 10
    assert math.isclose(out['estimate_error']['position_rmse_m'], math.hypot(0.3, -0.2), rel_tol=1e-6)


def test_ground_truth_mode_and_degenerate_timelines():
    out = compare(_exec(), 'ground_truth')
    assert out['estimate_error'] is None
    ex = _exec(); ex['actual']['amcl'] = [[0.0, 0, 0, 0]]
    assert '不足' in compare(ex, 'amcl')['estimate_error']
    ex = _exec(); ex['actual']['amcl'] = [[10.0 + i * 0.1, 0, 0, 0] for i in range(5)]   # past the truth timeline
    assert '重叠' in compare(ex, 'amcl')['estimate_error']
