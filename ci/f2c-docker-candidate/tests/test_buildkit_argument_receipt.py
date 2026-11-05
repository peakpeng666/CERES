"""Exact argv diagnostic binding; no Docker invocation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('image_args_fixture',ROOT/'preparation/prepare_image.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
ARGS={'WORKLOAD_UID':1001,'WORKLOAD_GID':1002,'INPUT_SOURCE_SHA':'a'*64,'BOOTSTRAP_SHA':'b'*64}
def command(args=ARGS):
 return ['docker','build']+[v for k,value in args.items() for v in ('--build-arg',k+'='+str(value))]
class ArgumentReceiptTests(unittest.TestCase):
 def test_exact_constructed_argv_record(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cmd=command();before=list(cmd)
   self.assertTrue(p.record_buildkit_args(root,cmd));self.assertEqual(cmd,before)
   path=root/'buildkit-args.json';self.assertEqual(json.loads(path.read_text()),ARGS);self.assertEqual(path.stat().st_mode&0o777,0o600)
 def test_invalid_unknown_duplicate_out_of_range_no_record(self):
  cases=[command()+['--build-arg','WORKLOAD_UID=1003'],command()+['--build-arg','PRIVATE=SECRET'],['docker','build','--build-arg']]
  for key,value in [('WORKLOAD_UID',0),('WORKLOAD_GID',4294967295),('BOOTSTRAP_SHA','PRIVATE'),('INPUT_SOURCE_SHA','A'*64)]:
   cases.append(command(dict(ARGS,**{key:value})))
  for cmd in cases:
   with self.subTest(cmd=cmd),tempfile.TemporaryDirectory() as t:
    root=Path(t);self.assertFalse(p.record_buildkit_args(root,cmd));self.assertFalse((root/'buildkit-args.json').exists())
 def test_diagnostic_write_failure_never_raises(self):
  with patch.object(p,'write',side_effect=OSError('PRIVATE')):
   self.assertFalse(p.record_buildkit_args(Path('/unexecuted'),command()))
 def test_cancellation_not_swallowed(self):
  with patch.object(p,'write',side_effect=KeyboardInterrupt):
   with self.assertRaises(KeyboardInterrupt):p.record_buildkit_args(Path('/unexecuted'),command())
 def test_existing_binding_never_overwritten(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'buildkit-args.json';path.write_text('original')
   self.assertFalse(p.record_buildkit_args(Path(t),command()));self.assertEqual(path.read_text(),'original')
 def test_actual_controller_records_exact_argv_before_run(self):
  import ast
  tree=ast.parse((ROOT/'preparation/prepare_image.py').read_text())
  calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
  record=next(n for n in calls if n.func.id=='record_buildkit_args')
  run=next(n for n in calls if n.func.id=='run_private' and n.args and isinstance(n.args[0],ast.Name) and n.args[0].id=='cmd')
  self.assertLess(record.lineno,run.lineno);self.assertEqual(ast.dump(record.args[1]),ast.dump(run.args[0]))
class PublicArgumentBindingTests(unittest.TestCase):
 def public(self):
  spec=importlib.util.spec_from_file_location('binding_public_fixture',ROOT/'public_receipt.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
 def state(self,path):
  (path/'prepared').mkdir()
  (path/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':1}))
  (path/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':'image_build','code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
  return path
 def test_missing_binding_never_promotes_literal_dynamic_header(self):
  module=self.public()
  with tempfile.TemporaryDirectory() as t:
   state=self.state(Path(t));detail={'schema':1,'stage':'python_provision','substage':'entry','code':'UNKNOWN_FAILURE','exception_class':'RuntimeError'}
   raw='#20 [10/20] RUN '+module._buildkit.RUN_COMMANDS['python_provision']+'\n#20 0.1 SAFE_PREPARATION_DIAGNOSTIC:'+json.dumps(detail)+'\n#20 ERROR: PRIVATE\n'
   (state/'prepared/preparation.log').write_text(raw)
   result=module.project(state)
   self.assertIsNone(result['buildkit_failure_detail'])
   self.assertEqual(result['buildkit_census']['binding_input'],'missing_or_invalid')
   self.assertEqual(result['buildkit_census']['vertex_candidate_count'],'one')
   self.assertEqual(result['buildkit_census']['vertex_marker_binding'],'unbound')
   self.assertNotIn('PRIVATE',json.dumps(result))
 def test_census_is_independently_revalidated(self):
  module=self.public()
  with tempfile.TemporaryDirectory() as t:
   state=self.state(Path(t))
   with patch.object(module._census,'inspect_census',return_value={'private':'SECRET'}):
    result=module.project(state)
   self.assertIsNone(result['buildkit_census']);self.assertNotIn('SECRET',json.dumps(result))
 def test_binding_values_never_published(self):
  module=self.public()
  with tempfile.TemporaryDirectory() as t:
   state=self.state(Path(t));(state/'prepared/buildkit-args.json').write_text(json.dumps(ARGS))
   (state/'prepared/preparation.log').write_text('PRIVATE ERROR\n')
   result=module.project(state);text=json.dumps(result)
   for key,value in ARGS.items():
    self.assertNotIn(key,text)
    if isinstance(value,str):self.assertNotIn(value,text)
   self.assertEqual(result['buildkit_census']['binding_input'],'verified_values')

if __name__=='__main__':unittest.main()
