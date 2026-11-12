"""Power simulation: exact McNemar + Holm and the field-level resampling contract."""
import numpy as np

from ceres_lab.evolve import power as P


def test_mcnemar_direction_and_ties():
    a = np.array([0, 0, 0, 0], dtype=bool)
    b = np.array([1, 1, 1, 1], dtype=bool)
    assert P.mcnemar_one_sided(a, b) == 0.5 ** 4
    assert P.mcnemar_one_sided(b, a) == 1.0
    assert P.mcnemar_one_sided(a, a) == 1.0


def test_holm_step_down():
    out = P.holm({'a': 0.01, 'b': 0.04, 'c': 0.03})
    assert abs(out['a'] - 0.03) < 1e-12
    assert abs(out['c'] - 0.06) < 1e-12
    assert abs(out['b'] - 0.06) < 1e-12          # monotone running maximum
    assert P.holm({'a': 0.5, 'b': 0.9}) == {'a': 1.0, 'b': 1.0}


def test_simulate_recovers_registered_direction():
    rng = np.random.default_rng(0)
    groups = np.repeat(np.array([f'f{i}' for i in range(40)]), 3)
    n = len(groups)
    base = rng.uniform(0.4, 0.9, n)
    arms = {'default': base * 0.5, 'static': base, 'static_ext': base * 0.95,
            'learned_ext': base * 0.95}
    res = P.simulate(arms, groups, n_fields=40, B=200, seed=1)
    assert set(res['power']) == {'H1c', 'H2c', 'H3c'}
    assert res['power']['H1c'] > 0.8                    # large registered effect
    assert res['power']['H2c'] < 0.5                    # identical arms cannot reject
    assert res['power']['H3c'] < 0.2                    # small effect with few fields
    assert res['instances_mean'] > 100
    hyp = P.hypotheses(arms)
    assert set(hyp) == {'H1c', 'H2c', 'H3c'}
