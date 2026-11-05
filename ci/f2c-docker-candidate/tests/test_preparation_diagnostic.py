import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import preparation_diagnostic as d
from public_receipt import project
ROOT=Path(__file__).resolve().parents[1]

class PreparationDiagnosticTests(unittest.TestCase):
    def test_exact_known_error_is_projected_without_free_text(self):
        row=d.project_exception(RuntimeError('SOURCE_CHANGED'),'source_verification')
        self.assertEqual(row,{'schema':1,'stage':'source_verification','code':'SOURCE_CHANGED','exception_type':'RuntimeError'})

    def test_unknown_error_text_commands_urls_and_headers_never_exposed(self):
        errors=[RuntimeError('PRIVATE_SECRET https://private/path Authorization: PRIVATE'),subprocess.CalledProcessError(17,['PRIVATE_COMMAND'],output='PRIVATE_OUTPUT',stderr='PRIVATE_HEADER'),ValueError('PRIVATE'),type('PRIVATE_CLASS',(Exception,),{})('PRIVATE')]
        for error in errors:
            row=d.project_exception(error,'PRIVATE_STAGE')
            self.assertNotIn('PRIVATE',json.dumps(row))
            self.assertEqual(row['code'],'UNCLASSIFIED_PREPARATION_FAILURE')
            self.assertEqual(row['stage'],'entry')

    def test_bounded_exact_schema_only(self):
        good=d.project_exception(RuntimeError('SOURCE_CHANGED'),'source_verification')
        bad=[dict(good,raw='PRIVATE'),dict(good,stage='PRIVATE'),dict(good,code='PRIVATE'),dict(good,exception_type='PRIVATE'),dict(good,schema=True),dict(good,code=['SOURCE_CHANGED']),{},'PRIVATE',None]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'diagnostic'
            for row in bad:
                path.write_text(json.dumps(row));self.assertIsNone(d.read_projected(path))
            for raw in ['PRIVATE\n'+json.dumps(good),'{"schema":1,"schema":1}', 'x'*4097]:
                path.write_text(raw);self.assertIsNone(d.read_projected(path))
            path.write_text(json.dumps(good));self.assertEqual(d.read_projected(path),good)
            alias=path.with_name('alias');alias.symlink_to(path);self.assertIsNone(d.read_projected(alias))

    def test_only_failed_preparation_receipt_exposes_validated_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);row=d.project_exception(RuntimeError('SOURCE_CHANGED'),'source_verification')
            (root/'preparation-stderr.log').write_text(json.dumps(row))
            for phase,code,wanted in [('preparation',1,row),('preparation',0,None),('bootstrap',1,None),('isolation_and_build',1,None)]:
                (root/'terminal.json').write_text(json.dumps({'phase':phase,'exit_code':code}))
                self.assertEqual(project(root)['preparation_failure_detail'],wanted)

    def test_preparation_main_emits_only_closed_json_on_failure(self):
        spec=importlib.util.spec_from_file_location('preparation_main_fixture',ROOT/'preparation/prepare_image.py')
        p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
        import contextlib,io
        arguments=['fixture','--execute-trusted-preparation']
        for flag in ('source','application','uv','bootstrap-lock','output','expected-source-index-sha256','expected-bootstrap-sha256'):
            arguments.extend(['--'+flag,'PRIVATE'])
        output=io.StringIO()
        with patch.object(sys,'argv',arguments),patch.object(p,'execute',side_effect=ValueError('PRIVATE trace URL header')),contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as caught:p.main()
        self.assertEqual(caught.exception.code,1)
        row=json.loads(output.getvalue());self.assertEqual(row['exception_type'],'ValueError')
        self.assertNotIn('PRIVATE',output.getvalue())

if __name__=='__main__':unittest.main()
