"""Closed-schema package diagnostics; fixtures never disclose private strings."""
import importlib.util
import hashlib
import textwrap
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
R = Path(__file__).resolve().parents[1]
S = importlib.util.spec_from_file_location('structured_projector', R / 'project_failure_code.py')
M = importlib.util.module_from_spec(S)
S.loader.exec_module(M)

def receipt():
    return dict(schema=1, code='APT_HTTP_ERROR', operation='index-download', exception_class='HTTPError', http_status=404, errno=None, returncode=None, suite='jammy', component='main')

class StructuredProjectionTests(unittest.TestCase):
    def test_package_producer_and_projector_share_closed_enums(self):
        spec = importlib.util.spec_from_file_location('package_receipt_contract', R / 'prepare_jammy_packages.py')
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        for producer_name, consumer_name in (
            ('DIAGNOSTIC_CODES', 'PACKAGE_CODES'),
            ('DIAGNOSTIC_OPERATIONS', 'PACKAGE_OPERATIONS'),
            ('DIAGNOSTIC_CLASSES', 'PACKAGE_CLASSES'),
            ('DIAGNOSTIC_ERRNOS', 'PACKAGE_ERRNOS'),
        ):
            self.assertEqual(getattr(producer, producer_name), getattr(M, consumer_name))
        producer.phase('native-solver')
        emitted = producer.failure_diagnostic(AttributeError('PRIVATE_SENTINEL /private/path'))
        self.assertEqual(M.validated_package_receipt(json.dumps(emitted)), emitted)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(emitted))

    def test_all_provider_reasons_are_finite_and_hide_private_details(self):
        codes = {
            'APT_PROVIDER_COEXISTENCE_REJECTED',
            'APT_PROVIDER_VERSION_UNAUTHENTICATED',
            'APT_PROVIDER_CAPABILITY_MISSING',
            'APT_PROVIDER_OWNER_MISMATCH',
            'APT_PROVIDER_EXECUTABLE_MISSING',
        }
        for code in codes:
            d = receipt()
            d.update(code=code, operation='provider-selection', exception_class='ProviderSelectionError', http_status=None, suite=None, component=None)
            self.assertEqual(M.validated_package_receipt(json.dumps(d)), d)
        for code in ('APT_PROVIDER_PRIVATE_SENTINEL', '/private/path', 'https://private.invalid/token'):
            d = receipt(); d['code'] = code
            self.assertIsNone(M.validated_package_receipt(json.dumps(d)))

    def test_closure_reasons_do_not_disclose_package_or_version(self):
        for code in ('APT_CLOSURE_PACKAGE_UNAVAILABLE', 'APT_CLOSURE_DOWNGRADE_REFUSED', 'APT_CLOSURE_PIN_UNRESOLVED', 'APT_CLOSURE_NO_PROGRESS'):
            d = receipt()
            d.update(code=code, operation='closure-provenance', exception_class='ClosureProvenanceError', http_status=None, suite=None, component=None)
            self.assertEqual(M.validated_package_receipt(json.dumps(d)), d)
            for key in ('package', 'version', 'selected_version', 'installed_version'):
                private = dict(d); private[key] = 'PRIVATE_SENTINEL'
                self.assertIsNone(M.validated_package_receipt(json.dumps(private)))

    def test_accepts_exact_public_receipt(self):
        d = receipt()
        self.assertEqual(M.validated_package_receipt(json.dumps(d)), d)

    def test_rejects_unknown_or_private_strings_in_every_field(self):
        for key in receipt():
            with self.subTest(key=key):
                d = receipt(); d[key] = 'PRIVATE_SENTINEL /private/path https://private.invalid/token'
                self.assertIsNone(M.validated_package_receipt(json.dumps(d)))

    def test_rejects_added_private_field(self):
        d = receipt(); d['url'] = 'https://private.invalid/token'
        self.assertIsNone(M.validated_package_receipt(json.dumps(d)))

    def test_rejects_missing_fields(self):
        for key in receipt():
            d = receipt(); del d[key]
            self.assertIsNone(M.validated_package_receipt(json.dumps(d)))

    def test_rejects_boolean_numbers(self):
        for key in ('schema','http_status','returncode','errno'):
            d = receipt(); d[key] = True
            self.assertIsNone(M.validated_package_receipt(json.dumps(d)))

    def test_rejects_unbounded_numbers(self):
        for key,value in [('http_status',600),('http_status',99),('returncode',256),('returncode',-256),('errno',999999)]:
            d = receipt(); d[key] = value
            self.assertIsNone(M.validated_package_receipt(json.dumps(d)))

    def test_rejects_duplicate_keys_and_large_or_nested_input(self):
        raw = json.dumps(receipt())
        self.assertIsNone(M.validated_package_receipt(raw[:-1]+',"code":"APT_HTTP_ERROR"}'))
        for value in (' '*4097, '[]', '{"code": []}', '{'):
            self.assertIsNone(M.validated_package_receipt(value))

    def test_invalid_latest_receipt_does_not_reveal_previous_data(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'private.log'
            p.write_text(M.PACKAGE_MARKER+json.dumps(receipt())+'\n'+M.PACKAGE_MARKER+'{"url":"https://private.invalid/token"}\n')
            self.assertEqual(M.project_details('packages',str(p)), {'code':'UNKNOWN'})

    def test_json_cli_projects_only_safe_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'private.log'
            p.write_text('URLError: PRIVATE_SENTINEL /private/path https://private.invalid/token\n'+M.PACKAGE_MARKER+json.dumps(receipt())+'\n')
            result = subprocess.run([sys.executable,'-I','-B',str(R/'project_failure_code.py'),'--json','--stage','packages','--log',str(p)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout),receipt())
            self.assertEqual(result.stderr,'')
            for forbidden in ('PRIVATE_SENTINEL','/private/path','https://private.invalid','token'):
                self.assertNotIn(forbidden,result.stdout)

    def test_other_stage_does_not_adopt_package_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'private.log';p.write_text(M.PACKAGE_MARKER+json.dumps(receipt()))
            self.assertEqual(M.project_details('native_validation',str(p)),{'code':'UNKNOWN'})


class CanonicalWorkflowProjectionTests(unittest.TestCase):
    def run_wrapper(self, target, corrupt=False, absent=False):
        script=(R/'report_candidate_failure.py').read_text()
        d=receipt()
        d.update(code='APT_CLOSURE_DOWNGRADE_REFUSED',operation='closure-provenance',exception_class='ClosureProvenanceError',http_status=None,suite=None,component=None,returncode=1)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'recipe').mkdir();(root/'private-workflow-evidence').mkdir()
            generic=(R/'project_failure_code.py').read_bytes()
            (root/'recipe/project_failure_code.py').write_bytes(generic)
            canonical=('def project(root,receipt,log_path):\n    return '+repr(target)+'\n').encode()
            if not absent:(root/'recipe/project_canonical_target.py').write_bytes(canonical+(b'# changed' if corrupt else b''))
            index={'files':{'recipe/project_failure_code.py':{'sha256':hashlib.sha256(generic).hexdigest()},'recipe/project_canonical_target.py':{'sha256':hashlib.sha256(canonical).hexdigest()}}}
            raw=json.dumps(index).encode();(root/'source-index.json').write_bytes(raw)
            (root/'private-workflow-evidence/packages.log').write_text('PRIVATE_SENTINEL /private/path https://private.invalid\n'+M.PACKAGE_MARKER+json.dumps(d)+'\n')
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','CANDIDATE_ROOT':td,'REVIEWED_SOURCE_INDEX_SHA256':hashlib.sha256(raw).hexdigest(),'FAILED_STAGE':'packages'}
            result=subprocess.run([sys.executable,'-I','-B','-'],input=script,text=True,capture_output=True,env=env,check=True)
            self.assertEqual(result.stderr,'')
            self.assertNotIn('PRIVATE_SENTINEL',result.stdout)
            self.assertNotIn('/private/path',result.stdout)
            self.assertNotIn('https://private.invalid',result.stdout)
            return result.stdout

    def target(self):
        return dict(canonical_package='libpq-dev',canonical_version='14.24-0ubuntu0.22.04.1',canonical_architecture='amd64',target_snapshot='20261021T000000Z',target_suite='jammy-updates',target_component='main',target_stanza=123)

    def test_verified_canonical_target(self):
        out=self.run_wrapper(self.target())
        self.assertIn('target_status=VERIFIED canonical_package=libpq-dev',out)
        self.assertIn('returncode=1',out)

    def test_invalid_canonical_outputs_never_disclosed(self):
        for key,value in [('canonical_package','PRIVATE_SENTINEL'),('canonical_version','https://private.invalid'),('canonical_architecture','/private/path'),('target_suite','PRIVATE_SENTINEL'),('target_component','PRIVATE_SENTINEL'),('target_snapshot','PRIVATE_SENTINEL'),('target_stanza',True),('installed_version','PRIVATE_SENTINEL')]:
            with self.subTest(key=key):
                target=self.target();target[key]=value
                out=self.run_wrapper(target)
                self.assertIn('code=APT_CLOSURE_DOWNGRADE_REFUSED',out)
                self.assertIn('target_status=UNAVAILABLE',out)
                self.assertNotIn('canonical_package=',out)

    def test_changed_helper_is_not_executed(self):
        self.assertIn('target_status=UNAVAILABLE',self.run_wrapper(self.target(),corrupt=True))

    def test_missing_helper_retains_original_failure(self):
        self.assertIn('code=APT_CLOSURE_DOWNGRADE_REFUSED',self.run_wrapper(self.target(),absent=True))
        self.assertIn('target_status=UNAVAILABLE',self.run_wrapper({},absent=True))

if __name__ == '__main__': unittest.main()
