"""Audit entrypoints cannot overwrite evidence or run when imported."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AuditOutputContracts(unittest.TestCase):
    def test_static_audits_require_a_new_output(self):
        for name in ('audit_inputs', 'audit_vendor_metrics'):
            script = Path(__file__).with_name(name + '.py')
            with self.subTest(script=name), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'existing.json'
                output.write_bytes(b'preserved evidence')
                result = subprocess.run([sys.executable, str(script), '--output', str(output)],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('FileExistsError', result.stderr)
                self.assertEqual(output.read_bytes(), b'preserved evidence')
                missing = subprocess.run([sys.executable, str(script)], capture_output=True,
                                         text=True, timeout=10)
                self.assertEqual(missing.returncode, 2)
                spec = importlib.util.spec_from_file_location(name, script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertTrue(callable(module.main))
                self.assertFalse(hasattr(module, 'rows'))
                self.assertFalse(hasattr(module, 'reports'))


if __name__ == '__main__':
    unittest.main()
