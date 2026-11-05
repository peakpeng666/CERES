import copy
import ast
from datetime import datetime, timedelta, timezone
import json
import random
from pathlib import Path
import unittest

from process_resource_diagnostics import LIMIT, project, sanitize
from startup_diagnostics import project as project_startup, sanitize as sanitize_startup

ORIGIN = datetime(2026, 10, 22, tzinfo=timezone.utc)


def row(second, cpu=2.5, count=4):
    return {'utc': (ORIGIN + timedelta(seconds=second)).isoformat(),
            'cpu_seconds_live_processes': cpu, 'rss_kib': 4096, 'process_count': count}


def report(rows):
    return {'started_utc': ORIGIN.isoformat(), 'resources': rows}


class ResourceProjectionTests(unittest.TestCase):
    def test_pinned_source_field_contract(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/process_resource_source_3acf.json').read_text())
        self.assertEqual(fixture['application_commit'], '3acf840d1a0c16ab1cfcca15744e4ea6609ddb35')
        source = fixture['sources'][0]
        self.assertEqual(source['path'], 'src/lab/ros/processes.py')
        tree = ast.parse(source['excerpts']['sampling'])
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'self.resources.append')
        self.assertEqual({k.value for k in call.args[0].keys}, {'utc', 'rss_kib', 'cpu_seconds_live_processes', 'process_count'})
        origin = ast.parse(fixture['sources'][1]['excerpts']['origin'])
        self.assertIn('started_utc', {k.arg for n in ast.walk(origin) if isinstance(n, ast.Call) for k in n.keywords})
        persist = ast.parse(fixture['sources'][1]['excerpts']['persistence'])
        self.assertTrue(any(k.arg == 'resources' and ast.unparse(k.value) == 'self.processes.resources'
                            for n in ast.walk(persist) if isinstance(n, ast.Call) for k in n.keywords))

    def test_actual_source_fields_and_relative_time(self):
        result = project(report([row(1.25), row(2.5, 3.0)]))
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['samples'][0], {'offset_from_report_start_utc_s': 1.25,
                         'cpu_seconds_live_processes': 2.5, 'rss_kib': 4096, 'process_count': 4})
        self.assertEqual(result['source_rows'], 2)
        self.assertEqual(result['utc_order'], 'nondecreasing')

    def test_missing_is_unavailable_not_zero_activity(self):
        for value in (None, {}, {'resources': None}, report([])):
            result = project(value)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['samples'], [])

    def test_counter_decrease_retained_without_utilization(self):
        result = project(report([row(1, 90, 5), row(2, 3, 5)]))
        self.assertEqual([x['cpu_seconds_live_processes'] for x in result['samples']], [90, 3])
        self.assertNotIn('utilization', json.dumps(result))
        self.assertEqual(result['scope'], 'all_owned_groups_including_cleanup')

    def test_backward_utc_is_flagged_not_sorted(self):
        result = project(report([row(3), row(1), row(2)]))
        self.assertEqual(result['utc_order'], 'backwards')
        self.assertEqual([x['offset_from_report_start_utc_s'] for x in result['samples']], [3, 1, 2])

    def test_zero_and_equal_timestamps_retained(self):
        result = project(report([row(0, 0, 0), row(0, 0, 0)]))
        self.assertEqual(result['utc_order'], 'nondecreasing')
        self.assertEqual(len(result['samples']), 2)

    def test_bounds_and_bad_numeric_types(self):
        for key in ('cpu_seconds_live_processes', 'rss_kib', 'process_count'):
            for value in (True, '1', -1, float('nan'), float('inf'), 10**500, None, [], {}):
                with self.subTest(key=key, value=type(value).__name__):
                    r = row(1); r[key] = value
                    result = project(report([r]))
                    self.assertEqual(result['status'], 'invalid')
                    self.assertEqual(result['samples'], [])
        for key in ('rss_kib', 'process_count'):
            r = row(1); r[key] = 1.5
            self.assertEqual(project(report([r]))['status'], 'invalid')

    def test_time_bounds_and_invalid_dates(self):
        for value in ('PRIVATE', '2026-99-99T00:00:00+00:00', '2026-10-22T00:00:00+01:00',
                      row(-1)['utc'], row(7201)['utc'], 1, None, [], {}):
            r = row(1); r['utc'] = value
            self.assertEqual(project(report([r]))['samples'], [])

    def test_missing_origin_is_invalid(self):
        self.assertEqual(project({'resources': [row(1)]})['status'], 'invalid')

    def test_z_timestamp(self):
        r = row(1); r['utc'] = r['utc'].replace('+00:00', 'Z')
        self.assertEqual(project(report([r]))['samples'][0]['offset_from_report_start_utc_s'], 1)

    def test_partial_rows_reported(self):
        result = project(report([row(1), None, {}, row(2)]))
        self.assertEqual((result['status'], result['source_rows'], result['invalid_rows']), ('partial', 4, 2))

    def test_bounded_selection_retains_endpoints(self):
        result = project(report([row(i) for i in range(500)]))
        self.assertEqual(len(result['samples']), LIMIT)
        self.assertEqual(result['selection'], 'evenly_sampled')
        self.assertEqual(result['samples'][0]['offset_from_report_start_utc_s'], 0)
        self.assertEqual(result['samples'][-1]['offset_from_report_start_utc_s'], 499)

    def test_source_limit(self):
        result = project(report([None] * 4097))
        self.assertEqual(result['status'], 'over_limit')
        self.assertEqual(result['samples'], [])

    def test_inputs_unchanged_and_roundtrip(self):
        source = report([row(1), row(2)]); before = copy.deepcopy(source)
        result = project(source)
        self.assertEqual(source, before)
        self.assertEqual(sanitize(result), result)

    def test_startup_projection_and_second_sanitizer(self):
        value = project_startup(report([row(1)]), {})
        self.assertEqual(value['process_resources']['status'], 'available')
        self.assertEqual(sanitize_startup(value)['process_resources'], value['process_resources'])

    def test_free_text_paths_identity_and_timestamps_removed(self):
        r = row(1); r.update(pid=123, command='PRIVATE', path='/PRIVATE')
        value = report([r]); value['session_id'] = 'PRIVATE'
        output = json.dumps(project(value))
        for secret in ('PRIVATE', '2026-', 'command', 'session_id', 'pid'):
            self.assertNotIn(secret, output)

    def test_public_schema_fuzz_is_bounded(self):
        random.seed(123)
        candidates = [None, True, False, -1, 0, 1, 10**500, float('nan'), 'PRIVATE', {}, [], {'PRIVATE': 'PRIVATE'}]
        for _ in range(1000):
            raw = {key: random.choice(candidates) for key in ('status', 'source_rows', 'invalid_rows', 'utc_order', 'selection', 'samples')}
            result = sanitize(raw)
            encoded = json.dumps(result, allow_nan=False)
            self.assertNotIn('PRIVATE', encoded)
            self.assertLess(len(encoded), 30000)


if __name__ == '__main__':
    unittest.main()
