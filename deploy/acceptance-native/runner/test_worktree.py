import os,pathlib,subprocess,tempfile,unittest
from runner.verify_worktree import verify
class WorktreeTests(unittest.TestCase):
    def test_crlf_sparse_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=pathlib.Path(tmp)
            def git(*args):return subprocess.check_output(['git','-C',tmp,*args],stderr=subprocess.DEVNULL)
            git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid');git('config','core.autocrlf','false')
            (r/'vendor.txt').write_bytes(b'one\r\ntwo\r\n');(r/'excluded.txt').write_text('sparse data')
            git('add','vendor.txt','excluded.txt');git('commit','-m','raw CRLF')
            (r/'.gitattributes').write_text('*.txt text eol=lf\n');git('add','.gitattributes');git('commit','-m','attributes')
            sha=git('rev-parse','HEAD').decode().strip()
            self.assertIn(b'vendor.txt',git('status','--porcelain'))
            self.assertEqual(verify(r,sha)['tracked_blobs_verified'],3)
            git('update-index','--skip-worktree','excluded.txt');(r/'excluded.txt').unlink()
            self.assertEqual(verify(r,sha),{'tracked_blobs_verified':2,'sparse_entries_skipped':1})
            (r/'vendor.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError,'tracked_blob_mismatch'):verify(r,sha)
if __name__=='__main__':unittest.main()
