"""Pure existing-report projection tests. All source producer effects are mocked."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import signal
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cleanup_diagnostics as cd
import warehouse_acceptance as acceptance
import warehouse_public_summary as public
from warehouse_lifecycle import physical_evidence, drive


# Exact source method appended below from git show 3acf840d:urp_sim_session.py.
PRODUCER_SHUTDOWN = "def shutdown(self):\n    self.shutting=True;self.phase('stopping');self.cancel_active();self.settle()\n    self.report['lifecycle_shutdown']={}\n    for name in ('navigation','localization'):\n        c=self.node.create_client(ManageLifecycleNodes,f'/lifecycle_manager_{name}/manage_nodes')\n        if c.service_is_ready():\n            try:\n                req=ManageLifecycleNodes.Request();req.command=ManageLifecycleNodes.Request.SHUTDOWN\n                self.report['lifecycle_shutdown'][name]=self.future(c.call_async(req),12).success\n            except Exception as exc:self.report['lifecycle_shutdown'][name]=str(exc)\n        self.node.destroy_client(c)\n    self.report['final_state']=self.state()\n    # Shut down this ROS consumer while its clock and Create3 services remain\n    # available. Pausing/removing publishers first can strand its teardown.\n    self.report['robot_node_shutdown']=self.processes.stop_executables(\n        [Path('/opt/ros/humble/lib/turtlebot4_node/turtlebot4_node')])\n    for a in self.actions:a.destroy()\n    self.listener.unregister();self.node.destroy_node();rclpy.shutdown()\n    self.report['bridge_shutdown']=self.processes.stop_bridges()\n    # The official launch uses shell=True. SIGINT to the launch leader only\n    # reaches that shell, not reliably the Ruby/FFI Gazebo server below it.\n    # Use Fortress's native stop service in this session's unique partition.\n    try:\n        p=subprocess.run(['ign','service','-s','/server_control','--reqtype','ignition.msgs.ServerControl',\n            '--reptype','ignition.msgs.Boolean','--timeout','3000','--req','stop: true'],\n            env=self.env,capture_output=True,text=True,timeout=5)\n        self.report['gazebo_shutdown']=dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr,\n            accepted=p.returncode==0 and 'data: true' in p.stdout)\n    except Exception as exc:self.report['gazebo_shutdown']={'accepted':False,'error':str(exc)}\n    self.report['cleanup']=self.processes.cleanup();self.report['remaining_owned_processes']=self.processes.sample()\n    bad=[]\n    for child in self.report['cleanup']:\n        bad.extend(dict(group=child['name'],**p) for p in child['reaped'] if p['returncode'] not in (0,-2,130))\n        if not child['complete'] or 'SIGKILL' in child['signals']:bad.append({'group':child['name'],'forced_or_incomplete':True})\n    for name,_,log,_ in self.processes.children:\n        txt=log.read_text(errors='replace');codes=[int(c) for c in re.findall(r'exit code (-?\\d+)',txt)]\n        if any(c not in (0,-2,130) for c in codes) or any(s in txt for s in ('SIGKILL','corrupted unsorted','Segmentation fault')):bad.append(dict(group=name,codes=codes,log=str(log)))\n    self.report['abnormal_exits']=bad\n    for name,component in [('navigation','nav2'),('localization','localization')]:\n        if self.st['components'].get(component)=='active' and self.report['lifecycle_shutdown'].get(name) is not True:\n            bad.append({'lifecycle_shutdown_failed':name})\n    self.report['status']='PASS' if not (bad or self.report['remaining_owned_processes'] or self.report['errors'] or self.st['errors']) else 'FAIL'\n    self.report.update(finished_utc=now(),counts=self.counts,stages=self.st['stages'],resources=self.processes.resources)\n    save(self.runtime/'session-report.json',self.report);print(json.dumps({'shutdown':self.report['status'],'report':str(self.runtime/'session-report.json')}),flush=True)\n    self.st['phase']='stopped' if self.report['status']=='PASS' else 'failed'\n    return 0 if self.report['status']=='PASS' else 1\n"
# Exact snippet SHA256 ae537b52ef10a2a9431b50a9dcafa042b88f7ae5432e0d7b51fc20fe3d70c731


def fixture():
    return {
        'session_id': 'sim_PRIVATE', 'status': 'PASS',
        'remaining_owned_processes': [], 'abnormal_exits': [], 'errors': [],
        'final_state': {'phase': 'stopping', 'errors': []},
        'lifecycle_shutdown': {'navigation': True, 'localization': True},
        'gazebo_shutdown': {'accepted': True, 'returncode': 0, 'stdout': 'data: true\nPRIVATE', 'stderr': ''},
        'cleanup': [{'name': 'PRIVATE', 'group': 123456789, 'command': ['PRIVATE'],
                     'signals': ['SIGINT_LEADER'], 'reaped': [{'pid': 123456789, 'returncode': 0}], 'complete': True}],
    }, {'stop': {'session_id': 'sim_PRIVATE', 'phase': 'stopped', 'errors': []}, 'passed': True}


def gate(session, lifecycle):
    return physical_evidence({}, session, lifecycle, {'path': None, 'tasks': []})['cleanup_pass']


class ProjectionTests(unittest.TestCase):
    def test_exact_original_gate_and_each_independent_conjunct(self):
        for target in (None, *cd.CONJUNCTS):
            session, lifecycle = fixture()
            if target == 'status_pass':session['status'] = 'FAIL'
            if target == 'remaining_owned_exact_empty':session['remaining_owned_processes'] = [{'pid': 7}]
            if target == 'abnormal_exits_exact_empty':session['abnormal_exits'] = [{'returncode': 9}]
            if target == 'report_errors_falsy':session['errors'] = ['PRIVATE']
            if target == 'lifecycle_stopped':lifecycle['stop']['phase'] = 'failed'
            if target == 'lifecycle_errors_falsy':lifecycle['stop']['errors'] = ['PRIVATE']
            with self.subTest(target=target):
                result = cd.collect(session, lifecycle)
                for key, value in result['conjuncts'].items():
                    self.assertIs(value, key != target)
                self.assertEqual(gate(session, lifecycle), all(result['conjuncts'].values()))
                self.assertEqual(gate(session, lifecycle), target is None)

    def test_every_falsy_json_shape_preserves_predicate_but_not_list_count(self):
        for value in (None, False, 0, 0.0, '', {}, []):
            for field in ('errors', 'remaining_owned_processes', 'abnormal_exits'):
                session, lifecycle = fixture()
                session[field] = copy.deepcopy(value)
                lifecycle['stop']['errors'] = copy.deepcopy(value)
                result = cd.collect(session, lifecycle)
                with self.subTest(field=field, value=value):
                    self.assertEqual(result['conjuncts']['report_errors_falsy'], not session.get('errors'))
                    self.assertEqual(result['conjuncts']['remaining_owned_exact_empty'], session.get('remaining_owned_processes') == [])
                    self.assertEqual(result['conjuncts']['abnormal_exits_exact_empty'], session.get('abnormal_exits') == [])
                    self.assertTrue(result['conjuncts']['lifecycle_errors_falsy'])
                    count_name = {'errors': 'report_errors', 'remaining_owned_processes': 'remaining_owned', 'abnormal_exits': 'abnormal_exits'}[field]
                    self.assertEqual(result['counts'][count_name]['count'], 0 if type(value) is list else None)
                    self.assertEqual(gate(session, lifecycle), all(result['conjuncts'].values()))

    def test_missing_fields_have_exact_original_get_semantics(self):
        for field in ('status', 'remaining_owned_processes', 'abnormal_exits', 'errors'):
            session, lifecycle = fixture()
            del session[field]
            lifecycle['stop'].pop('errors')
            result = cd.collect(session, lifecycle)
            self.assertTrue(result['conjuncts']['lifecycle_errors_falsy'])
            if field == 'errors':self.assertTrue(result['conjuncts']['report_errors_falsy'])
            self.assertEqual(gate(session, lifecycle), all(result['conjuncts'].values()))
        session, lifecycle = fixture()
        del lifecycle['stop']['phase']
        self.assertFalse(cd.collect(session, lifecycle)['conjuncts']['lifecycle_stopped'])

    def test_truthy_malformed_error_values_do_not_become_false_or_zero(self):
        for value in (True, 1, 1.5, 'PRIVATE', {'PRIVATE': []}, ['PRIVATE']):
            session, lifecycle = fixture()
            session['errors'] = value
            lifecycle['stop']['errors'] = value
            result = cd.collect(session, lifecycle)
            self.assertFalse(result['conjuncts']['report_errors_falsy'])
            self.assertFalse(result['conjuncts']['lifecycle_errors_falsy'])
            self.assertEqual(result['counts']['report_errors']['count'], 1 if type(value) is list else None)
            self.assertEqual(gate(session, lifecycle), all(result['conjuncts'].values()))

    def test_stop_snapshot_is_never_replaced_by_final_state(self):
        session, lifecycle = fixture()
        session['final_state'] = {'phase': 'failed', 'errors': [cd.SETTLE_ERROR]}
        result = cd.collect(session, lifecycle)
        self.assertTrue(result['conjuncts']['lifecycle_stopped'])
        self.assertTrue(result['conjuncts']['lifecycle_errors_falsy'])
        self.assertTrue(result['final_state_error_categories']['stop_velocity_not_converged'])
        self.assertTrue(gate(session, lifecycle))
        lifecycle['stop'] = {'phase': 'failed', 'errors': ['stop PRIVATE']}
        session['final_state'] = {'phase': 'stopped', 'errors': []}
        result = cd.collect(session, lifecycle)
        self.assertFalse(result['conjuncts']['lifecycle_stopped'])
        self.assertFalse(result['conjuncts']['lifecycle_errors_falsy'])

    def test_final_state_error_categories_exact_and_unknown(self):
        session, lifecycle = fixture()
        session['final_state']['errors'] = [cd.CANCEL_PREFIX + 'PRIVATE\ntraceback', cd.SETTLE_ERROR, 'PRIVATE other']
        result = cd.collect(session, lifecycle)
        self.assertEqual(result['final_state_error_categories'], {
            'status': 'available', 'cancel_active_failed': True,
            'stop_velocity_not_converged': True, 'other_present': True})
        self.assertEqual(result['counts']['final_state_errors'], {'count': 3, 'overflow': False})
        for value in ('停车观察速度未收敛 extra', 'prefix' + cd.CANCEL_PREFIX, '', 'unknown'):
            session['final_state']['errors'] = [value]
            categories = cd.collect(session, lifecycle)['final_state_error_categories']
            self.assertFalse(categories['cancel_active_failed'])
            self.assertFalse(categories['stop_velocity_not_converged'])
            self.assertTrue(categories['other_present'])

    def test_error_category_malformed_and_overflow_unknown_absence(self):
        session, lifecycle = fixture()
        for errors in (None, 'PRIVATE', {}, [None], [cd.SETTLE_ERROR, False]):
            session['final_state']['errors'] = errors
            result = cd.collect(session, lifecycle)
            self.assertEqual(result['final_state_error_categories']['status'], 'invalid')
            self.assertIsNone(result['final_state_error_categories']['cancel_active_failed'])
        session['final_state']['errors'] = ['PRIVATE'] * cd.MAX_COUNT + [cd.CANCEL_PREFIX + 'hidden']
        result = cd.collect(session, lifecycle)
        self.assertEqual(result['counts']['final_state_errors'], {'count': cd.MAX_COUNT, 'overflow': True})
        self.assertEqual(result['final_state_error_categories']['status'], 'truncated')
        self.assertIsNone(result['final_state_error_categories']['cancel_active_failed'])
        self.assertTrue(result['final_state_error_categories']['other_present'])

    def test_shutdown_outcomes_are_strict_booleans_and_finite_code_classes(self):
        for rc, expected in ((0, 'normal'), (-9, 'nonzero'), (1, 'nonzero'), (130, 'nonzero'),
                             (None, 'unavailable'), (True, 'unavailable'), ('0', 'unavailable'), (9999999999, 'unavailable')):
            session, lifecycle = fixture()
            session['gazebo_shutdown']['returncode'] = rc
            session['lifecycle_shutdown']['navigation'] = 'PRIVATE exception'
            session['lifecycle_shutdown']['localization'] = False
            result = cd.collect(session, lifecycle)['shutdown']
            self.assertEqual(result['gazebo_returncode_class'], expected)
            self.assertIsNone(result['navigation'])
            self.assertFalse(result['localization'])
        for value in ('True', 1, None, {}, []):
            session, lifecycle = fixture()
            session['gazebo_shutdown']['accepted'] = value
            self.assertIsNone(cd.collect(session, lifecycle)['shutdown']['gazebo_accepted'])

    def test_group_aggregation_matches_actual_reaped_shape(self):
        session, lifecycle = fixture()
        session['cleanup'] = [
            {'complete': True, 'signals': ['SIGINT_LEADER'], 'reaped': [{'returncode': rc} for rc in (0, -2, 130, 1, -9)]},
            {'complete': False, 'signals': ['SIGINT', 'SIGTERM', 'SIGKILL'], 'reaped': [{'returncode': 3}]},
        ]
        result = cd.collect(session, lifecycle)['process_groups']
        self.assertEqual(result['status'], 'available')
        self.assertTrue(result['any_forced_or_incomplete'])
        self.assertEqual(result['abnormal_nonzero_exit_count'], 3)
        self.assertTrue(result['abnormal_exit_count_complete'])
        self.assertFalse(result['scan_limited'])
        # Accepted signal exits -2/130 are intentionally not called abnormal.
        session['cleanup'] = [{'complete': True, 'signals': [], 'reaped': [{'returncode': -2}, {'returncode': 130}]}]
        self.assertEqual(cd.collect(session, lifecycle)['process_groups']['abnormal_nonzero_exit_count'], 0)

    def test_each_group_forced_condition(self):
        for complete, signals in ((False, []), (True, ['SIGKILL'])):
            session, lifecycle = fixture()
            session['cleanup'][0].update(complete=complete, signals=signals)
            self.assertTrue(cd.collect(session, lifecycle)['process_groups']['any_forced_or_incomplete'])

    def test_malformed_group_fields_never_claim_complete_zero(self):
        for cleanup in (None, 'PRIVATE', {}, [None], [{}], [{'complete': True, 'signals': ['UNKNOWN'], 'reaped': []}],
                        [{'complete': True, 'signals': [], 'reaped': [True]}]):
            session, lifecycle = fixture()
            session['cleanup'] = cleanup
            result = cd.collect(session, lifecycle)['process_groups']
            self.assertEqual(result['status'], 'invalid')
            self.assertIsNone(result['abnormal_nonzero_exit_count'])
            self.assertFalse(result['abnormal_exit_count_complete'])

    def test_group_scan_and_count_caps(self):
        session, lifecycle = fixture()
        session['cleanup'] = [{'complete': True, 'signals': [], 'reaped': []}] * (cd.MAX_GROUPS + 1)
        result = cd.collect(session, lifecycle)['process_groups']
        self.assertEqual(result['group_count'], cd.MAX_GROUPS)
        self.assertTrue(result['group_count_overflow'])
        self.assertTrue(result['scan_limited'])
        self.assertIsNone(result['any_forced_or_incomplete'])
        session['cleanup'] = [{'complete': True, 'signals': [], 'reaped': [{'returncode': 1}] * (cd.MAX_REAPED + 1)}]
        result = cd.collect(session, lifecycle)['process_groups']
        self.assertEqual(result['abnormal_nonzero_exit_count'], cd.MAX_REAPED)
        self.assertFalse(result['abnormal_exit_count_complete'])
        self.assertTrue(result['scan_limited'])

    def test_no_report_or_lifecycle_mutation(self):
        session, lifecycle = fixture()
        before = copy.deepcopy((session, lifecycle))
        cd.collect(session, lifecycle)
        self.assertEqual((session, lifecycle), before)

    def test_caps_all_list_counts(self):
        session, lifecycle = fixture()
        for key in ('remaining_owned_processes', 'abnormal_exits', 'errors'):
            session[key] = ['PRIVATE'] * (cd.MAX_COUNT + 20)
        session['final_state']['errors'] = ['PRIVATE'] * (cd.MAX_COUNT + 1)
        result = cd.collect(session, lifecycle)
        for row in result['counts'].values():self.assertEqual(row, {'count': cd.MAX_COUNT, 'overflow': True})
        self.assertLess(len(json.dumps(result)), 4000)

    def test_crossfield_group_overflow_invalid_counts_never_claim_absence(self):
        session, lifecycle = fixture()
        valid = cd.collect(session, lifecycle)
        changes = ({'group_count': cd.MAX_GROUPS, 'group_count_overflow': True},
                   {'scan_limited': True}, {'group_count': None}, {'group_count': True},
                   {'group_count_overflow': None}, {'group_count': 1, 'group_count_overflow': True},
                   {'abnormal_nonzero_exit_count': None}, {'scan_limited': None})
        for change in changes:
            for positive in (False, True):
                raw = copy.deepcopy(valid)
                raw['process_groups'].update(change)
                raw['process_groups']['any_forced_or_incomplete'] = positive
                raw['process_groups']['abnormal_exit_count_complete'] = True
                result = cd.sanitize(raw)
                with self.subTest(change=change, positive=positive):
                    self.assertFalse(result['process_groups']['abnormal_exit_count_complete'])
                    self.assertIs(result['process_groups']['any_forced_or_incomplete'], True if positive else None)
                    self.assertEqual(cd.sanitize(result), result)

    def test_crossfield_error_category_truncation_never_claims_absence(self):
        session, lifecycle = fixture()
        for row in ({'count': cd.MAX_COUNT, 'overflow': True}, {'count': None, 'overflow': None}):
            raw = cd.collect(session, lifecycle)
            raw['counts']['final_state_errors'] = row
            raw['final_state_error_categories']['cancel_active_failed'] = True
            result = cd.sanitize(raw)
            self.assertTrue(result['final_state_error_categories']['cancel_active_failed'])
            self.assertIsNone(result['final_state_error_categories']['stop_velocity_not_converged'])
            self.assertIsNone(result['final_state_error_categories']['other_present'])
            self.assertEqual(cd.sanitize(result), result)

    def test_strict_availability_prevents_unmatched_claims(self):
        session, lifecycle = fixture()
        valid = cd.collect(session, lifecycle)
        for availability in ('missing', 'invalid', 'identity_mismatch', 'unavailable', 'PRIVATE'):
            result = cd.sanitize(dict(valid, availability=availability))
            self.assertTrue(all(value is None for value in result['conjuncts'].values()))
            self.assertEqual(result['session_status'], 'unknown')
            self.assertEqual(cd.sanitize(result), result)

    def test_privacy_and_closed_schema(self):
        session, lifecycle = fixture()
        session.update(private='/SECRET/path', pid=123456789, argv=['TOKEN'])
        session['abnormal_exits'] = [{'group': 'SECRET', 'pid': 123456789, 'codes': [999], 'log': '/SECRET'}]
        session['final_state']['errors'] = [cd.CANCEL_PREFIX + 'SECRET token', 'SECRET unrelated']
        result = cd.collect(session, lifecycle)
        public_text = json.dumps(result)
        for marker in ('PRIVATE', 'SECRET', '123456789', 'TOKEN', 'sim_', 'stderr', 'stdout'):
            self.assertNotIn(marker, public_text)
        self.assertEqual(cd.sanitize(dict(result, secret='SECRET', argv=['TOKEN'])), result)
        self.assertEqual(cd.sanitize(result), result)

    def test_unknown_session_status_and_missing_structure(self):
        for session in (None, [], 'PRIVATE'):
            self.assertEqual(cd.collect(session, {})['availability'], 'invalid')
        session, lifecycle = fixture()
        session['status'] = 'PRIVATE'
        result = cd.collect(session, {'stop': []})
        self.assertEqual(result['session_status'], 'unknown')
        self.assertFalse(result['conjuncts']['status_pass'])
        self.assertIsNone(result['conjuncts']['lifecycle_stopped'])
        self.assertIsNone(result['conjuncts']['lifecycle_errors_falsy'])

    def test_final_constraints_are_independent_and_do_not_change_acceptance(self):
        session, lifecycle = fixture()
        result = cd.finish(cd.collect(session, lifecycle), forced_cleanup=True, remaining_owned_count=0, runner_remaining_count=2)
        self.assertEqual(result['final_constraints'], {'forced_cleanup': True, 'remaining_owned_zero': True, 'runner_remaining_zero': False})
        self.assertTrue(all(result['conjuncts'].values()))
        self.assertTrue(gate(session, lifecycle))
        for malformed in (False, -1, '0', {}, 0.0):
            self.assertIsNone(cd.finish(None, remaining_owned_count=malformed)['final_constraints']['remaining_owned_zero'])

    def test_fuzz_closed_projection_idempotence(self):
        randomizer = random.Random(117)
        values = [None, False, True, 0, 1, -1, [], {}, '', 'PRIVATE', 'available', 'truncated', 'invalid']
        for _ in range(2000):
            raw = {name: randomizer.choice(values) for name in ('availability', 'session_status', 'conjuncts', 'counts', 'shutdown', 'process_groups', 'final_constraints')}
            raw['process_groups'] = {key: randomizer.choice(values) for key in ('status','group_count','group_count_overflow','any_forced_or_incomplete','abnormal_nonzero_exit_count','abnormal_exit_count_complete','scan_limited')}
            result = cd.sanitize(raw)
            self.assertEqual(cd.sanitize(result), result)
            self.assertLess(len(json.dumps(result)), 4000)
            self.assertNotIn('PRIVATE', json.dumps(result))


class ExactProducerTests(unittest.TestCase):
    def produce(self, errors=None, groups=None):
        # Only the exact method is compiled. ROS imports and native operations do
        # not run; all original I/O call sites here use inert, verified mocks.
        node = ast.parse(PRODUCER_SHUTDOWN).body[0]
        self.assertIsInstance(node, ast.FunctionDef)
        ns = {'ManageLifecycleNodes': SimpleNamespace(Request=type('Request', (), {'SHUTDOWN': 4})),
              'Path': Path, 'rclpy': SimpleNamespace(shutdown=Mock()),
              'subprocess': SimpleNamespace(run=Mock(return_value=SimpleNamespace(returncode=0, stdout='data: true', stderr=''))),
              're': re, 'save': Mock(), 'print': Mock(), 'json': json, 'now': lambda: 'fixture-time'}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'exact-3acf-shutdown', 'exec'), ns)
        session = SimpleNamespace(shutting=False, report={'errors': []}, env={}, runtime=Path('/NEVER_READ'),
            st={'errors': list(errors or []), 'phase': 'ready', 'components': {'nav2': 'active', 'localization': 'active'}, 'stages': []},
            counts={}, actions=[], listener=SimpleNamespace(unregister=Mock()))
        session.phase = lambda value: session.st.update(phase=value)
        session.cancel_active = Mock()
        session.settle = Mock()
        session.state = lambda: copy.deepcopy(session.st)
        client = SimpleNamespace(service_is_ready=lambda: True, call_async=Mock())
        session.node = SimpleNamespace(create_client=lambda *args: client, destroy_client=Mock(), destroy_node=Mock())
        session.future = lambda *args: SimpleNamespace(success=True)
        session.processes = SimpleNamespace(stop_executables=Mock(return_value={}), stop_bridges=Mock(return_value={}),
            cleanup=Mock(return_value=groups or []), sample=Mock(return_value=[]), children=[], resources=[])
        code = ns['shutdown'](session)
        saved = ns['save'].call_args.args[1]
        # Source saves the report before st.phase is changed to stopped/failed.
        self.assertEqual(saved['final_state']['phase'], 'stopping')
        return saved, {'stop': {'phase': session.st['phase'], 'errors': []}, 'passed': code == 0}

    def test_exact_source_producer_shape_and_pass(self):
        session, lifecycle = self.produce()
        result = cd.collect(session, lifecycle)
        self.assertEqual(session['status'], 'PASS')
        self.assertTrue(all(result['conjuncts'].values()))
        self.assertEqual(result['final_state_error_categories']['status'], 'available')
        self.assertTrue(result['shutdown']['navigation'])
        self.assertTrue(result['shutdown']['localization'])
        self.assertTrue(result['shutdown']['gazebo_accepted'])
        self.assertEqual(result['process_groups']['abnormal_nonzero_exit_count'], 0)

    def test_actual_producer_state_error_sets_report_fail_without_report_errors(self):
        session, lifecycle = self.produce(errors=[cd.SETTLE_ERROR])
        result = cd.collect(session, lifecycle)
        self.assertEqual(session['status'], 'FAIL')
        self.assertFalse(result['conjuncts']['status_pass'])
        self.assertTrue(result['conjuncts']['report_errors_falsy'])
        self.assertEqual(result['counts']['report_errors']['count'], 0)
        self.assertTrue(result['final_state_error_categories']['stop_velocity_not_converged'])
        self.assertFalse(gate(session, lifecycle))

    def test_actual_producer_abnormal_group_shape(self):
        groups = [{'name': 'PRIVATE', 'signals': ['SIGKILL'], 'complete': False,
                   'reaped': [{'pid': 123456789, 'returncode': -9}]}]
        session, lifecycle = self.produce(groups=groups)
        self.assertEqual(len(session['abnormal_exits']), 2)
        result = cd.collect(session, lifecycle)
        self.assertFalse(result['conjuncts']['abnormal_exits_exact_empty'])
        self.assertTrue(result['process_groups']['any_forced_or_incomplete'])
        self.assertEqual(result['process_groups']['abnormal_nonzero_exit_count'], 1)
        self.assertNotIn('PRIVATE', json.dumps(result))


class IntegrationTests(unittest.TestCase):
    def errors(self):
        class Cancel(BaseException):pass
        return [ValueError('PRIVATE ordinary'), RuntimeError('ordinary'), RuntimeError('interrupted'), KeyboardInterrupt(), SystemExit(4), Cancel()]

    def exact(self, error, call):
        try:call()
        except BaseException as actual:self.assertIs(actual, error)
        else:self.fail('exact original exception not propagated')

    def test_collect_failure_optional_cancellation_deferred_original_authoritative(self):
        for error in self.errors():
            for original in (None, OSError('original API'), RuntimeError('original cleanup')):
                capture = acceptance.IdentityCapture()
                capture.original_error = original
                with patch.object(acceptance.cleanup_diagnostics, 'collect', side_effect=error):
                    result = acceptance.capture_cleanup_diagnostics(*fixture(), capture)
                self.assertEqual(result, cd.UNAVAILABLE)
                if acceptance.is_cancellation(error) and original is None:
                    self.exact(error, capture.raise_pending)
                else:
                    capture.raise_pending()
                if original is not None:self.assertIs(capture.original_error, original)

    def test_projection_failure_optional_or_exact_cancellation(self):
        baseline = public.project({}, require_runner=False)
        for error in self.errors():
            with patch.object(public.cleanup_diagnostics, 'finish', side_effect=error):
                if acceptance.is_cancellation(error):
                    self.exact(error, lambda: public.project({}, require_runner=False))
                else:
                    result = public.project({}, require_runner=False)
                    self.assertEqual(result, baseline)
            for original in (None, OSError('original API'), KeyboardInterrupt()):
                capture = acceptance.IdentityCapture()
                capture.original_error = original
                with patch.object(public.cleanup_diagnostics, 'finish', side_effect=error):
                    result = public.project({}, require_runner=False, runtime_identity_cancellation=capture.defer_cancellation)
                self.assertEqual(result['warehouse_cleanup_diagnostics'], cd.UNAVAILABLE)
                if acceptance.is_cancellation(error) and original is None:self.exact(error, capture.raise_pending)
                else:capture.raise_pending()

    def test_project_reaches_final_schema_and_preserves_all_other_fields(self):
        session, lifecycle = fixture()
        raw = {'state': 'FAIL', 'cleanup_pass': False, 'remaining_owned_process_count': 0,
               'warehouse_diagnostics': {'session_report': 'available', 'harness': {'forced_cleanup': False}}}
        baseline = public.project(raw, require_runner=False)
        raw['warehouse_cleanup_diagnostics'] = cd.collect(session, lifecycle)
        result = public.project(raw, {'cleanup_remaining': 0, 'exit_code': 0, 'forced_kill_count': 0}, require_runner=False)
        self.assertEqual(result['warehouse_cleanup_diagnostics']['availability'], 'available')
        self.assertEqual(result['warehouse_cleanup_diagnostics']['final_constraints'], {
            'forced_cleanup': False, 'remaining_owned_zero': True, 'runner_remaining_zero': True})
        self.assertFalse(result['cleanup_pass'])
        without_runner = public.project(raw, require_runner=False)
        for key in baseline:
            if key != 'warehouse_cleanup_diagnostics':self.assertEqual(without_runner[key], baseline[key])

    def test_report_availability_without_new_read(self):
        for status in ('missing', 'invalid', 'identity_mismatch', 'unavailable'):
            result = public.project({'warehouse_diagnostics': {'session_report': status}}, require_runner=False)
            self.assertEqual(result['warehouse_cleanup_diagnostics']['availability'], status)
            self.assertTrue(all(value is None for value in result['warehouse_cleanup_diagnostics']['conjuncts'].values()))

    def test_actual_integration_after_identity_before_unchanged_physical_gate(self):
        text = Path(acceptance.__file__).read_text()
        projection = text.index("raw['warehouse_cleanup_diagnostics']=capture_cleanup_diagnostics(session,lifecycle,identity_capture)")
        self.assertLess(text.index("if session.get('session_id')!=sid:"), projection)
        self.assertLess(text.index("diagnostic['session_report']='available'"), projection)
        self.assertLess(projection, text.index("raw.update(physical_evidence(lifecycle['result'],session,lifecycle,plan))"))
        self.assertEqual(text.count('session=read(report_path,{})'), 1)
        self.assertEqual(hashlib.sha256(Path(acceptance.__file__).with_name('warehouse_lifecycle.py').read_bytes()).hexdigest(),
                         'd86fbf03014b61195263c2d505240611b0c77c17d94abff59fceba06c3adaba6')

    def finalizer(self, namespace):
        namespace.setdefault('lifecycle_only', False)
        tree = ast.parse(Path(acceptance.__file__).read_text())
        execute = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
        body = [ast.Global(names=['forced', 'diagnostic'])] + next(node for node in execute.body if isinstance(node, ast.Try)).finalbody
        function = ast.FunctionDef(name='finalize', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=body, decorator_list=[])
        module = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, 'production-cleanup-projection-finalizer', 'exec'), namespace)
        return namespace['finalize']

    def finalizer_namespace(self, temp, capture, raw, calls, cleanup_error=None):
        class Gateway:
            returncode = None
            def poll(self):return None
            def wait(self, timeout):
                calls.append(('wait', timeout))
                if cleanup_error is not None:raise cleanup_error
                self.returncode = 0
        def project(data, **kwargs):
            calls.append('projection')
            return public.project(data, **kwargs)
        return dict(process_observer=None, observer=None, is_cancellation=acceptance.is_cancellation,
                    identity_capture=capture, raw=raw, base='mock_api', gateway=Gateway(),
                    api=lambda *args, **kwargs:calls.append('shutdown'), diagnostic={'harness': {}},
                    describe_failure=lambda exc:{'exception_kind': 'unknown'}, owned=lambda token:[],
                    token='fixture', forced=False, subprocess=subprocess, signal=signal, time=time,
                    collect=lambda *args:{}, runtime=Path(temp), sid='sim_fixture', project=project,
                    a=SimpleNamespace(summary=Path(temp) / 'summary.json'), json=json)

    def test_actual_finalizer_projection_faults_cleanup_and_original_precedence(self):
        for site in ('collect', 'finish', 'sanitize'):
            for error in self.errors():
                for original in (None, OSError('original API')):
                    with self.subTest(site=site, error=type(error).__name__, original=original), tempfile.TemporaryDirectory() as temp:
                        capture = acceptance.IdentityCapture()
                        capture.original_error = original
                        session, lifecycle = fixture()
                        observed = cd.collect(session, lifecycle)
                        if site == 'collect':
                            with patch.object(cd, 'collect', side_effect=error):
                                observed = acceptance.capture_cleanup_diagnostics(session, lifecycle, capture)
                        raw = {'state': 'ERROR' if original is not None else 'FAIL', 'cleanup_pass': False,
                               'warehouse_cleanup_diagnostics': observed,
                               'warehouse_diagnostics': {'session_report': 'available', 'harness': {}}}
                        calls = []
                        ns = self.finalizer_namespace(temp, capture, raw, calls)
                        finalize = self.finalizer(ns)
                        def invoke():
                            try:
                                if original is not None:raise original
                            finally:
                                finalize()
                        if site == 'collect':
                            if original is not None:self.exact(original, invoke)
                            elif acceptance.is_cancellation(error):self.exact(error, invoke)
                            else:invoke()
                        else:
                            with patch.object(cd, site, side_effect=error):
                                if original is not None:self.exact(original, invoke)
                                elif acceptance.is_cancellation(error):self.exact(error, invoke)
                                else:invoke()
                        self.assertEqual(calls.count('shutdown'), 1)
                        self.assertLess(calls.index(('wait', 5)), calls.index('projection'))
                        saved = json.loads(ns['a'].summary.read_text())
                        self.assertEqual(saved['state'], 'ERROR' if original is not None else 'FAIL')
                        self.assertFalse(saved['cleanup_pass'])
                        self.assertEqual(saved['warehouse_cleanup_diagnostics']['availability'], 'unavailable')

    def test_new_diagnostic_cancellation_never_masks_raised_cleanup_error(self):
        for original in (OSError('cleanup failed'), RuntimeError('interrupted'), KeyboardInterrupt(), SystemExit(9)):
            with tempfile.TemporaryDirectory() as temp:
                capture = acceptance.IdentityCapture()
                diagnostic_error = RuntimeError('interrupted')
                with patch.object(cd, 'collect', side_effect=diagnostic_error):
                    acceptance.capture_cleanup_diagnostics(*fixture(), capture)
                ns = self.finalizer_namespace(temp, capture, {'state': 'FAIL', 'cleanup_pass': False}, [], cleanup_error=original)
                self.exact(original, self.finalizer(ns))
                self.assertFalse(ns['a'].summary.exists())

    def test_drive_caught_failure_remains_fail_with_diagnostic_cancellation(self):
        from test_lifecycle import API, Clock, PLAN
        api = API('sim_failed')
        clock = Clock()
        lifecycle = drive(api, 'replay_id', PLAN, lambda:None, lambda *args:None, clock.clock, clock.sleep)
        self.assertFalse(lifecycle['passed'])
        self.assertTrue(api.stopping)
        session, _ = fixture()
        raw = physical_evidence(lifecycle['result'], session, lifecycle, PLAN)
        self.assertEqual(raw['state'], 'FAIL')
        capture = acceptance.IdentityCapture()
        error = RuntimeError('interrupted')
        with patch.object(cd, 'collect', side_effect=error):
            raw['warehouse_cleanup_diagnostics'] = acceptance.capture_cleanup_diagnostics(session, lifecycle, capture)
        with tempfile.TemporaryDirectory() as temp:
            ns = self.finalizer_namespace(temp, capture, raw, [])
            self.exact(error, self.finalizer(ns))
            saved = json.loads(ns['a'].summary.read_text())
            self.assertEqual(saved['state'], 'FAIL')
            self.assertNotEqual(saved['state'], 'PASS')

    def test_diagnostic_module_has_no_io_or_process_imports(self):
        tree = ast.parse(Path(cd.__file__).read_text())
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        # Reuse only the existing module's pure in-memory adapter, never capture/read_file.
        self.assertEqual(len(imports), 1)
        self.assertIsInstance(imports[0], ast.ImportFrom)
        self.assertEqual(imports[0].module, 'shutdown_diagnostics')
        self.assertEqual([(n.name, n.asname) for n in imports[0].names],
                         [('session_detail', None), ('sanitize_session_detail', None)])
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ('open', 'exec', 'eval', '__import__') for n in ast.walk(tree)))


if __name__ == '__main__':unittest.main()
