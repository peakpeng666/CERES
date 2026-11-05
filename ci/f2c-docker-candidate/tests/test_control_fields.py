"""Data-only Debian control parser regressions; no package execution."""
import ast
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('control_parser_fixture',ROOT/'preparation/prepare_packages.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class ControlFieldsTests(unittest.TestCase):
 def test_authenticated_font_inventory_original_validator(self):
  status=(ROOT/'tests/fixtures/fonts-dejavu-core-status.txt').read_text()
  fields=p.fields(status)
  self.assertEqual(fields['Version'],'2.37-2build1')
  self.assertEqual(len(fields['Conffiles'].splitlines())-1,12)
  after=p.inventory(status)
  p.validate_inventory_delta({},after,[dict(name='fonts-dejavu-core',version='2.37-2build1',architecture='all')])
 def test_real_version_difference_still_rejected(self):
  status=(ROOT/'tests/fixtures/fonts-dejavu-core-status.txt').read_text()
  with self.assertRaises(RuntimeError):p.validate_inventory_delta({},p.inventory(status),[dict(name='fonts-dejavu-core',version='2.37-OTHER',architecture='all')])
 def test_empty_initial_header(self):
  self.assertEqual(p.fields('Version: 1\nConffiles:\n /a abc\nDescription: d'),{'Version':'1','Conffiles':'\n /a abc','Description':'d'})
 def test_empty_header_no_continuation(self):
  self.assertEqual(p.fields('A:\nB: b'),{'A':'','B':'b'})
 def test_normal_headers(self):
  self.assertEqual(p.fields('Package: p\nVersion: 1:2-3'),{'Package':'p','Version':'1:2-3'})
 def test_horizontal_whitespace(self):
  self.assertEqual(p.fields('A:\t value \t\nB:value\nC: \t\n\tcontinued'),{'A':'value','B':'value','C':'\n\tcontinued'})
 def test_indented_version_is_description(self):
  self.assertEqual(p.fields('Version: 1\nDescription: d\n Version: 99\n\tVersion: 88')['Version'],'1')
 def test_duplicate_empty_header_rejected(self):
  with self.assertRaises(RuntimeError):p.fields('A:\nA: x')
 def test_duplicate_normal_header_rejected(self):
  with self.assertRaises(RuntimeError):p.fields('A: a\nA: b')
 def test_malformed_headers(self):
  for value in ('no colon', ': value','Bad Name: x','#Bad: x','-Bad: x','Bad\x00: x','Badé: x'):
   with self.subTest(value=value),self.assertRaises(RuntimeError):p.fields(value)
 def test_orphan_continuation_rejected(self):
  with self.assertRaises(RuntimeError):p.fields(' continuation')
 def test_stanza_boundary_does_not_attach(self):
  with self.assertRaises(RuntimeError):p.fields('A: a\n\n continuation')
 def test_other_identity_and_held_unchanged(self):
  status='Package: example\nStatus: hold ok installed\nArchitecture: amd64\nVersion: 1:2-3\nDepends: other\nConffiles:\n /a abc\nDescription: d\n'
  row=dict(name='example',version='1:2-3',architecture='amd64',held=True)
  before={'example:amd64':row}
  self.assertEqual(p.inventory(status),before)
  p.validate_inventory_delta(before,p.inventory(status),[])
 def test_original_validator_exact(self):
  tree=ast.parse((ROOT/'preparation/prepare_packages.py').read_text())
  fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='validate_inventory_delta')
  expected=ast.parse("""def validate_inventory_delta(before, after, transaction):
    expected = {k: dict(v) for k, v in before.items()}
    for row in transaction:
        key = row['name'] + ':' + row['architecture']
        expected[key] = {'name': row['name'], 'version': row['version'],
                         'architecture': row['architecture'], 'held': False}
    require(expected == after, 'Unexpected installed inventory change; no readiness lock emitted')
""").body[0]
  self.assertEqual(ast.dump(fn),ast.dump(expected))
if __name__=='__main__':unittest.main()
