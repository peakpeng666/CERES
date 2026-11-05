"""Cross-patch regression checks: historical provenance and install outcomes coexist."""
import copy
import json
import unittest

import test_lifecycle
from warehouse_public_summary import project, BOOLS


ZERO = {'exit_code': 0, 'cleanup_remaining': 0, 'forced_kill_count': 0}
RAW_HASH = '2c3acde82c25c0372c8793225abcbc94d4e74be4b441ad130de7c3a475790d66'
CANONICAL_HASH = '64f4c3b93af78800aefc036c58035a8556401c82627f55409a16483d12dc7055'
PRIVATE = 'INTEGRATION_PRIVATE_CANARY_https://private.invalid/not-real'


class NativeIntegrationTests(unittest.TestCase):
    def good(self):
        return test_lifecycle.LifecycleTests().good()

    def runner(self, **overrides):
        result = dict(ZERO, termination_reason='completed', stage='simulation',
                      failure_hint='none', apt_projection_scope='before_repair',
                      apt_projection={'status': 'metadata_unavailable', 'dependencies': [],
                                      'metadata_phase': 'metadata_project',
                                      'metadata_error': 'api_type',
                                      'metadata_operation': 'dependency_groups'})
        result.update(overrides)
        return result

    def test_completed_receipt_preserves_both_provenance_and_diagnostic_fields(self):
        result = project(self.good(), self.runner())
        self.assertEqual(result['state'], 'PASS')
        self.assertEqual(result['runner_termination_reason'], 'completed')
        self.assertEqual(result['apt_diagnostic_scope'], 'before_repair')
        self.assertEqual(result['apt_metadata_operation'], 'dependency_groups')
        self.assertEqual(result['historical_materialized_file_count'], 61)
        self.assertIs(result['historical_cache_verified'], True)
        self.assertEqual(result['raw_resource_manifest_sha256'], RAW_HASH)
        self.assertIs(result['raw_resources_match_historical'], False)

    def test_each_typed_failure_overrides_complete_historical_physics(self):
        for reason in ('child_exit', 'stage_timeout', 'cancelled', 'supervisor_error'):
            with self.subTest(reason=reason):
                result = project(self.good(), self.runner(termination_reason=reason))
                self.assertEqual(result['state'], 'FAIL')
                self.assertEqual(result['warehouse_state'], 'FAIL')
                self.assertEqual(result['runner_termination_reason'], reason)
                self.assertIs(result['historical_cache_verified'], True)

    def test_completed_install_diagnostic_cannot_replace_missing_provenance(self):
        for key in ('resource_cache_cohort', 'historical_cache_verified',
                    'historical_fuel_archive_sha256', 'historical_selected_manifest_sha256',
                    'historical_materialized_file_count', 'historical_alias_projection',
                    'raw_resource_manifest_sha256', 'raw_resources_match_historical'):
            with self.subTest(key=key):
                raw = self.good()
                del raw[key]
                self.assertEqual(project(raw, self.runner())['state'], 'FAIL')

    def test_both_documented_raw_pairs_preserve_their_distinct_meaning(self):
        for digest, equal in ((RAW_HASH, False), (CANONICAL_HASH, True)):
            for reason in ('completed', 'stage_timeout'):
                with self.subTest(digest=digest, reason=reason):
                    raw = self.good()
                    raw.update(raw_resource_manifest_sha256=digest,
                               raw_resources_match_historical=equal)
                    result = project(raw, self.runner(termination_reason=reason))
                    self.assertEqual(result['state'], 'PASS' if reason == 'completed' else 'FAIL')
                    self.assertEqual(result['raw_resource_manifest_sha256'], digest)
                    self.assertIs(result['raw_resources_match_historical'], equal)

    def test_crossed_or_unknown_raw_pairs_never_pass_with_completed_receipt(self):
        for digest, equal in ((RAW_HASH, True), (CANONICAL_HASH, False), ('0' * 64, False)):
            with self.subTest(digest=digest, equal=equal):
                raw = self.good()
                raw.update(raw_resource_manifest_sha256=digest,
                           raw_resources_match_historical=equal)
                self.assertEqual(project(raw, self.runner())['state'], 'FAIL')

    def test_combined_provenance_does_not_replace_any_physical_boolean(self):
        for field in BOOLS:
            with self.subTest(field=field):
                raw = self.good()
                raw[field] = False
                self.assertEqual(project(raw, self.runner())['state'], 'FAIL')

    def test_combined_diagnostics_do_not_weaken_runner_cleanup(self):
        for key in ZERO:
            with self.subTest(key=key):
                self.assertEqual(project(self.good(), self.runner(**{key: 1}))['state'], 'FAIL')

    def test_hostile_metadata_and_provenance_strings_never_leak(self):
        for value in (PRIVATE, {'private': PRIVATE}, [PRIVATE], True, 2, float('nan'), None):
            with self.subTest(value=value):
                raw = self.good()
                raw.update(resource_cache_cohort=value, historical_alias_projection=value,
                           historical_fuel_archive_sha256=value, raw_resource_manifest_sha256=value)
                runner = self.runner(termination_reason=value, failure_hint=value,
                                     apt_projection_scope=value)
                runner['apt_projection']['metadata_operation'] = value
                result = project(raw, runner)
                public = json.dumps(result, allow_nan=False)
                self.assertEqual(result['state'], 'FAIL')
                self.assertNotIn('INTEGRATION_PRIVATE_CANARY', public)
                self.assertNotIn('private.invalid', public)
                self.assertEqual(result['runner_termination_reason'], 'unknown')
                self.assertEqual(result['apt_diagnostic_scope'], 'unknown')
                self.assertEqual(result['apt_metadata_operation'], 'not_run')

    def test_two_stage_projection_retains_provenance_and_requires_outer_receipt(self):
        raw = self.good()
        before = copy.deepcopy(raw)
        private = project(raw, require_runner=False)
        self.assertEqual(raw, before)
        self.assertEqual(private['state'], 'PASS')
        self.assertEqual(project(private)['state'], 'FAIL')
        result = project(private, self.runner())
        self.assertEqual(result['state'], 'PASS')
        self.assertEqual(result, project(result, self.runner()))
        self.assertEqual(project(private, self.runner(termination_reason='cancelled'))['state'], 'FAIL')

    def test_install_failure_without_warehouse_evidence_stays_diagnostic_only(self):
        result = project({}, self.runner(stage='installation', exit_code=124,
                                        termination_reason='stage_timeout', failure_hint='stage_timeout'))
        self.assertEqual(result['state'], 'FAIL')
        self.assertEqual(result['runner_failure_hint'], 'stage_timeout')
        self.assertEqual(result['apt_diagnostic_scope'], 'before_repair')
        self.assertIsNone(result['historical_cache_verified'])
        self.assertIsNone(result['historical_materialized_file_count'])
        self.assertIsNone(result['tasks_reached'])


if __name__ == '__main__':
    unittest.main()
