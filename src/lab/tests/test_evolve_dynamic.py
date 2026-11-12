"""Discrete-event dynamic stream simulator: service completeness, commitment and accounting invariants."""
import math

import numpy as np
import pytest

from ceres_lab.evolve import dynamic as Y


def _stream(n=12, K=2, seed=0, horizon=600.0):
    rng = np.random.default_rng(seed)
    xy = np.vstack([[0.0, 0.0], rng.uniform(0, 200, (n, 2))])
    D = np.hypot(*(xy[:, None, :] - xy[None, :, :]).transpose(2, 0, 1))
    release = np.concatenate([[0.0], np.zeros(3), np.sort(rng.uniform(1, horizon, n - 3))])
    return Y.Stream(key='t', D=D, release=release, vehicles=K, speed=1.0, service_s=5.0, xy=xy)


@pytest.mark.parametrize('policy', [Y.always('insert'), Y.always('reopt'), Y.periodic(3)])
def test_every_task_is_served_once_after_release(policy):
    s = _stream()
    out = Y.simulate(s, policy, budget_s=0.05, seed=1)
    assert out['distance_m'] > 0 and out['mean_response_s'] >= 0
    assert len(out['decisions']) == len(s.release) - 1
    assert all(d['task'] >= 1 for d in out['decisions'])


def test_reopt_policy_never_increases_distance_versus_insert_plan_at_decision_time():
    s = _stream(seed=3)
    out = Y.simulate(s, Y.always('insert'), budget_s=0.05, seed=1, label=True)
    gains = [d['gain_rel'] for d in out['decisions'] if 'gain_rel' in d]
    assert gains and min(gains) > -1e-6          # PyVRP from the insertion plan's tasks is never worse here


def test_cheapest_insertion_matches_brute_force():
    s = _stream(n=6, seed=5)
    fleet = [Y.Vehicle(node=0, route=[1, 2]), Y.Vehicle(node=3, route=[4])]
    k, pos, delta = Y.cheapest_insertion(s.D, fleet, 5)
    best = math.inf
    for kk, v in enumerate(fleet):
        for p in range(len(v.route) + 1):
            r = v.route[:p] + [5] + v.route[p:]
            best = min(best, Y.route_cost(s.D, v.node, r) - Y.route_cost(s.D, v.node, v.route))
    assert delta == pytest.approx(best)


def test_idle_vehicle_departs_no_earlier_than_assignment():
    xy = np.array([[0.0, 0.0], [10.0, 0.0], [20.0, 0.0]])
    D = np.hypot(*(xy[:, None, :] - xy[None, :, :]).transpose(2, 0, 1))
    s = Y.Stream(key='idle', D=D, release=np.array([0.0, 0.0, 100.0]), vehicles=1, speed=1.0, service_s=0.0, xy=xy)
    out = Y.simulate(s, Y.always('insert'), budget_s=0.05)
    # task 1 served at 10 s; task 2 released at 100 s, vehicle idle at node 1 departs at 100 → arrives 110.
    assert out['makespan_s'] == pytest.approx(110.0)
    assert out['distance_m'] == pytest.approx(20.0)


def test_gain_model_recovers_linear_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(Y.FEATURES)))
    y = 0.3 * X[:, 0] - 0.1 * X[:, 3] + rng.normal(scale=0.01, size=200)
    m = Y.GainModel(alpha=1e-3).fit(X, y)
    assert np.corrcoef(m.predict(X), y)[0, 1] > 0.99
