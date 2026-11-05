import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('data',Path(__file__).resolve().parents[1]/'prepare_validation_data.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Response:
    url='https://example.test/file'
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,n):return b'official data'

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_download_verified_before_write(self):
        out=self.root/'file'
        with patch.object(m.urllib.request,'urlopen',return_value=Response()):
            m.fetch('https://example.test/file',out,hashlib.sha256(b'official data').hexdigest(),13)
        self.assertEqual(out.read_bytes(),b'official data')
    def test_wrong_hash_no_output(self):
        out=self.root/'file'
        with patch.object(m.urllib.request,'urlopen',return_value=Response()):
            with self.assertRaisesRegex(RuntimeError,'SHA256'):m.fetch('https://example.test/file',out,'0'*64)
        self.assertFalse(out.exists())
    def test_wrong_size_no_output(self):
        with patch.object(m.urllib.request,'urlopen',return_value=Response()):
            with self.assertRaisesRegex(RuntimeError,'size'):m.fetch('https://example.test/file',self.root/'file','0'*64,1)
    def test_plain_http_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'HTTPS'):m.fetch('http://example.test/file',self.root/'file','0'*64)
    def test_site_symlink_escape(self):
        site=self.root/'site';site.mkdir();outside=self.root/'outside';outside.write_text('x');(site/'link').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError,'escapes'):m.check_tree(site)
    def test_site_files_hashed(self):
        site=self.root/'site';site.mkdir();(site/'file').write_text('x')
        self.assertEqual(m.check_tree(site),{'file':hashlib.sha256(b'x').hexdigest()})
    def test_fresh_root_before_subprocess(self):
        with patch.object(m.subprocess,'check_output') as run:
            with self.assertRaisesRegex(RuntimeError,'fresh'):m.materialize(self.root,self.root,Path('/python'),Path('/uv'))
            run.assert_not_called()

if __name__=='__main__':unittest.main()
