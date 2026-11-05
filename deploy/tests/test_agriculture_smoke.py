"""Offline regressions for the real deployment HTTP smoke, stdlib only."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agriculture_smoke', Path(__file__).resolve().parents[1] / 'check-agriculture.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class AgricultureSmokeTests(unittest.TestCase):
    def fake_api(self, *, corrupt_duplicate=False, valid=True, record_seed=42, state='done'):
        saved = None
        calls = []
        def request(base, path, body=None, expected=200):
            nonlocal saved
            calls.append((path, expected))
            if path == '/scenarios':
                if body is None:
                    return {'scenarios': []}
                if saved is None:
                    self.assertEqual(expected, 201)
                    saved = copy.deepcopy(body)
                    return copy.deepcopy(saved)
                self.assertEqual(expected, 409)
                if corrupt_duplicate:
                    saved['title'] = body['title']
                return {'message': 'id exists'}
            if path.startswith('/scenarios/'):
                return copy.deepcopy(saved)
            if path == '/solve':
                self.assertEqual(expected, 202)
                self.assertEqual(body['algorithms'][0]['id'], 'panel.boustrophedon')
                return {'runs': ['run_test']}
            if path.endswith('/record'):
                result = copy.deepcopy(saved)
                result['provenance']['seed'] = record_seed
                return {'record': {'scenario': result}}
            return {'state': state, 'status': 'feasible', 'check': {'valid': valid}}
        return request, calls

    def test_complete_flow(self):
        request, calls = self.fake_api()
        with patch.object(smoke, 'request_json', request):
            result = smoke.run('http://127.0.0.1:4100')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(calls[0], ('/scenarios', 200))
        self.assertIn(('/scenarios', 409), calls)
        self.assertEqual(calls[-1], ('/runs/run_test/record', 200))

    def test_obstacle_option_adds_fixed_hole_and_preserves_default(self):
        for obstacle in [False, True]:
            request, _ = self.fake_api()
            def inspect(base, path, body=None, expected=200):
                if path == '/scenarios' and body is not None:
                    self.assertEqual(bool(body['instance']['obstacles']), obstacle)
                    if obstacle:
                        self.assertEqual(body['instance']['obstacles'], [[[80, 50], [120, 50], [120, 90], [80, 90]]])
                        self.assertEqual(body['provenance']['params']['obstacle_fixture'], 'central-hole-v1')
                return request(base, path, body, expected)
            with self.subTest(obstacle=obstacle), patch.object(smoke, 'request_json', inspect):
                result = smoke.run('http://localhost:4100', obstacle=obstacle)
            self.assertEqual(result['obstacle'], obstacle)
            self.assertEqual(result['status'], 'passed')

    def test_detects_duplicate_overwrite(self):
        request, _ = self.fake_api(corrupt_duplicate=True)
        with patch.object(smoke, 'request_json', request), self.assertRaisesRegex(RuntimeError, 'Duplicate import'):
            smoke.run('http://localhost:4100')

    def test_requires_independent_check(self):
        request, _ = self.fake_api(valid=False)
        with patch.object(smoke, 'request_json', request), self.assertRaisesRegex(RuntimeError, 'Independent check'):
            smoke.run('http://localhost:4100')

    def test_requires_record_seed_roundtrip(self):
        request, _ = self.fake_api(record_seed=99)
        with patch.object(smoke, 'request_json', request), self.assertRaisesRegex(RuntimeError, 'Record lost'):
            smoke.run('http://localhost:4100')

    def test_rejects_failed_run(self):
        request, _ = self.fake_api(state='error')
        with patch.object(smoke, 'request_json', request), self.assertRaisesRegex(RuntimeError, 'Solve failed'):
            smoke.run('http://localhost:4100')

    def test_all_failure_terminal_states_fail_immediately(self):
        for state in ['error', 'cancelled', 'canceled', 'timeout']:
            request, _ = self.fake_api(state=state)
            with self.subTest(state=state), patch.object(smoke, 'request_json', request), patch.object(smoke.time, 'sleep') as sleep, self.assertRaisesRegex(RuntimeError, 'Solve failed'):
                smoke.run('http://localhost:4100')
            sleep.assert_not_called()

    def test_timeout_is_failure(self):
        request, _ = self.fake_api(state='running')
        with patch.object(smoke, 'request_json', request), patch.object(smoke.time, 'monotonic', side_effect=[0, 61]), self.assertRaisesRegex(RuntimeError, 'did not complete'):
            smoke.run('http://localhost:4100')

    def test_listing_cannot_silently_fail(self):
        with patch.object(smoke, 'request_json', return_value={'error': 'missing assets'}), self.assertRaisesRegex(RuntimeError, 'listing'):
            smoke.run('http://localhost:4100')

    def test_only_local_http_targets(self):
        for base in ['https://localhost:4100', 'http://example.com', 'http://localhost:4100/lab/api', 'http://user:secret@localhost:4100']:
            with self.subTest(base=base), self.assertRaisesRegex(RuntimeError, 'local HTTP'):
                smoke.run(base)


if __name__ == '__main__':
    unittest.main()
