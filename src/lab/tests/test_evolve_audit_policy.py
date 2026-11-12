"""Synthetic checks for the complementarity audit and try-order policies (no solver runs)."""
import json
import math

import numpy as np
import pytest

from ceres_lab.evolve import audit, instances, policy


def _matrix(cost, groups=None):
    n, k = cost.shape
    return {'keys': [f'i{i}' for i in range(n)], 'meta': [{'field': (groups or [f'g{i}' for i in range(n)])[i]} for i in range(n)],
            'configs': [f'c{j}' for j in range(k)], 'cost': cost, 'status': np.full((n, k), 'feasible', dtype=object),
            'wall': np.ones((n, k))}


def test_rho_penalises_failures_and_marks_unsolved():
    cost = np.array([[10.0, 12.0], [np.nan, 5.0], [np.nan, np.nan]])
    r, solvable = audit.rho(cost, 2.0)
    assert solvable.tolist() == [True, True, False]
    assert r[0].tolist() == [1.0, 1.2] and r[1].tolist() == [2.0, 1.0]


def test_shapley_is_efficient_and_symmetric():
    rng = np.random.default_rng(0)
    r = np.minimum(2.0, 1.0 + rng.random((40, 5)))
    phi = audit.shapley(r, 2.0)
    full = (2.0 - r.min(axis=1)).mean()
    assert phi.sum() == pytest.approx(full)                     # efficiency
    r2 = np.column_stack([r[:, 0], r[:, 0], r[:, 2]])            # identical players get equal value
    phi2 = audit.shapley(r2, 2.0)
    assert phi2[0] == pytest.approx(phi2[1])


def test_shapley_matches_brute_force_for_small_k():
    import itertools
    rng = np.random.default_rng(3)
    r = np.where(rng.random((15, 4)) < 0.3, 2.0, 1.0 + rng.random((15, 4)))
    k, P = r.shape[1], 2.0

    def v(S):
        return 0.0 if not S else (P - r[:, list(S)].min(axis=1)).mean()
    brute = np.zeros(k)
    for perm in itertools.permutations(range(k)):
        seen = []
        for a in perm:
            brute[a] += v(seen + [a]) - v(seen)
            seen.append(a)
    brute /= math.factorial(k)
    assert np.allclose(audit.shapley(r, P), brute)


def test_family_summary_headroom_and_greedy_curve():
    # c0 best on half the instances, c1 on the other half, c2 always infeasible.
    cost = np.array([[1.0, 2.0, np.nan]] * 6 + [[3.0, 1.5, np.nan]] * 6)
    s = audit.family_summary(_matrix(cost), [f'g{i % 4}' for i in range(12)])
    assert s['solvable'] == 12 and s['vbs']['feasible'] == 12
    assert s['headroom']['rho'] > 0
    assert s['greedy_static_order'][-1]['mean_rho'] == pytest.approx(1.0)
    assert {c['config'] for c in s['shapley']} == {'c0', 'c1', 'c2'}
    assert next(c for c in s['shapley'] if c['config'] == 'c2')['shapley'] == pytest.approx(0.0)


def test_execute_stops_at_first_feasible_and_counts_walls():
    rho = np.array([2.0, 1.3, 1.0])
    wall = np.array([5.0, 1.0, 2.0])
    e = policy.execute([0, 1, 2], rho, wall)
    assert e['tries'] == 2 and e['rho'] == 1.3 and e['wall_s'] == 6.0 and not e['first_feasible']
    b = policy.execute([0, 1, 2], rho, wall, best_of=True)
    assert b['rho'] == 1.0 and b['tries'] == 3
    f = policy.execute([0], rho, wall)
    assert f['rho'] == policy.PENALTY and not f['feasible']


