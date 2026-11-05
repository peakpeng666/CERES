import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('compare_interfaces', Path(__file__).with_name('compare_interfaces.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def archive(self, members):
        p = self.root / 'source.tar.gz'
        with tarfile.open(p, 'w:gz') as archive:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return p, hashlib.sha256(p.read_bytes()).hexdigest()
    def test_hashes_headers_not_native(self):
        p, h = self.archive([('root/include/ortools/test.h', b'header'), ('root/lib/libortools.so', b'not executed')])
        self.assertEqual(module.inspect(p,h), {'include/ortools/test.h':hashlib.sha256(b'header').hexdigest()})
    def test_bad_archive_hash(self):
        p, h = self.archive([('root/include/ortools/test.h', b'header')])
        with self.assertRaisesRegex(ValueError,'SHA256'): module.inspect(p,'0'*64)
    def test_traversal(self):
        p, h = self.archive([('root/../escape', b'bad')])
        with self.assertRaisesRegex(ValueError,'Unsafe'): module.inspect(p,h)
    def test_duplicate(self):
        p, h = self.archive([('root/include/ortools/test.h', b'one'), ('root/include/ortools/test.h', b'two')])
        with self.assertRaisesRegex(ValueError,'Unexpected'): module.inspect(p,h)
    def test_missing_headers(self):
        p, h = self.archive([('root/lib/libortools.so', b'not executed')])
        with self.assertRaisesRegex(ValueError,'Missing'): module.inspect(p,h)
    def test_archive_symlink(self):
        p, h = self.archive([('root/include/ortools/test.h', b'header')])
        link = self.root / 'link'; link.symlink_to(p)
        with self.assertRaisesRegex(ValueError,'symbolic'): module.inspect(link,h)

if __name__ == '__main__': unittest.main()
