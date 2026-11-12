"""Admission-rule pairwise comparison: family sizes, Holm monotonicity and selection overlap."""
import numpy as np

from ceres_lab.evolve import admission_rules as A


def _result():
    rng = np.random.default_rng(0)
    groups = [f'f{i // 2}' for i in range(20)]
    rules = {}
    per = {'first': {}, 'logit_first': {}, 'rho': {}}
    for rule in A.RULES:
        for m in A.CAPACITIES:
            key = f'{rule}@{m}'
            rules[key] = {'selection_frequency': {f'cfg{i}': 10 - i for i in range(5)}, 'selections': 10,
                          'static_first_try': (0.8, [0.7, 0.9]), 'static_stop_rho': (1.02, [1.0, 1.04])}
            for metric in per:
                per[metric][key] = (rng.random(20) * 0.1 + (0.9 if metric == 'first' else 0.01)).tolist()
    return {'per_instance': {'groups': groups, **per, 'control_first': (rng.random(20) * 0.1 + 0.7).tolist(),
                             'control_rho': (rng.random(20) * 0.02 + 1.0).tolist()}, 'rules': rules}


def test_compare_shapes_and_holm():
    res = A.compare(_result(), B=50)
    assert len(res['families']) == 3 * len(A.CAPACITIES)
    for fam in res['families']:
        assert len(fam['comparisons']) == 15                     # 6 arms (5 rules + L0) choose 2
        for c in fam['comparisons']:
            assert c['ci95'][0] <= c['mean_diff'] <= c['ci95'][1]
            if c['p_two_sided'] is not None:
                assert c['p_holm'] >= c['p_two_sided'] - 1e-12
    assert len(res['selection_overlap']) == len(A.CAPACITIES) * 10
    assert all(0 <= v['jaccard'] <= 1 for v in res['selection_overlap'].values())
