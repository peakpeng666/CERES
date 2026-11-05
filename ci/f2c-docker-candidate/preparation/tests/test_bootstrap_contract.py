import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 s=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
b=load('bootstrap_tools');p=load('prepare_image')

def lock():
 return {'schema':1,'trust':'pinned-action-bootstrap-measured-not-ubuntu-signed','tools':{'uv':{'action':b.UV_ACTION,'version':'0.12.23','bytes':3,'sha256':'1'*64}},'python':{'key':b.PYTHON_KEY,'url':b.PYTHON_URL,'sha256':b.PYTHON_SHA}}
class BootstrapTests(unittest.TestCase):
 def test_schema(self):self.assertEqual(b.validate_lock(lock()),lock())
 def test_negative_schema(self):
  for change in (lambda x:x.update(secret='no'),lambda x:x['tools'].update(extra={}),lambda x:x['tools']['uv'].update(version='0.12.24'),lambda x:x['tools'].update(node={}),lambda x:x['tools']['uv'].update(action='evil'),lambda x:x['tools']['uv'].update(bytes=True),lambda x:x['python'].update(sha256='3'*64),lambda x:x['python'].update(url='http://evil')):
   obj=lock();change(obj)
   with self.assertRaises(RuntimeError):b.validate_lock(obj)
 def test_no_install_without_image_root(self):
  with mock.patch.object(b.os,'getuid',return_value=1001),mock.patch.object(b.subprocess,'run') as run:
   with self.assertRaises(RuntimeError):b.install(Path('/not-used'),'0'*64)
   run.assert_not_called()
 def test_unsafe_paths(self):
  for name in ('../bad','/bad','a//b','a/./b','a/../b',''):
   with self.assertRaises(RuntimeError):p.safe_relative(name)
 def test_duplicate_json(self):
  with self.assertRaises(RuntimeError):json.loads('{"a":1,"a":2}',object_pairs_hook=p.unique)
 def test_anchor_hash_and_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);s=base/'source';s.mkdir();f=s/'file';f.write_bytes(b'abc')
   index={'schema':1,'status':'reviewed-source','files':{'file':{'sha256':hashlib.sha256(b'abc').hexdigest()}}}
   raw=json.dumps(index).encode();(s/'source-index.json').write_bytes(raw)
   p.anchor_copy(s,base/'copy',hashlib.sha256(raw).hexdigest())
   self.assertEqual((base/'copy/file').read_bytes(),b'abc')
   f.write_bytes(b'changed')
   with self.assertRaises(RuntimeError):p.anchor_copy(s,base/'bad',hashlib.sha256(raw).hexdigest())
 def test_alias_and_hardlink_refused(self):
  with tempfile.TemporaryDirectory() as d:
   x=Path(d)/'x';x.write_bytes(b'abc');y=Path(d)/'y';y.symlink_to(x)
   with self.assertRaises(RuntimeError):p.read_file(y)
   y.unlink();b.os.link(x,y)
   with self.assertRaises(RuntimeError):p.read_file(x)
 def test_explicit_default_no_execution(self):
  with mock.patch.object(p.os,'getuid',return_value=0),mock.patch.object(p.subprocess,'run') as run:
   with self.assertRaises(RuntimeError):p.execute(None,None,None,None,None,None,Path('/tmp/unused'))
   run.assert_not_called()
 def test_fixed_image_and_application(self):
  self.assertEqual(p.APPLICATION,'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35')
  self.assertTrue(p.BASE.endswith('98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04'))
if __name__=='__main__':unittest.main()

