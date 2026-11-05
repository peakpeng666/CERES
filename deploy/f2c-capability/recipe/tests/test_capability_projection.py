import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

R=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('capability_report',R/'report_capability_status.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
# Synthetic codebook intentionally differs from the production acceptance contract.
CODES={'schema':1,'authentication':{'pass_code':'AUTHENTICATED','codes':['AUTHENTICATED','TOOL_MISSING','CAP_MODE_REJECTED']},'capability':{'pass_code':'PROVED','codes':['PROVED','MAPPING_UNAVAILABLE']}}
CODES['mode_diagnostics']={'codes':['CAP_MODE_REJECTED'],'subjects':['ARCHIVE_KEYRING','UNKNOWN'],'checks':['COMPONENT_WRITABLE','UNKNOWN']}
RAW=json.dumps(CODES).encode()

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.outcomes={s:'success' for s in M.STAGES}
        self.write('capability-preparation-status.json','PASS','AUTHENTICATED')
        self.write('capability-probe-status.json','PASS','PROVED')
    def tearDown(self):self.tmp.cleanup()
    def write(self,name,status,code):
        (self.root/name).write_text(json.dumps({'schema':1,'status':status,'code':code}))
    def project(self,raw=RAW):return M.project(self.root,self.outcomes,raw)
    def test_indexed_code_sets_match_producers(self):
        actual=json.loads((R/'public_codes.json').read_bytes())
        for section,name,attribute,ok in (('authentication','prepare_capability_inputs','PREPARATION_CODES','CAP_INPUTS_VERIFIED'),('capability','probe_namespace_capabilities','PROBE_CODES','OK')):
            spec=importlib.util.spec_from_file_location('codes_'+name,R/(name+'.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertEqual(actual[section],{'pass_code':ok,'codes':sorted(getattr(module,attribute))})
            if section=='authentication':
                self.assertEqual(actual['mode_diagnostics'],{key:sorted(getattr(module,attr)) for key,attr in [('codes','MODE_CODES'),('subjects','MODE_SUBJECTS'),('checks','MODE_CHECKS')]})
                for code in module.MODE_CODES:
                    for subject in module.MODE_SUBJECTS:
                        for check in module.MODE_CHECKS:
                            self.assertEqual(M.mode_diagnostic_valid(actual['mode_diagnostics'],code,subject,check),module.mode_diagnostic_valid(code,subject,check))

    def test_production_public_codes_accept_only_production_pass_receipts(self):
        raw=(R/'public_codes.json').read_bytes()
        codes=json.loads(raw)
        self.assertEqual(codes['authentication']['pass_code'],'CAP_INPUTS_VERIFIED')
        self.assertEqual(codes['capability']['pass_code'],'OK')
        self.assertNotIn('PROVED',codes['capability']['codes'])
        self.write('capability-preparation-status.json','PASS','CAP_INPUTS_VERIFIED')
        self.write('capability-probe-status.json','PASS','OK')
        self.assertEqual(self.project(raw),'stage=capability status=PASS code=OK')
        self.write('capability-probe-status.json','PASS','PROVED')
        self.assertEqual(self.project(raw),'stage=report status=FAIL code=UNKNOWN')
        self.outcomes['capability']='failure'
        self.write('capability-probe-status.json','FAIL','PROVED')
        self.assertEqual(self.project(raw),'stage=capability status=FAIL code=UNKNOWN')

    def test_tool_subject_consistency(self):
        modes=json.loads((R/'public_codes.json').read_text())['mode_diagnostics']
        self.assertFalse(M.mode_diagnostic_valid(modes,'CAP_PYTHON_MODE_REJECTED','GROUP','COMPONENT_OWNER'))
        self.assertTrue(M.mode_diagnostic_valid(modes,'CAP_PYTHON_MODE_REJECTED','PYTHON','COMPONENT_OWNER'))
        self.assertTrue(M.mode_diagnostic_valid(modes,'CAP_PYTHON_MODE_REJECTED','UNKNOWN','UNKNOWN'))
        self.assertTrue(M.mode_diagnostic_valid(modes,'CAP_MODE_REJECTED','UNKNOWN','COMPONENT_OWNER'))
        for value in ('/private/SECRET',1001,{},None):
            self.assertFalse(M.mode_diagnostic_valid(modes,'CAP_MODE_REJECTED',value,'COMPONENT_OWNER'))

    def test_finite_mode_details(self):
        self.outcomes['authentication']='failure';self.outcomes['capability']='skipped'
        row={'schema':1,'status':'FAIL','code':'CAP_MODE_REJECTED','subject':'ARCHIVE_KEYRING','check':'COMPONENT_WRITABLE'}
        path=self.root/'capability-preparation-status.json';path.write_text(json.dumps(row))
        self.assertEqual(self.project(),'stage=authentication status=FAIL code=CAP_MODE_REJECTED subject=ARCHIVE_KEYRING check=COMPONENT_WRITABLE')
    def test_mode_details_reject_private_and_invalid_values(self):
        self.outcomes['authentication']='failure';self.outcomes['capability']='skipped'
        valid={'schema':1,'status':'FAIL','code':'CAP_MODE_REJECTED','subject':'ARCHIVE_KEYRING','check':'COMPONENT_WRITABLE'}
        variants=[]
        for key in ('subject','check'):
            for value in ('/home/private/token','https://private.invalid','SECRET_USERNAME','0777',1001,{'secret':'token'},None,'X'*4096):
                variants.append(dict(valid,**{key:value}))
            row=dict(valid);del row[key];variants.append(row)
        variants.extend([dict(valid,uid=1001),dict(valid,mode='0777'),dict(valid,path='/private/token'),dict(valid,code='TOOL_MISSING'),dict(valid,status='PASS')])
        for row in variants:
            with self.subTest(row=row):
                (self.root/'capability-preparation-status.json').write_text(json.dumps(row))
                self.assertEqual(self.project(),'stage=authentication status=FAIL code=UNKNOWN')
    def test_mode_fields_rejected_on_capability_receipt(self):
        self.outcomes['capability']='failure'
        self.write('capability-probe-status.json','FAIL','MAPPING_UNAVAILABLE')
        p=self.root/'capability-probe-status.json';row=json.loads(p.read_text());row.update(subject='ARCHIVE_KEYRING',check='COMPONENT_WRITABLE');p.write_text(json.dumps(row))
        self.assertEqual(self.project(),'stage=capability status=FAIL code=UNKNOWN')

    def test_success(self):self.assertEqual(self.project(),'stage=capability status=PASS code=PROVED')
    def test_known_failure(self):
        self.outcomes['capability']='failure';self.write('capability-probe-status.json','FAIL','MAPPING_UNAVAILABLE')
        self.assertEqual(self.project(),'stage=capability status=FAIL code=MAPPING_UNAVAILABLE')
    def test_prior_stage_failure(self):
        self.outcomes['helper_tests']='failure'
        self.assertEqual(self.project(),'stage=helper_tests status=FAIL code=HELPER_TESTS_FAILED')
    def test_private_strings_never_emitted(self):
        self.outcomes['capability']='failure'
        for secret in ('SECRET_TOKEN','/home/private/data','https://private.invalid/token','RuntimeError: password','BAD\nTOKEN'):
            with self.subTest(secret=secret):
                self.write('capability-probe-status.json','FAIL',secret)
                self.assertEqual(self.project(),'stage=capability status=FAIL code=UNKNOWN')
    def test_wrong_schema_unknown_fields_duplicates(self):
        path=self.root/'capability-probe-status.json'
        for raw in (b'{"schema":true,"status":"PASS","code":"PROVED"}',b'{"schema":1,"status":"PASS","code":"PROVED","secret":"x"}',b'{"schema":1,"status":"PASS","code":"PROVED","code":"PROVED"}'):
            path.write_bytes(raw)
            self.assertEqual(self.project(),'stage=report status=FAIL code=UNKNOWN')
    def test_outcome_mismatch(self):
        self.outcomes['capability']='failure'
        self.assertEqual(self.project(),'stage=capability status=FAIL code=UNKNOWN')
    def test_missing_oversize_nonutf8(self):
        path=self.root/'capability-probe-status.json'
        for data in (None,b' '*4097,b'\xff'):
            if data is None:path.unlink()
            else:path.write_bytes(data)
            self.assertEqual(self.project(),'stage=report status=FAIL code=UNKNOWN')
    def test_nonregular_or_link(self):
        path=self.root/'capability-probe-status.json';path.unlink()
        original=self.root/'other';original.write_bytes(b'{}')
        path.symlink_to(original);self.assertEqual(self.project(),'stage=report status=FAIL code=UNKNOWN');path.unlink()
        os.link(original,path);self.assertEqual(self.project(),'stage=report status=FAIL code=UNKNOWN');path.unlink()
        os.mkfifo(path);self.assertEqual(self.project(),'stage=report status=FAIL code=UNKNOWN')
    def test_unverified_or_invalid_code_bytes(self):
        for data in (None,b'{}',b'[]',b'null',b'{"schema":1,"schema":1}',b' '*16385):
            self.assertEqual(self.project(data),'stage=report status=FAIL code=UNKNOWN')
    def test_untrusted_outcomes(self):
        self.outcomes['capability']='https://private.invalid/token'
        self.assertEqual(self.project(),'stage=report status=FAIL code=UNKNOWN')

if __name__=='__main__':unittest.main()
