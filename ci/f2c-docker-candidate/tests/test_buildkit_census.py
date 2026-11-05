"""Pure bounded diagnostic census fixtures, not actual hosted log replays."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('census_test', ROOT/'buildkit_census.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
ARGS = {'WORKLOAD_UID': 1001, 'WORKLOAD_GID': 1001, 'INPUT_SOURCE_SHA': 'a'*64, 'BOOTSTRAP_SHA':'b'*64}

def marker(stage='closure_verify', code='BOOTSTRAP_RECEIPT', substage='receipt_schema'):
 return 'SAFE_PREPARATION_DIAGNOSTIC:'+json.dumps({'schema':1,'stage':stage,'substage':substage,'code':code,'exception_class':'RuntimeError'},separators=(',',':'))
def header(stage='closure_verify', vertex=7, args=None):
 return '#%d [8/15] RUN %s\n'%(vertex,c._b.render_run_commands(args)[stage])
def scan(text,args=None):
 with tempfile.TemporaryDirectory() as d:
  p=Path(d)/'private.log';p.write_text(text)
  return c.inspect_census(p,args)

class CensusTests(unittest.TestCase):
 def test_bound_candidate_and_error(self):
  r=scan(header()+'#7 0.100 '+marker()+'\n#7 ERROR: PRIVATE /path https://secret.invalid\n')
  self.assertEqual(r['vertex_candidate_count'],'one'); self.assertEqual(r['vertex_marker_binding'],'bound')
  self.assertEqual(r['error_binding'],'bound');self.assertEqual(r['candidate_stages'],['closure_verify'])
  self.assertNotIn('PRIVATE',json.dumps(r));self.assertEqual(c.validate_census(r),r)
 def test_unbound_candidate_survives_without_attribution(self):
  r=scan('#7 0.100 '+marker()+'\n#7 ERROR: PRIVATE\n')
  self.assertEqual(r['vertex_candidate_count'],'one');self.assertEqual(r['vertex_marker_binding'],'unbound');self.assertEqual(r['error_binding'],'unbound')
 def test_plain_error_summary_repeat_is_separate(self):
  r=scan(header()+'#7 0.100 '+marker()+'\n#7 ERROR: PRIVATE\n------\n > [8/15] RUN PRIVATE:\n0.100 '+marker()+'\n------\n')
  self.assertEqual(r['vertex_marker_relation'],'single');self.assertEqual(r['summary_candidate_count'],'one')
 def test_summary_only_no_binding(self):
  r=scan('0.100 '+marker()+'\n')
  self.assertEqual(r['summary_candidate_count'],'one');self.assertEqual(r['vertex_marker_binding'],'none');self.assertEqual(r['error_binding'],'none')
 def test_unframed_candidate_counted_not_bound(self):
  r=scan(marker()+'\n');self.assertEqual(r['unframed_candidate_count'],'one');self.assertEqual(r['vertex_marker_binding'],'none')
 def test_bootstrap_success_is_not_failure_candidate(self):
  r=scan('#3 0.100 SAFE_BOOTSTRAP_DIAGNOSTIC:'+c._b.BOOTSTRAP_SUCCESS+'\n'+header()+'#7 0.100 '+marker()+'\n')
  self.assertEqual(r['vertex_candidate_count'],'one');self.assertEqual(r['candidate_stages'],['closure_verify'])
 def test_identical_repeated_header_idempotent(self):
  r=scan(header()+header()+'#7 0.100 '+marker()+'\n#7 ERROR: x\n')
  self.assertEqual(r['header_relation'],'repeated_identical');self.assertEqual(r['error_binding'],'bound')
 def test_changed_header_conflict(self):
  r=scan(header()+header('package')+'#7 0.100 '+marker()+'\n#7 ERROR: x\n')
  self.assertEqual(r['header_relation'],'conflict');self.assertEqual(r['vertex_marker_binding'],'conflict');self.assertEqual(r['error_binding'],'conflict')
 def test_same_stage_two_vertices_conflict(self):
  r=scan(header()+header(vertex=8)+'#8 ERROR: x\n')
  self.assertEqual(r['header_relation'],'conflict');self.assertEqual(r['error_binding'],'conflict')
 def test_same_vertex_duplicate_marker_count(self):
  line='#7 0.100 '+marker()+'\n';r=scan(header()+line+line)
  self.assertEqual(r['vertex_candidate_count'],'multiple');self.assertEqual(r['vertex_marker_relation'],'duplicate')
 def test_conflicting_valid_markers(self):
  r=scan(header()+'#7 0.100 '+marker()+'\n#7 0.200 '+marker(code='BOOTSTRAP_SCOPE')+'\n')
  self.assertEqual(r['vertex_marker_relation'],'conflict')
 def test_stage_marker_mismatch(self):
  r=scan(header('package')+'#7 0.100 '+marker()+'\n')
  self.assertEqual(r['vertex_marker_binding'],'conflict')
 def test_unknown_stage_and_code_never_echoed(self):
  for text in (marker(stage='PRIVATE'),marker(code='PRIVATE')):
   r=scan('#7 0.100 '+text+'\n');self.assertEqual(r['invalid_candidate_count'],'one');self.assertNotIn('PRIVATE',json.dumps(r))
 def test_duplicate_json_fields_rejected(self):
  text=marker().replace('"schema":1','"schema":1,"schema":1')
  r=scan('#7 0.100 '+text+'\n');self.assertEqual(r['invalid_candidate_count'],'one')
 def test_nested_fake_header_not_bound(self):
  r=scan('#2 0.100 '+header().strip()+'\n#7 0.200 '+marker()+'\n')
  self.assertEqual(r['vertex_marker_binding'],'unbound')
 def test_unrelated_interleaving(self):
  r=scan(header()+'#8 [9/15] RUN PRIVATE_COMMAND\n#8 0.1 private\n#7 0.200 '+marker()+'\n')
  self.assertEqual(r['vertex_marker_binding'],'bound')
 def test_error_shape_mixed(self):
  r=scan('#7 ERROR: private\nERROR: private\n')
  self.assertEqual(r['error_shape'],'mixed');self.assertEqual(r['error_binding'],'unbound')
 def test_multiple_errors_conflict(self):
  r=scan(header()+'#7 ERROR: x\n#7 ERROR: y\n');self.assertEqual(r['top_level_error_count'],'multiple');self.assertEqual(r['error_binding'],'conflict')
 def test_missing_args_preserve_static_binding(self):
  r=scan(header()+'#7 ERROR: x\n',{})
  self.assertEqual(r['binding_input'],'missing_or_invalid');self.assertEqual(r['error_binding'],'bound')
 def test_known_args_rendered_header(self):
  stage='python_provision';r=scan(header(stage,args=ARGS)+'#7 ERROR: x\n',ARGS)
  self.assertEqual(r['binding_input'],'verified_values');self.assertEqual(r['error_binding'],'bound')
 def test_unknown_args_no_variable_literal_fallback(self):
  r=scan(header('python_provision')+'#7 ERROR: x\n',{})
  self.assertEqual(r['error_binding'],'unbound')
 def test_truncated_and_oversized_lines_failclosed(self):
  for text in (marker(), 'X'*(c._b.MAX_LINE_BYTES+1)+'\n'):
   self.assertEqual(scan(text)['state'],'invalid')
 def test_vertex_limit(self):
  with patch.object(c._b,'MAX_VERTICES',2):
   self.assertEqual(scan('#1 [1/3] RUN a\n#2 [2/3] RUN b\n#3 [3/3] RUN c\n')['state'],'invalid')
 def test_missing_empty_symlink_and_oversized(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'p';self.assertEqual(c.inspect_census(p)['state'],'missing')
   p.write_bytes(b'');self.assertEqual(c.inspect_census(p)['state'],'empty')
   link=Path(d)/'link';link.symlink_to(p);self.assertEqual(c.inspect_census(link)['state'],'unsupported_file')
   with patch.object(c._b,'MAX_LOG_BYTES',2):
    p.write_bytes(b'123');self.assertEqual(c.inspect_census(p)['state'],'oversized')
 def test_validator_closed_fresh_and_relations(self):
  r=scan(header()+'#7 0.1 '+marker()+'\n');v=c.validate_census(r);self.assertIsNot(v,r);self.assertIsNot(v['candidate_stages'],r['candidate_stages'])
  for key,value in [('schema',True),('state','PRIVATE'),('candidate_stages',['PRIVATE']),('vertex_candidate_count','none'),('binding_input','not_used')]:
   bad=dict(r);bad[key]=value;self.assertIsNone(c.validate_census(bad))
  bad=dict(r);bad['extra']='PRIVATE';self.assertIsNone(c.validate_census(bad))
 def test_schema_failure_output_is_finite(self):
  r=scan('SAFE_PACKAGE_DIAGNOSTIC:{"private":"https://PRIVATE/path"}\n')
  self.assertEqual(r['invalid_candidate_count'],'one');self.assertNotIn('PRIVATE',json.dumps(r))
 def test_changed_file_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'p';p.write_text('original\n')
   def mutate(stream, args):
    p.write_text('changed bytes\n');return c._receipt('read',binding_input='legacy_literal')
   with patch.object(c,'_scan',side_effect=mutate):
    self.assertEqual(c.inspect_census(p)['state'],'changed')
 def test_malformed_marker_size_is_only_bucket(self):
  r=scan('0.100 SAFE_PREPARATION_DIAGNOSTIC:'+('PRIVATE'*200)+'\n')
  self.assertEqual(r['invalid_candidate_count'],'one');self.assertNotIn('PRIVATE',json.dumps(r))
 def test_no_candidate_with_arbitrary_log_text(self):
  r=scan('PRIVATE SAFE_PREPARATION_DIAGNOSTIC: payload\n')
  self.assertEqual(r['candidate_stages'],[]);self.assertEqual(r['invalid_candidate_count'],'none')
if __name__=='__main__':unittest.main()
