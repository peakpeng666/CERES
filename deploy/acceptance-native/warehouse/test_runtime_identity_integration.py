"""Pure optional-evidence integration tests; no sockets, devices or native runs."""
import ast
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import warehouse_acceptance as acceptance
import warehouse_public_summary as public


class Cancel(BaseException):pass


class RuntimeIdentityIntegrationTests(unittest.TestCase):
    def errors(self):
        return (ValueError('ordinary_private'),RuntimeError('ordinary'),RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7),Cancel())

    def exact(self, error, call):
        try:call()
        except BaseException as got:self.assertIs(got,error)
        else:self.fail('missing exact exception')

    def test_constructor_and_preflight_faults(self):
        for site in ('constructor','before_gateway'):
            for error in self.errors():
                with self.subTest(site=site,error=type(error)):
                    obj=Mock()
                    factory=Mock(return_value=obj)
                    if site=='constructor':factory.side_effect=error
                    else:obj.before_gateway.side_effect=error
                    capture=acceptance.IdentityCapture()
                    with patch.object(acceptance.runtime_identity,'RuntimeIdentity',factory):
                        if acceptance.is_cancellation(error):
                            self.exact(error,lambda:capture.begin(Path('/fixed/private')))
                            self.assertIs(capture.pending,error)
                        else:
                            capture.begin(Path('/fixed/private'))
                            self.assertIsNone(capture.pending)
                        if site=='constructor':self.assertIsNone(capture.instance)
                        else:self.assertIs(capture.instance,obj)
                    factory.assert_called_once_with(Path('/fixed/private'))

    def test_finish_faults_are_deferred_until_cleanup_and_preserve_original(self):
        for site in ('cleanup_query_child','after_cleanup','result'):
            for error in self.errors():
                for original in (None,OSError('original_api_failure')):
                    with self.subTest(site=site,error=type(error),original=original):
                        capture=acceptance.IdentityCapture();capture.instance=Mock();capture.original_error=original
                        getattr(capture.instance,site).side_effect=error
                        result=capture.finish()
                        expected=dict(acceptance.runtime_identity.UNAVAILABLE)
                        if site=='cleanup_query_child':expected['query_cleanup_status']='uncertain'
                        self.assertEqual(result,expected)
                        state={'state':'PASS','warehouse_state':'PASS'};capture.apply_pending(state)
                        if acceptance.is_cancellation(error) and original is None:
                            self.assertEqual(state,{'state':'ERROR','warehouse_state':'ERROR'})
                            self.exact(error,capture.raise_pending)
                        else:
                            capture.raise_pending();self.assertEqual(state['state'],'PASS')
                        capture.instance.cleanup_query_child.assert_called_once()
                        if site!='cleanup_query_child':capture.instance.after_cleanup.assert_called_once()

    def test_capture_is_once_only_and_cannot_start_after_finalization(self):
        obj=Mock();obj.result.return_value={}
        with patch.object(acceptance.runtime_identity,'RuntimeIdentity',return_value=obj) as factory:
            capture=acceptance.IdentityCapture()
            capture.begin(Path('/fixed/private'));capture.begin(Path('/fixed/private'))
            first=capture.finish();self.assertIs(capture.finish(),first)
            factory.assert_called_once();obj.before_gateway.assert_called_once()
            obj.after_cleanup.assert_called_once();obj.result.assert_called_once()
        capture=acceptance.IdentityCapture();capture.finish()
        with patch.object(acceptance.runtime_identity,'RuntimeIdentity') as factory:
            capture.begin(Path('/fixed/private'));factory.assert_not_called()

    def test_query_cleanup_uncertainty_aborts_before_gateway_and_retains_handle_owner(self):
        error=acceptance.runtime_identity.QueryCleanupUncertain('query_child_cleanup_uncertain')
        obj=Mock();obj.before_gateway.side_effect=error
        capture=acceptance.IdentityCapture();gateway=Mock()
        def begin_then_gateway():
            capture.begin(Path('/fixed/private'))
            gateway()
        with patch.object(acceptance.runtime_identity,'RuntimeIdentity',return_value=obj):
            self.exact(error,begin_then_gateway)
        gateway.assert_not_called();self.assertIs(capture.instance,obj)
        self.assertIs(capture.containment_error,error)
        capture.original_error=error;obj.result.return_value={'query_cleanup_status':'reaped'}
        self.assertEqual(capture.finish()['query_cleanup_status'],'reaped')
        obj.cleanup_query_child.assert_called_once()
        self.exact(error,capture.raise_pending)

    def test_final_child_cleanup_uncertainty_is_reported_without_overriding_original(self):
        error=acceptance.runtime_identity.QueryCleanupUncertain('query_child_cleanup_uncertain')
        for original in (None,OSError('original_api'),KeyboardInterrupt()):
            capture=acceptance.IdentityCapture();capture.original_error=original;capture.instance=Mock()
            capture.instance.cleanup_query_child.side_effect=error
            self.assertEqual(capture.finish()['query_cleanup_status'],'uncertain')
            result={'state':'PASS'};capture.apply_pending(result)
            if original is None:
                self.assertEqual(result['state'],'ERROR');self.exact(error,capture.raise_pending)
            else:
                self.assertEqual(result['state'],'PASS');capture.raise_pending()

    def test_cleanup_fallback_keeps_confirmed_in_memory_reap_evidence(self):
        capture=acceptance.IdentityCapture()
        capture.instance=Mock()
        capture.instance.raw={'query_cleanup_status':'reaped','query_cleanup_was_uncertain':True,'secret':'PRIVATE'}
        capture.instance.cleanup_query_child.side_effect=OSError('earlier_cleanup_failure')
        result=capture.finish()
        self.assertEqual(result['query_cleanup_status'],'reaped')
        self.assertTrue(result['query_cleanup_was_uncertain'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        capture.raise_pending()

    def test_cleanup_evidence_getter_faults_preserve_first_cancellation(self):
        for error in self.errors():
            for first in (None,RuntimeError('interrupted')):
                capture=acceptance.IdentityCapture();capture.pending=first
                class Collector:
                    @property
                    def raw(self):raise error
                    def cleanup_query_child(self):raise ValueError('ordinary')
                capture.instance=Collector();capture.finish()
                expected=first or (error if acceptance.is_cancellation(error) else None)
                if expected is not None:self.exact(expected,capture.raise_pending)
                else:capture.raise_pending()

    def test_first_cancellation_is_authoritative(self):
        first=RuntimeError('interrupted');second=KeyboardInterrupt()
        capture=acceptance.IdentityCapture();capture.defer_cancellation(first);capture.defer_cancellation(second)
        self.exact(first,capture.raise_pending)
        capture.original_error=first;self.exact(first,capture.raise_pending)
        capture.original_error=second;capture.raise_pending()

    def test_unstarted_capture_does_no_collection(self):
        capture=acceptance.IdentityCapture()
        with patch.object(acceptance.runtime_identity,'RuntimeIdentity') as factory:
            self.assertEqual(capture.finish(),acceptance.runtime_identity.UNAVAILABLE)
            capture.raise_pending();factory.assert_not_called()

    def test_summary_projection_faults_and_exact_cancellation(self):
        baseline=public.project({},require_runner=False)
        for error in self.errors():
            with self.subTest(error=type(error)),patch.object(public.runtime_identity,'sanitize',side_effect=error):
                if acceptance.is_cancellation(error):
                    self.exact(error,lambda:public.project({},require_runner=False))
                else:
                    result=public.project({},require_runner=False)
                    for key in ('state','warehouse_state','cleanup_pass','gateway_exit_code'):
                        self.assertEqual(result[key],baseline[key])
                    self.assertEqual(result['warehouse_runtime_identity'],public.runtime_identity.UNAVAILABLE)
                    self.assertNotIn('ordinary_private',json.dumps(result))

    def test_summary_projection_deferred_callback_preserves_original(self):
        for error in self.errors():
            for original in (None,OSError('original_api_failure')):
                capture=acceptance.IdentityCapture();capture.original_error=original
                with patch.object(public.runtime_identity,'sanitize',side_effect=error):
                    result=public.project({},require_runner=False,runtime_identity_cancellation=capture.defer_cancellation)
                self.assertEqual(result['state'],'ERROR')
                self.assertEqual(result['warehouse_runtime_identity'],public.runtime_identity.UNAVAILABLE)
                if acceptance.is_cancellation(error) and original is None:self.exact(error,capture.raise_pending)
                else:capture.raise_pending()

    def finalizer(self, namespace):
        namespace.setdefault('lifecycle_only', False)
        tree=ast.parse(Path(acceptance.__file__).read_text())
        execute=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='execute')
        body=[ast.Global(names=['forced','diagnostic'])]+next(node for node in execute.body if isinstance(node,ast.Try)).finalbody
        function=ast.FunctionDef(name='finalize',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body,decorator_list=[])
        module=ast.Module(body=[function],type_ignores=[]);ast.fix_missing_locations(module)
        exec(compile(module,'production-metadata-finalizer','exec'),namespace)
        return namespace['finalize']

    def test_actual_finalizer_cleanup_precedes_metadata_and_writes_result(self):
        for site in ('after_cleanup','result','projection'):
            for error in self.errors():
                for original in (None,OSError('original_api')):
                    with self.subTest(site=site,error=type(error),original=original),tempfile.TemporaryDirectory() as temp:
                        calls=[];capture=acceptance.IdentityCapture();capture.original_error=original
                        def after():
                            calls.append('metadata')
                            if site=='after_cleanup':raise error
                        def result():
                            if site=='result':raise error
                            return {}
                        capture.instance=SimpleNamespace(cleanup_query_child=lambda:None,after_cleanup=after,result=result)
                        class Gateway:
                            returncode=None
                            def poll(self):return None
                            def wait(self,timeout):calls.append(('wait',timeout));self.returncode=0
                        def projection(raw,**kwargs):
                            calls.append('projection')
                            if site=='projection':
                                with patch.object(public.runtime_identity,'sanitize',side_effect=error):
                                    public.project({},require_runner=False,runtime_identity_cancellation=kwargs['runtime_identity_cancellation'])
                            return dict(raw)
                        raw={'state':'PASS' if original is None else 'ERROR','cleanup_pass':True}
                        ns=dict(process_observer=None,observer=None,is_cancellation=acceptance.is_cancellation,
                                identity_capture=capture,raw=raw,base='mock_api',gateway=Gateway(),
                                api=lambda *args,**kwargs:calls.append('shutdown'),diagnostic={'harness':{}},
                                describe_failure=lambda exc:{'exception_kind':'unknown'},owned=lambda token:[],
                                token='fixture',forced=False,subprocess=subprocess,signal=signal,time=time,
                                collect=lambda *args:{},runtime=Path(temp),sid='sim_fixture',project=projection,
                                a=SimpleNamespace(summary=Path(temp)/'summary.json'),json=json)
                        finalize=self.finalizer(ns)
                        if acceptance.is_cancellation(error) and original is None:self.exact(error,finalize)
                        else:finalize()
                        self.assertEqual(calls.count('shutdown'),1)
                        self.assertLess(calls.index(('wait',5)),calls.index('metadata'))
                        self.assertLess(calls.index('metadata'),calls.index('projection'))
                        saved=json.loads(ns['a'].summary.read_text())
                        self.assertEqual(saved['gateway_exit_code'],0)
                        self.assertEqual(saved['remaining_owned_process_count'],0)
                        self.assertTrue(saved['cleanup_pass'])
                        expected='ERROR' if original is not None or acceptance.is_cancellation(error) else 'PASS'
                        self.assertEqual(saved['state'],expected)

    def test_actual_main_records_original_error_and_preserves_observer_window(self):
        text=Path(acceptance.__file__).read_text()
        self.assertLess(text.index('identity_capture.begin(private)'),text.index('gateway=subprocess.Popen'))
        self.assertIn('identity_capture.original_error=exc',text)
        self.assertIn("env.get('URP_WAREHOUSE_STARTUP_OBSERVER_DELAY_S')=='30'",text)
        frozen_hashes={'startup_observer.py': '078d89effd31c07e95089ebb124a5934c2854b5e7d1f8d8ba65df82589dcf831', 'process_observer.py': 'bda5a909febcc8de160b16832c877552e03dd238e95c3cebe59e0e6f5ad4e66f', 'thread_observer.py': '802f37293d54dbc0555a997ccc0f8fa5210142b6ffbf831ed20238380730d691', 'warehouse_lifecycle.py': 'd86fbf03014b61195263c2d505240611b0c77c17d94abff59fceba06c3adaba6'}
        for name, expected in frozen_hashes.items():
            self.assertEqual(hashlib.sha256(Path(acceptance.__file__).with_name(name).read_bytes()).hexdigest(),expected)


if __name__=='__main__':unittest.main()
