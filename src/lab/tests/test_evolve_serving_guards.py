"""Synthetic offline model inspection. No model installation or live recommendation wiring."""
import hashlib
import json
import os

import pytest

from urp_lab.evolve import features, serve


def case():
    instance = {'field': [[0, 0], [260, 0], [260, 140], [0, 140]], 'obstacles': [],
                'swath_width': 6.0, 'robot_width': 2.5, 'turn_radius': 4.5, 'headland_width': 12.0}
    return {'problem_class': 'coverage'}, instance


def artifact():
    _, inst = case()
    f = features.coverage_features(inst['field'], [], 6.0, 4.5, 12.0)['area_ha']
    return {'kind': 'static', 'feature_version': features.VERSION,
            'profiles': {'synthetic': {k: inst[k] for k in serve.MACHINE_KEYS}},
            'features': ['area_ha'], 'log_features': [], 'scaler': {'mu': [0.0], 'sd': [1.0]},
            'train_Z': [[f]], 'scope': {'feature_min': [f], 'feature_max': [f], 'nn_distance_q99': 0.01},
            'train_rho_mean': [1.0], 'static_order': [0], 'configs': ['full:none/n_swath'],
            'config_params': {'full:none/n_swath': ['f2c.full', {'decomposition': 'none', 'swath_objective': 'n_swath', 'turn_model': 'dubins'}]}}


def save(path, art=None, provenance=True):
    art = artifact() if art is None else art
    digest = hashlib.sha256(json.dumps(art, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    membership = hashlib.sha256(b'synthetic rectangles only').hexdigest()
    doc = {'status': 'evaluated', 'artifact': art, 'artifact_sha256': digest}
    if provenance:
        doc['provenance'] = {'split': 'development', 'training_membership_sha256': membership}
    payload = json.dumps(doc).encode()
    path.write_bytes(payload)
    return serve.ModelApproval(hashlib.sha256(payload).hexdigest(), membership, 'synthetic-test-review')


def inspect(path, approval, inst=None):
    sc, default = case()
    return serve.recommend(sc, default if inst is None else inst, model_path=path, approval=approval)


def test_no_approval_never_reads_model(tmp_path, monkeypatch):
    path = tmp_path / 'model.json'
    save(path)
    monkeypatch.setattr(serve, '_reviewed_document', lambda *a: pytest.fail('unapproved model read'))
    assert inspect(path, None)['status'] == 'unavailable'


def test_reviewed_static_is_explicitly_not_learned_or_deployed(tmp_path):
    path = tmp_path / 'model.json'
    result = inspect(path, save(path))
    assert result['status'] == 'available', result
    assert result['model_kind'] == result['prediction']['kind'] == 'static'
    assert '非学习模型' in result['reason'] and '未部署' in result['reason']
    assert 'cv_evaluation' not in result


def test_self_hash_does_not_authorize_missing_provenance(tmp_path):
    path = tmp_path / 'model.json'
    assert inspect(path, save(path, provenance=False))['status'] == 'unavailable'


def test_learned_kind_stays_blocked_even_with_matching_review_identity(tmp_path):
    path = tmp_path / 'model.json'
    art = artifact(); art['kind'] = 'knn5'
    result = inspect(path, save(path, art))
    assert result['status'] == 'unavailable' and 'learned activation disabled' in result['reason']


def test_document_tamper_is_rejected_even_if_artifact_self_hash_is_unchanged(tmp_path):
    path = tmp_path / 'model.json'
    approval = save(path)
    doc = json.loads(path.read_text()); doc['status'] = 'unreviewed'
    path.write_text(json.dumps(doc))
    assert inspect(path, approval)['status'] == 'unavailable'


def test_robot_width_is_part_of_machine_gate(tmp_path):
    path = tmp_path / 'model.json'; approval = save(path)
    _, inst = case(); inst['robot_width'] = 99.0
    assert inspect(path, approval, inst)['status'] == 'out_of_scope'


def test_static_order_cannot_ignore_feature_domain(tmp_path):
    path = tmp_path / 'model.json'; approval = save(path)
    _, inst = case(); inst['field'] = [[0, 0], [900, 0], [900, 140], [0, 140]]
    assert inspect(path, approval, inst)['status'] == 'out_of_scope'


def test_same_mtime_different_paths_and_content_do_not_reuse_model(tmp_path):
    serve._load.cache_clear()
    a = tmp_path / 'a.json'; b = tmp_path / 'b.json'
    approval_a = save(a)
    art = artifact(); art['train_rho_mean'] = [1.5]
    approval_b = save(b, art)
    os.utime(a, (1000, 1000)); os.utime(b, (1000, 1000))
    assert inspect(a, approval_a)['prediction']['order'][0]['expected_ratio'] == 1.0
    assert inspect(b, approval_b)['prediction']['order'][0]['expected_ratio'] == 1.5
    art['train_rho_mean'] = [1.75]; approval_b = save(b, art)
    os.utime(b, (1000, 1000))
    assert inspect(b, approval_b)['prediction']['order'][0]['expected_ratio'] == 1.75
    assert inspect(b, approval_a)['status'] == 'unavailable'


def test_noncoverage_is_unchanged_without_approval():
    assert serve.recommend({'problem_class': 'fleet_routing'}, {})['status'] == 'not_applicable'


@pytest.mark.parametrize('change', [
    lambda a: a['scaler'].update(sd=[0]),
    lambda a: a['scope'].update(feature_max=[float('inf')]),
    lambda a: a.update(static_order=[True]),
    lambda a: a.update(train_rho_mean=[float('nan')]),
    lambda a: a['config_params'].update({'full:none/n_swath': ['unreviewed', {}]}),
])
def test_malformed_reviewed_artifacts_fail_closed(tmp_path, change):
    path = tmp_path / 'model.json'; art = artifact(); change(art)
    assert inspect(path, save(path, art))['status'] == 'unavailable'


def test_returned_params_cannot_mutate_cached_artifact(tmp_path):
    path = tmp_path / 'model.json'; approval = save(path)
    first = inspect(path, approval)
    first['prediction']['order'][0]['params']['decomposition'] = 'tampered'
    assert inspect(path, approval)['prediction']['order'][0]['params']['decomposition'] == 'none'


def test_reviewed_identity_requires_matching_membership_digest(tmp_path):
    path = tmp_path / 'model.json'; approval = save(path)
    different = serve.ModelApproval(approval.document_sha256, '0' * 64, approval.review_reference)
    assert inspect(path, different)['status'] == 'unavailable'
