"""Fake backend and ordinary temporary files only; never invokes Docker."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cleanup_controller',
    Path(os.environ.get('CLEANUP_CONTROLLER_ROOT', ROOT))/'engine_controller.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
CID = 'c'*64
IMAGE = 'sha256:'+'a'*64
NONCE = 'b'*64


class CleanupTests(unittest.TestCase):
    def exercise(self, *, write_errors=None, backend_error=None, cleanup_error=None,
                 export_error=None, exit_code=0):
        self.calls = []
        self.exports = []
        write_errors = write_errors or {}
        original_write = Path.write_text
        original_read = Path.read_text
        def write(path, *args, **kwargs):
            if path.name in write_errors: raise write_errors[path.name]
            return original_write(path, *args, **kwargs)
        def read(path, *args, **kwargs):
            if str(path)=='/proc/sys/kernel/random/boot_id': return 'fixture-boot'
            return original_read(path, *args, **kwargs)
        def call(argv):
            self.calls.append(argv)
            action = argv[1]
            if action == 'rm':
                self.assertEqual(argv, ['docker','rm','-f',CID])
                if cleanup_error: raise cleanup_error
                return b''
            if backend_error and action==backend_error[0]: raise backend_error[1]
            if action=='info': return json.dumps({'SecurityOptions':['name=seccomp,profile=builtin'],'MemTotal':8*1024**3}).encode()
            if action=='image': return json.dumps([{'Id':IMAGE,'Os':'linux','Architecture':'amd64'}]).encode()
            if action=='create': return (CID+'\n').encode()
            self.assertEqual(argv[-1], CID)
            if action=='start': return b''
            if action=='inspect': return json.dumps([{'State':{'Running':True,'Pid':123}}]).encode()
            if action=='logs': return (json.dumps({'kind':'ready','nonce':NONCE,'observed':{'netns':'net:[2]'}})+'\n'+json.dumps({'kind':'finished','nonce':NONCE,'exit_code':exit_code})).encode()
            self.fail('Unexpected fake operation: '+repr(argv))
        def export(cid, out, bound):
            self.assertEqual(cid, CID)
            self.exports.append(cid)
            if export_error: raise export_error
            (out/'private-output.tar').write_bytes(b'fake evidence')
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp).resolve()
            args=SimpleNamespace(inputs=str(root/'inputs'),output=str(root/'output'),
                expected_input_sha256='d'*64,phase='build',image=IMAGE,
                out_bytes=1024,memory_bytes=2048,timeout=1)
            patches = [patch.object(c,'call',side_effect=call),
                patch.object(c,'verify_inputs',return_value={'commands':{'build':['fixture']}}),
                patch.object(c,'check_inspect'),patch.object(c,'validate_inside'),
                patch.object(c,'start_identity',return_value='start'),
                patch.object(c,'export_output',side_effect=export),
                patch.object(c.shutil,'disk_usage',return_value=SimpleNamespace(free=10*1024**3)),
                patch.object(c.os,'getuid',return_value=1001),patch.object(c.os,'getgid',return_value=1001),
                patch.object(c.os,'readlink',return_value='net:[2]'),
                patch.object(c.secrets,'token_hex',return_value=NONCE),
                patch.object(Path,'write_text',write),patch.object(Path,'read_text',read),
                patch.object(c.subprocess,'run',side_effect=AssertionError('real subprocess prohibited')),
                patch.object(c.subprocess,'Popen',side_effect=AssertionError('real subprocess prohibited'))]
            for item in patches: stack.enter_context(item)
            try:
                return c.run(args)
            finally:
                self.files={p.relative_to(root/'output').as_posix():p.read_bytes()
                    for p in (root/'output').rglob('*') if p.is_file()}

    def removed_once(self):
        self.assertEqual([x for x in self.calls if x[1]=='rm'], [['docker','rm','-f',CID]])

    def test_evidence_write_failure_cleans_owned_container_without_start(self):
        error=OSError('disk full writing ID')
        with self.assertRaises(OSError) as caught:
            self.exercise(write_errors={'container-id':error})
        self.assertIs(caught.exception,error)
        self.removed_once()
        self.assertNotIn('start',[x[1] for x in self.calls])

    def test_later_evidence_write_failures_always_cleanup(self):
        for name in ('inspect-approved.json','approval.tmp','result.json','release'):
            error=OSError('failed evidence: '+name)
            with self.subTest(name=name):
                with self.assertRaises(OSError) as caught:
                    self.exercise(write_errors={name:error})
                self.assertIs(caught.exception,error)
                self.removed_once()

    def test_normal_lifecycle_and_nonzero_stage_result(self):
        for code in (0,17):
            with self.subTest(code=code):
                self.assertEqual(self.exercise(exit_code=code),code)
                self.removed_once()
                self.assertEqual(self.exports,[CID])
                self.assertEqual(json.loads(self.files['result.json'])['exit_code'],code)

    def test_cancellation_at_evidence_write_and_started_work(self):
        for where in ('write','start','logs','inspect'):
            for error in (KeyboardInterrupt(),SystemExit(23)):
                with self.subTest(where=where,error=type(error).__name__):
                    kwargs=({'write_errors':{'container-id':error}} if where=='write' else
                        {'backend_error':(where,error)})
                    with self.assertRaises(type(error)) as caught: self.exercise(**kwargs)
                    self.assertIs(caught.exception,error)
                    self.removed_once()

    def test_failure_reporting_io_does_not_mask_original_or_skip_cleanup(self):
        original=KeyboardInterrupt('cancelled')
        for diagnostic in ('controller-failure.json','export-failure.json'):
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    self.exercise(backend_error=('logs',original),
                        write_errors={diagnostic:OSError('disk full')},export_error=RuntimeError('export failed'))
                self.assertIs(caught.exception,original)
                self.removed_once()

    def test_cleanup_failure_preserves_primary_even_when_record_fails(self):
        for primary in (OSError('ID write failed'),KeyboardInterrupt(),SystemExit(17)):
            with self.subTest(primary=type(primary).__name__):
                with self.assertRaises(type(primary)) as caught:
                    self.exercise(write_errors={'container-id':primary,
                        'cleanup-failure.json':OSError('report failed')},cleanup_error=RuntimeError('remove failed'))
                self.assertIs(caught.exception,primary)
                self.removed_once()

    def test_cleanup_failure_is_visible_after_success_even_when_record_fails(self):
        for record_fails in (False,True):
            error=RuntimeError('remove failed')
            with self.subTest(record_fails=record_fails):
                with self.assertRaises(RuntimeError) as caught:
                    self.exercise(cleanup_error=error,write_errors=(
                        {'cleanup-failure.json':OSError('report failed')} if record_fails else {}))
                self.assertIs(caught.exception,error)
                self.removed_once()
                if not record_fails:
                    self.assertFalse(json.loads(self.files['cleanup-failure.json'])['removed'])

    def test_precreate_failure_never_removes_any_container(self):
        with self.assertRaisesRegex(RuntimeError,'create failed'):
            self.exercise(backend_error=('create',RuntimeError('create failed')))
        self.assertFalse(any(x[1]=='rm' for x in self.calls))

    def test_cleanup_call_retains_sixty_second_timeout_and_exact_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(c,'CLIENT_CONFIG',Path(tmp)/'client'), \
                 patch.object(c.subprocess,'run',side_effect=subprocess.TimeoutExpired('fake',60)) as run:
                with self.assertRaises(subprocess.TimeoutExpired): c.call(['docker','rm','-f',CID])
                self.assertEqual(run.call_args.kwargs['timeout'],60)
                self.assertEqual(run.call_args.args[0][-3:],['rm','-f',CID])

if __name__=='__main__': unittest.main()
