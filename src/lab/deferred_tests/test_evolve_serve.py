"""The recommender uses a coverage try-order model only behind fingerprint, profile and scope gates."""
import copy
import hashlib
import json

import numpy as np
import pytest

from urp_lab import recommend
from urp_lab.evolve import features as F
from urp_lab.evolve import instances as I
from urp_lab.evolve import plans, serve
from urp_lab.evolve.policy import LOG_FEATURES

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


def test_available_model_sets_order_and_params(model_at, tmp_path):
    model_at(_model(tmp_path))
    sc, inst = _scenario()
    r = recommend.recommend(sc, inst)
    sel = r['learned_selector']
    assert sel['status'] == 'available', sel['reason']
    assert sel['prediction']['order'][0]['config'] == 'full:none/swath_length'
    assert r['policy'] == 'coverage-order-v1'
    full = next(x for x in r['ranking'] if x['algorithm_id'] == 'f2c.full')
    assert full['suggested_params']['decomposition'] == 'none'
    assert full['suggested_params']['swath_objective'] == 'swath_length'
    assert '封存测试未运行' in full['reasons'][0]


def test_corrupt_fingerprint_is_rejected(model_at, tmp_path):
    model_at(_model(tmp_path, corrupt=True))
    sc, inst = _scenario()
    r = recommend.recommend(sc, inst)
    assert r['learned_selector']['status'] == 'unavailable' and r['policy'] == 'rules-v2'


def test_unknown_machine_is_out_of_scope(model_at, tmp_path):
    model_at(_model(tmp_path))
    sc, inst = _scenario()
    inst['turn_radius'] = 3.3
    r = recommend.recommend(sc, inst)
    assert r['learned_selector']['status'] == 'out_of_scope' and r['policy'] == 'rules-v2'


def test_far_field_is_out_of_scope(model_at, tmp_path):
    path = _model(tmp_path)
    doc = json.loads(path.read_text())
    doc['artifact']['scope']['nn_distance_q99'] = 1e-6
    doc['artifact_sha256'] = hashlib.sha256(json.dumps(doc['artifact'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path.write_text(json.dumps(doc))
    model_at(path)
    sc, inst = _scenario(field=[[0, 0], [900, 0], [900, 60], [0, 60]])
    r = recommend.recommend(sc, inst)
    assert r['learned_selector']['status'] == 'out_of_scope'


def test_non_coverage_problems_are_untouched(model_at, tmp_path):
    model_at(_model(tmp_path))
    out = serve.recommend({'problem_class': 'fleet_routing'}, {})
    assert out['status'] == 'not_applicable'


def test_static_kind_serves_experience_order(model_at, tmp_path):
    path = _model(tmp_path)
    doc = json.loads(path.read_text())
    art = doc['artifact']
    art.update(kind='static', static_order=[3, 1, 0, 2], train_rho_mean=[2.0] * len(art['configs']))
    for key in ('train_rho', 'k'):
        art.pop(key, None)
    doc['artifact_sha256'] = hashlib.sha256(json.dumps(art, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path.write_text(json.dumps(doc))
    model_at(path)
    sc, inst = _scenario()
    r = recommend.recommend(sc, inst)
    sel = r['learned_selector']
    assert sel['status'] == 'available', sel['reason']
    assert sel['prediction']['kind'] == 'static'
    assert [s['config'] for s in sel['prediction']['order']] == [art['configs'][j] for j in art['static_order']]
    assert '经验' in sel['reason'] and '封存测试未运行' in sel['reason']
    assert r['policy'] == 'coverage-order-v1'


def test_static_kind_ignores_feature_scope_but_keeps_machine_gate(model_at, tmp_path):
    """A static order does not read field features: far fields keep it; unknown machines still fall back."""
    path = _model(tmp_path)
    doc = json.loads(path.read_text())
    art = doc['artifact']
    art.update(kind='static', static_order=[3, 1, 0, 2], train_rho_mean=[2.0] * len(art['configs']))
    art['scope']['nn_distance_q99'] = 1e-6                       # every field is "far" in feature space
    for key in ('train_rho', 'k'):
        art.pop(key, None)
    doc['artifact_sha256'] = hashlib.sha256(json.dumps(art, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path.write_text(json.dumps(doc))
    model_at(path)
    sc, inst = _scenario(field=[[0, 0], [900, 0], [900, 60], [0, 60]])
    sel = recommend.recommend(sc, inst)['learned_selector']
    assert sel['status'] == 'available' and '仅机具配置门控' in sel['gate']
    sc2, inst2 = _scenario()
    inst2['turn_radius'] = 3.3
    assert recommend.recommend(sc2, inst2)['learned_selector']['status'] == 'out_of_scope'
