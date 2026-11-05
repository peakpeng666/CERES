"""Finite acceptance for session lifecycle diagnostics, never warehouse routes.

This reducer is intentionally independent of warehouse route acceptance. It uses
that reducer ONLY as a sanitizer, with state forced to NOT_RUN. All predicates
below concern unchanged provenance, observed readiness, authenticated cleanup,
and the supervisor. No task/trajectory/protocol/route-budget gate is evaluated.
Input is trusted harness evidence, not a substitute for authenticating a report.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

# Direct script execution must not depend on a caller-provided PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cleanup_diagnostics
import warehouse_public_summary as warehouse
from historical_input import SOURCE
from historical_fuel import (ARCHIVE_SHA256, SELECTED_SHA256, COHORT,
                             ALIAS_PROJECTION)
from process_observer import is_cancellation

STAGE = 'warehouse-lifecycle-only'
EXECUTION_SCOPE = 'lifecycle_only'
SUMMARY_KIND = 'warehouse_lifecycle_only_summary_v1'
STATES = ('PASS', 'FAIL', 'ERROR', 'NOT_RUN')
IDENTITY_BOOLS = ('source_identity_verified', 'map_identity_verified',
                  'runtime_compatibility_verified', 'assets_complete',
                  'resources_match_historical')
RESOURCE_COUNTS = ('historical_resource_count', 'current_resource_count',
                   'missing_resource_count', 'additional_resource_count',
                   'changed_resource_count')
TASK_COUNTS = ('tasks_total', 'tasks_reached', 'trajectory_samples')
TASK_METRICS = warehouse.METRICS
OBSERVATIONS = ('startup_diagnostics', 'warehouse_diagnostics',
                'warehouse_startup_observation', 'warehouse_process_observation',
                'warehouse_runtime_identity', 'warehouse_cleanup_diagnostics')
PROVENANCE = ('source_sha', 'harness_sha', 'historical_source_sha',
              'raw_resource_manifest_sha256', 'raw_resources_match_historical',
              'historical_alias_projection', 'resource_cache_cohort',
              'historical_fuel_archive_sha256', 'historical_selected_manifest_sha256',
              'historical_materialized_file_count', 'historical_cache_verified')
RUNNER_FIELDS = ('runner_failure_hint', 'runner_termination_reason', 'runner_stage',
                 'runner_exit_code', 'runner_cleanup_remaining',
                 'runner_forced_kill_count')
# Sanitization of fixed empty data is pure. Keep an independent finite fallback
# so failures/cancellation cannot trigger a second failing sanitizer invocation.
_EMPTY_CLEAN = warehouse.project({'state': 'NOT_RUN'}, require_runner=False)

RAW_RESOURCE_PAIRS = (
    ('2c3acde82c25c0372c8793225abcbc94d4e74be4b441ad130de7c3a475790d66', False),
    ('64f4c3b93af78800aefc036c58035a8556401c82627f55409a16483d12dc7055', True),
)


def _bool(value):
    return value if type(value) is bool else None


def _exact(value, expected):
    return type(value) is str and value == expected


def _runner_from_projection(raw):
    """Recover only previously projected, finite supervisor observations."""
    if (not _exact(raw.get('summary_kind'), SUMMARY_KIND) or
            raw.get('runner_status_present') is not True):
        return None
    return {target: raw.get(source) for source, target in (
        ('runner_failure_hint', 'failure_hint'),
        ('runner_termination_reason', 'termination_reason'),
        ('runner_stage', 'stage'), ('runner_requested_stage', 'requested_stage'),
        ('runner_exit_code', 'exit_code'),
        ('runner_cleanup_remaining', 'cleanup_remaining'),
        ('runner_forced_kill_count', 'forced_kill_count'))}


def project(raw, runner=None, *, require_runner=True,
            runtime_identity_cancellation=None):
    """Pure, bounded, fail-closed projection; never reads a report or process.

    require_runner=False is only the harness's intermediate, pre-supervisor
    reduction. Final CLI reductions always require successful runner evidence.
    Reprojection with the same requirement preserves all finite observations.
    """
    raw = raw if type(raw) is dict else {}
    incoming_state = raw.get('state')
    state = incoming_state if type(incoming_state) is str and incoming_state in STATES else 'ERROR'
    if runner is None:
        runner = _runner_from_projection(raw)
    runner_present = runner is not None
    runner = runner if type(runner) is dict else ({} if runner_present else None)
    sanitation_failed = False
    cancellation_seen = False

    def defer(error):
        nonlocal cancellation_seen
        cancellation_seen = True
        if runtime_identity_cancellation is None:
            raise error
        runtime_identity_cancellation(error)

    # Never ask the ordinary route reducer to evaluate PASS. Select explicitly
    # from its allowlisted output instead of copying its route conclusions.
    safe_input = dict(raw)
    safe_input['state'] = 'NOT_RUN'
    try:
        clean = warehouse.project(safe_input, runner, require_runner=False,
                                  runtime_identity_cancellation=defer)
    except BaseException as error:
        if is_cancellation(error):
            defer(error)
        sanitation_failed = True
        clean = deepcopy(_EMPTY_CLEAN)

    result = {key: clean[key] for key in (
        *OBSERVATIONS, *PROVENANCE, *warehouse.HASHES, *IDENTITY_BOOLS,
        *RESOURCE_COUNTS, *RUNNER_FIELDS, 'cleanup_pass', 'gateway_exit_code',
        'remaining_owned_process_count', 'overall_wall_budget_s')}
    result.update(
        schema_version=1, summary_kind=SUMMARY_KIND,
        stage=STAGE if _exact(raw.get('stage'), STAGE) else 'unknown',
        execution_scope=EXECUTION_SCOPE if _exact(raw.get('execution_scope'), EXECUTION_SCOPE) else 'unknown',
        state=state, lifecycle_state=state, warehouse_state='NOT_RUN',
        route_acceptance='NOT_RUN',
        state_semantics='lifecycle_diagnostic_only_not_route_acceptance',
        route_origin='not_executed', solver_executed=False,
        task_not_submitted=_bool(raw.get('task_not_submitted')),
        session_ready_observed=_bool(raw.get('session_ready_observed')),
        task_metrics_semantics='not_run_not_zero_of_five',
        runner_status_present=runner_present,
        runner_requested_stage=STAGE if runner is not None and _exact(runner.get('requested_stage'), STAGE) else 'unknown',
    )
    # Imported historical witness metrics are not measurements of this run.
    result.update({key: None for key in (*TASK_COUNTS, *TASK_METRICS)})
    result['measurement_availability'] = {
        key: 'measured' if result[key] is not None else 'not_measured'
        for key in (*TASK_COUNTS, *TASK_METRICS, *RESOURCE_COUNTS,
                    'remaining_owned_process_count')}
    diagnostic = result['warehouse_diagnostics']
    lifecycle = diagnostic['lifecycle']
    cleanup = result['warehouse_cleanup_diagnostics']
    gates = {
        'scope_verified': (result['stage'] == STAGE and
                           result['execution_scope'] == EXECUTION_SCOPE and
                           result['task_not_submitted'] is True),
        'identity_verified': (result['source_sha'] == SOURCE and
                              result['harness_sha'] is not None and
                              all(result[key] is not None for key in warehouse.HASHES) and
                              all(result[key] is True for key in IDENTITY_BOOLS)),
        'historical_cache_verified': (
            result['resource_cache_cohort'] == COHORT and
            result['historical_cache_verified'] is True and
            result['historical_fuel_archive_sha256'] == ARCHIVE_SHA256 and
            result['historical_selected_manifest_sha256'] == SELECTED_SHA256 and
            result['historical_materialized_file_count'] == 61 and
            result['historical_alias_projection'] == ALIAS_PROJECTION),
        'resources_verified': (
            (result['raw_resource_manifest_sha256'], result['raw_resources_match_historical']) in RAW_RESOURCE_PAIRS and
            result['historical_resource_count'] == result['current_resource_count'] == 45 and
            all(result[key] == 0 for key in ('missing_resource_count',
                'additional_resource_count', 'changed_resource_count'))),
        'ready_verified': (result['session_ready_observed'] is True and
                           lifecycle['session_id_observed'] is True and
                           lifecycle['last_session_phase'] == 'ready'),
        'cleanup_verified': (
            result['cleanup_pass'] is True and
            diagnostic['session_report'] == cleanup['availability'] == 'available' and
            cleanup['session_status'] == 'PASS' and
            all(cleanup['conjuncts'][key] is True for key in cleanup_diagnostics.CONJUNCTS) and
            lifecycle['cleanup_session_phase'] == 'stopped'),
        'final_cleanup_verified': (
            result['gateway_exit_code'] == 0 and
            result['remaining_owned_process_count'] == 0 and
            diagnostic['harness']['forced_cleanup'] is False),
        'runner_verified': (
            result['runner_requested_stage'] == STAGE and
            result['runner_stage'] == 'simulation' and
            result['runner_termination_reason'] == 'completed' and
            all(result[key] == 0 for key in ('runner_exit_code',
                'runner_cleanup_remaining', 'runner_forced_kill_count'))),
    }
    result['lifecycle_gates'] = gates
    required = [value for key, value in gates.items() if key != 'runner_verified']
    if require_runner or runner_present:
        required.append(gates['runner_verified'])
    if state == 'PASS' and (sanitation_failed or cancellation_seen):
        result['state'] = 'ERROR'
    elif state == 'PASS' and not all(required):
        result['state'] = 'FAIL'
    result['lifecycle_state'] = result['state']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--runner-status', type=Path)
    args = parser.parse_args()

    def read(path):
        try:
            return json.loads(path.read_text())
        except BaseException as error:
            if is_cancellation(error):
                raise
            return {}

    runner = read(args.runner_status) if args.runner_status else None
    try:
        result = project(read(args.input), runner)
    except BaseException as error:
        if is_cancellation(error):
            raise
        result = project({}, {})
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['state'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
