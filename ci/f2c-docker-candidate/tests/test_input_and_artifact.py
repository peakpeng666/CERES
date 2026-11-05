import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine_gate import PROTOCOL
from freeze_input_index import freeze
from engine_controller import verify_inputs
from stage_build_output import stage

class InputTests(unittest.TestCase):
    def test_authored_internal_directory_and_dangling_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp)
            for n in ('engine_gate.py','offline_build.py'):(r/n).write_text('fixture')
            (r/'directory').mkdir();(r/'alias').symlink_to('directory',target_is_directory=True)
            (r/'dangling').symlink_to('missing')
            anchor=freeze(r,'build');verify_inputs(r,anchor)
            (r/'dangling').unlink();(r/'dangling').symlink_to('other')
            with self.assertRaises(RuntimeError):verify_inputs(r,anchor)
    def test_external_alias_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'bad').symlink_to('/etc/passwd')
            with self.assertRaises(RuntimeError):freeze(r,'build')
    def tar(self,path,bad=False):
        data=b'wheel-fixture';name='build/wheels/f2c.whl'
        result={'status':'built_not_accepted','protocol':PROTOCOL,'application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','python_version':'3.12.13','wheel':{'path':name,'sha256':hashlib.sha256(data).hexdigest()}}
        with tarfile.open(path,'w') as t:
            directory=tarfile.TarInfo('./build/wheels/');directory.type=tarfile.DIRTYPE;t.addfile(directory)
            for name,data in [('build-result.json',json.dumps(result).encode()),(name,data)]:
                m=tarfile.TarInfo('./'+name);m.size=len(data)
                if bad and name.endswith('.whl'):m.type=tarfile.SYMTYPE;m.linkname='/etc/passwd';m.size=0
                t.addfile(m,io.BytesIO(data) if m.isreg() else None)
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def test_extract_only_regular_known_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);i=r/'inputs';i.mkdir();t=r/'out.tar';anchor=self.tar(t)
            stage(t,i,anchor);self.assertEqual((i/'build/wheels/f2c.whl').read_bytes(),b'wheel-fixture')
    def test_wheel_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);i=r/'inputs';i.mkdir();t=r/'out.tar';anchor=self.tar(t,True)
            with self.assertRaises(RuntimeError):stage(t,i,anchor)
if __name__=='__main__':unittest.main()
