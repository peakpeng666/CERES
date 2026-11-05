import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine_gate import PROTOCOL, validate_inside
from engine_controller import create_argv,check_inspect,verify_inputs

class EngineTests(unittest.TestCase):
    def launch(self):
        return dict(protocol=PROTOCOL,uid=1001,gid=1001,host_boot_id='fixture',host_netns='net:[1]',image_id='sha256:'+'a'*64,input_anchor='b'*64,nonce='c'*64,out_bytes=1024**3,memory_bytes=2*1024**3)
    def observed(self):
        return dict(protocol=PROTOCOL,uids=[1001]*4,gids=[1001]*4,boot_id='fixture',netns='net:[2]',caps=dict.fromkeys(('CapEff','CapPrm','CapInh','CapBnd','CapAmb'),0),no_new_privs=1,seccomp=2,interfaces=['lo'],external_routes=[],socket_fds=[],docker_socket=False)
    def info(self):
        l=self.launch()
        return {'Image':l['image_id'],'State':{'Running':True},'Config':{'User':'1001:1001','Entrypoint':['/usr/bin/env'],'WorkingDir':'/tmp','Cmd':['-i','PATH=/usr/bin:/bin','LANG=C.UTF-8','LC_ALL=C.UTF-8','/opt/urp-python/bin/python3.12','-I','-B','/inputs/engine_gate.py','--hold']},'Mounts':[{'Type':'bind','Source':'/fixture/inputs','Destination':'/inputs','RW':False},{'Type':'bind','Source':'/fixture/control','Destination':'/control','RW':False}], 'HostConfig':dict(NetworkMode='none',Privileged=False,ReadonlyRootfs=True,CapDrop=['ALL'],CapAdd=[],SecurityOpt=['no-new-privileges'],PidMode='',IpcMode='private',UTSMode='',CgroupnsMode='private',Memory=l['memory_bytes'],MemorySwap=l['memory_bytes'],PidsLimit=512,NanoCpus=2000000000,RestartPolicy={'Name':'no'},LogConfig={'Type':'local','Config':{'max-size':'1m','max-file':'1'}},Ulimits=[dict(Name='fsize',Soft=1073741824,Hard=1073741824)],Tmpfs={'/out':f"rw,nosuid,nodev,size={l['out_bytes']},uid=1001,gid=1001,mode=0700",'/tmp':'rw,nosuid,nodev,size=268435456,mode=1777'})}
    def test_kernel_fixture(self):validate_inside(self.observed(),self.launch())
    def test_kernel_rejects(self):
        changes={'netns':'net:[1]','boot_id':'other','uids':[0]*4,'gids':[0]*4,'seccomp':0,'no_new_privs':0,'interfaces':['lo','eth0'],'external_routes':['default'],'socket_fds':[3],'docker_socket':True,'caps':{'CapEff':0}}
        for k,v in changes.items():
            with self.subTest(k=k):
                o=self.observed();o[k]=v
                with self.assertRaises(RuntimeError):validate_inside(o,self.launch())
    def test_controller_inspect_fixture(self):check_inspect(self.info(),self.launch(),Path('/fixture/inputs'),Path('/fixture/control'))
    def test_controller_rejects(self):
        changes={'NetworkMode':'host','Privileged':True,'ReadonlyRootfs':False,'CapDrop':[],'CapAdd':['SYS_ADMIN'],'SecurityOpt':['seccomp=unconfined'],'PidMode':'host','IpcMode':'host','UTSMode':'host','CgroupnsMode':'host','Memory':0,'PidsLimit':0,'Devices':['device'],'PortBindings':{'80/tcp':[]},'Tmpfs':{},'Ulimits':[],'LogConfig':{'Type':'json-file','Config':{}}}
        for k,v in changes.items():
            with self.subTest(k=k):
                i=self.info();i['HostConfig'][k]=v
                with self.assertRaises(RuntimeError):check_inspect(i,self.launch(),Path('/fixture/inputs'),Path('/fixture/control'))
    def test_no_implicit_pull_or_privilege(self):
        a=create_argv(self.launch()['image_id'],'/inputs','/control',1001,1001,1024,2048)
        self.assertIn('--pull=never',a);self.assertIn('--network=none',a)
        self.assertNotIn('--privileged',a);self.assertNotIn('seccomp=unconfined',' '.join(a));self.assertNotIn('docker.sock',' '.join(a))
        with self.assertRaises(RuntimeError):create_argv('ubuntu:22.04','/inputs','/control',1001,1001,1024,2048)
    def test_input_exact_membership_and_hash(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'engine_gate.py').write_text('fixture')
            index={'protocol':PROTOCOL,'application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','python':'/opt/urp-python/bin/python3.12','files':{'engine_gate.py':{'sha256':hashlib.sha256(b'fixture').hexdigest()}}}
            raw=json.dumps(index).encode();(root/'input-index.json').write_bytes(raw);anchor=hashlib.sha256(raw).hexdigest()
            verify_inputs(root,anchor)
            (root/'extra').write_text('unreviewed')
            with self.assertRaises(RuntimeError):verify_inputs(root,anchor)
if __name__=='__main__':unittest.main()

class ExportTests(unittest.TestCase):
    def test_private_export_stream_pure_python_fixture(self):
        from unittest.mock import patch
        import engine_controller as controller
        with tempfile.TemporaryDirectory() as t, patch.object(controller,'docker_argv',return_value=[sys.executable,'-c','import sys;sys.stdout.buffer.write(b"fixture-tar")']):
            controller.export_output('fixture',Path(t),1024)
            self.assertEqual((Path(t)/'private-output.tar').read_bytes(),b'fixture-tar')
            self.assertFalse((Path(t)/'private-output.partial.tar').exists())
    def test_failed_export_retains_partial_evidence(self):
        from unittest.mock import patch
        import engine_controller as controller
        with tempfile.TemporaryDirectory() as t, patch.object(controller,'docker_argv',return_value=[sys.executable,'-c','import sys;sys.stdout.buffer.write(b"partial");sys.exit(2)']):
            with self.assertRaisesRegex(RuntimeError,'output export failed'):controller.export_output('fixture',Path(t),1024)
            self.assertEqual((Path(t)/'private-output.partial.tar').read_bytes(),b'partial')
            self.assertFalse((Path(t)/'private-output.tar').exists())

class IsolatedSourceTests(unittest.TestCase):
    def test_freezer_runs_under_isolated_python(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            (root/'engine_gate.py').write_text('fixture input only')
            (root/'offline_build.py').write_text('fixture entry only')
            program=Path(__file__).resolve().parents[1]/'freeze_input_index.py'
            result=subprocess.run([sys.executable,'-I','-B',str(program),'--root',str(root),'--phase','build'],capture_output=True,text=True,check=True)
            verify_inputs(root,result.stdout.strip())
    def test_compiler_flags_are_reviewed_body_exact(self):
        import ast
        source=(Path(__file__).resolve().parents[1]/'build_plan.py').read_text()
        function=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name=='build_command')
        body=ast.dump(ast.Module(body=function.body,type_ignores=[]),include_attributes=False).encode()
        self.assertEqual(hashlib.sha256(body).hexdigest(),'403fd931c2df165990a54779775f8be221d07f45c76bc2e409f4f833f6ae58eb')
