"""Historical coverage documents cannot activate a live recommendation without independent review."""
import copy
import hashlib
import json

import numpy as np
import pytest

from ceres_lab import recommend
from ceres_lab.evolve import features as F
from ceres_lab.evolve import instances as I
from ceres_lab.evolve import plans, serve
from ceres_lab.evolve.policy import LOG_FEATURES

FIELD = [[0, 0], [260, 0], [260, 140], [0, 140]]


def _scenario(machine='tractor_m', field=FIELD):
    spec = I.PROFILES[machine]
    inst = {'field': field, 'obstacles': [], 'swath_width': spec['swath_width'], 'robot_width': spec['robot_width'],
            'turn_radius': spec['turn_radius'], 'headland_width': spec['headland_width'], 'start': field[0],
            'route_pattern': 'boustrophedon', 'angle_deg': None}
    return {'id': 't', 'problem_class': 'coverage', 'frame': {'kind': 'field', 'units': 'm'}, 'instance': inst}, inst


def _model(tmp_path, first='full:none/swath_length', corrupt=False):
    names = F.COVERAGE
    rows = []
    for k, w in enumerate((200, 260, 320, 380)):
        for prof in I.PROFILES:
            s = I.PROFILES[prof]
            f = F.coverage_features([[0, 0], [w, 0], [w, 140 + 10 * k], [0, 140 + 10 * k]], [], s['swath_width'], s['turn_radius'], s['headland_width'])
            rows.append([np.log1p(max(f[n], 0)) if n in LOG_FEATURES else f[n] for n in names])
    X = np.array(rows)
    mu, sd = X.mean(axis=0), np.where(X.std(axis=0) > 1e-12, X.std(axis=0), 1.0)
    configs = list(plans.COVERAGE_PORTFOLIO)
    rho = np.full((len(X), len(configs)), 2.0)
    rho[:, configs.index(first)] = 1.0
    art = {'kind': 'knn5', 'feature_set': 'agri', 'feature_version': F.VERSION, 'features': list(names),
           'log_features': sorted(LOG_FEATURES & set(names)), 'configs': configs,
           'config_params': {c: plans.COVERAGE_PORTFOLIO[c] for c in configs}, 'scaler': {'mu': mu.tolist(), 'sd': sd.tolist()},
           'static_order': list(range(len(configs))), 'sbs': 0, 'penalty': 2.0, 'profiles': I.PROFILES,
           'scope': {'nn_distance_q99': 50.0, 'nn_distance_max': 50.0, 'feature_min': X.min(axis=0).tolist(), 'feature_max': X.max(axis=0).tolist()},
           'training': {}, 'train_Z': ((X - mu) / sd).tolist(), 'train_rho': rho.tolist(), 'k': 5}
    digest = hashlib.sha256(json.dumps(art, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    doc = {'status': 'evaluated', 'artifact': art, 'artifact_sha256': ('0' * 64 if corrupt else digest), 'evaluation': {}}
    path = tmp_path / 'coverage-order-v1.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


@pytest.fixture
def model_at(monkeypatch):
    def use(path):
        monkeypatch.setattr(serve, 'MODEL', path)
        serve._load.cache_clear()
    return use


def test_missing_model_keeps_rules(model_at, tmp_path):
    model_at(tmp_path / 'absent.json')
    sc, inst = _scenario()
    r = recommend.recommend(sc, inst)
    assert r['policy'] == 'rules-v2'
    assert r['learned_selector']['id'] == serve.SELECTOR_ID and r['learned_selector']['status'] == 'unavailable'




def test_corrupt_fingerprint_is_rejected(model_at, tmp_path):
    model_at(_model(tmp_path, corrupt=True))
    sc, inst = _scenario()
    r = recommend.recommend(sc, inst)
    assert r['learned_selector']['status'] == 'unavailable' and r['policy'] == 'rules-v2'






def test_non_coverage_problems_are_untouched(model_at, tmp_path):
    model_at(_model(tmp_path))
    out = serve.recommend({'problem_class': 'fleet_routing'}, {})
    assert out['status'] == 'not_applicable'





@pytest.mark.parametrize('kind', ['knn5', 'static'])
def test_self_described_model_does_not_change_live_ranking(model_at, tmp_path, kind):
    path = _model(tmp_path)
    doc = json.loads(path.read_text())
    if kind == 'static':
        doc['artifact'].update(kind=kind, static_order=[3, 1, 0, 2], train_rho_mean=[2.0] * len(doc['artifact']['configs']))
        doc['artifact_sha256'] = hashlib.sha256(json.dumps(doc['artifact'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        path.write_text(json.dumps(doc))
    model_at(path)
    sc, inst = _scenario()
    response = recommend.recommend(sc, inst)
    assert response['policy'] == 'rules-v2'
    assert response['learned_selector']['status'] == 'unavailable'
    assert response['learned_selector']['prediction'] is None
    assert '独立审核' in response['learned_selector']['reason']
    assert all('学习型尝试顺序' not in reason for row in response['ranking'] for reason in row['reasons'])
