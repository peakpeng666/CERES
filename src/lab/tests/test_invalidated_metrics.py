"""Source-invalidated candidates are audit evidence, never published metrics."""
import copy
import csv
import io
import json
import sys
from types import SimpleNamespace

import pytest

from ceres_lab import jobs
from ceres_lab.analysis import _value
from ceres_lab.assets.bindings import digest
from ceres_lab.experiment import record as experiment_record
from ceres_lab.export import export_compare, export_run
from ceres_lab.store import Store


RESULT_METRICS = {
    'cost': 7, 'length': 12, 'n_routes': 2, 'n_tasks': 4,
    'turns': 3, 'turn_penalty_total': 2, 'forbidden_turn_violations': 0,
    'coverage_fraction': .93, 'working_area_fraction': .97,
    'total_distance_m': 15, 'makespan_s': 20, 'vehicles_used': 2,
    'trips': 3, 'balance_cv': .12, 'gap_to_bks_pct': 1.5,
    'max_curvature': .3, 'reverse_states': 4, 'turn_radius_enforced': True,
    # An allowlist must also reject future adapter metrics without a code update.
    'future_metric': {'quality': 100},
}


def candidate():
    return {
        'status': 'feasible', 'routes': [{'vehicle': 0, 'polyline': [[0, 0], [1, 1]]}],
        'objective': {'value': 7, 'unit': 'm', 'lower_bound': 6},
        'check': {'valid': True}, 'map_check': {'checked': True, 'collision_free': True},
        'metrics': {**RESULT_METRICS, 'wall_s': .25, 'cpu_s': .2, 'length_unit': 'm'},
        'raw_result': {'candidate': 'retain for audit', 'cost': 7},
        'trace': [{'cost': 7}],
    }


def record(**overrides):
    return {
        'run_id': 'run_test', 'job_id': 'job_test', 'scenario_id': 'scenario_test',
        'instance_sha256': 'test-sha', 'problem_class': 'coverage',
        'algorithm': {'id': 'test', 'name': 'test'}, 'frame': {'units': 'm'},
        'state': 'done', 'error': None, 'log': 'worker diagnostic',
        'process': {'returncode': 0, 'source_identity_verified': True},
        **candidate(), **overrides,
    }


def test_inflight_invalidation_suppresses_all_candidate_metrics_and_retains_evidence(tmp_path, monkeypatch):
    extra = tmp_path / 'executed-dependency.py'
    extra.write_text('VERSION = 1\n')
    original_files = jobs._execution_files
    monkeypatch.setattr(jobs, '_execution_files', lambda: original_files() + [extra])
    monkeypatch.setattr(jobs, '_GATEWAY_IMPORT_MANIFEST', jobs.execution_manifest())
    output = json.dumps(candidate())
    code = ('import sys;from pathlib import Path;'
            f'Path({str(extra)!r}).write_text("VERSION = 2\\n");'
            f'print({output!r});print("worker diagnostic",file=sys.stderr)')
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, command=[sys.executable, '-c', code], concurrency=1)
    sol = record(state='queued', metrics={}, reproducibility={
        'execution_sha256': jobs._GATEWAY_IMPORT_MANIFEST['sha256']})
    request = {'run_id': sol['run_id']}
    try:
        result = manager.execute(sol, request, budget=10)
        assert result['state'] == 'error' and '执行期间来源身份核对失败' in result['error']
        assert result['process']['source_identity_verified'] is False
        assert result['status'] is None and result['routes'] == []
        assert result['check'] is None and result['map_check'] is None
        assert result['objective'] == {'value': None, 'unit': 'm', 'lower_bound': None}
        assert {k: result['metrics'][k] for k in RESULT_METRICS} == dict.fromkeys(RESULT_METRICS)
        assert result['metrics']['process_wall_s'] >= 0
        assert result['metrics']['wall_s'] == .25 and result['metrics']['cpu_s'] == .2
        assert result['metrics']['length_unit'] == 'm'
        assert result['raw_result'] == candidate()['raw_result']
        assert result['trace'] == candidate()['trace']
        assert result['log'].strip() == 'worker diagnostic'
        assert result['process']['returncode'] == 0 and result['process']['budget_s'] == 10
        evidence = tmp_path / 'runs' / sol['run_id']
        assert (evidence / 'stdout.json').read_text() == output + '\n'
        assert (evidence / 'stderr.log').read_text() == 'worker diagnostic\n'
        assert json.loads((evidence / 'request.json').read_text()) == request
        assert db.get_run(sol['run_id']) == result
        assert _value(result) is None
    finally:
        manager.shutdown()
        db.close()