def test_learned_policies_recover_a_feature_rule():
    """If feasibility of c0 vs c1 is decided by one feature, learned orders beat the static order."""
    rng = np.random.default_rng(1)
    n = 120
    x = rng.random(n)
    rho = np.where(x[:, None] > 0.5, [[1.0, 2.0]], [[2.0, 1.0]])
    X = np.column_stack([x, rng.random(n)])
    train, test = np.arange(0, 80), np.arange(80, n)
    for kind in policy.LEARNED:
        model = policy.fit(kind, X[train], rho[train])
        orders = policy.order(model, X[test])
        first = np.mean([rho[i, o[0]] < 2.0 for i, o in zip(test, orders)])
        assert first > 0.9, kind
    static = policy.fit('static', None, rho[train])
    first_static = np.mean([rho[i, static['static'][0]] < 2.0 for i in test])
    assert first_static < 0.7


def test_logit_handles_constant_labels():
    model = policy._logit_fit(np.zeros((5, 2)), np.ones(5))
    assert 'const' in model and 0 < model['const'] <= 1


def test_sealed_fields_are_guarded(tmp_path):
    with pytest.raises(PermissionError):
        instances._guard('lt_field_1', None)
    with pytest.raises(PermissionError):
        instances._guard('ee_field_5', 'not-a-token')
    instances._guard('nl_field_1', None)
    instances._guard('ee_field_130', None)
    draft = tmp_path / 'p.md'
    draft.write_text('状态：**草案 v0.9，待冻结**', encoding='utf-8')
    with pytest.raises(PermissionError):
        instances.sealed_token(draft)
    frozen = tmp_path / 'f.md'
    frozen.write_text('状态：FROZEN（导师确认）', encoding='utf-8')
    assert instances.sealed_token(frozen).startswith('FROZEN:')


def test_split_rule():
    assert instances.split_of('nl_field_3') == 'dev'
    assert instances.split_of('ee_field_130') == 'dev'
    assert instances.split_of('ee_field_131') == 'sealed'
    assert instances.split_of('lt_field_1') == 'sealed'


def test_repair_portfolio_and_labels():
    from ceres_lab.evolve import plans
    assert len(plans.REPAIR_PORTFOLIO) == 6
    assert all(c.startswith(('repair:none/', 'repair:boustrophedon/')) for c in plans.REPAIR_PORTFOLIO)
    assert all(a == 'evolve.f2c_repair' and p['turn_model'] == 'dubins' for a, p in plans.REPAIR_PORTFOLIO.values())
    assert plans.config_label('repair:none/n_swath') == '修补·不分解·最少作业行'


def test_export_static_model_is_fingerprinted_and_greedy(tmp_path):
    """A static export carries the greedy order, training means and the scope cloud — no learned payload."""
    from ceres_lab.evolve import features as F
    from ceres_lab.evolve import plans
    rng = np.random.default_rng(5)
    configs = list(plans.COVERAGE_PORTFOLIO)
    n, k = 30, len(configs)
    cost = np.where(rng.random((n, k)) < 0.5, 1.0 + rng.random((n, k)), np.nan)
    rho, solvable = audit.rho(cost, policy.PENALTY)
    feats = [{name: float(v) for name, v in zip(F.COVERAGE, rng.random(len(F.COVERAGE)) * 10)} for _ in range(n)]
    groups = np.array([f'g{i % 10}' for i in range(n)])
    ds = {'m': {'keys': [f'k{i}' for i in range(n)]}, 'rho': rho, 'solvable': solvable, 'feats': feats,
          'groups': groups, 'configs': configs, 'wall': np.ones((n, k))}
    doc = policy.export_model(ds, 'static', 'agri', {'note': 'test'}, tmp_path / 'm.json')
    art = doc['artifact']
    assert art['kind'] == 'static' and 'logit' not in art and 'train_rho' not in art
    assert art['static_order'] == policy.fit('static', None, rho[solvable])['static']
    assert len(art['train_rho_mean']) == k and len(art['train_Z']) == int(solvable.sum())
    payload = json.dumps(art, sort_keys=True, separators=(',', ':')).encode()
    assert doc['artifact_sha256'] == __import__('hashlib').sha256(payload).hexdigest()
