"""Pure producer contracts: no preparation/native commands are invoked."""
import ast
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
R=Path(__file__).resolve().parents[1]
PATHS={'closure_verify':'preparation/verify_bootstrap_closure.py','python_provision':'preparation/bootstrap_tools.py','inputs_prepare':'preparation/inputs/recipe/prepare_inputs.py','inputs_freeze':'freeze_input_index.py'}
def load(path):
 spec=importlib.util.spec_from_file_location('test_producer_'+path.stem,path)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
S=load(R/'preparation_stage_diagnostic.py')
M={k:load(R/v) for k,v in PATHS.items()}
class Producers(unittest.TestCase):
 def test_every_literal_schema_parity(self):
  for stage,m in M.items():
   for sub in m.DIAGNOSTIC_SUBSTAGES:
    m.diagnostic_phase(sub)
    for message,code in m.DIAGNOSTIC_MESSAGES.items():
     d=m.diagnostic_detail(RuntimeError(message));self.assertEqual(d['code'],code);self.assertEqual(S.validate(d),d)
 def test_every_dynamic_prefix_private_suffix(self):
  for stage,m in M.items():
   for prefix,code in m.DIAGNOSTIC_PREFIXES.items():
    d=m.diagnostic_detail(RuntimeError(prefix+'PRIVATE /secret https://private.invalid KEY=secret'))
    self.assertEqual(d['code'],code);self.assertEqual(S.validate(d),d)
    self.assertNotIn('PRIVATE',json.dumps(d));self.assertNotIn('/secret',json.dumps(d))
 def test_unknown_message_and_subclass_not_classified(self):
  class Other(RuntimeError):pass
  for m in M.values():
   for e in (RuntimeError('PRIVATE'),Other(next(iter(m.DIAGNOSTIC_MESSAGES)))):
    self.assertEqual(m.diagnostic_detail(e)['code'],'UNKNOWN_FAILURE')
 def test_exception_str_never_used(self):
  class Private(Exception):
   def __str__(self):raise AssertionError('must not stringify')
  for m in M.values():self.assertEqual(m.diagnostic_detail(Private())['exception_class'],'OtherError')
 def test_unknown_substage_resets_finite_entry(self):
  for m in M.values():
   m.diagnostic_phase('PRIVATE');self.assertEqual(m.diagnostic_detail(ValueError())['substage'],'entry')
 def test_marker_is_finite_schema(self):
  for m in M.values():
   f=io.StringIO()
   with contextlib.redirect_stderr(f):m.emit_diagnostic(RuntimeError('PRIVATE'))
   raw=f.getvalue();self.assertTrue(raw.startswith(S.PREFIX));self.assertLess(len(raw),S.MAX_BYTES)
   self.assertIsNotNone(S.validate(json.loads(raw[len(S.PREFIX):])))
 def test_diagnostic_write_failure_is_nonmasking(self):
  for m in M.values():
   with patch('builtins.print',side_effect=OSError('PRIVATE')):m.emit_diagnostic(RuntimeError('original'))
 def test_ca_exact_owned_type_and_numeric_mapping(self):
  m=M['closure_verify'];ca=load(R/'preparation/bootstrap/provision_system_ca.py')
  original=m._DIAGNOSTIC_CA_FAILURE_TYPE
  try:
   m._DIAGNOSTIC_CA_FAILURE_TYPE=ca.Failure
   for number,code in m.DIAGNOSTIC_CA_CODES.items():
    e=ca.Failure(number);d=m.diagnostic_detail(e)
    self.assertEqual(d['code'],code);self.assertEqual(d['exception_class'],'SystemCAFailure');self.assertIsNotNone(S.validate(d))
   class Fake(RuntimeError):
    code=71
   self.assertEqual(m.diagnostic_detail(Fake())['code'],'UNKNOWN_FAILURE')
   for number in (70,78,True,'71'):
    self.assertEqual(m.diagnostic_detail(ca.Failure(number))['code'],'UNKNOWN_FAILURE')
  finally:m._DIAGNOSTIC_CA_FAILURE_TYPE=original
 def test_ca_original_error_object(self):
  m=M['closure_verify'];ca=load(R/'preparation/bootstrap/provision_system_ca.py');error=ca.Failure(72)
  ca.verify=lambda:(_ for _ in ()).throw(error)
  with patch.object(m.importlib.util,'spec_from_file_location'),patch.object(m.importlib.util,'module_from_spec',return_value=ca),patch.object(ca,'__spec__',create=True):
   # Fake loader only supplies the exact module; do not execute any CA verifier.
   spec=m.importlib.util.spec_from_file_location.return_value
   spec.loader.exec_module=lambda module:None
   with self.assertRaises(ca.Failure) as got:m.verify_system_ca()
   self.assertIs(got.exception,error)
 def run_cli_failure(self,stage,error,emit=None):
  # Execute only the real CLI exception handling, replacing its success body
  # with a pure injection. No preparer, argparse or native operation runs.
  tree=ast.parse((R/PATHS[stage]).read_text());entry=next(x for x in tree.body if isinstance(x,ast.If) and isinstance(x.test,ast.Compare) and ast.unparse(x.test)=="__name__ == '__main__'")
  handlers=copy.deepcopy(entry.body[0]);self.assertIsInstance(handlers,ast.Try)
  handlers.body=[ast.Raise(exc=ast.Name(id='injected',ctx=ast.Load()),cause=None)]
  module=ast.fix_missing_locations(ast.Module(body=[handlers],type_ignores=[]))
  namespace=dict(M[stage].__dict__,injected=error)
  if emit:namespace['emit_diagnostic']=emit
  with contextlib.redirect_stderr(io.StringIO()):exec(compile(module,'<pure-cli-handlers>','exec'),namespace)
 def test_same_exception_rethrow(self):
  for stage in M:
   error=LookupError('PRIVATE')
   with self.assertRaises(LookupError) as got:self.run_cli_failure(stage,error)
   self.assertIs(got.exception,error)
 def test_original_prepare_inputs_exit2(self):
  for cls in (RuntimeError,OSError,ValueError,KeyError,TypeError):
   with self.assertRaises(SystemExit) as got:self.run_cli_failure('inputs_prepare',cls('PRIVATE'))
   self.assertEqual(got.exception.code,2)
 def test_cancellation_not_newly_handled(self):
  for stage in M:
   for error in (KeyboardInterrupt(),SystemExit(42),GeneratorExit()):
    with self.assertRaises(type(error)) as got:self.run_cli_failure(stage,error,lambda e:self.fail('handled BaseException'))
    self.assertIs(got.exception,error)
 def test_write_failure_preserves_rethrow(self):
  for stage in M:
   error=LookupError('original')
   with patch('builtins.print',side_effect=OSError('diagnostic write')):
    with self.assertRaises(LookupError) as got:self.run_cli_failure(stage,error)
   self.assertIs(got.exception,error)
 def test_predicate_bodies_only_add_phase_tracking(self):
  # Compare source functions to an independently retained base if available.
  baseline=json.loads((R/'tests/fixtures/v14-producer-functions.json').read_text())
  class Strip(ast.NodeTransformer):
   def visit_Expr(self,node):
    if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='diagnostic_phase':return None
    return self.generic_visit(node)
   def visit_Global(self,node):
    if node.names==['_DIAGNOSTIC_CA_FAILURE_TYPE']:return None
    return node
   def visit_Assign(self,node):
    if len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='_DIAGNOSTIC_CA_FAILURE_TYPE':return None
    return self.generic_visit(node)
  for stage,rel in PATHS.items():
   old={name:ast.parse(source).body[0] for name,source in baseline[rel].items()}
   new={n.name:n for n in ast.parse((R/rel).read_text()).body if isinstance(n,ast.FunctionDef)}
   for name,n in old.items():
    self.assertEqual(ast.dump(n),ast.dump(Strip().visit(new[name])),(stage,name))
if __name__=='__main__':unittest.main()
