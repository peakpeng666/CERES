"""Asset indexes must retain verified copies and reject a copy for different source bytes."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('index_assets', Path(__file__).resolve().parents[1] / 'index_assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class AssetCopyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.payload = self.repo / 'experiment.zip'
        self.payload.write_bytes(b'controlled archive bytes')
        for command in [ ['init', '-q'], ['add', '.'],
                         ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture'] ]:
            subprocess.run(['git', *command], cwd=self.repo, check=True)
        self.oid = subprocess.check_output(['git', 'rev-parse', 'HEAD:experiment.zip'], cwd=self.repo, text=True).strip()
        self.registry = self.root / 'copies.json'
        self.copy = {'asset_path': 'experiment.zip', 'state': 'restore-verified',
                     'url': 'https://drive.example/verified', 'git_blob_sha1': self.oid,
                     'sha256': hashlib.sha256(self.payload.read_bytes()).hexdigest(), 'restore_evidence': 'fixture roundtrip'}

    def generate(self, copy):
        self.registry.write_text(json.dumps({'copies': [copy]}))
        output = self.root / 'index'
        with patch.object(assets, 'ROOT', self.repo):
            assets.index('HEAD', output, self.registry)
        with (output / 'assets.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        return rows, json.loads((output / 'inventory.json').read_text())

    def test_reindex_retains_verified_copy_without_calling_it_migrated(self):
        rows, summary = self.generate(self.copy)
        self.assertEqual(rows[0]['external_copy'], 'copies.json#/copies/0')
        self.assertNotIn(self.copy['url'], json.dumps(rows))
        self.assertEqual(rows[0]['external_sha256'], self.copy['sha256'])
        self.assertEqual(summary['verified_external_copies'], 1)
        self.assertEqual(summary['migrated_assets'], 0)
        self.assertEqual(summary['third_party_directories'], 0)

    def test_metadata_only_copy_is_not_promoted_to_verified(self):
        rows, summary = self.generate({**self.copy, 'state': 'uploaded-not-restored'})
        self.assertEqual(rows[0]['external_copy'], '')
        self.assertEqual(summary['external_copy_candidates'], 1)
        self.assertEqual(summary['verified_external_copies'], 0)

    def test_wrong_source_or_missing_restore_evidence_is_rejected(self):
        for field, value in [('git_blob_sha1', '0' * 40), ('restore_evidence', '')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.generate({**self.copy, field: value})
            output = self.root / 'index'
            if output.exists():
                self.assertEqual(list(output.iterdir()), [])
                output.rmdir()

    def space_record(self, **updates):
        return {**self.copy, 'expected_git_blob_sha1': self.oid, 'exact_path_blob_match': True, **updates}

    def write_restoration(self, records):
        path = self.repo / 'docs/assets/restoration.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema': 'urp.asset-restoration/1',
                                   'source_kind': 'independent owner-held Space copies',
                                   'current_report_assets': records,
                                   'historical_supplement': {'pdfs': 21}}))

    def test_union_keeps_provider_counts_and_redacts_private_locators(self):
        self.write_restoration([self.space_record(private_locator='https://private.example/secret')])
        rows, summary = self.generate(self.copy)
        self.assertEqual(summary['verified_external_copies'], 2)
        self.assertEqual(summary['verified_current_assets'], 1)
        self.assertEqual(summary['verified_external_copies_by_provider'], {'drive': 1, 'space': 1})
        self.assertEqual(summary['migrated_assets'], 0)
        self.assertEqual(summary['asset_counts'], {'archive': 1})
        self.assertEqual(summary['known_migration_events'], 0)
        self.assertFalse(summary['migration_event_schema_supported'])
        self.assertEqual(rows[0]['verified_providers'], 'drive;space')
        self.assertNotIn('https://', json.dumps(rows) + json.dumps(summary))

    def test_space_does_not_promote_drive_metadata(self):
        self.write_restoration([self.space_record()])
        rows, summary = self.generate({**self.copy, 'state': 'existing-unverified'})
        self.assertEqual(summary['verified_external_copies_by_provider'], {'drive': 0, 'space': 1})
        self.assertEqual(summary['verified_external_copies'], 1)

    def test_unclassified_drive_fetch_error_does_not_inherit_space_success(self):
        self.write_restoration([self.space_record()])
        rows, summary = self.generate({**self.copy, 'state': 'fetch-error-unclassified',
                                       'downloaded': False, 'permission_denial_confirmed': False})
        self.assertEqual(summary['verified_external_copies_by_provider'], {'drive': 0, 'space': 1})
        self.assertEqual(rows[0]['verified_providers'], 'space')
        self.assertEqual(summary['migrated_assets'], 0)

    def test_wrong_blob_or_sha_cannot_inherit_restoration(self):
        for update in ({'git_blob_sha1': '0' * 40}, {'sha256': '0' * 64}, {'exact_path_blob_match': False}):
            self.write_restoration([self.space_record(**update)])
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.generate({**self.copy, 'state': 'existing-unverified'})

    def test_absent_path_does_not_infer_migration_or_current_copy(self):
        self.write_restoration([self.space_record(asset_path='old-name.zip')])
        rows, summary = self.generate({**self.copy, 'asset_path': 'old-name.zip'})
        self.assertEqual(summary['verified_external_copies'], 0)
        self.assertEqual(summary['migrated_assets'], 0)
        self.assertEqual(summary['asset_counts'], {'archive': 1})
        self.assertEqual(summary['known_migration_events'], 0)
        self.assertFalse(summary['migration_event_schema_supported'])

    def test_tar_xz_results_are_indexed_without_inferring_restoration(self):
        names = ('cloud-results', 'native-results', 'ubuntu-local-seed1-results',
                 'ubuntu-local-seeds3-4-results')
        for name in names:
            path = self.repo / f'experiments/baselines/cvrp/raw/{name}.tar.xz'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'controlled result archive')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True)
        subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'result archives'], cwd=self.repo, check=True)
        rows, summary = self.generate({**self.copy, 'state': 'existing-unverified'})
        results = [row for row in rows if row['kind'] == 'experiment-archive']
        self.assertEqual(len(results), 4)
        self.assertEqual({row['path'] for row in results},
                         {f'experiments/baselines/cvrp/raw/{name}.tar.xz' for name in names})
        self.assertTrue(all(row['external_copy'] == '' for row in results))
        self.assertEqual(summary['asset_counts'], {'archive': 1, 'experiment-archive': 4})
        self.assertEqual(summary['verified_external_copies'], 0)
        self.assertEqual(summary['migrated_assets'], 0)

    def test_current_repository_has_four_assets_and_six_provider_copies(self):
        root = Path(__file__).resolve().parents[2]
        entries = assets.tree('HEAD')
        copies, records = assets.verified_copies(root / 'docs/assets/copies.json')
        joined = assets.restoration_view(entries, copies, records, root / 'docs/assets/restoration.json')
        self.assertEqual(len(joined), 6)
        self.assertEqual({provider: sum(row['provider'] == provider for row in joined)
                          for provider in ('drive', 'space')}, {'drive': 2, 'space': 4})
        self.assertEqual(len({r['asset_path'] for r in joined}), 4)
        self.assertEqual({row['asset_path'] for row in joined if row['provider'] == 'drive'},
                         {'docs/reports/URP研究进展简报.pdf', 'docs/reports/URP研究进展简报_LaTeX源码.zip'})


if __name__ == '__main__':
    unittest.main()