class ExtendedTests(unittest.TestCase):
 def test_node_bootstrap_prohibited(self):
  obj=lock();obj['tools']['node']={'version':'24.0.0'}
  with self.assertRaises(RuntimeError):b.validate_lock(obj)
 def test_no_node_assets_in_compiler(self):
  self.assertNotIn('/opt/urp-node',(ROOT/'Dockerfile').read_text())
  self.assertNotIn('node-prefix',(ROOT/'prepare_image.py').read_text())
 def test_tampered_action_and_unknown_fields(self):
  for key,value in [('action','astral-sh/setup-uv@main'),('token','PRIVATE_TOKEN')]:
   x=lock();x['tools']['uv'][key]=value
   with self.assertRaises(RuntimeError):b.validate_lock(x)
 def test_setuid_bootstrap_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'uv';path.write_bytes(b'fixture');path.chmod(0o4755)
   with self.assertRaises(RuntimeError):b.regular(path)
 def test_application_exact_selected_git_blobs(self):
  raw=b'uv fixture';records=[('100644','src/lab/uv.lock',raw),('100644','src/lab/pyproject.toml',b'project fixture')]
  blobs={hashlib.sha1(b'blob '+str(len(r)).encode()+b'\0'+r).hexdigest():r for _,_,r in records}
  tree=b''.join(mode.encode()+b' blob '+hashlib.sha1(b'blob '+str(len(r)).encode()+b'\0'+r).hexdigest().encode()+b'\t'+name.encode()+b'\0' for mode,name,r in records)
  def git(source,args):
   if args[0]=='rev-parse':return (p.APPLICATION+'\n').encode()
   if args[0]=='status':return b''
   if args[0]=='ls-tree':
    self.assertEqual(args[4],p.APPLICATION);self.assertEqual(args[-3:],['--','src/lab/pyproject.toml','src/lab/uv.lock']);return tree
   return blobs[args[-1]]
  with tempfile.TemporaryDirectory() as d,mock.patch.object(p,'git_bytes',side_effect=git):
   out=Path(d)/'application';rows=p.stage_application(Path('/synthetic'),out)
   self.assertEqual(set(rows),{'src/lab/uv.lock','src/lab/pyproject.toml'});self.assertEqual((out/'src/lab/uv.lock').read_bytes(),raw)
 def test_application_external_link_rejected(self):
  raw=b'../secret';oid=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
  def git(source,args):
   if args[0]=='rev-parse':return (p.APPLICATION+'\n').encode()
   if args[0]=='status':return b''
   if args[0]=='ls-tree':return ('120000 blob '+oid+'\tbad\0').encode()
   return raw
  with tempfile.TemporaryDirectory() as d,mock.patch.object(p,'git_bytes',side_effect=git):
   with self.assertRaises(RuntimeError):p.stage_application(Path('/synthetic'),Path(d)/'application')
 def test_dockerfile_compiler_only(self):
  text=(ROOT/'Dockerfile').read_text()
  self.assertIn(p.BASE,text);self.assertIn('USER ${WORKLOAD_UID}:${WORKLOAD_GID}',text)
  self.assertNotIn('npm ci',text);self.assertNotIn('uv sync',text)
  self.assertNotIn('--privileged',text);self.assertNotIn('seccomp=unconfined',text)
  self.assertIn('--phase build',text)
 def test_controller_isolated_engine_config(self):
  text=(ROOT/'prepare_image.py').read_text()
  self.assertIn("'unix:///var/run/docker.sock'",text)
  self.assertIn("'--config',str(docker_config)",text)
  self.assertNotIn('DOCKER_HOST',p.ENV)
 def test_python_metadata_reference(self):
  evidence=json.loads((ROOT/'evidence/python-distribution-reference.json').read_text())
  self.assertEqual(evidence['key'],b.PYTHON_KEY);self.assertEqual(evidence['selected']['sha256'],b.PYTHON_SHA)
  self.assertEqual(evidence['selected']['url'],b.PYTHON_URL)
 def test_source_bootstrap_preparation_requires_fixed_schema(self):
  for value in ('arbitrary',{},None):
   with self.assertRaises(RuntimeError):b.validate_lock({'schema':1,'trust':value,'tools':{},'python':{}})

