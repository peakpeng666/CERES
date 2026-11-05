"""Create a NEW independently checked record from immutable historical route data.

No solver invocation, history mutation, old execution identity promotion, or ROS
launch. CLI writes a fresh isolated Store only after current code/map checks pass.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sys
from datetime import datetime, timezone
import uuid

from historical_input import SOURCE, HISTORICAL_SOURCE, ARCHIVE_SHA256, canonical_sha, from_directory, from_archive

EXPECTED = {
 'instance': 'f45c5fe1e869864842e5eec73e3cd799c8314dbeb6391175d12bdaf8a47dd61e',
 'scenario_snapshot': 'bd07404febad7f83f6c8479f59843926a2a8aa278b09a95374025b2bef676a35',
 'routes': '009eca56bd0aceb86ff941b0ab285cc1989004a2dc11bf637e59413be4e68a19',
 'planned': 'bf25146828fb938ebd9c63db2eb0e4b0002ff3b0cc4f47d4fc808a4b037cbd86',
}
SPAWN = {'x': -5.08, 'y': -0.75, 'yaw': 1.570796}


def historical_pair(inputs):
    native = inputs['evidence/native-tb4-warehouse/export.json']
    container = inputs['evidence/container-tb4-warehouse-headless/export.json']
    native_sim = inputs['evidence/native-tb4-warehouse/sim-follow_path.json']
    container_sim = inputs['evidence/container-tb4-warehouse-headless/sim-follow_path.json']
    for field in ('instance', 'scenario_snapshot', 'routes'):
        if native[field] != container[field] or canonical_sha(native[field]) != EXPECTED[field]: raise ValueError('historical_route_mismatch')
    if native_sim['planned'] != container_sim['planned'] or canonical_sha(native_sim['planned']) != EXPECTED['planned']: raise ValueError('historical_plan_mismatch')
    if native['scenario_snapshot']['sim']['spawn'] != SPAWN: raise ValueError('historical_spawn_mismatch')
    for record in (native, container):
        if (record.get('process') or {}).get('source_identity_verified') is False: raise ValueError('historical_identity_invalidated')
    if len(native['instance']['customers']) != 5: raise ValueError('historical_task_count_mismatch')
    return native, native_sim


def build_record(inputs, route_check, map_check, plan_fn, identity_fn, map_fingerprint, adapter_sha):
    old, old_sim = historical_pair(inputs)
    before = identity_fn()
    instance = copy.deepcopy(old['instance']); scenario = copy.deepcopy(old['scenario_snapshot'])
    routes = copy.deepcopy(old['routes'])
    # The independent checker expects raw witness services named customer. Retain
    # the raw historical status only as checker input; do not infer new optimality.
    witness = copy.deepcopy(old['raw_result'])
    if witness.get('status') != 'optimal': raise ValueError('unsupported_historical_witness')
    for raw, route in zip(witness['routes'], routes, strict=True):
        if (raw['nodes'] != route['nodes'] or raw['cost'] != route['cost'] or
            [{'task': s['customer'], 'position': s['position']} for s in raw['services']] != route['services'] or
            [s['customer'] for s in raw['services']] != route['sequence'] or
            [instance['coordinates'][str(n)] for n in raw['nodes']] != route['polyline']):
            raise ValueError('historical_witness_geometry_mismatch')
    checked = route_check(instance, witness)
    if checked.get('valid') is not True: raise ValueError('current_route_check_failed')
    mapped = map_check('warehouse', routes)
    if mapped.get('checked') is not True or mapped.get('collision_free') is not True: raise ValueError('current_map_check_failed')
    now = datetime.now(timezone.utc).isoformat()
    record = {
        'run_id': 'replay_' + uuid.uuid4().hex, 'job_id': None,
        'scenario_id': scenario['id'], 'problem_class': 'turn_routing',
        'instance': instance, 'scenario_snapshot': scenario, 'frame': copy.deepcopy(old['frame']),
        'cost_unit': old['cost_unit'], 'instance_sha256': EXPECTED['instance'],
        'algorithm': {'id': 'acceptance.historical_route_recheck', 'name': 'Historical fixed route independent recheck',
                      'source': 'project', 'origin': 'historical_fixed_route_recheck', 'version': adapter_sha},
        'params': {}, 'state': 'done', 'status': 'feasible',
        'objective': {'value': checked['computed_cost'], 'unit': old['cost_unit'], 'lower_bound': None},
        'routes': routes, 'check': checked, 'map_check': mapped,
        'metrics': {'cost': checked['computed_cost'], 'length_unit': 'm', 'n_routes': len(routes), 'n_tasks': 5,
                    'wall_s': None, 'cpu_s': None},
        'created_utc': now, 'finished_utc': now, 'error': None, 'log': None,
        'process': {'kind': 'independent_historical_witness_recheck', 'solver_executed': False},
        'reproducibility': {'execution_sha256': before['sha256'], 'execution_manifest': before,
                            'adapter_sha256': adapter_sha, 'worker_environment_verified': None},
        'replay': {'route_origin': 'historical_fixed_witness', 'historical_source_sha': HISTORICAL_SOURCE,
                   'execution_source_sha': SOURCE, 'archive_sha256': ARCHIVE_SHA256,
                   'historical_routes_sha256': EXPECTED['routes'], 'historical_planned_sha256': EXPECTED['planned'],
                   'historical_execution_identity_status': 'not_recorded',
                   'current_route_check': 'passed', 'current_map_check': 'passed',
                   'current_map_fingerprint_sha256': map_fingerprint(),
                   'physical_budget_origin': 'source_reconstructed_not_observed',
                   'api_spawn': SPAWN, 'expected_session_spawn': {k: float(f'{v:g}') for k, v in SPAWN.items()},
                   'historical_map_bytes_identity': 'not_recorded',
                   'historical_counts_semantics': 'tasks_reached_single_route_not_trials'},
    }
    plan = plan_fn(record, scenario, 'follow_path')
    expected_tasks = [{k: t[k] for k in ('task', 'label', 'xy')} for t in old_sim['tasks']]
    if plan['path'] != old_sim['planned']['path'] or plan['tasks'] != expected_tasks or len(plan['tasks']) != 5:
        raise ValueError('current_plan_differs_from_historical')
    if not math.isclose(plan['planned_length_m'], old_sim['planned']['length_m'], rel_tol=0, abs_tol=1e-9): raise ValueError('current_plan_length_mismatch')
    # Keep the actual current to_sim_plan budget, never smuggle an override.
    if not math.isclose(plan['budget_s'], 649.6, rel_tol=0, abs_tol=1e-7): raise ValueError('unexpected_reconstructed_budget')
    after = identity_fn()
    if before['sha256'] != after['sha256']: raise ValueError('current_execution_identity_changed')
    record['process']['source_identity_verified'] = True
    record['metrics']['length'] = plan['planned_length_m']
    record['replay']['physical_budget_s'] = plan['budget_s']
    record['replay']['current_plan_sha256'] = canonical_sha(plan)
    return record, plan


def public_receipt(record):
    """Fixed keys/enums and hashes only; validation is NOT a physical PASS."""
    replay = record['replay']
    hashes = [record['reproducibility']['execution_sha256'], record['reproducibility']['adapter_sha256'],
              replay['current_map_fingerprint_sha256'], replay['current_plan_sha256']]
    if not all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in hashes):
        raise ValueError('receipt_hash_invalid')
    if not (type(replay['physical_budget_s']) in (int, float) and math.isfinite(replay['physical_budget_s'])
            and 0 < replay['physical_budget_s'] <= 1800): raise ValueError('receipt_budget_invalid')
    return {'schema_version': 1, 'state': 'VALIDATED_NOT_EXECUTED', 'stage': 'warehouse',
            'route_origin': 'historical_fixed_witness', 'solver_executed': False,
            'execution_source_sha': SOURCE, 'historical_source_sha': HISTORICAL_SOURCE,
            'historical_routes_sha256': EXPECTED['routes'], 'historical_planned_sha256': EXPECTED['planned'],
            'current_execution_sha256': record['reproducibility']['execution_sha256'],
            'adapter_sha256': record['reproducibility']['adapter_sha256'],
            'current_map_fingerprint_sha256': replay['current_map_fingerprint_sha256'],
            'current_plan_sha256': replay['current_plan_sha256'],
            'tasks_total': 5, 'tasks_reached': None, 'route_check_valid': True,
            'map_collision_free': True, 'physical_budget_s': replay['physical_budget_s'],
            'physical_budget_origin': 'source_reconstructed_not_observed',
            'historical_map_bytes_identity': 'not_recorded',
            'historical_execution_identity_status': 'not_recorded',
            'historical_counts_semantics': 'tasks_reached_single_route_not_trials',
            'api_spawn_yaw': SPAWN['yaw'], 'expected_session_spawn_yaw': float(f"{SPAWN['yaw']:g}"),
            'overall_wall_budget_s': None, 'resources_match_historical': None,
            'simulation_state': 'NOT_RUN', 'cleanup_pass': None}


def main():
    p = argparse.ArgumentParser(); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--harness', type=Path, required=True, help='Frozen harness containing runner/verify_worktree.py')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--historical-directory', type=Path); group.add_argument('--archive', type=Path)
    p.add_argument('--runtime', type=Path, required=True); p.add_argument('--private-output', type=Path, required=True)
    a = p.parse_args()
    # Must be a new empty runtime, not a writable historical archive or live DB.
    a.runtime.mkdir(mode=0o700, parents=True, exist_ok=False)
    a.private_output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = a.source.resolve()
    sys.path.insert(0, str(a.harness.resolve()))
    from runner.verify_worktree import verify
    verify(source, SOURCE)
    os.environ['URP_LAB_RUNTIME'] = str(a.runtime.resolve())
    sys.path.insert(0, str(source / 'src/lab'))
    sys.path.insert(0, str(source / 'src/research/src'))
    from urp_lab.jobs import execution_manifest
    from urp_lab.solvers import map_check
    from urp_lab.scenarios import maps
    from urp_lab.simplan import to_sim_plan
    from urp_lab.store import Store
    from urp_research.turn_modes.checker import check
    if Path(sys.prefix).resolve() != (source / 'src/lab/.venv').resolve(): raise ValueError('wrong_checker_interpreter')
    inputs = from_directory(a.historical_directory) if a.historical_directory else from_archive(a.archive)
    adapter_sha = canonical_sha({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))})
    record, plan = build_record(inputs, check, map_check, to_sim_plan, execution_manifest, lambda: maps.fingerprint('warehouse'), adapter_sha)
    verify(source, SOURCE)
    db = Store(a.runtime / 'lab.sqlite3')
    try:
        if db.list_runs() or db.active_sim_runs(): raise ValueError('runtime_not_empty')
        db.put_run(record)
        if db.get_run(record['run_id']) != record: raise ValueError('import_readback_mismatch')
    finally: db.close()
    (a.private_output / 'new-record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    (a.private_output / 'current-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    (a.private_output / 'receipt.json').write_text(json.dumps(public_receipt(record), indent=2))
    return 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        # No raw errors or paths to public logs; wrapper owns private diagnostics.
        raise SystemExit(1)
