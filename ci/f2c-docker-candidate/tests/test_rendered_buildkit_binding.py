"""Pure official-display semantic fixtures, not captured hosted BuildKit logs."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rendered_binding',ROOT/'buildkit_diagnostic.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
ARGS=dict(WORKLOAD_UID=1001,WORKLOAD_GID=1002,INPUT_SOURCE_SHA='a'*64,BOOTSTRAP_SHA='b'*64)
# This independent limited reference follows official shell/lex.go processDollar,
# processSingleQuote and processDoubleQuote with RawQuotes/RawEscapes/SkipUnsetEnv.
# It deliberately handles only characters used by the fixed reviewed commands.
def official_display_subset(command, values):
 out=[];i=0;single=False;double=False
 while i<len(command):
  c=command[i]
  if c=="'" and not double:single=not single;out.append(c);i+=1;continue
  if c=='"' and not single:double=not double;out.append(c);i+=1;continue
  if c=='\\' and not single:
   out.append(command[i:i+2]);i+=2;continue
  if c=='$' and not single:
   j=i+1
   while j<len(command) and (command[j].isalnum() or command[j]=='_'):j+=1
   name=command[i+1:j]
   if name in values:out.append(str(values[name]));i=j;continue
  out.append(c);i+=1
 return ''.join(out)
def header(stage,vertex=7,args=ARGS):
 return f'#{vertex} [ 8/20] RUN {d.render_run_commands(args)[stage]}\n'
def marker(stage,vertex=7):
 if stage=='bootstrap':body=d.BOOTSTRAP_PREFIX+'BLOCKED_signed_metadata'
 elif stage=='package':body=d.PACKAGE_PREFIX+json.dumps(dict(schema=1,code='APT_INVARIANT_REJECTED',operation='post-delta-version',exception_class='RuntimeError',http_status=None,errno=None,returncode=None,suite=None,component=None))
 else:
  row=d._stage.STAGES[stage]
  sub=row['substages'][0];code=row['codes'][0];cls='RuntimeError'
  if stage=='input_ownership':code=d._stage.OWNERSHIP_PAIRS[sub];cls='ShellExit'
  body=d._stage.PREFIX+json.dumps(dict(schema=1,stage=stage,substage=sub,code=code,exception_class=cls))
 return f'#{vertex} 0.123 {body}\n'
class RenderedBindingTests(unittest.TestCase):
 def test_official_convert_test_golden(self):
  # moby/buildkit b1ab902b2c63ea148f16b42a162af0c7381fb4ae,
  # frontend/dockerfile/dockerfile2llb/convert_test.go:
  # TestRunCustomNameKeepsEscapes. Data-only replay of its expected semantics.
  self.assertEqual(official_display_subset(r'RUN echo \$FOO $FOO "a\"b"',{'FOO':'bar'}),
                   r'RUN echo \$FOO bar "a\"b"')

 def test_all_seven_templates_match_official_display_subset(self):
  rendered=d.render_run_commands(ARGS)
  self.assertEqual(len(rendered),7)
  for stage,command in d.RUN_COMMANDS.items():
   with self.subTest(stage=stage):self.assertEqual(rendered[stage],official_display_subset(command,ARGS))
 def test_unknown_shell_status_variables_and_quotes_preserved(self):
  value=d.render_run_commands(ARGS)['input_ownership']
  self.assertIn('rc=$?',value);self.assertIn('exit "$rc"',value)
  self.assertIn('"1001:1002"',value)
  self.assertIn("'SAFE_PREPARATION_DIAGNOSTIC:",value)
 def test_all_seven_stages_bind_rendered_headers_and_markers(self):
  for stage in d.RUN_COMMANDS:
   raw=header(stage)+marker(stage)
   with self.subTest(stage=stage):
    self.assertEqual(d._parse_lines(io.BytesIO(raw.encode()),ARGS)['source'],stage)
    structural=d._inspect_lines(io.BytesIO((raw+'#7 ERROR: PRIVATE\n').encode()),len(raw)+18,ARGS)
    self.assertEqual(structural['failed_stage'],stage)
 def test_wrong_known_values_cannot_bind_dynamic_headers(self):
  for stage in ('python_provision','input_ownership','inputs_prepare'):
   raw=header(stage)+marker(stage)
   wrong=dict(ARGS,BOOTSTRAP_SHA='c'*64,WORKLOAD_UID=42,INPUT_SOURCE_SHA='d'*64)
   self.assertIsNone(d._parse_lines(io.BytesIO(raw.encode()),wrong))
 def test_missing_invalid_binding_only_matches_immutable_commands(self):
  static={'bootstrap','package','closure_verify','inputs_freeze'}
  for bad in ({},None,False,dict(ARGS,WORKLOAD_UID=True),dict(ARGS,BOOTSTRAP_SHA='SECRET')):
   if bad is None:continue
   self.assertEqual(set(d.render_run_commands(bad)),static)
  for stage in set(d.RUN_COMMANDS)-static:
   self.assertIsNone(d._parse_lines(io.BytesIO((header(stage,args=None)+marker(stage)).encode()),{}))
 def test_legacy_none_mode_remains_literal(self):
  self.assertEqual(d.render_run_commands(),d.RUN_COMMANDS)
 def test_argument_schema_bounds_and_fresh_copy(self):
  self.assertEqual(d.validate_build_args(ARGS),ARGS)
  value=d.validate_build_args(ARGS);value['WORKLOAD_UID']=2;self.assertEqual(ARGS['WORKLOAD_UID'],1001)
  for key in ('WORKLOAD_UID','WORKLOAD_GID'):
   for bad in (True,False,0,-1,4294967295,1.0,'1',None):self.assertIsNone(d.validate_build_args(dict(ARGS,**{key:bad})))
   self.assertIsNotNone(d.validate_build_args(dict(ARGS,**{key:4294967294})))
  for key in ('INPUT_SOURCE_SHA','BOOTSTRAP_SHA'):
   for bad in ('a'*63,'A'*64,'a'*65,'a'*63+';',None,1):self.assertIsNone(d.validate_build_args(dict(ARGS,**{key:bad})))
  self.assertIsNone(d.validate_build_args(dict(ARGS,EXTRA='PRIVATE')))
 def test_near_match_command_injections_rejected(self):
  raw=header('python_provision');mark=marker('python_provision')
  for altered in (raw.replace('"'+'b'*64+'"','"'+'b'*64+';PRIVATE"'),raw.replace(' -I ',' '),raw.replace('/usr/bin/python3','/PRIVATE/python3'),raw.replace('RUN ','RUN echo PRIVATE && '),raw.replace('"'+'b'*64+'"','${BOOTSTRAP_SHA}')):
   self.assertIsNone(d._parse_lines(io.BytesIO((altered+mark).encode()),ARGS))
 def test_identical_switchback_headers_idempotent(self):
  h=header('closure_verify');other='#9 [internal] unrelated\n'
  raw=h+other+h+marker('closure_verify')+h+'#7 ERROR: PRIVATE\n'
  self.assertEqual(d._parse_lines(io.BytesIO(raw.encode()),ARGS)['source'],'closure_verify')
  self.assertEqual(d._inspect_lines(io.BytesIO(raw.encode()),len(raw),ARGS)['failed_stage'],'closure_verify')
 def test_rebound_header_or_source_still_rejected(self):
  h=header('closure_verify')
  for raw in (h+header('closure_verify',8)+marker('closure_verify'),h+h.replace('8/20','9/20')+marker('closure_verify'),h+h.replace('RUN ','RUN echo ')+marker('closure_verify')):
   self.assertIsNone(d._parse_lines(io.BytesIO(raw.encode()),ARGS))
   self.assertEqual(d._inspect_lines(io.BytesIO((raw+'#7 ERROR: x\n').encode()),len(raw)+12,ARGS)['failed_stage'],'unknown')
 def test_duplicate_failure_marker_remains_rejected(self):
  raw=header('closure_verify')+marker('closure_verify')+header('closure_verify')+marker('closure_verify')
  self.assertIsNone(d._parse_lines(io.BytesIO(raw.encode()),ARGS))
 def test_actual_plain_summary_shape_does_not_duplicate_marker(self):
  h=header('closure_verify');m=marker('closure_verify')
  # Official printErrorLogs emits original timestamp+payload, without #vertex.
  raw=h+m+'#7 ERROR: PRIVATE\n------\n > '+h.split('] ',1)[1].rstrip()+':\n'+m.split(' ',1)[1]+'------\n'
  self.assertEqual(d._parse_lines(io.BytesIO(raw.encode()),ARGS)['source'],'closure_verify')
 def test_binding_file_valid_bounded_duplicate_and_nonfile(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'args.json';p.write_text(json.dumps(ARGS));self.assertEqual(d.read_build_args(p),ARGS)
   p.write_text(json.dumps(ARGS)[:-1]+',"WORKLOAD_UID":1001}');self.assertIsNone(d.read_build_args(p))
   p.write_text(' '*4097);self.assertIsNone(d.read_build_args(p))
   p.write_bytes(b'\xff');self.assertIsNone(d.read_build_args(p))
   self.assertIsNone(d.read_build_args(Path(td)))
 def test_binding_file_symlink_hardlink_and_missing_refused(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'args';p.write_text(json.dumps(ARGS));q=Path(td)/'other'
   q.symlink_to(p);self.assertIsNone(d.read_build_args(q));q.unlink()
   os.link(p,q);self.assertIsNone(d.read_build_args(p));q.unlink()
   self.assertIsNone(d.read_build_args(Path(td)/'missing'))
 def test_binding_file_atime_ignored_but_mutation_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'args';p.write_text(json.dumps(ARGS));os.utime(p,(0,p.stat().st_mtime))
   self.assertEqual(d.read_build_args(p),ARGS)
   real=d.os.fstat;calls=[0]
   def changed(fd):
    calls[0]+=1
    if calls[0]==2:p.write_text(json.dumps(dict(ARGS,WORKLOAD_UID=2)))
    return real(fd)
   with patch.object(d.os,'fstat',side_effect=changed):self.assertIsNone(d.read_build_args(p))
if __name__=='__main__':unittest.main()
