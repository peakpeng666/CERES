"""Real Python CLI smoke tests; no native runner, simulator, or network work.

Fixture imports belong only to the test process. Every CLI runs from a fresh,
unrelated working directory with PYTHONPATH removed, including under python -O.
The lifecycle execution entry and runner are invoked with --help ONLY.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WAREHOUSE = Path(__file__).resolve().parent
HARNESS = WAREHOUSE.parent
# Allow this test file itself to be run directly without caller setup. These
# in-process paths are never supplied to the subprocess environment.
sys.path[:0] = [str(WAREHOUSE), str(HARNESS)]

from test_lifecycle_only_summary import passing_raw, passing_runner


class LifecycleCLISubprocessTests(unittest.TestCase):
    MODES = (False, True)
    CANARY = 'PRIVATE_CLI_CANARY_session_token_20261023'

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='lifecycle-cli-test-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.cwd = self.directory / 'unrelated-working-directory'
        self.cwd.mkdir()
        self.assertNotEqual(self.cwd, WAREHOUSE)
        self.assertNotEqual(self.cwd, HARNESS)
        self.env = os.environ.copy()
        self.env.pop('PYTHONPATH', None)
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.env['PYTHONNOUSERSITE'] = '1'

    def run_cli(self, script, *arguments, optimized=False):
        self.assertNotIn('PYTHONPATH', self.env)
        command = [sys.executable]
        if optimized:
            command.append('-O')
        command += [str(script), *map(str, arguments)]
        completed = subprocess.run(
            command, cwd=self.cwd, env=self.env, capture_output=True,
            text=True, timeout=10, check=False,
        )
        self.assertNotIn('Traceback', completed.stdout + completed.stderr)
        self.assertNotIn(self.CANARY, completed.stdout + completed.stderr)
        self.assertEqual(completed.stderr, '')
        return completed

    def write_json(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def assert_summary(self, completed, expected_state=None):
        def reject_nonfinite(value):
            self.fail('Non-finite JSON constant in public CLI output: ' + value)

        result = json.loads(completed.stdout, parse_constant=reject_nonfinite)
        self.assertIsInstance(result, dict)
        # Also rejects numeric overflow such as 1e1000, including nested values.
        encoded = json.dumps(result, allow_nan=False)
        self.assertLess(len(encoded), 50000)
        self.assertEqual(result['summary_kind'], 'warehouse_lifecycle_only_summary_v1')
        self.assertEqual(result['warehouse_state'], 'NOT_RUN')
        self.assertEqual(result['route_acceptance'], 'NOT_RUN')
        self.assertEqual(result['state'], result['lifecycle_state'])
        self.assertIn(result['state'], ('PASS', 'FAIL', 'ERROR', 'NOT_RUN'))
        self.assertIs(result['solver_executed'], False)
        if expected_state is not None:
            self.assertEqual(result['state'], expected_state)
        self.assertEqual(completed.returncode, 0 if result['state'] == 'PASS' else 1)
        return result

    def test_empty_missing_and_malformed_inputs_fail_closed(self):
        cases = (
            ('empty-file', ''), ('empty-object', '{}'),
            ('malformed', '{not-json:' + self.CANARY),
            ('wrong-type', '[]'), ('missing', None),
        )
        for optimized in self.MODES:
            for name, contents in cases:
                with self.subTest(optimized=optimized, input=name):
                    source = self.directory / (name + '.json')
                    if contents is not None:
                        source.write_text(contents, encoding='utf-8')
                    completed = self.run_cli(
                        WAREHOUSE / 'lifecycle_only_public_summary.py',
                        '--input', source, optimized=optimized,
                    )
                    result = self.assert_summary(completed)
                    self.assertIn(result['state'], ('ERROR', 'FAIL'))
                    self.assertEqual(completed.returncode, 1)

    def test_known_good_lifecycle_and_runner_fixtures_really_pass(self):
        source = self.write_json('input.json', passing_raw())
        runner = self.write_json('runner.json', passing_runner())
        for optimized in self.MODES:
            with self.subTest(optimized=optimized):
                completed = self.run_cli(
                    WAREHOUSE / 'lifecycle_only_public_summary.py',
                    '--input', source, '--runner-status', runner,
                    optimized=optimized,
                )
                result = self.assert_summary(completed, 'PASS')
                self.assertEqual(result['stage'], 'warehouse-lifecycle-only')
                self.assertEqual(result['execution_scope'], 'lifecycle_only')
                self.assertIs(result['task_not_submitted'], True)
                self.assertIs(result['runner_status_present'], True)
                self.assertTrue(all(result['lifecycle_gates'].values()))
                for field in ('tasks_total', 'tasks_reached', 'trajectory_samples'):
                    self.assertIsNone(result[field])

    def test_normal_failure_exits_one_with_valid_summary(self):
        raw = passing_raw()
        raw['cleanup_pass'] = False
        source = self.write_json('input.json', raw)
        runner = self.write_json('runner.json', passing_runner())
        for optimized in self.MODES:
            with self.subTest(optimized=optimized):
                completed = self.run_cli(
                    WAREHOUSE / 'lifecycle_only_public_summary.py',
                    '--input', source, '--runner-status', runner,
                    optimized=optimized,
                )
                result = self.assert_summary(completed, 'FAIL')
                self.assertEqual(completed.returncode, 1)
                self.assertIs(result['lifecycle_gates']['cleanup_verified'], False)
                self.assertIs(result['lifecycle_gates']['runner_verified'], True)

    def test_missing_and_malformed_runner_cannot_promote_pass(self):
        source = self.write_json('input.json', passing_raw())
        malformed = self.directory / 'malformed-runner.json'
        malformed.write_text(self.CANARY, encoding='utf-8')
        for optimized in self.MODES:
            for runner in (None, self.directory / 'missing-runner.json', malformed):
                with self.subTest(optimized=optimized, runner=runner):
                    arguments = ['--input', source]
                    if runner is not None:
                        arguments += ['--runner-status', runner]
                    result = self.assert_summary(self.run_cli(
                        WAREHOUSE / 'lifecycle_only_public_summary.py',
                        *arguments, optimized=optimized,
                    ), 'FAIL')
                    self.assertIs(result['lifecycle_gates']['runner_verified'], False)

    def test_public_cli_redacts_private_canaries_and_nonfinite_numbers(self):
        raw = passing_raw()
        for field in ('path', 'token', 'stdout', 'stderr', 'error', 'session_id', 'pid'):
            raw[field] = self.CANARY
        raw['overall_wall_budget_s'] = float('nan')
        raw['warehouse_diagnostics']['harness']['error'] = self.CANARY
        raw['warehouse_diagnostics']['lifecycle']['error'] = self.CANARY
        raw['warehouse_cleanup_diagnostics']['process_group_details'] = {
            'status': 'available',
            'groups': [{'component': self.CANARY, 'command': self.CANARY,
                        'pid': 987654321, 'signals': [self.CANARY]}],
            'leaves': {'private': self.CANARY},
        }
        runner = dict(passing_runner(), token=self.CANARY, stdout=self.CANARY)
        source = self.write_json('input.json', raw)
        runner_path = self.write_json('runner.json', runner)
        for optimized in self.MODES:
            with self.subTest(optimized=optimized):
                completed = self.run_cli(
                    WAREHOUSE / 'lifecycle_only_public_summary.py',
                    '--input', source, '--runner-status', runner_path,
                    optimized=optimized,
                )
                result = self.assert_summary(completed, 'PASS')
                self.assertNotIn('987654321', completed.stdout + completed.stderr)
                self.assertIsNone(result['overall_wall_budget_s'])

    def test_reducer_entry_and_runner_help_without_pythonpath(self):
        scripts = (
            (WAREHOUSE / 'lifecycle_only_public_summary.py', '--input'),
            (WAREHOUSE / 'warehouse_lifecycle_only_entry.py', '--diagnostic-deadline'),
            (HARNESS / 'runner' / 'run.py', '--harness'),
        )
        for optimized in self.MODES:
            for script, expected_option in scripts:
                with self.subTest(optimized=optimized, script=script.name):
                    # Never call the lifecycle entry or runner in execution mode.
                    completed = self.run_cli(script, '--help', optimized=optimized)
                    self.assertEqual(completed.returncode, 0)
                    self.assertIn('usage:', completed.stdout)
                    self.assertIn(expected_option, completed.stdout)


if __name__ == '__main__':
    unittest.main()
