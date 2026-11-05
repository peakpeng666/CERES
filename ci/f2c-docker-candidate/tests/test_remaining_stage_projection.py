"""Adversarial projection fixtures only; never execute preparation commands."""
import importlib.util
import io
import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remaining_stage_projection',ROOT/'buildkit_diagnostic.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
STAGES=('closure_verify','python_provision','input_ownership','inputs_prepare','inputs_freeze')
PRIVATE='PRIVATE_ENV /PRIVATE/path https://private.invalid SECRET_TOKEN'

def header(stage,vertex=20):
 return '#%d [10/20] RUN %s\n' % (vertex,d.RUN_COMMANDS[stage])
def output(value,vertex=20):
 return '#%d 0.123 %s%s\n' % (vertex,d._stage.PREFIX,json.dumps(value,separators=(',',':')))
def receipt(stage):
 # Choose an explicitly accepted finite error, not a success marker.
 for substage in sorted(d._stage.STAGES[stage]['substages']):
  for code in sorted(d._stage.STAGES[stage]['codes']):
   for cls in ('RuntimeError','ShellExit','OtherError'):
    item={'schema':1,'stage':stage,'substage':substage,'code':code,'exception_class':cls}
    if d._stage.validate(item) is not None:return item
 raise AssertionError('No finite fixture for '+stage)

class RemainingStageProjectionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'private.log'
 def parse(self,text):
  self.path.write_text(text);return d.parse_log(self.path)
 def structure(self,text):
  self.path.write_text(text);return d.inspect_structure(self.path)
 def test_all_five_exact_stage_details(self):
  for stage in STAGES:
   with self.subTest(stage=stage):
    value=receipt(stage);result=self.parse(header(stage)+output(value))
    self.assertEqual(result,{'schema':1,'source':stage,'diagnostic_only':True,'detail':value})
    self.assertEqual(d.validate_projection(result),result)
 def test_all_static_failures_are_structurally_visible(self):
  for stage in STAGES:
   with self.subTest(stage=stage):
    result=self.structure(header(stage)+'#20 ERROR: '+PRIVATE+'\n')
    self.assertEqual(result['failed_stage'],stage)
    self.assertEqual(result['stages_reached'],[stage])
    self.assertNotIn(PRIVATE,json.dumps(result))
 def test_no_marker_does_not_infer_success(self):
  for stage in STAGES:self.assertIsNone(self.parse(header(stage)+'#20 DONE 0.1s\n'))
 def test_header_commands_cover_all_dockerfile_runs_exactly(self):
  commands=[' '.join(line[4:].split()) for line in (ROOT/'preparation/Dockerfile').read_text().replace('\\\n','').splitlines() if line.startswith('RUN ')]
  self.assertEqual(set(commands),set(d.RUN_COMMANDS.values()))
  self.assertEqual(len(commands),len(d.RUN_COMMANDS))
 def test_same_vertex_source_mismatch_rejected(self):
  for stage in STAGES:
   other=next(x for x in STAGES if x!=stage)
   self.assertIsNone(self.parse(header(stage)+output(receipt(other))))
 def test_unknown_vertex_cannot_supply_detail(self):
  self.assertIsNone(self.parse(header('closure_verify')+output(receipt('closure_verify'),21)))
 def test_unrelated_interleaving_is_permitted(self):
  value=receipt('closure_verify')
  data=header('closure_verify')+'#30 [11/20] RUN /private/unknown\n#30 0.1 '+PRIVATE+'\n'+output(value)
  self.assertEqual(self.parse(data)['detail'],value)
 def test_duplicate_marker_rejected(self):
  value=receipt('closure_verify')
  self.assertIsNone(self.parse(header('closure_verify')+output(value)+output(value)))
 def test_conflicting_stage_failures_rejected(self):
  self.assertIsNone(self.parse(header('closure_verify')+header('inputs_freeze',21)+output(receipt('closure_verify'))+output(receipt('inputs_freeze'),21)))
 def test_identical_header_repeat_is_idempotent(self):
  self.assertEqual(self.parse(header('closure_verify')+header('closure_verify')+output(receipt('closure_verify')))['source'], 'closure_verify')
 def test_unknown_fields_codes_substages_classes_rejected(self):
  for key in ('stage','code','substage','exception_class'):
   value=receipt('closure_verify');value[key]=PRIVATE
   self.assertIsNone(self.parse(header('closure_verify')+output(value)))
  value=receipt('closure_verify');value['private']=PRIVATE
  self.assertIsNone(self.parse(header('closure_verify')+output(value)))
 def test_duplicate_json_fields_rejected(self):
  value=receipt('closure_verify');encoded=json.dumps(value)
  payload=encoded[:-1]+',"stage":"closure_verify"}'
  self.assertIsNone(self.parse(header('closure_verify')+'#20 0.1 '+d._stage.PREFIX+payload+'\n'))
 def test_nested_fake_source_header_is_not_binding(self):
  data='#1 [1/20] RUN echo fake\n#1 0.1 '+header('closure_verify').rstrip('\n')+'\n'+output(receipt('closure_verify'))
  self.assertIsNone(self.parse(data))
 def test_command_suffix_or_prefix_is_not_recognized(self):
  for command in ('RUN '+d.RUN_COMMANDS['closure_verify']+' && echo private','RUN echo private; '+d.RUN_COMMANDS['closure_verify']):
   self.assertIsNone(self.parse('#20 [10/20] '+command+'\n'+output(receipt('closure_verify'))))
 def test_truncation_and_oversize_rejected(self):
  self.assertIsNone(self.parse(header('closure_verify')+output(receipt('closure_verify')).rstrip('\n')))
  self.assertIsNone(self.parse(header('closure_verify')+'#20 0.1 '+d._stage.PREFIX+' '*1024+'\n'))
 def test_old_marker_wrong_new_vertex_rejected(self):
  self.assertIsNone(self.parse(header('closure_verify')+'#20 0.1 '+d.BOOTSTRAP_PREFIX+'BLOCKED_receipt\n'))
 def test_new_marker_old_vertex_rejected(self):
  self.assertIsNone(self.parse(header('bootstrap')+output(receipt('closure_verify'))))
 def test_no_raw_fields_in_projection(self):
  value=receipt('inputs_prepare')
  result=self.parse(PRIVATE+'\n'+header('inputs_prepare')+'#20 0.1 '+PRIVATE+'\n'+output(value))
  self.assertNotIn(PRIVATE,json.dumps(result));self.assertEqual(set(result['detail']),set(value))
 def test_result_is_fresh_and_schema_bound(self):
  value=receipt('closure_verify');env={'schema':1,'source':'closure_verify','diagnostic_only':True,'detail':value}
  result=d.validate_projection(env);self.assertIsNot(result['detail'],value)
  value['code']=PRIVATE;self.assertNotIn(PRIVATE,json.dumps(result))
 def test_reference_is_not_allowed_on_new_stages(self):
  env={'schema':1,'source':'closure_verify','diagnostic_only':True,'detail':receipt('closure_verify'),'reference':{}}
  self.assertIsNone(d.validate_projection(env))
 def test_all_five_reach_public_receipt_without_success_claim(self):
  spec=importlib.util.spec_from_file_location('remaining_public_receipt',ROOT/'public_receipt.py')
  public=importlib.util.module_from_spec(spec);spec.loader.exec_module(public)
  state=Path(self.tmp.name)/'state';state.mkdir();(state/'prepared').mkdir()
  (state/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':1}))
  (state/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':'image_build','code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
  for stage in STAGES:
   value=receipt(stage)
   args={'WORKLOAD_UID':1001,'WORKLOAD_GID':1001,'BOOTSTRAP_SHA':'a'*64,'INPUT_SOURCE_SHA':'b'*64}
   (state/'prepared/buildkit-args.json').write_text(json.dumps(args))
   framed=header(stage).replace(d.RUN_COMMANDS[stage],d.render_run_commands(args)[stage])
   (state/'prepared/preparation.log').write_text(framed+output(value)+'#20 ERROR: '+PRIVATE+'\n')
   result=public.project(state)
   self.assertEqual(result['buildkit_failure_detail']['detail'],value)
   self.assertEqual(result['buildkit_structure']['failed_stage'],stage)
   self.assertEqual(result['outcome'],'failed_or_not_run')
   self.assertNotIn(PRIVATE,json.dumps(result))
 def test_source_index_only_isolated_receipt_all_stages(self):
  stage_root=Path(self.tmp.name)/'indexed';stage_root.mkdir()
  members=('public_receipt.py','preparation_diagnostic.py','buildkit_diagnostic.py','package_reference.py','preparation_stage_diagnostic.py','buildkit_census.py')
  index={'files':{}}
  for name in members:
   data=(ROOT/name).read_bytes();(stage_root/name).write_bytes(data)
   index['files'][name]={'sha256':hashlib.sha256(data).hexdigest()}
  spec=importlib.util.spec_from_file_location('remaining_source_freezer',ROOT/'freeze_source_index.py')
  freezer=importlib.util.module_from_spec(spec);spec.loader.exec_module(freezer)
  digest=freezer.freeze(stage_root)
  data=(stage_root/'source-index.json').read_bytes()
  self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
  index=json.loads(data)
  self.assertEqual(set(index['files']),set(members))
  relocated=Path(self.tmp.name)/'relocated';relocated.mkdir()
  for name,row in index['files'].items():
   payload=(stage_root/name).read_bytes();self.assertEqual(hashlib.sha256(payload).hexdigest(),row['sha256'])
   (relocated/name).write_bytes(payload)
  shutil.rmtree(stage_root)
  state=Path(self.tmp.name)/'isolated-state';state.mkdir();(state/'prepared').mkdir()
  (state/'terminal.json').write_text(json.dumps({'phase':'preparation','exit_code':1}))
  (state/'preparation-stderr.log').write_text(json.dumps({'schema':1,'stage':'image_build','code':'UNCLASSIFIED_PREPARATION_FAILURE','exception_type':'CalledProcessError'}))
  for optimized in (False,True):
   for stage in STAGES:
    with self.subTest(stage=stage,optimized=optimized):
     value=receipt(stage)
     args={'WORKLOAD_UID':1001,'WORKLOAD_GID':1001,'BOOTSTRAP_SHA':'a'*64,'INPUT_SOURCE_SHA':'b'*64}
     (state/'prepared/buildkit-args.json').write_text(json.dumps(args))
     framed=header(stage).replace(d.RUN_COMMANDS[stage],d.render_run_commands(args)[stage])
     (state/'prepared/preparation.log').write_text(framed+output(value)+'#20 ERROR: '+PRIVATE+'\n')
     run=subprocess.run([sys.executable,'-I','-B']+(['-O'] if optimized else [])+[str(relocated/'public_receipt.py'),'--state',str(state)],cwd=self.tmp.name,capture_output=True,text=True,timeout=10)
     self.assertEqual(run.returncode,1);self.assertEqual(run.stderr,'')
     result=json.loads(run.stdout)
     self.assertEqual(result['buildkit_failure_detail']['detail'],value)
     self.assertEqual(result['buildkit_structure']['failed_stage'],stage)
     self.assertEqual(result['outcome'],'failed_or_not_run')
     self.assertNotIn(PRIVATE,run.stdout)
 def test_structural_duplicate_errors_remain_unknown(self):
  result=self.structure(header('closure_verify')+'#20 ERROR: a\n#20 ERROR: b\n')
  self.assertEqual(result['failed_stage'],'unknown')
 def test_structural_only_exact_header_matches(self):
  result=self.structure(header('closure_verify').replace(' -I ',' ')+'#20 ERROR: a\n')
  self.assertEqual(result['failed_stage'],'unknown');self.assertEqual(result['stages_reached'],[])
if __name__=='__main__':unittest.main()
