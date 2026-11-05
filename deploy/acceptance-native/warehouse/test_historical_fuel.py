"""Independent adversarial tests. Archives are in-memory data, never executed."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

import historical_fuel as subject

PREFIX = 'project/setup/2026-10-15/cache/ignition-fuel/'
KEY = 'fuel.gazebosim.org/openrobotics/models/table/4/model.sdf'
DATA = b'<sdf version="1.7"/>\n'


def manifest(data=DATA):
    return {KEY: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}


def regular(name=PREFIX + KEY, data=DATA):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def archive(*members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if info.isreg() else None)
    buf.seek(0)
    return buf


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / 'private'
        self.destination.mkdir(mode=0o700)

    def extract(self, stream, selected=None):
        return subject._extract_tar(stream, self.destination, manifest() if selected is None else selected)

    def refused(self, *members, selected=None):
        with self.assertRaises(Exception):
            self.extract(archive(*members), selected)

    def test_selected_bytes_exact(self):
        self.extract(archive(regular()))
        self.assertEqual((self.destination / KEY).read_bytes(), DATA)

    def test_unknown_regular_ignored(self):
        self.extract(archive(regular(PREFIX + 'unrelated.txt', b'unrelated'), regular()))
        self.assertFalse((self.destination / 'unrelated.txt').exists())
        self.assertEqual([p for p in self.destination.rglob('*') if p.is_file()], [self.destination / KEY])

    def test_selected_data_never_executed(self):
        payload = b'#!/bin/sh\ntouch /tmp/URP_TEST_MUST_NOT_EXECUTE\n'
        with mock.patch('subprocess.Popen', side_effect=AssertionError('unexpected process')), mock.patch('os.system', side_effect=AssertionError('unexpected execution')):
            self.extract(archive(regular(data=payload)), manifest(payload))
        self.assertEqual((self.destination / KEY).read_bytes(), payload)

    def test_hash_mismatch(self):
        self.refused(regular(data=b'x' * len(DATA)))

    def test_size_mismatch(self):
        self.refused(regular(data=DATA + b'extra'))

    def test_missing_selected(self):
        self.refused(regular(PREFIX + 'irrelevant', b'x'))

    def test_duplicate_selected(self):
        self.refused(regular(), regular())

    def test_duplicate_unknown(self):
        self.refused(regular(PREFIX + 'irrelevant', b'x'), regular(PREFIX + 'irrelevant', b'x'), regular())

    def test_absolute_paths(self):
        for name in ('/' + PREFIX + KEY, '/tmp/outside-urp-test'):
            with self.subTest(name=name):
                self.refused(regular(name), regular())

    def test_traversal_paths(self):
        for name in (PREFIX + '../escape', PREFIX + 'a/../../escape', '../escape', PREFIX + 'a/../' + KEY):
            with self.subTest(name=name):
                self.refused(regular(name), regular())
        self.assertFalse((self.root / 'escape').exists())

    def test_symlink_and_hardlink_even_unselected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            info = tarfile.TarInfo(PREFIX + 'unselected-link')
            info.type = kind
            info.linkname = '../../escape'
            with self.subTest(kind=kind):
                self.refused((info, b''), regular())

    def test_special_files(self):
        for kind in (tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            info = tarfile.TarInfo(PREFIX + 'special')
            info.type = kind
            with self.subTest(kind=kind):
                self.refused((info, b''), regular())

    def test_gnu_sparse_type(self):
        info = tarfile.TarInfo(PREFIX + 'sparse')
        info.type = tarfile.GNUTYPE_SPARSE
        self.refused((info, b''), regular())

    def test_pax_sparse(self):
        info, data = regular(PREFIX + 'sparse', b'')
        info.pax_headers = {'GNU.sparse.map': '0,0', 'GNU.sparse.size': '0'}
        self.refused((info, data), regular())

    def test_oversized_declared_member(self):
        info = tarfile.TarInfo(PREFIX + 'huge')
        info.size = 1 << 40
        # Header only: must reject without trying to allocate/read the stated size.
        stream = io.BytesIO(info.tobuf() + b'\0' * 1024)
        with self.assertRaises(Exception):
            self.extract(stream)

    def test_member_count_limit(self):
        members = [regular(PREFIX + 'unknown-' + str(i), b'') for i in range(1001)]
        self.refused(*members, regular())

    def test_aggregate_declared_size_limit(self):
        # Synthetic TarFile iterator avoids allocating 250+ MiB of irrelevant data.
        members = []
        for index in range(5):
            item = tarfile.TarInfo(PREFIX + 'large-' + str(index))
            item.size = 64 * 1024 * 1024
            members.append(item)
        tar = mock.MagicMock()
        tar.__enter__.return_value = tar
        tar.__iter__.return_value = iter(members)
        with mock.patch.object(subject.tarfile, 'open', return_value=tar):
            with self.assertRaisesRegex(ValueError, 'member_rejected'):
                self.extract(io.BytesIO())
        tar.extractfile.assert_not_called()

    def test_truncated_selected_member(self):
        info, data = regular()
        with self.assertRaises(Exception):
            self.extract(io.BytesIO(info.tobuf() + data[:1]))

    def test_preexisting_symlink_destination_is_not_followed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.destination / 'fuel.gazebosim.org').symlink_to(outside, target_is_directory=True)
        self.refused(regular())
        self.assertEqual(list(outside.iterdir()), [])

    def test_unsafe_selected_key(self):
        selected = {'../escape': {'size': len(DATA), 'sha256': hashlib.sha256(DATA).hexdigest()}}
        self.refused(regular(PREFIX + '../escape'), selected=selected)
        self.assertFalse((self.root / 'escape').exists())


class PublicApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_materialize_rejects_wrong_archive(self):
        source = self.root / 'wrong.tar.zst'
        source.write_bytes(b'not the pinned archive')
        target = self.root / 'target'
        with self.assertRaises(Exception):
            subject.materialize(source, target)
        self.assertFalse(target.exists())

    def test_materialize_does_not_overwrite_existing_target(self):
        source = self.root / 'wrong.tar.zst'
        source.write_bytes(b'not the pinned archive')
        target = self.root / 'target'
        target.mkdir()
        sentinel = target / 'keep'
        sentinel.write_bytes(b'original')
        with self.assertRaises(Exception):
            subject.materialize(source, target)
        self.assertEqual(sentinel.read_bytes(), b'original')
        self.assertEqual(list(target.iterdir()), [sentinel])

    def test_materialize_rejects_wrong_hash_without_decompressing(self):
        source = self.root / 'wrong-hash.tar.zst'
        source.write_bytes(b'wrong hash')
        target = self.root / 'target'
        with mock.patch.object(subject, 'ARCHIVE_SIZE', source.stat().st_size):
            with mock.patch.object(subject.subprocess, 'Popen') as proc:
                with self.assertRaises(Exception):
                    subject.materialize(source, target)
                proc.assert_not_called()
        self.assertFalse(target.exists())

    def test_verify_detects_hash_tampering(self):
        path = self.root / KEY
        path.parent.mkdir(parents=True)
        path.write_bytes(b'x' * len(DATA))
        with mock.patch.object(subject, 'selected_files', return_value=manifest()):
            with self.assertRaises(Exception):
                subject.verify_cache(self.root)

    def test_verify_detects_size_tampering(self):
        path = self.root / KEY
        path.parent.mkdir(parents=True)
        path.write_bytes(DATA + b'x')
        with mock.patch.object(subject, 'selected_files', return_value=manifest()):
            with self.assertRaises(Exception):
                subject.verify_cache(self.root)

    def test_verify_rejects_selected_symlink(self):
        outside = self.root / 'original'
        outside.write_bytes(DATA)
        path = self.root / KEY
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        with mock.patch.object(subject, 'selected_files', return_value=manifest()):
            with self.assertRaises(Exception):
                subject.verify_cache(self.root)

    def test_verify_rejects_empty_cache(self):
        with self.assertRaises(Exception):
            subject.verify_cache(self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
