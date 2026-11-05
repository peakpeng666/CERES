"""Native cohort audit boundaries; synthetic records only, no solver runs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from audit_vendor_results import audit
from validate import sha


class VendorAuditContract(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs = self.root / 'runs'
        self.runs.mkdir()
        instance = self.root / 'tiny.vrp'
        instance.write_text('NAME: tiny\nTYPE: CVRP\nDIMENSION: 2\nCAPACITY: 1\n'
                            'EDGE_WEIGHT_TYPE: EUC_2D\nNODE_COORD_SECTION\n1 0 0\n2 1 0\n'
                            'DEMAND_SECTION\n1 0\n2 1\nDEPOT_SECTION\n1\n-1\nEOF\n')
        manifest = self.root / 'src/research/configs/cvrplib_cases.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'cases': [{'case_id': 'tiny', 'source_family': 'X',
                            'instance_path': 'tiny.vrp', 'instance_sha256': sha(instance),
                            'contract': {'vehicle_limit': None}}]}))
        self.record = dict(cohort='vendor_X12', case_id='tiny', algorithm='native', seed=0,
                           instance_sha256=sha(instance), start_ns=1, deadline_ns=1000000001,
                           budget_seconds=1, incumbent=None, status='NO_SOLUTION', returncode=0)
        scripts = self.root / 'independent'
        scripts.mkdir()
        for name in ('audit_vendor_results.py', 'validate.py'):
            shutil.copy2(Path(__file__).with_name(name), scripts / name)
        self.command = [sys.executable, str(scripts / 'audit_vendor_results.py'), str(self.runs),
                        '--planned', '2', '--output', str(self.root / 'audit.json')]

    def test_denominator_and_input_directory_contract(self):
        for planned in (-1, True, 1.5):
            with self.subTest(planned=planned), self.assertRaises(ValueError):
                audit(self.root, self.runs, planned)
        with self.assertRaises(ValueError):
            audit(self.root, self.root / 'absent', 1)

    def test_cli_fails_for_invalid_evidence_and_duplicates(self):
        for name in ('a.json', 'b.json'):
            (self.runs / name).write_text(json.dumps(dict(self.record, invalid_outputs=['invalid route'])))
        result = subprocess.run(self.command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads((self.root / 'audit.json').read_text())
        self.assertEqual(report['attempted'], 2)
        self.assertEqual(report['audit_errors'], 2)
        self.assertTrue(report['duplicates'])

    def test_cli_preserves_existing_evidence(self):
        output = self.root / 'audit.json'
        output.write_bytes(b'original evidence')
        result = subprocess.run(self.command, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b'original evidence')

    def test_valid_failed_attempt_does_not_become_an_audit_error(self):
        (self.runs / 'a.json').write_text(json.dumps(self.record))
        report = audit(self.root, self.runs, 2)
        self.assertEqual((report['attempted'], report['not_started'], report['audit_errors']), (1, 1, 0))
        self.assertFalse(report['rows'][0]['has_valid_on_time_solution'])


if __name__ == '__main__':
    unittest.main()
