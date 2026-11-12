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
import warnings
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
        self.archive = Path(temporary.name) / 'CERES.zip'

    def zip(self, files):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.archive, 'w') as archive:
                for name, content in files.items() if isinstance(files, dict) else files:
                    archive.writestr(name, content)
        self.refresh_manifest()

    def refresh_manifest(self):
        self.record = {'schema': 'urp.materials-archive/1', 'archive': {
            'bytes': self.archive.stat().st_size,
            'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}}
        self.manifest.write_text(json.dumps(self.record))

    def run_restore(self, *args):
        output = io.StringIO()
        try:
            with patch.object(restore, 'ROOT', self.root), patch('sys.argv', ['restore_materials.py', str(self.archive), *map(str, args)]), redirect_stdout(output), redirect_stderr(output):
                restore.main()
        finally:
            self.last_output = output.getvalue()
        return output.getvalue()

    def test_default_manifest_lists_then_restores_without_storage_archive_key(self):
        self.zip({'CERES/data/new.txt': b'material', 'CERES/README.md': b'outside scope',
                  'CERES/data/.venv/cache': b'skip'})
        summary = json.loads(self.run_restore('--list'))
        self.assertEqual(summary, {'files': 1, 'bytes': 8, 'archive_sha256': 'PASS',
                                   'matched_files': 1, 'existing_files': 0,
                                   'tracked_files_skipped': 0, 'status': 'planned'})
        self.assertFalse((self.root / 'data').exists())
        self.assertIn('Materials restored', self.run_restore())
        self.assertEqual((self.root / 'data/new.txt').read_bytes(), b'material')
        self.assertFalse((self.root / 'README.md').exists())
        self.assertEqual(json.loads(self.run_restore('--list'))['files'], 0)

    def test_explicit_manifest_can_select_a_separate_historical_fingerprint(self):
        self.zip({'CERES/papers/example.pdf': b'fixture'})
        explicit = self.manifest.with_name('explicit.json')
        self.manifest.rename(explicit)
        self.assertEqual(json.loads(self.run_restore('--manifest', explicit, '--list'))['files'], 1)

    def test_missing_or_invalid_manifest_fails_before_writing(self):
        self.zip({'CERES/data/new.txt': b'fixture'})
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
        self.zip({'CERES/data/new.txt': b'fixture'})
        for field, value in [('bytes', self.record['archive']['bytes'] + 1), ('sha256', '0' * 64)]:
            with self.subTest(field=field):
                document = {**self.record, 'archive': {**self.record['archive'], field: value}}
                self.manifest.write_text(json.dumps(document))
                with self.assertRaisesRegex(SystemExit, 'differs from the historical manifest'):
                    self.run_restore('--list')
                self.assertFalse((self.root / 'data').exists())

    def test_tracked_and_conflicting_local_materials_are_never_overwritten(self):
        self.zip({'CERES/data/tracked.txt': b'archive', 'CERES/data/local.txt': b'archive'})
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
        self.zip({'CERES/data/new.txt': b'valid', 'CERES/data/../../escape': b'unsafe'})
        with self.assertRaisesRegex(SystemExit, 'Unsafe archive member'):
            self.run_restore()
        self.assertFalse((self.root / 'data').exists())
        with self.assertRaises(SystemExit) as error:
            self.run_restore('--include', '../outside')
        self.assertEqual(error.exception.code, 2)

    def test_missing_root_empty_archive_and_unmatched_selection_are_errors(self):
        for members, args, message in [
                ({'OTHER/data/material.txt': b'wrong root'}, (), 'no CERES/ root'),
                ({}, (), 'no CERES/ root'),
                ({'CERES': b'not a directory', 'CERES/data/material.txt': b'data'}, (), 'CERES archive root is not a directory'),
                ({'CERES/': b''}, (), 'No material files match'),
                ({'CERES/README.md': b'no material'}, (), 'No material files match'),
                ({'CERES/data': b'not a material directory'}, (), 'Material root must be a directory'),
                ({'CERES/data/material.txt': b'data'}, ('--include', 'papers'), 'No material files match')]:
            with self.subTest(members=list(members), args=args):
                self.zip(members)
                with self.assertRaisesRegex(SystemExit, message):
                    self.run_restore(*args)
                self.assertFalse((self.root / 'data').exists())
                self.assertNotIn('Materials restored', self.last_output)

    def test_already_present_is_reported_without_claiming_a_restore(self):
        self.zip({'CERES/data/existing.txt': b'identical'})
        destination = self.root / 'data/existing.txt'
        destination.parent.mkdir()
        destination.write_bytes(b'identical')
        self.assertEqual(json.loads(self.run_restore('--list'))['status'], 'already-present')
        output = self.run_restore()
        self.assertNotIn('Materials restored', output)
        self.assertIn('Materials already present', output)
        self.assertEqual(destination.read_bytes(), b'identical')

    def test_only_tracked_files_are_not_reported_as_present_or_restored(self):
        self.zip({'CERES/data/tracked.txt': b'archive'})
        path = self.root / 'data/tracked.txt'
        path.parent.mkdir()
        path.write_bytes(b'tracked')
        subprocess.run(['git', '-C', str(self.root), 'add', 'data/tracked.txt'], check=True)
        path.unlink()
        summary = json.loads(self.run_restore('--list'))
        self.assertEqual(summary['status'], 'tracked-only')
        self.assertEqual(summary['tracked_files_skipped'], 1)
        self.assertEqual(summary['existing_files'], 0)
        self.assertNotIn('Materials restored', self.run_restore())
        self.assertFalse(path.exists())

    def test_duplicate_and_normalized_alias_targets_fail_before_any_writes(self):
        for index, duplicate in enumerate(('CERES/data/repeat.txt', 'CERES/data/./repeat.txt')):
            with self.subTest(duplicate=duplicate):
                self.zip([(f'CERES/data/first-{index}.txt', b'must not be written'),
                          ('CERES/data/repeat.txt', b'first'), (duplicate, b'second')])
                with self.assertRaisesRegex(SystemExit, 'Duplicate material target'):
                    self.run_restore()
                self.assertFalse((self.root / 'data').exists())

    def test_archive_file_directory_conflict_is_rejected_before_writes(self):
        self.zip({'CERES/data/first.txt': b'must not be written',
                  'CERES/data/parent': b'file', 'CERES/data/parent/child.txt': b'child'})
        with self.assertRaisesRegex(SystemExit, 'Archive file conflicts with a child path'):
            self.run_restore()
        self.assertFalse((self.root / 'data').exists())

    def test_existing_parent_file_conflict_is_rejected_before_writes(self):
        self.zip({'CERES/data/first.txt': b'must not be written', 'CERES/data/parent/child.txt': b'child'})
        parent = self.root / 'data/parent'
        parent.parent.mkdir()
        parent.write_bytes(b'local evidence')
        with self.assertRaisesRegex(SystemExit, 'Local parent is not a directory'):
            self.run_restore()
        self.assertEqual(parent.read_bytes(), b'local evidence')
        self.assertFalse((self.root / 'data/first.txt').exists())

    def test_existing_symlink_components_are_rejected_in_real_directories(self):
        data = self.root / 'data'
        data.mkdir()
        external = self.root.parent / 'outside'
        external.mkdir()
        evidence = self.root / 'tracked-evidence.txt'
        evidence.write_bytes(b'original evidence')
        subprocess.run(['git', '-C', str(self.root), 'add', evidence.name], check=True)
        targets = [(self.root / '.git/hooks/ceres-restore-fixture', False),
                   (self.root / '.git/hooks', True), (external, True),
                   (evidence, False), (self.root / 'setup', True)]
        for index, (target, directory) in enumerate(targets):
            with self.subTest(target_kind=index):
                link = data / f'link-{index}'
                link.symlink_to(target, target_is_directory=directory)
                relative = f'data/{link.name}/new.txt' if directory else f'data/{link.name}'
                self.zip({f'CERES/data/first-{index}.txt': b'must not be written',
                          f'CERES/{relative}': b'archive content'})
                with self.assertRaisesRegex(SystemExit, 'Symbolic link in material destination'):
                    self.run_restore()
                self.assertFalse((data / f'first-{index}.txt').exists())
                self.assertEqual(evidence.read_bytes(), b'original evidence')
        self.assertFalse((self.root / '.git/hooks/ceres-restore-fixture').exists())
        self.assertFalse((self.root / '.git/hooks/new.txt').exists())
        self.assertFalse((external / 'new.txt').exists())
        self.assertFalse((self.root / 'setup/new.txt').exists())

    def test_archive_symlink_is_rejected_before_writes(self):
        link = zipfile.ZipInfo('CERES/data/link')
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        self.zip([('CERES/data/first.txt', b'must not be written'), (link, b'../../.git/hooks/fixture')])
        with self.assertRaisesRegex(SystemExit, 'Symbolic link archive material'):
            self.run_restore()
        self.assertFalse((self.root / 'data').exists())

    def test_file_created_after_preflight_is_preserved(self):
        self.zip({'CERES/data/new.txt': b'archive'})
        destination = self.root / 'data/new.txt'
        original_open = Path.open
        injected = False

        def concurrent_open(path, mode='r', *args, **kwargs):
            nonlocal injected
            if path == destination and mode in ('wb', 'xb') and not injected:
                injected = True
                with original_open(path, 'wb') as stream:
                    stream.write(b'concurrent evidence')
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', concurrent_open), self.assertRaisesRegex(SystemExit, 'Restore failed after 0 completed files'):
            self.run_restore()
        self.assertEqual(destination.read_bytes(), b'concurrent evidence')
        self.assertNotIn('Materials restored', self.last_output)

    def test_corrupt_payload_reports_partial_failure_without_overwriting_evidence(self):
        payload = b'payload with deliberately invalid CRC'
        self.zip({'CERES/data/first.txt': b'complete', 'CERES/data/bad.txt': payload})
        raw = self.archive.read_bytes()
        self.assertEqual(raw.count(payload), 1)
        self.archive.write_bytes(raw.replace(payload, b'X' + payload[1:], 1))
        self.refresh_manifest()
        evidence = self.root / 'data/evidence.txt'
        evidence.parent.mkdir()
        evidence.write_bytes(b'original local evidence')
        with self.assertRaisesRegex(SystemExit, 'Restore failed after 1 completed files') as error:
            self.run_restore()
        self.assertIn('partial file may remain', str(error.exception))
        self.assertEqual((self.root / 'data/first.txt').read_bytes(), b'complete')
        self.assertEqual(evidence.read_bytes(), b'original local evidence')
        self.assertNotIn('Materials restored', self.last_output)
        self.assertNotIn('PASS', self.last_output)

    def test_archive_path_replacement_cannot_change_the_verified_open_file(self):
        self.zip({'CERES/data/verified.txt': b'verified payload'})
        replacement = self.archive.with_name('replacement.zip')
        with zipfile.ZipFile(replacement, 'w') as archive:
            archive.writestr('CERES/data/replacement.txt', b'unverified replacement')
        original_zip = zipfile.ZipFile

        def replace_after_verification(source, *args, **kwargs):
            self.archive.rename(self.archive.with_name('verified-original.zip'))
            replacement.rename(self.archive)
            return original_zip(source, *args, **kwargs)

        with patch.object(restore.zipfile, 'ZipFile', replace_after_verification):
            self.run_restore()
        self.assertEqual((self.root / 'data/verified.txt').read_bytes(), b'verified payload')
        self.assertFalse((self.root / 'data/replacement.txt').exists())


if __name__ == '__main__':
    unittest.main()
