"""Stdlib-only fixture metadata; no package execution, system APT, or network."""
import ast
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'project_canonical_target.py'
spec = importlib.util.spec_from_file_location('project_canonical_target', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class TestCanonicalTarget(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.metadata = self.root / 'metadata' / ('snapshot-' + p.SNAPSHOT)
        self.metadata.mkdir(parents=True)
        self.release = self.metadata / 'jammy.InRelease'
        self.index = self.metadata / 'jammy.main.amd64.Packages.xz'
        self.reference = self.root / 'package-target-reference.json'
        self.log = self.root / 'private.log'
        self.receipt = {'schema': 1, 'code': 'APT_CLOSURE_DOWNGRADE_REFUSED',
            'operation': 'closure-provenance', 'exception_class': 'ClosureProvenanceError',
            'http_status': None, 'errno': None, 'returncode': 1, 'suite': None, 'component': None}
        self.ref = {'schema': 1, 'snapshot': p.SNAPSHOT, 'suite': 'jammy', 'component': 'main', 'stanza': 0}
        self.data = (b'Package: signed-package\nVersion: 1:2.3-1~22.04.1\nArchitecture: amd64\n\n'
                     b'Package: other-package\nVersion: 3.4+dfsg-2\nArchitecture: all\n\n')
        self.anchor_hash, self.anchor_size, self.counts, self.sizes = {}, {}, {}, {}
        for name, value in (('RELEASE_HASHES', self.anchor_hash), ('RELEASE_SIZES', self.anchor_size),
                            ('INDEX_STANZA_COUNTS', self.counts), ('INDEX_UNCOMPRESSED_SIZES', self.sizes)):
            patcher = mock.patch.object(p, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.set_metadata(self.data)
        self.set_reference(self.ref)

    def set_metadata(self, data, count=2, decoded_size=None, decoded_hash=None):
        compressed = lzma.compress(data)
        size = len(data) if decoded_size is None else decoded_size
        digest = hashlib.sha256(data).hexdigest() if decoded_hash is None else decoded_hash
        release = ('-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n'
            'Origin: Ubuntu\nSuite: jammy\nCodename: jammy\nMD5Sum:\nSHA256:\n'
            f' {hashlib.sha256(compressed).hexdigest()} {len(compressed)} main/binary-amd64/Packages.xz\n'
            f' {digest} {size} main/binary-amd64/Packages\n'
            '-----BEGIN PGP SIGNATURE-----\nfixture\n').encode()
        self.release.write_bytes(release); self.index.write_bytes(compressed)
        self.anchor_hash['jammy'] = hashlib.sha256(release).hexdigest()
        self.anchor_size['jammy'] = len(release)
        self.counts[('jammy', 'main')] = count
        self.sizes[('jammy', 'main')] = size

    def set_reference(self, ref, bind=True):
        data = (json.dumps(ref, sort_keys=True, separators=(',', ':')) + '\n').encode()
        self.reference.write_bytes(data)
        if bind:
            self.bind(data)

    def bind(self, data):
        self.log.write_text(p.REFERENCE_PREFIX + hashlib.sha256(data).hexdigest() + '\n' +
            p.DIAGNOSTIC_PREFIX + json.dumps(self.receipt, sort_keys=True, separators=(',', ':')) + '\n')

    def project(self):
        return p.project(self.root, self.receipt, self.log)

    def test_exact_canonical_output_only(self):
        self.assertEqual(self.project(), {'canonical_package': 'signed-package',
            'canonical_version': '1:2.3-1~22.04.1', 'canonical_architecture': 'amd64',
            'target_snapshot': p.SNAPSHOT, 'target_suite': 'jammy', 'target_component': 'main', 'target_stanza': 0})
        self.assertEqual(set(self.project()), p.OUTPUT_KEYS)

    def test_architecture_all_comes_from_index_not_native_architecture(self):
        self.set_reference(dict(self.ref, stanza=1))
        self.assertEqual(self.project()['canonical_architecture'], 'all')
        self.assertEqual(self.project()['canonical_package'], 'other-package')

    def test_changed_valid_ordinal_is_rejected_without_terminal_hash_match(self):
        self.set_reference(dict(self.ref, stanza=1), bind=False)
        self.assertEqual(self.project(), {})

    def test_closed_reference_schema_and_finite_coordinates(self):
        invalid = [dict(self.ref, schema=True), dict(self.ref, schema=2), dict(self.ref, stanza=True),
            dict(self.ref, stanza=-1), dict(self.ref, stanza=2), dict(self.ref, stanza=0.0),
            dict(self.ref, snapshot='SECRET'), dict(self.ref, suite='SECRET'),
            dict(self.ref, component='../private'), dict(self.ref, canonical_package='SECRET'),
            dict(self.ref, stanza={'SECRET': 0}), {k:v for k,v in self.ref.items() if k != 'suite'}, []]
        for ref in invalid:
            with self.subTest(ref=ref):
                self.set_reference(ref)
                self.assertEqual(self.project(), {})

    def test_duplicate_reference_keys_rejected_even_when_hash_matches(self):
        data = json.dumps(self.ref)[:-1].encode() + b', "stanza": 1}'
        self.reference.write_bytes(data); self.bind(data)
        self.assertEqual(self.project(), {})

    def test_oversized_reference_rejected_even_when_hash_matches(self):
        data = json.dumps(self.ref).encode() + b' ' * 512
        self.reference.write_bytes(data); self.bind(data)
        self.assertEqual(self.project(), {})

    def test_unknown_or_changed_receipt_cannot_select_target(self):
        good = dict(self.receipt)
        changes = [('schema', True), ('schema', 2), ('code', 'SECRET'),
            ('operation', 'provider-selection'), ('exception_class', 'RuntimeError'),
            ('http_status', 403), ('errno', 2), ('suite', 'jammy'), ('component', 'main'),
            ('returncode', None), ('returncode', 0), ('returncode', True), ('returncode', 256)]
        for key,value in changes:
            with self.subTest(key=key, value=value):
                self.receipt = dict(good, **{key:value}); self.bind(self.reference.read_bytes())
                self.assertEqual(self.project(), {})
        self.receipt = dict(good, SECRET='private'); self.bind(self.reference.read_bytes())
        self.assertEqual(self.project(), {})

    def test_comparison_error_returncode_is_preserved_without_versions(self):
        self.receipt['returncode'] = 2
        self.bind(self.reference.read_bytes())
        self.assertEqual(self.project()['canonical_package'], 'signed-package')

    def test_terminal_receipt_must_equal_supplied_receipt(self):
        self.receipt['returncode'] = 2
        self.assertEqual(self.project(), {})

    def test_pair_must_be_adjacent_and_terminal(self):
        good = self.log.read_text(); first, second = good.splitlines()
        for text in (first + '\nSECRET\n' + second + '\n', good + 'SECRET\n', good + '\n',
                     second + '\n' + first + '\n', second + '\n', good.rstrip('\n')):
            with self.subTest(text=text):
                self.log.write_text(text)
                self.assertEqual(self.project(), {})

    def test_duplicate_terminal_json_key_rejected(self):
        text = self.log.read_text().rstrip('\n')[:-1] + ',"returncode":1}\n'
        self.log.write_text(text)
        self.assertEqual(self.project(), {})

    def test_secret_log_prefix_never_becomes_output(self):
        self.log.write_text(('SECRET /private https://example.invalid\n' * 300) + self.log.read_text())
        result = self.project()
        self.assertEqual(result['canonical_package'], 'signed-package')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_changed_inrelease_bytes_fail_before_index_decode(self):
        raw = self.release.read_bytes()
        self.release.write_bytes(raw.replace(b'Ubuntu', b'SECRET', 1))
        with mock.patch.object(p.lzma, 'LZMADecompressor') as decoder:
            self.assertEqual(self.project(), {}); decoder.assert_not_called()

    def test_changed_index_bytes_fail_before_decode(self):
        raw = self.index.read_bytes()
        self.index.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with mock.patch.object(p.lzma, 'LZMADecompressor') as decoder:
            self.assertEqual(self.project(), {}); decoder.assert_not_called()

    def test_wrong_index_size_fails(self):
        self.index.write_bytes(self.index.read_bytes() + b'x')
        self.assertEqual(self.project(), {})

    def test_uncompressed_hash_mismatch_fails(self):
        self.set_metadata(self.data, decoded_hash='0' * 64)
        self.assertEqual(self.project(), {})

    def test_decompression_is_bounded_by_signed_size(self):
        self.set_metadata(self.data, decoded_size=len(self.data) - 1)
        self.assertEqual(self.project(), {})

    def test_stanza_count_must_equal_fixed_reviewed_count(self):
        self.counts[('jammy', 'main')] = 3
        self.assertEqual(self.project(), {})

    def test_unknown_target_or_unbounded_fields_fail(self):
        for first in (b'Package: signed-package\nArchitecture: amd64\n',
                      b'Package: SECRET/path\nVersion: 1\nArchitecture: amd64\n',
                      b'Package: signed-package\nVersion: SECRET VERSION\nArchitecture: amd64\n',
                      b'Package: signed-package\nVersion: 1\nArchitecture: i386\n',
                      b'Package: ' + b'a' * 61 + b'\nVersion: 1\nArchitecture: amd64\n',
                      b'Package: signed-package\nVersion: 1' + b'0' * 54 + b'\nArchitecture: amd64\n',
                      b'Package: signed-package\nVersion: 1\nVersion: 2\nArchitecture: amd64\n'):
            with self.subTest(first=first):
                self.set_metadata(first + b'\n', count=1)
                self.assertEqual(self.project(), {})

    def test_no_symlink_or_hardlink_input_is_accepted(self):
        for path in (self.reference, self.log, self.release, self.index):
            original = path.read_bytes(); backup = path.with_name(path.name + '.backup')
            for kind in ('symlink', 'hardlink'):
                with self.subTest(path=path.name, kind=kind):
                    path.rename(backup)
                    if kind == 'symlink': path.symlink_to(backup)
                    else: os.link(backup, path)
                    self.assertEqual(self.project(), {})
                    path.unlink(); backup.rename(path)
            self.assertEqual(path.read_bytes(), original)

    def test_parent_directory_alias_is_rejected(self):
        alias = self.root.parent / (self.root.name + '-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        self.assertEqual(p.project(alias, self.receipt, self.log), {})

    def test_fifo_and_directory_are_rejected_without_waiting(self):
        self.reference.unlink(); os.mkfifo(self.reference)
        self.assertEqual(self.project(), {})
        self.reference.unlink(); self.reference.mkdir()
        self.assertEqual(self.project(), {})

    def test_missing_artifacts_are_diagnostic_only(self):
        self.reference.unlink()
        self.assertEqual(self.project(), {})
        self.assertEqual(p.project(self.root / 'missing', self.receipt, self.log), {})


class TestCanonicalProjectorSource(unittest.TestCase):
    def test_concatenated_xz_and_empty_trailing_stream(self):
        self.assertEqual(p._decompress(lzma.compress(b'a') + lzma.compress(b'b'), 2), b'ab')
        self.assertEqual(p._decompress(lzma.compress(b'ab') + lzma.compress(b''), 2), b'ab')

    def test_concatenation_cannot_exceed_total_size_or_stream_bound(self):
        for data, size in ((lzma.compress(b'ab') + lzma.compress(b'c'), 2),
                           (lzma.compress(b'') * 9, 0)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                p._decompress(data, size)

    def test_invalid_or_truncated_trailing_xz_is_not_ignored(self):
        for suffix in (b'SECRET', lzma.compress(b'b')[:-1]):
            with self.subTest(suffix=suffix), self.assertRaises((ValueError, lzma.LZMAError)):
                p._decompress(lzma.compress(b'a') + suffix, 2)

    def test_self_contained_python310_syntax_without_native_or_network(self):
        tree = ast.parse(SCRIPT.read_text(), feature_version=(3, 10))
        modules = {item.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names}
        self.assertFalse(modules & {'subprocess', 'urllib', 'socket', 'prepare_jammy_packages'})

    def test_reviewed_release_anchors_and_counts_match_producer(self):
        source = SCRIPT.with_name('prepare_jammy_packages.py')
        spec = importlib.util.spec_from_file_location('producer_anchor_fixture', source)
        producer = importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
        for key in ('SNAPSHOT', 'RELEASE_HASHES', 'RELEASE_SIZES', 'INDEX_STANZA_COUNTS'):
            self.assertEqual(getattr(p, key), getattr(producer, key))


if __name__ == '__main__':
    unittest.main()
