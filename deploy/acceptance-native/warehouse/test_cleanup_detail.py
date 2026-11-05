"""Exact producer contracts and pure bounded in-memory shutdown detail adapter."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import random
import re
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import cleanup_diagnostics as cd
import shutdown_diagnostics as sd
import test_cleanup_diagnostics as existing
import warehouse_acceptance as acceptance
import warehouse_public_summary as public


PRODUCER_RUN = "def run(self):\n    try:\n        self.startup()\n        while not self.stop.is_set():\n            self.spin()\n            try:kind,body=self.commands.get_nowait()\n            except queue.Empty:continue\n            if kind=='execute':self.execute(body)\n            else:self.window(kind,body['on'])\n    except Interrupted:pass\n    except Exception as exc:\n        traceback.print_exc();self.st['errors'].append(f'{type(exc).__name__}: {exc}');self.report['errors'].append(traceback.format_exc());self.phase('failed')\n    finally:return self.shutdown()\n"
RUN_SHA = '413bbc1d8a62f520fc2b4fd059fe363d91a347b968ff4824478c898626d5320b'
SHUTDOWN_SHA = 'ae537b52ef10a2a9431b50a9dcafa042b88f7ae5432e0d7b51fc20fe3d70c731'

def group(name='sim', signals=None, complete=True, rc=0):
    return {'name':name, 'signals':signals if signals is not None else ['SIGINT_LEADER'],
            'complete':complete, 'reaped':[{'pid':123456789,'returncode':rc}]}


def produce(groups, history=False, still=0, startup=False, logtext=None):
    ns={'ManageLifecycleNodes': NS(Request=type('Request',(),{'SHUTDOWN':4})), 'Path':Path,
        'rclpy':NS(shutdown=Mock()), 'subprocess':NS(run=Mock(return_value=NS(returncode=0,stdout='data: true',stderr=''))),
        're':re, 'save':Mock(), 'print':Mock(), 'json':json, 'now':lambda:'fixture',
        'Interrupted':type('Interrupted',(Exception,),{}), 'traceback':NS(print_exc=Mock(),format_exc=lambda:'fixture traceback')}
    nodes=[ast.parse(existing.PRODUCER_SHUTDOWN).body[0],ast.parse(PRODUCER_RUN).body[0]]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'exact-3acf-methods','exec'),ns)
    s=NS(report={'errors':['old'] if history else []},st={'errors':['old'] if history else [],'phase':'starting','components':{},'stages':[]},
         env={},runtime=Path('/NEVER_READ'),counts={},actions=[],listener=NS(unregister=Mock()))
    s.phase=lambda p:s.st.update(phase=p);s.state=lambda:copy.deepcopy(s.st)
    s.cancel_active=Mock();s.settle=Mock()
    s.node=NS(create_client=lambda *a:NS(service_is_ready=lambda:False),destroy_client=Mock(),destroy_node=Mock())
    leaves={'signaled':[{'pid':123456789}]*still,'still_running':[{'pid':123456789}]*still}
    s.processes=NS(stop_executables=Mock(return_value=leaves),stop_bridges=Mock(return_value=leaves),
        cleanup=Mock(return_value=groups),sample=Mock(return_value=[]),
        children=[] if logtext is None else [('sim',None,NS(read_text=lambda **k:logtext),None)],resources=[])
    s.shutdown=lambda:ns['shutdown'](s)
    s.startup=Mock(side_effect=TimeoutError('fixture startup timeout'))
    code=ns['run'](s) if startup else s.shutdown()
    return s.report, {'stop':{'phase':s.st['phase'],'errors':[]},'passed':code==0}


class DetailTests(unittest.TestCase):
    def test_producer_source_hashes(self):
        self.assertEqual(hashlib.sha256(existing.PRODUCER_SHUTDOWN.encode()).hexdigest(), SHUTDOWN_SHA)
        self.assertEqual(hashlib.sha256(PRODUCER_RUN.encode()).hexdigest(), RUN_SHA)

    def test_seven_actual_producer_cases_and_original_fields(self):
        cases=[([group()],False,False),([group()],True,False),([group(rc=-15,signals=['SIGINT_LEADER','SIGTERM'])],False,False),
               ([group(rc=-9,signals=['SIGINT_LEADER','SIGTERM','SIGKILL'])],False,False),
               ([group(complete=False)],False,False),([group()],False,True),([],False,False)]
        for groups,history,startup in cases:
            with self.subTest(groups=groups,history=history,startup=startup):
                session,life=produce(groups,history=history,startup=startup)
                before=copy.deepcopy(session)
                result=cd.collect(session,life)
                self.assertEqual(session,before)
                self.assertEqual(result['conjuncts']['status_pass'],session['status']=='PASS')
                self.assertTrue(result['shutdown']['gazebo_accepted'])
                self.assertEqual(result['process_group_details'],sd.session_detail(session))
                self.assertEqual(cd.sanitize(result),result)
                self.assertEqual(session['final_state']['phase'],'stopping')
                self.assertEqual(len(result['process_group_details']['groups']),len(groups))
                if groups:
                    detail=result['process_group_details']['groups'][0]
                    self.assertEqual(detail['complete'],groups[0]['complete'])
                    self.assertEqual(detail['signals'],groups[0]['signals'])
                    self.assertEqual(sum(detail['returncode_counts'].values()),1)

    def test_actual_producer_leaf_still_running(self):
        session,life=produce([group()],still=2)
        detail=cd.collect(session,life)['process_group_details']
        for leaf in detail['leaves'].values():self.assertEqual(leaf,{'status':'available','count':2})
        # Preserve producer gate: leaf report alone is not newly made a veto.
        self.assertEqual(session['status'],'PASS')

    def test_log_exit_not_confused_with_reaped_exit(self):
        session, life = produce([group()], logtext='process has died [exit code -15]')
        out = cd.collect(session, life)
        self.assertEqual(session['status'], 'FAIL')
        self.assertEqual(out['counts']['abnormal_exits']['count'], 1)
        row = out['process_group_details']['groups'][0]
        self.assertEqual(row['returncode_counts']['sigterm'], 0)
        self.assertEqual(row['returncode_counts']['normal'], 1)

    def test_malformed_sanitized_counts_and_truncation(self):
        valid = sd.session_detail({'cleanup':[group()], 'bridge_shutdown':{'still_running':[]}})
        for count in (True, -1, 257, 'SECRET', None):
            value = copy.deepcopy(valid)
            value['groups'][0]['returncode_counts']['normal'] = count
            out = sd.sanitize_session_detail(value)['groups'][0]
            self.assertEqual(out['reaped_status'], 'invalid'); self.assertIsNone(out['returncode_counts'])
        value = copy.deepcopy(valid)
        value['groups'][0]['signals_status'] = 'truncated'
        value['groups'][0]['reaped_status'] = 'truncated'
        value['leaves']['bridge_shutdown']['status'] = 'truncated'
        out = sd.sanitize_session_detail(value)
        self.assertEqual(out['groups'][0]['signals_status'], 'invalid')
        self.assertEqual(out['groups'][0]['reaped_status'], 'invalid')
        self.assertEqual(out['leaves']['bridge_shutdown'], {'status':'invalid','count':None})
        self.assertEqual(sd.sanitize_session_detail(out), out)

    def test_fixed_groups_only(self):
        for name in sd.GROUPS:
            row = sd.session_detail({'cleanup':[group(name=name)]})['groups'][0]
            self.assertEqual(row['component'], name)
        for name in ([],{},None,False,'SECRET', 'x'*100000):
            row = sd.session_detail({'cleanup':[group(name=name)]})['groups'][0]
            self.assertEqual(row['component'], 'unknown')

    def test_returncode_categories(self):
        session,life=produce([group(rc=rc) for rc in (0,-2,130,-15,-9,1)])
        rows=cd.collect(session,life)['process_group_details']['groups']
        for row,category in zip(rows,('normal','sigint','sigint','sigterm','sigkill','other')):
            self.assertEqual(row['returncode_counts'][category],1)

    def test_missing_invalid_not_empty(self):
        for raw,status in (({},'missing'),({'cleanup':None},'invalid'),({'cleanup':False},'invalid')):
            out=sd.session_detail(raw)
            self.assertEqual(out['status'],status)
            self.assertIsNone(out['leaves']['bridge_shutdown']['count'])
        for child in ({},{'signals':None,'reaped':False},{'signals':['SECRET'],'reaped':[{'returncode':True}]}):
            out=sd.session_detail({'cleanup':[child]})['groups'][0]
            self.assertIsNone(out['signals']);self.assertIsNone(out['returncode_counts']);self.assertIsNone(out['complete'])

    def test_empty_is_known_empty(self):
        g=group(signals=[]);g['reaped']=[]
        out=sd.session_detail({'cleanup':[g],'bridge_shutdown':{'still_running':[]}})
        row=out['groups'][0]
        self.assertEqual(row['signals'],[]);self.assertEqual(row['signals_status'],'available')
        self.assertEqual(sum(row['returncode_counts'].values()),0)
        self.assertEqual(out['leaves']['bridge_shutdown'],{'status':'available','count':0})

    def test_all_bounds_explicit(self):
        g=group(signals=['SIGINT']*4);g['reaped']=[{'returncode':-15}]*257
        out=sd.session_detail({'cleanup':[g]*9,'bridge_shutdown':{'still_running':[{}]*257}})
        self.assertEqual(out['status'],'truncated');self.assertEqual(len(out['groups']),8)
        for row in out['groups']:
            self.assertEqual(row['signals_status'],'truncated');self.assertEqual(len(row['signals']),3)
            self.assertEqual(row['reaped_status'],'truncated');self.assertEqual(row['returncode_counts']['sigterm'],256)
        self.assertEqual(out['leaves']['bridge_shutdown'],{'status':'truncated','count':256})
        self.assertEqual(sd.sanitize_session_detail(out),out)
        self.assertLess(len(json.dumps(out)),3500)

    def test_no_private_values(self):
        g=group(name='SECRET',signals=['SECRET']);g.update(command=['SECRET'],group=123456789,seconds='SECRET');g['reaped']=[{'returncode':'SECRET','pid':123456789}]
        out=sd.session_detail({'cleanup':[g],'bridge_shutdown':{'still_running':[{'pid':123456789,'token':'SECRET'}]}})
        text=json.dumps(out)
        self.assertNotIn('SECRET',text);self.assertNotIn('123456789',text)
        self.assertEqual(out['groups'][0]['component'],'unknown')

    def test_identity_untrusted_clears_details(self):
        data=cd.collect(*produce([group()]))
        for status in ('identity_mismatch','missing','invalid','unavailable'):
            data['availability']=status
            detail=cd.sanitize(data)['process_group_details']
            self.assertEqual(detail,sd.sanitize_session_detail(None))

    def test_no_io_or_mutation(self):
        session,life=produce([group()]);before=copy.deepcopy(session)
        with patch.object(sd,'capture',side_effect=AssertionError('no reads')),patch.object(sd,'read_file',side_effect=AssertionError('no reads')),patch('builtins.open',side_effect=AssertionError('no reads')):
            cd.collect(session,life)
        self.assertEqual(session,before)

    def test_cancellation_uses_unchanged_capture_path(self):
        for exc in (KeyboardInterrupt(),SystemExit(),RuntimeError('interrupted')):
            identity=acceptance.IdentityCapture()
            with patch.object(cd,'session_detail',side_effect=exc):
                result=acceptance.capture_cleanup_diagnostics(*produce([group()]),identity)
            self.assertEqual(result,cd.UNAVAILABLE)
            self.assertIs(identity.pending,exc)

    def test_public_output_roundtrip(self):
        session,life=produce([group(rc=-15)],still=1)
        raw={'warehouse_cleanup_diagnostics':cd.collect(session,life)}
        out=public.project(raw)
        self.assertEqual(out['warehouse_cleanup_diagnostics']['process_group_details'],sd.session_detail(session))
        self.assertEqual(cd.sanitize(out['warehouse_cleanup_diagnostics']),out['warehouse_cleanup_diagnostics'])

    def test_fuzz_closed_idempotent(self):
        rng=random.Random(991)
        values=[None,False,True,0,1,-1,[],{},'SECRET','available','missing','truncated','invalid', ['SECRET']]
        for _ in range(1000):
            raw={key:rng.choice(values) for key in ('status','groups','leaves')}
            if rng.random()<.5:raw['groups']=[{key:rng.choice(values) for key in ('component','complete','signals','signals_status','returncode_counts','reaped_status')}]
            out=sd.sanitize_session_detail(raw)
            self.assertEqual(sd.sanitize_session_detail(out),out)
            self.assertNotIn('SECRET',json.dumps(out))

if __name__=='__main__':unittest.main()
