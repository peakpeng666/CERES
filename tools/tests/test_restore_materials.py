"""Historical ZIP restore has its own fingerprint and never overwrites evidence."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('restore_materials', Path(__file__).resolve().parents[1] / 'restore_materials.py')
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class HistoricalMaterialsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'repo'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'storage.json').write_text('{"source_repository":"fixture"}')
        (self.root / 'setup').mkdir()
        self.manifest = self.root / 'setup/materials-archive.json'
        self.archive = Path(temporary.name) / 'URP.zip'

    def zip(self, files):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        self.record = {'schema': 'urp.materials-archive/1', 'archive': {
            'bytes': self.archive.stat().st_size,
            'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}}
        self.manifest.write_text(json.dumps(self.record))

    def run_restore(self, *args):
        output = io.StringIO()
        with patch.object(restore, 'ROOT', self.root), patch('sys.argv', ['restore_materials.py', str(self.archive), *map(str, args)]), redirect_stdout(output), redirect_stderr(output):
            restore.main()
        return output.getvalue()

    def test_default_manifest_lists_then_restores_without_storage_archive_key(self):
        self.zip({'URP/data/new.txt': b'material', 'URP/README.md': b'outside scope',
                  'URP/data/.venv/cache': b'skip'})
        summary = json.loads(self.run_restore('--list'))
        self.assertEqual(summary, {'files': 1, 'bytes': 8, 'archive_sha256': 'PASS'})
        self.assertFalse((self.root / 'data').exists())
        self.assertIn('Materials restored', self.run_restore())
        self.assertEqual((self.root / 'data/new.txt').read_bytes(), b'material')
        self.assertFalse((self.root / 'README.md').exists())
        self.assertEqual(json.loads(self.run_restore('--list'))['files'], 0)

    def test_explicit_manifest_can_select_a_separate_historical_fingerprint(self):
        self.zip({'URP/papers/example.pdf': b'fixture'})
        explicit = self.manifest.with_name('explicit.json')
        self.manifest.rename(explicit)
        self.assertEqual(json.loads(self.run_restore('--manifest', explicit, '--list'))['files'], 1)

    def test_missing_or_invalid_manifest_fails_before_writing(self):
        self.zip({'URP/data/new.txt': b'fixture'})
        for document in (None, {}, {'schema': 'urp.materials-archive/1', 'archive': {'bytes': True, 'sha256': '0' * 64}},
                         {'schema': 'urp.materials-archive/1', 'archive': {'bytes': 1, 'sha256': 'bad'}}):
            with self.subTest(document=document):
                if document is None:
                    self.manifest.unlink()
                else:
                    self.manifest.write_text(json.dumps(document))
                with self.assertRaises(SystemExit) as error:
                    self.run_restore('--list')
                self.assertEqual(error.exception.code, 2)
                self.assertFalse((self.root / 'data').exists())

    def test_size_and_sha_mismatch_reject_even_list_mode(self):
        self.zip({'URP/data/new.txt': b'fixture'})
        for field, value in [('bytes', self.record['archive']['bytes'] + 1), ('sha256', '0' * 64)]:
            with self.subTest(field=field):
                document = {**self.record, 'archive': {**self.record['archive'], field: value}}
                self.manifest.write_text(json.dumps(document))
                with self.assertRaisesRegex(SystemExit, 'differs from the historical manifest'):
                    self.run_restore('--list')
                self.assertFalse((self.root / 'data').exists())

    def test_tracked_and_conflicting_local_materials_are_never_overwritten(self):
        self.zip({'URP/data/tracked.txt': b'archive', 'URP/data/local.txt': b'archive'})
        (self.root / 'data').mkdir()
        tracked = self.root / 'data/tracked.txt'
        tracked.write_bytes(b'original tracked evidence')
        subprocess.run(['git', '-C', str(self.root), 'add', 'data/tracked.txt'], check=True)
        local = self.root / 'data/local.txt'
        local.write_bytes(b'different local evidence')
        with self.assertRaisesRegex(SystemExit, 'Local material differs'):
            self.run_restore()
        self.assertEqual(tracked.read_bytes(), b'original tracked evidence')
        self.assertEqual(local.read_bytes(), b'different local evidence')

    def test_unsafe_member_or_include_fails_without_extracting(self):
        self.zip({'URP/data/new.txt': b'valid', 'URP/data/../../escape': b'unsafe'})
        with self.assertRaisesRegex(SystemExit, 'Unsafe archive member'):
            self.run_restore()
        self.assertFalse((self.root / 'data').exists())
        with self.assertRaises(SystemExit) as error:
            self.run_restore('--include', '../outside')
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
