"""Adversarial offline regressions, not ROS/Gazebo execution evidence."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ceres_route_sim.control_core import command, yaw_from_quaternion
from ceres_route_sim.runtime_core import FEEDBACK, REQUIRED_CHECKS, FeedbackTracker, report_failures
from ceres_route_sim.scene_core import SceneError, prepare_scene, read_json
from ceres_route_sim.upstream_core import check_upstream, git_blob_sha1, inspect_reference

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parent


class SceneHardeningTests(unittest.TestCase):
    def setUp(self):
        self.scene = read_json(PACKAGE / 'config' / 'route_scene_synthetic_fixture.json')

    def test_nonmetric_scene_cannot_claim_robot_map_frame(self):
        for frame in ('map', 'odom', 'base_link'):
            self.scene['frame']['id'] = frame
            with self.subTest(frame=frame), self.assertRaisesRegex(SceneError, 'benchmark_xy'):
                prepare_scene(self.scene)

    def test_duplicate_vehicle_identity_is_rejected(self):
        self.scene['vehicles'].append(copy.deepcopy(self.scene['vehicles'][0]))
        with self.assertRaisesRegex(SceneError, 'duplicate vehicle_id'):
            prepare_scene(self.scene)

    def test_status_and_snapshot_identity_survive_visualization(self):
        self.scene['status']['solution_status'] = 'invalid'
        self.scene['status']['full_service_success'] = False
        view = prepare_scene(self.scene)
        self.assertIn(self.scene['run_id'], view.identity_label)
        self.assertIn('revision 1', view.identity_label)
        self.assertIn('solution=invalid', view.status_label)
        self.assertIn('full_service=False', view.status_label)

    def test_missing_identity_or_truthy_status_not_silently_accepted(self):
        del self.scene['run_id']
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)
        self.setUp()
        self.scene['status']['has_candidate'] = 'false'
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_invalid_revision_rejected(self):
        for revision in (True, 0, -1, '1'):
            self.scene['revision'] = revision
            with self.subTest(revision=revision), self.assertRaises(SceneError):
                prepare_scene(self.scene)

    def test_duplicate_keys_overflow_and_deep_json_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ambiguous.json'
            for payload in ('{"simulation_ready":true,"simulation_ready":false}',
                            '{"x":1e999}', '[' * 2000 + '0' + ']' * 2000):
                path.write_text(payload)
                with self.subTest(payload=payload[:80]), self.assertRaises(SceneError):
                    read_json(path)

    def test_duplicate_unserved_nodes_rejected(self):
        self.scene['unserved_task_ids'] = [1, 1]
        with self.assertRaisesRegex(SceneError, 'duplicate unserved'):
            prepare_scene(self.scene)

    def test_aggregate_route_budget_prevents_unbounded_marker_output(self):
        self.scene['vehicles'] = [
            {'vehicle_id': i, 'visit_node_ids': [0] * 20000, 'polyline': [[0, 0]] * 20000}
            for i in range(6)
        ]
        with self.assertRaisesRegex(SceneError, 'total route visits'):
            prepare_scene(self.scene)

    def test_nearly_unit_quaternion_is_normalized_before_yaw(self):
        q = math.sqrt(0.5) * 0.995
        self.assertAlmostEqual(yaw_from_quaternion(0, 0, q, q), math.pi / 2, places=12)

    def test_goal_outside_fixture_corridor_rejected_before_motion(self):
        with self.assertRaisesRegex(ValueError, 'goal is outside'):
            command(0, 0, 0, (9, 0))


class FeedbackProgressTests(unittest.TestCase):
    def test_duplicate_messages_cannot_satisfy_readiness(self):
        tracker = FeedbackTracker()
        for index in range(10):
            for kind in FEEDBACK:
                tracker.observe(kind, 0, 0, float(index))
        self.assertFalse(tracker.ready())
        self.assertFalse(tracker.fresh(9.0))
        self.assertEqual(tracker.count, dict.fromkeys(FEEDBACK, 1))
        self.assertEqual(tracker.received, dict.fromkeys(FEEDBACK, 10))

    def test_frozen_stream_stops_being_fresh_despite_receipt(self):
        tracker = FeedbackTracker()
        for index in range(5):
            for kind in FEEDBACK:
                tracker.observe(kind, 0, index * 100_000_000, index * 0.1)
        self.assertTrue(tracker.ready())
        self.assertTrue(tracker.fresh(0.5))
        for kind in FEEDBACK:
            tracker.observe(kind, 0, 400_000_000, 3.0)
        self.assertFalse(tracker.fresh(3.0))

    def test_nanosecond_precision_and_backward_clock(self):
        tracker = FeedbackTracker()
        tracker.observe('clock', 2_000_000_000, 5, 0.0)
        tracker.observe('clock', 2_000_000_000, 6, 0.1)
        self.assertEqual(tracker.count['clock'], 2)
        with self.assertRaisesRegex(ValueError, 'backwards'):
            tracker.observe('clock', 2_000_000_000, 5, 0.2)

    def test_invalid_timestamp_components_are_rejected(self):
        for sec, nanosec in ((-1, 0), (True, 0), (0, -1), (0, 1_000_000_000), (0, 0.5)):
            with self.subTest(sec=sec, nanosec=nanosec), self.assertRaises(ValueError):
                FeedbackTracker().observe('clock', sec, nanosec, 0.0)


def report_fixture():
    """Report-gate unit data only; no simulator generated these numbers."""
    return {
        'scope': 'SYNTHETIC_GREENHOUSE_ONLY', 'success': True, 'runtime_executed': True,
        'checks': dict.fromkeys(REQUIRED_CHECKS, True), 'failed_checks': [],
        'completed_waypoints': 3, 'counts': dict.fromkeys(FEEDBACK, 10),
        'odom_return_error_m': 0.1, 'ground_truth_return_error_m': 0.2,
        'max_odom_displacement_m': 1.9, 'max_physical_displacement_m': 1.8,
        'max_imu_yaw_rate_rad_s': 0.6, 'wall_seconds': 30.0,
    }


class ReportGateTests(unittest.TestCase):
    def test_consistent_fixture_and_nonzero_controller_exit(self):
        self.assertEqual(report_failures(report_fixture(), 0), [])
        self.assertIn('controller_exit_code', report_failures(report_fixture(), 1))

    def test_empty_success_report_or_missing_required_check_never_passes(self):
        self.assertTrue(report_failures({'success': True}, 0))
        report = report_fixture()
        del report['checks']['physical_motion_observed']
        self.assertIn('check:physical_motion_observed', report_failures(report, 0))

    def test_truthy_nonboolean_check_and_failed_extra_check_rejected(self):
        report = report_fixture()
        report['checks']['clock_advanced'] = 1
        report['checks']['additional_observation'] = False
        failures = report_failures(report, 0)
        self.assertIn('check:clock_advanced', failures)
        self.assertIn('check:additional_observation', failures)

    def test_report_booleans_cannot_contradict_numeric_observations(self):
        report = report_fixture()
        report['max_physical_displacement_m'] = 0.01
        report['ground_truth_return_error_m'] = 5
        failures = report_failures(report, 0)
        self.assertIn('max_physical_displacement_m', failures)
        self.assertIn('ground_truth_return_error_m', failures)

    def test_zero_exit_without_report_fails_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(ROOT / 'scripts' / 'check_smoke_result.py'),
                 str(Path(directory) / 'absent.json'), '--exit-code', '0'],
                capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(completed.returncode, 1)
        self.assertIs(json.loads(completed.stdout)['success'], False)

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / 'smoke_result.json'
            sentinel.write_text('historical evidence\n')
            completed = subprocess.run(['bash', str(ROOT / 'scripts' / 'run_smoke.sh'), directory],
                                       capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(sentinel.read_text(), 'historical evidence\n')
            self.assertEqual(list(Path(directory).iterdir()), [sentinel])


class UpstreamPreflightTests(unittest.TestCase):
    def test_git_blob_identity_matches_known_git_object(self):
        self.assertEqual(git_blob_sha1(b'hello\n'), 'ce013625030ba8dba906f756967f9e9ca394464a')

    def test_installed_source_changes_and_missing_files_block_reference(self):
        manifest = {'package': 'reference', 'package_version': '1.0.3',
                    'files': [{'path': 'launch/example.py',
                               'git_blob_sha1': 'ce013625030ba8dba906f756967f9e9ca394464a'}]}
        with tempfile.TemporaryDirectory() as directory:
            share = Path(directory)
            (share / 'launch').mkdir()
            (share / 'package.xml').write_text('<package><name>reference</name><version>1.0.3</version></package>')
            source = share / 'launch' / 'example.py'
            source.write_text('hello\n')
            self.assertEqual(inspect_reference(share, manifest)['blockers'], [])
            source.write_text('changed\n')
            self.assertIn('upstream_source_mismatch:launch/example.py', inspect_reference(share, manifest)['blockers'])
            source.unlink()
            self.assertIn('upstream_source_mismatch:launch/example.py', inspect_reference(share, manifest)['blockers'])

    def test_missing_stack_stays_unexecuted_and_lists_pending_physics(self):
        with patch('ceres_route_sim.upstream_core.shutil.which', return_value=None), \
                patch.dict('os.environ', {}, clear=True), \
                patch.dict('sys.modules', {'ament_index_python': None, 'ament_index_python.packages': None}):
            report = check_upstream()
        self.assertEqual(report['status'], 'blocked')
        self.assertIs(report['runtime_executed'], False)
        self.assertIs(report['simulation_ready'], False)
        self.assertIs(report['task_execution_verified'], False)
        self.assertFalse(report['full_dependency_lock'])
        self.assertTrue(report['remaining_acceptance'])


if __name__ == '__main__':
    unittest.main()