def test_legacy_invalidated_record_exports_are_sanitized_without_rewriting_history():
    legacy = record(state='error', process={'returncode': 0, 'source_identity_verified': False})
    before = copy.deepcopy(legacy)
    data, _, _ = export_compare([legacy], 'csv')
    row = next(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    for key in ('cost', 'valid', 'length', 'total_distance_m', 'makespan_s',
                'vehicles_used', 'trips', 'balance_cv', 'gap_to_bks_pct', 'status'):
        assert row[key] == '', key
    assert row['state'] == 'error' and row['wall_s'] == '0.25'
    for exporter, argument in ((export_run, legacy), (export_compare, [legacy])):
        data, _, _ = exporter(argument, 'json')
        exported = json.loads(data)
        exported = exported[0] if isinstance(exported, list) else exported
        assert exported['metrics']['coverage_fraction'] is None
        assert exported['metrics']['future_metric'] is None
        assert exported['objective']['lower_bound'] is None
        assert exported['raw_result'] == before['raw_result']
        assert exported['process'] == before['process']
    data, _, _ = export_run(legacy, 'csv')
    assert list(csv.DictReader(io.StringIO(data.decode('utf-8-sig')))) == []
    data, _, _ = export_run(legacy, 'geojson')
    assert json.loads(data)['features'] == []
    assert legacy == before
    assert _value(legacy) is None


@pytest.mark.parametrize('verified', [True, None])
def test_ordinary_solver_failure_keeps_candidate_diagnostics(verified):
    process = {'returncode': 1}
    if verified is not None:
        process['source_identity_verified'] = verified
    failed = record(state='error', error='solver failure', process=process)
    before = copy.deepcopy(failed)
    data, _, _ = export_run(failed, 'json')
    assert json.loads(data) == before
    assert failed == before
    assert _value(failed) is None


def test_verified_worker_failure_does_not_trigger_metric_invalidation(tmp_path):
    output = json.dumps({**candidate(), 'error': 'solver failed'})
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, command=[sys.executable, '-c', f'print({output!r})'], concurrency=1)
    try:
        result = manager.execute(record(state='queued', metrics={}), {}, budget=10)
        assert result['state'] == 'error' and result['error'] == 'solver failed'
        assert result['process']['source_identity_verified'] is True
        assert {key: result['metrics'][key] for key in RESULT_METRICS} == RESULT_METRICS
        assert result['raw_result'] == candidate()['raw_result']
        assert _value(result) is None
    finally:
        manager.shutdown()
        db.close()


def test_successful_comparison_values_remain_available():
    successful = record()
    before = copy.deepcopy(successful)
    data, _, _ = export_compare([successful], 'csv')
    row = next(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    assert row['cost'] == '7' and row['length'] == '12'
    assert row['makespan_s'] == '20' and row['valid'] == 'True'
    assert successful == before


@pytest.mark.parametrize('invalidated', [True, False])
def test_experiment_record_separates_invalidated_evidence_and_hashes_both(invalidated):
    run = record(state='error', error='source changed' if invalidated else 'solver failed',
                 process={'returncode': 0, 'source_identity_verified': not invalidated},
                 asset_snapshot=[])
    before = copy.deepcopy(run)
    lab = SimpleNamespace(fieldsim=SimpleNamespace(results=lambda run_id: []),
                          store=SimpleNamespace(list_sim_runs=lambda **kwargs: []))
    envelope = experiment_record(lab, run)
    payload = envelope['record']
    assert payload['run_id'] == run['run_id']
    assert envelope['integrity']['sha256'] == digest(payload)
    assert run == before
    if invalidated:
        assert payload['planning']['metrics']['coverage_fraction'] is None
        assert payload['planning']['metrics']['future_metric'] is None
        assert payload['planning']['objective']['lower_bound'] is None
        assert payload['planning']['raw_result'] == before['raw_result']
        assert payload['invalidated_planning_evidence'] == before
        assert any('仅供审计' in warning for warning in payload['warnings'])
    else:
        assert payload['planning'] == before
        assert 'invalidated_planning_evidence' not in payload
        assert payload['warnings'] == []
