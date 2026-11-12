"""Execution aliases must not rewrite scientific provenance."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from ceres_lab.algorithm_ids import canonical_algorithm_id, canonical_algorithm_values
from ceres_lab import learned
from ceres_lab.solvers import catalog, run_algorithm
from ceres_lab.solve_request import resolve_algorithms, SolveRequestError
from ceres_lab.scenarios import get_scenario
from ceres_lab.studies import StudyManager
from ceres_lab.store import Store
from ceres_lab.figures.names import alg_name, alg_label, alg_index


@pytest.mark.parametrize('old,new,pc', [
    ('pyvrp.hgs', 'pyvrp.ils', 'euclidean_cvrp'),
    ('pyvrp.hgs+urp.layered', 'pyvrp.ils+urp.layered', 'turn_routing'),
])
def test_catalog_and_request_aliases(old, new, pc):
    cat = catalog()
    assert old not in {a['id'] for a in cat}
    assert new in {a['id'] for a in cat}
    for item in (old, {'id': old}, new):
        assert resolve_algorithms(cat, pc, [item])[0][0]['id'] == new
    assert alg_index(old) == alg_index(new)


def test_alias_validation_errors():
    cat = catalog()
    for requested, pc, code in [(['pyvrp.hgs.other'], 'euclidean_cvrp', 'unknown_algorithm'),
                                (['pyvrp.hgs'], 'coverage', 'algorithm_not_applicable'),
                                ([{'id': 'pyvrp.hgs', 'params': []}], 'euclidean_cvrp', 'invalid_params')]:
        with pytest.raises(SolveRequestError) as exc:
            resolve_algorithms(cat, pc, requested)
        assert exc.value.code == code
    unavailable = [dict(a, available=False) for a in cat]
    with pytest.raises(SolveRequestError) as exc:
        resolve_algorithms(unavailable, 'euclidean_cvrp', ['pyvrp.hgs'])
    assert exc.value.status == 409


def test_model_read_only_aliases_and_hash():
    raw = learned.MODEL.read_bytes()
    doc = json.loads(raw)
    artifact = copy.deepcopy(doc['artifact'])
    payload = json.dumps(artifact, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(payload).hexdigest() == doc['artifact_sha256']
    instance = get_scenario('bench-cvrp-uniform-n20')['instance']
    legacy = learned.predict(artifact['model'], instance)
    current = copy.deepcopy(artifact['model'])
    current['sbs'] = canonical_algorithm_id(current['sbs'])
    current['train_means'] = canonical_algorithm_values(current['train_means'])
    for row in current['train']:
        row['targets'] = canonical_algorithm_values(row['targets'])
    assert learned.predict(current, instance) == legacy
    assert legacy['sbs'] == 'pyvrp.ils'
    assert doc['artifact'] == artifact
    assert learned.MODEL.read_bytes() == raw
    advice = learned.recommend(get_scenario('bench-cvrp-uniform-n20'), instance, .25)
    assert advice['status'] == 'available'
    assert advice['prediction']['selected'] in learned.ALGOS
    assert advice['evaluation'] == doc['evaluation']


def test_alias_collisions_fail_closed():
    assert canonical_algorithm_values({'pyvrp.hgs': 1, 'pyvrp.ils': 1}) == {'pyvrp.ils': 1}
    for values in ({'pyvrp.hgs': 1, 'pyvrp.ils': 2}, {'pyvrp.ils': 2, 'pyvrp.hgs': 1}):
        with pytest.raises(ValueError, match='Conflicting'):
            canonical_algorithm_values(values)


def test_study_alias_and_history(tmp_path):
    store = Store(tmp_path / 'history.db')
    try:
        spec = {'scenarios': ['bench-cvrp-uniform-n20'], 'algorithms': ['pyvrp.hgs']}
        cells, _ = StudyManager(store, None).plan(spec, get_scenario, catalog())
        assert cells[0][1]['id'] == 'pyvrp.ils'
        assert spec['algorithms'] == ['pyvrp.hgs']
        run = {'run_id': 'old', 'algorithm': {'id': 'pyvrp.hgs', 'name': 'PyVRP HGS', 'version': '0.9.1'}}
        study = {'study_id': 'old-study', 'created_utc': '', 'spec': spec, 'design': []}
        store.put_run(run); store.put_study(study)
        assert store.get_run('old') == run
        assert store.get_study('old-study') == study
    finally:
        store.close()


@pytest.mark.parametrize('version', ['0.9.1', None, '0.14.0'])
def test_history_display_version_guard(version):
    run = {'algorithm': {'id': 'pyvrp.hgs', 'name': 'PyVRP HGS', 'version': version}}
    original = copy.deepcopy(run)
    for lang in ('en', 'zh'):
        name = alg_name(run, SimpleNamespace(lang=lang))
        assert ('ILS' if version == '0.14.0' else 'HGS') in name
    assert run == original
    assert 'ILS' not in alg_label('pyvrp.hgs', SimpleNamespace(lang='en'))


def test_legacy_direct_dispatch_records_current_id():
    scenario = get_scenario('bench-cvrp-uniform-n20')
    result = run_algorithm(scenario, scenario['instance'], 'pyvrp.hgs', {'time_limit_s': .05, 'seed': 42})
    assert result['algorithm']['id'] == 'pyvrp.ils'
    assert result['check']['valid']


def test_prediction_rejects_conflicting_means():
    model = copy.deepcopy(json.loads(learned.MODEL.read_text())['artifact']['model'])
    model['train_means']['pyvrp.ils'] = model['train_means']['pyvrp.hgs'] + 1
    with pytest.raises(ValueError, match='Conflicting'):
        learned.predict(model, get_scenario('bench-cvrp-uniform-n20')['instance'])


def test_api_and_agent_submit_canonical_ids(tmp_path):
    from fastapi.testclient import TestClient
    from ceres_lab.gateway import create_app, Config
    from ceres_lab.agent import research_tools as tools
    app = create_app(Config(runtime=tmp_path, background=False, extra_hosts={'testserver'}))
    with TestClient(app) as client:
        response = client.post('/lab/api/solve', json={
            'scenario_id': 'bench-cvrp-uniform-n20', 'algorithms': ['pyvrp.hgs']})
        assert response.status_code == 202, response.text
        body = response.json()
        runs = client.get('/lab/api/jobs/' + body['job_id']).json()['runs']
        assert runs[0]['algorithm_id'] == 'pyvrp.ils'
    submitted = []
    def submit(sc, instance, resolved, label):
        submitted.extend(resolved)
        return 'job', ['run']
    ctx = SimpleNamespace(session_id='test', lab=SimpleNamespace(catalog=catalog,
                          jobs=SimpleNamespace(submit=submit)))
    response = tools.solve(ctx, {'scenario_id': 'bench-cvrp-uniform-n20', 'algorithms': ['pyvrp.hgs']})
    assert submitted[0][0]['id'] == response['algorithms'][0]['id'] == 'pyvrp.ils'


@pytest.mark.requires_maps('maze')
def test_legacy_layered_dispatch_records_current_id():
    scenario = get_scenario('maze-inspection')
    result = run_algorithm(scenario, scenario['instance'], 'pyvrp.hgs+urp.layered',
                           {'time_limit_s': .15, 'seed': 42})
    assert result['algorithm']['id'] == 'pyvrp.ils+urp.layered'
    assert result['check']['valid']


def test_fit_build_legacy_schema_equivalence_without_mutation():
    instance = get_scenario('bench-cvrp-uniform-n20')['instance']
    records = [dict(id=str(n), instance=instance, features=learned.features(instance),
                    split='train' if n < 5 else 'calibration',
                    costs={'pyvrp.hgs': 100 + n, 'ortools.gls': 110 - n}) for n in range(10)]
    original = copy.deepcopy(records)
    current = [dict(row, costs=canonical_algorithm_values(row['costs'])) for row in records]
    assert learned.fit(records[:5]) == learned.fit(current[:5])
    assert learned.build(records) == learned.build(current)
    assert records == original


def test_recommend_verifies_original_hash_before_aliasing(tmp_path, monkeypatch):
    doc = json.loads(learned.MODEL.read_text())
    doc['artifact']['model']['sbs'] = 'pyvrp.ils'  # equivalent alias, but tampered signed bytes
    model = tmp_path / 'tampered-model.json'
    model.write_text(json.dumps(doc))
    monkeypatch.setattr(learned, 'MODEL', model)
    scenario = get_scenario('bench-cvrp-uniform-n20')
    result = learned.recommend(scenario, scenario['instance'], .25)
    assert result['status'] == 'unavailable'
    assert '指纹' in result['reason']