class ClosureTests(unittest.TestCase):
 def fixture(self,root):
  (root/'bootstrap.json').write_text(json.dumps({'status':'bootstrap-installed-transaction-verified','transaction_authenticated':True,'snapshot':'20261021T000000Z','full_closure_verified':False}))
  (root/'roots.tsv').write_text('python3\t1\npython3-apt\t1\nca-certificates\t1\n')
  (root/'transaction.tsv').write_text('python3\tamd64\t1\t-\tinstall\tpool/python.deb\t3\t'+'a'*64+'\tjammy\tmain\n')
  return {'installed':True,'signed_metadata_verified':True,'closure_complete':True,'snapshot':{'kind':'immutable-signed-snapshot','identifier':'20261021T000000Z'},'resolved':[{'name':x,'architecture':'amd64','version':'1','archive_filename':'pool/python.deb','archive_size':3,'archive_sha256':'a'*64} for x in ('python3','python3-apt','ca-certificates')]}
 def test_full_binding(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);module=load('verify_bootstrap_closure')
   with mock.patch.object(module,'verify_system_ca',return_value={'fixture':'not-real-CA'}) as check:
    result=module.verify(root,self.fixture(root))
   check.assert_called_once_with()
   self.assertEqual(result['derived_system_ca'],{'fixture':'not-real-CA'})
   self.assertEqual(len(result['bootstrap_packages']),3)
 def test_derived_ca_verification_cannot_be_skipped(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);module=load('verify_bootstrap_closure');pkg=self.fixture(root)
   with mock.patch.object(module,'verify_system_ca',side_effect=RuntimeError('FIXED_CA_FAILURE')) as check:
    with self.assertRaises(RuntimeError):module.verify(root,pkg)
   check.assert_called_once_with()
 def test_closure_missing_and_changed(self):
  for field,value in [('version','2'),('archive_sha256','b'*64),('archive_filename','pool/foreign.deb'),('archive_size',4)]:
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);pkg=self.fixture(root);pkg['resolved'][0][field]=value
    with self.assertRaises(RuntimeError):load('verify_bootstrap_closure').verify(root,pkg)
 def test_missing_package(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=self.fixture(root);pkg['resolved'].pop()
   with self.assertRaises(RuntimeError):load('verify_bootstrap_closure').verify(root,pkg)
 def test_incomplete_closure(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=self.fixture(root);pkg['closure_complete']=False
   with self.assertRaises(RuntimeError):load('verify_bootstrap_closure').verify(root,pkg)
 def test_root_version_binding(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);pkg=self.fixture(root);pkg['resolved'][-1]['version']='2'
   with self.assertRaises(RuntimeError):load('verify_bootstrap_closure').verify(root,pkg)

class SelectedSourceTests(unittest.TestCase):
 def test_outside_source_refused_before_read(self):
  calls=[]
  def git(source,args):
   calls.append(args[0])
   if args[0]=='rev-parse':return (p.APPLICATION+'\n').encode()
   if args[0]=='status':return b''
   if args[0]=='ls-tree':return ('100644 blob '+'a'*40+'\tliterature/private.pdf\0').encode()
   self.fail('Excluded blob must not be read')
  with tempfile.TemporaryDirectory() as d,mock.patch.object(p,'git_bytes',side_effect=git):
   with self.assertRaisesRegex(RuntimeError,'APPLICATION_SOURCE_SCOPE'):p.stage_application(Path('/synthetic'),Path(d))
  self.assertNotIn('cat-file',calls)
 def test_selected_symlink_refused_before_read(self):
  def git(source,args):
   if args[0]=='rev-parse':return (p.APPLICATION+'\n').encode()
   if args[0]=='status':return b''
   if args[0]=='ls-tree':return ('120000 blob '+'a'*40+'\tsrc/lab/uv.lock\0').encode()
   self.fail('Alias bytes must not be adopted')
  with tempfile.TemporaryDirectory() as d,mock.patch.object(p,'git_bytes',side_effect=git):
   with self.assertRaisesRegex(RuntimeError,'APPLICATION_ENTRY'):p.stage_application(Path('/synthetic'),Path(d))
