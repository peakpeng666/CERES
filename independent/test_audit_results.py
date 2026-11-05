import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from audit_results import audit


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AuditAccounting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        instance = self.root / 'tiny.vrp'
        instance.write_text('NAME: tiny\nTYPE: CVRP\nDIMENSION: 3\nCAPACITY: 4\n'
                            'EDGE_WEIGHT_TYPE: EUC_2D\nNODE_COORD_SECTION\n1 0 0\n2 3 0\n3 0 4\n'
                            'DEMAND_SECTION\n1 0\n2 2\n3 2\nDEPOT_SECTION\n1\n-1\nEOF\n')
        manifest = self.root / 'src/research/configs/cvrplib_cases.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'cases': [{'case_id': 'tiny', 'instance_path': 'tiny.vrp',
                                                   'instance_sha256': digest(instance),
                                                   'contract': {'id': 'fixed', 'vehicle_limit': 1}}]}))
        self.runs = self.root / 'runs'
        self.runs.mkdir()
        payload = {'coordinates': [[0.0, 0.0], [3.0, 0.0], [0.0, 4.0]], 'demands': [0, 2, 2],
                   'capacity': 4, 'K': 1, 'algorithm': 'a', 'seed': 0, 'budget_seconds': 1}
        self.record = dict(schema='urp-cvrplib-result-v1', status='OK', feasible=True, case_id='tiny',
                           algorithm='a', seed=0, budget_seconds=1, routes=[[0, 1, 2, 0]], cost=12,
                           start_monotonic_ns=1, incumbent_emitted_ns=3, incumbent_received_ns=5,
                           deadline_monotonic_ns=1000000001,
                           events=[dict(event='incumbent', routes=[[0, 1, 2, 0]], cost=12, emitted_ns=3, received_ns=5)],
                           contract={'id': 'fixed', 'K': 1, 'fixed_cost': 0},
                           instance_sha256=digest(instance), case_manifest_sha256=digest(manifest),
                           payload_sha256=hashlib.sha256(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()).hexdigest())

    def put(self, name, value):
        path = self.runs / name / 'result.json'
        path.parent.mkdir()
        path.write_text(json.dumps(value))
        return path

    def test_missing_or_file_input_directory_is_not_an_empty_success(self):
        for path in (self.root / 'absent', self.root / 'tiny.vrp'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                audit(self.root, path, 1)

    def test_cli_never_overwrites_existing_evidence(self):
        output = self.root / 'audit.json'
        original = b'preserved historical evidence\n'
        output.write_bytes(original)
        command = [sys.executable, str(Path(__file__).with_name('audit_results.py')),
                   str(self.runs), '--root', str(self.root), '--planned', '0', '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)

    def test_invalid_failure_status_is_counted_and_reported(self):
        record = dict(self.record, feasible=False)
        record.pop('status')
        self.put('missing-status', record)
        report = audit(self.root, self.runs, 1)
        self.assertEqual(report['attempted'], 1)
        self.assertEqual(report['eligible'], 0)
        self.assertEqual(len(report['audit_errors']), 1)

    def test_valid_result_and_missing_identity_hashes(self):
        self.put('valid', self.record)
        missing = dict(self.record)
        missing.pop('instance_sha256')
        missing.pop('case_manifest_sha256')
        self.put('missing', missing)
        report = audit(self.root, self.runs, 2)
        self.assertEqual((report['attempted'], report['not_started'], report['eligible']), (2, 0, 1))
        row = next(r for r in report['rows'] if r['file'].startswith('missing/'))
        self.assertEqual(len(row['identity_errors']), 2)
        self.assertTrue(report['duplicate_keys'])
        self.assertEqual(len(report['audit_errors']), 1)

    def test_malformed_or_missing_schema_result_is_not_skipped(self):
        malformed = self.put('malformed', None)
        malformed.write_text('{')
        self.put('wrong-shape', [])
        self.put('missing-schema', {k: v for k, v in self.record.items() if k != 'schema'})
        (self.runs / 'environment.json').write_text('{"python": "test"}')
        report = audit(self.root, self.runs, 4)
        self.assertEqual((report['attempted'], report['not_started'], report['eligible']), (3, 1, 0))
        self.assertEqual(len(report['audit_errors']), 3)
        self.assertTrue(all(r['count_in_denominator'] for r in report['rows']))


if __name__ == '__main__':
    unittest.main()
