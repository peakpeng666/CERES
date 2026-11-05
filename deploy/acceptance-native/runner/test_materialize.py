import pathlib,subprocess,tempfile,unittest
from runner.materialize_checkout import materialize
class MaterializeTests(unittest.TestCase):
    def fixture(self,r):
        def git(*args):return subprocess.check_output(['git','-C',str(r),*args],stderr=subprocess.DEVNULL)
        git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid');git('config','core.autocrlf','false')
        (r/'a.ps1').write_bytes(b'hello\nworld\n');git('add','a.ps1');git('commit','-m','LF')
        (r/'.gitattributes').write_text('*.ps1 text eol=crlf\n');git('add','.gitattributes');git('commit','-m','eol')
        sha=git('rev-parse','HEAD').decode().strip();(r/'a.ps1').unlink();git('checkout','--','a.ps1');return git,sha
    def test_declared_eol_preserves_index_mode_attributes(self):
        with tempfile.TemporaryDirectory() as d:
            r=pathlib.Path(d);git,sha=self.fixture(r);before=git('ls-files','--stage');mode=(r/'a.ps1').stat().st_mode;attrs=(r/'.gitattributes').read_bytes()
            self.assertEqual((r/'a.ps1').read_bytes(),b'hello\r\nworld\r\n')
            self.assertEqual(materialize(r,sha)['declared_eol_materializations'],1)
            self.assertEqual((r/'a.ps1').read_bytes(),b'hello\nworld\n');self.assertEqual(before,git('ls-files','--stage'));self.assertEqual(mode,(r/'a.ps1').stat().st_mode);self.assertEqual(attrs,(r/'.gitattributes').read_bytes())
    def test_unknown_change_and_mode_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r=pathlib.Path(d);git,sha=self.fixture(r);p=r/'a.ps1';original=p.read_bytes();p.write_bytes(original+b'tamper')
            with self.assertRaisesRegex(ValueError,'unknown_byte_difference'):materialize(r,sha)
            p.write_bytes(original);p.chmod(0o755)
            with self.assertRaisesRegex(ValueError,'tracked_mode_mismatch'):materialize(r,sha)
    def test_symlink_substitution_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r=pathlib.Path(d);git,sha=self.fixture(r);(r/'a.ps1').unlink();(r/'a.ps1').symlink_to('.gitattributes')
            with self.assertRaisesRegex(ValueError,'tracked_mode_mismatch'):materialize(r,sha)
if __name__=='__main__':unittest.main()
