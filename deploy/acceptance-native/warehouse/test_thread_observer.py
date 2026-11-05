import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import thread_observer as t


class Reader:
    def __init__(self):
        self.rows={40:dict(pid=40,start=100,ticks=0,name='ruby SECRET'),
                   41:dict(pid=41,start=101,ticks=0,name='llvmpipe-0'),
                   42:dict(pid=42,start=102,ticks=0,name='SECRET unknown')}
        self.calls=[]
    def tids(self,pid):self.calls.append(('tids',pid));return list(self.rows)
    def thread_stat(self,pid,tid):
        self.calls.append(('stat',pid,tid));return dict(self.rows[tid])


class Tests(unittest.TestCase):
    def setUp(self):
        self.reader=Reader();self.now=30.;self.process_ticks=0
        self.obs=t.ThreadObserver(reader=self.reader,clock=lambda:self.now,ticks=100,origin=0.)
        self.candidate=dict(row=dict(pid=40,start=100),exe='/SECRET/ruby',exe_id=(3,4),lifetime_id=7,delta_ticks=300,category='ruby_runtime')
    def validate(self,pid,start,exe,exe_id):
        self.assertEqual((pid,start,exe,exe_id),(40,100,'/SECRET/ruby',(3,4)))
        return dict(pid=40,start=100,ticks=self.process_ticks),exe,exe_id
    def sample(self,candidates=None):self.obs.observe([self.candidate] if candidates is None else candidates,self.validate)
    def advance(self):
        self.now+=1;self.process_ticks+=123
        for row in self.reader.rows.values():row['ticks']+=40
    def test_matched_cpu_and_public_categories(self):
        self.sample();self.advance();self.sample();out=self.obs.result()
        self.assertEqual(out['snapshots'],2);self.assertEqual(out['process_cpu_delta_s'],1.23)
        self.assertEqual(out['process_covered_wall_s'],1)
        self.assertEqual({r['category']:r['cpu_delta_s'] for r in out['categories']},{c:.4 for c in t.CATEGORIES})
        self.assertEqual([r['thread_lifetime_id'] for r in out['rows']],[1,2,3])
    def test_exact_name_catalogue(self):
        for i in range(32):self.assertEqual(t.classify(40,41,f'llvmpipe-{i}'),'named_llvmpipe_worker')
        for name in ['llvmpipe','llvmpipe-32','llvmpipe-01',' llvmpipe-1','llvmpipe-1 SECRET','RenderThread','ruby','lp-0']:
            self.assertEqual(t.classify(40,41,name),'unknown_worker')
        self.assertEqual(t.classify(40,40,'llvmpipe-0'),'leader')
    def test_no_target_zero_tie_and_nonruby(self):
        c=dict(self.candidate,delta_ticks=0);self.sample([c]);self.assertIsNone(self.obs.target)
        self.sample([self.candidate,dict(self.candidate,row=dict(pid=50,start=200))]);self.assertIsNone(self.obs.target)
        self.sample([dict(self.candidate,category='python_runtime')]);self.assertIsNone(self.obs.target)
        self.assertEqual(self.reader.calls,[])
        self.assertEqual([self.obs.raw[k] for k in ('no_positive_intervals','selection_ties','nonruby_leaders')],[1,1,1])
        self.sample();self.assertIsNotNone(self.obs.target)
    def test_target_never_retargets_when_other_becomes_dominant(self):
        self.sample();self.advance()
        self.sample([dict(self.candidate,row=dict(pid=50,start=200),delta_ticks=9999)])
        self.assertEqual(self.obs.target[0],40);self.assertEqual(self.obs.raw['snapshots'],2)
    def test_target_loss_permanent_and_no_silent_retarget(self):
        self.sample();self.advance()
        def failed(*a):raise ProcessLookupError()
        self.obs.observe([self.candidate],failed);calls=len(self.reader.calls)
        self.advance();self.sample()
        self.assertEqual(len(self.reader.calls),calls);self.assertTrue(self.obs.closed)
        self.assertEqual(self.obs.result()['end_reason'],'target_identity_lost')
    def test_target_process_reuse_or_exec_invalidates(self):
        for row,exe,exeid in [(dict(pid=40,start=101,ticks=0),'/SECRET/ruby',(3,4)),(dict(pid=40,start=100,ticks=0),'/SECRET/python',(3,4)),(dict(pid=40,start=100,ticks=0),'/SECRET/ruby',(3,5))]:
            self.setUp();self.obs.observe([self.candidate],lambda *a:(row,exe,exeid))
            self.assertTrue(self.obs.closed);self.assertEqual(self.reader.calls,[])
    def test_tid_reuse_breaks_interval(self):
        self.sample();self.advance();self.reader.rows[41]['start']=999;self.sample()
        rows=self.obs.result()['rows'];self.assertEqual(len(rows),4)
        self.assertEqual(sum(r['cpu_delta_s'] for r in rows if r['category']=='named_llvmpipe_worker'),0)
        self.assertEqual(self.obs.raw['lost_identities'],1)
    def test_rename_breaks_interval_and_new_category(self):
        self.sample();self.advance();self.reader.rows[41]['name']='PRIVATE changed';self.sample()
        self.assertEqual(self.obs.raw['name_changes'],1)
        self.assertEqual(self.obs.raw['categories'][1]['cpu_delta_s'],0)
        self.advance();self.sample()
        self.assertAlmostEqual(self.obs.raw['categories'][2]['cpu_delta_s'],1.2)
    def test_rename_after_gap_never_preserves_stale_category(self):
        self.sample();row=self.reader.rows.pop(41);self.advance();self.sample()
        row['name']='PRIVATE changed';self.reader.rows[41]=row;self.advance();self.sample();self.advance();self.sample()
        self.assertEqual(self.obs.raw['name_changes'],1)
        self.assertEqual(self.obs.raw['categories'][1]['cpu_delta_s'],0)
    def test_rename_after_explicit_break_starts_new_lifetime(self):
        self.sample();self.obs.break_intervals();self.reader.rows[41]['name']='new unknown'
        self.advance();self.sample();self.advance();self.sample()
        self.assertEqual(self.obs.raw['name_changes'],1)
        self.assertEqual(self.obs.raw['categories'][1]['cpu_delta_s'],0)
        self.assertEqual(len(self.obs.raw['rows']),4)
    def test_second_validation_cancellation_exact_object(self):
        for error in [RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7)]:
            self.setUp();calls=[]
            def validate(*args):
                calls.append(1)
                if len(calls)==2:raise error
                return self.validate(*args)
            with self.assertRaises(type(error)) as caught:self.obs.observe([self.candidate],validate)
            self.assertIs(caught.exception,error)
            self.assertEqual(self.obs.raw['snapshots'],0)
    def test_loss_and_return_do_not_bridge_gap(self):
        self.sample();row=self.reader.rows.pop(41);self.advance();self.sample()
        self.reader.rows[41]=row;self.advance();self.sample()
        self.assertEqual(self.obs.raw['categories'][1]['cpu_delta_s'],0)
    def test_between_read_tid_reuse_or_rename_omitted(self):
        original=self.reader.thread_stat;seen={}
        def racing(pid,tid):
            seen[tid]=seen.get(tid,0)+1;row=original(pid,tid)
            if seen[tid]%2==0:row['start']+=1
            return row
        self.reader.thread_stat=racing;self.sample()
        self.assertEqual(self.obs.raw['read_omissions'],3);self.assertEqual(self.obs.raw['rows'],[])
    def test_ancestry_loss_after_batch_discards_all_rows(self):
        calls=[]
        def validate(*args):
            calls.append(1)
            if len(calls)==2:raise ValueError('ancestry changed')
            return self.validate(*args)
        self.obs.observe([self.candidate],validate)
        self.assertEqual(self.obs.raw['snapshots'],0);self.assertEqual(self.obs.raw['discarded_batches'],1)
    def test_permission_omissions_no_leak(self):
        def denied(*a):raise PermissionError('SECRET')
        self.reader.thread_stat=denied;self.sample()
        self.assertEqual(self.obs.raw['read_omissions'],3)
        self.assertNotIn('SECRET',json.dumps(self.obs.result()))
    def test_bad_thread_fields_omitted(self):
        for field,value in [('pid',999),('start',-1),('ticks',float('nan')),('name',None),('ticks',True)]:
            self.setUp();self.reader.rows[41][field]=value;self.sample();self.assertEqual(self.obs.raw['read_omissions'],1)
    def test_counter_regression_breaks_only_current_interval(self):
        self.sample();self.advance();self.sample();self.advance();self.reader.rows[41]['ticks']=1;self.sample()
        self.assertEqual(self.obs.raw['counter_regressions'],1)
        self.assertEqual(self.obs.raw['categories'][1]['cpu_delta_s'],.4)
    def test_scan_cap_and_duplicate_ids(self):
        self.reader.tids=lambda pid:list(range(1,130));self.sample()
        self.assertEqual(self.obs.raw['scan_overflows'],1);self.assertEqual(self.obs.raw['snapshots'],0)
        self.reader.tids=lambda pid:[1,1];self.sample();self.assertEqual(self.obs.raw['read_omissions'],1)
    def test_lifetime_and_public_caps_idempotent(self):
        self.reader.rows={i:dict(pid=i,start=i,ticks=0,name='PRIVATE') for i in range(100,228)}
        self.sample();self.advance();self.sample()
        out=self.obs.result();self.assertEqual(len(out['rows']),16);self.assertEqual(out['public_rows_omitted'],112)
        self.assertEqual(out,t.sanitize(out))
        self.reader.rows={i:dict(pid=i,start=i+1,ticks=0,name='PRIVATE') for i in range(100,228)}
        self.advance();self.sample();self.assertEqual(self.obs.raw['lifetime_omissions'],128)
    def test_adversarial_projection_bound_privacy_idempotence(self):
        self.sample();raw=copy.deepcopy(self.obs.raw);raw['SECRET']='SECRET'
        row=raw['rows'][0];raw['rows']=[dict(row,thread_lifetime_id=i+1,SECRET='SECRET') for i in range(128)]
        raw['public_rows_omitted']=128;raw['status']='SECRET';raw['end_reason']='SECRET'
        out=t.sanitize(raw);self.assertEqual(out,t.sanitize(out));self.assertEqual(out['public_rows_omitted'],128)
        self.assertNotIn('SECRET',json.dumps(out));self.assertLess(len(json.dumps(out)),8000)
    def test_malformed_projection_no_arbitrary_fields(self):
        for raw in [None,[],{'rows':'SECRET'},{'rows':[{'category':[]}]},{'categories':[]},{'snapshots':True}]:
            out=t.sanitize(raw);self.assertEqual(out,t.sanitize(out));self.assertNotIn('SECRET',json.dumps(out))
    def test_before_window_and_after_cutoff_no_reads(self):
        self.now=29;self.sample();self.assertEqual(self.reader.calls,[])
        self.now=39;self.sample();self.assertEqual(self.reader.calls,[]);self.assertTrue(self.obs.closed)
    def test_crossed_cutoff_discards_batch(self):
        original=self.reader.thread_stat
        def crossing(pid,tid):self.now=38.1;return original(pid,tid)
        self.reader.thread_stat=crossing;self.sample()
        self.assertEqual(self.obs.raw['snapshots'],0);self.assertEqual(self.obs.raw['discarded_batches'],1)
        self.assertEqual(sum(c[0]=='stat' for c in self.reader.calls),1)
    def test_prior_valid_snapshots_cutoff_discard_is_partial(self):
        for point in ['thread_read','post_validation']:
            self.setUp();self.sample();self.advance();self.sample();self.now=37.9
            original=self.reader.thread_stat
            def crossing(pid,tid):self.now=38.1;return original(pid,tid)
            calls=[]
            def validate(*args):
                calls.append(1)
                if len(calls)==2:self.now=38.1
                return self.validate(*args)
            if point=='thread_read':self.reader.thread_stat=crossing
            self.obs.observe([self.candidate],validate)
            self.assertEqual(self.obs.raw['snapshots'],2)
            self.assertEqual(self.obs.raw['discarded_batches'],1)
            self.assertEqual(self.obs.result()['status'],'partial')
    def test_actual_per_row_timestamps(self):
        original=self.reader.thread_stat
        def delayed(pid,tid):self.now+=.01;return original(pid,tid)
        self.reader.thread_stat=delayed;self.sample();self.advance();self.sample()
        for row in self.obs.raw['rows']:self.assertAlmostEqual(row['covered_wall_s'],1.06)
    def test_monotonic_mismatch_discards_and_stops(self):
        self.sample();self.now=29;self.sample();self.assertTrue(self.obs.closed)
        self.assertEqual(self.obs.raw['end_reason'],'clock_invalid')
    def test_cleanup_and_phase_cutoff(self):
        for reason in ['cleanup_started','phase_left']:
            self.setUp();self.sample();calls=len(self.reader.calls);self.obs.stop(reason);self.advance();self.sample()
            self.assertEqual(len(self.reader.calls),calls);self.assertEqual(self.obs.raw['end_reason'],reason)
    def test_continuity_break_and_sample_cap(self):
        self.sample();self.advance();self.obs.break_intervals();self.sample()
        self.assertEqual(self.obs.raw['process_intervals'],0)
        self.assertEqual(sum(r['intervals'] for r in self.obs.raw['rows']),0)
        self.obs.raw['snapshots']=9;calls=len(self.reader.calls);self.sample();self.assertEqual(len(self.reader.calls),calls)
    def test_cancellation_exact_objects_propagate_from_all_io(self):
        for error in [RuntimeError('interrupted'),KeyboardInterrupt(),SystemExit(7)]:
            for point in ['clock','tids','stat','validate']:
                self.setUp()
                def fail(*a):raise error
                validator=self.validate
                if point=='clock':self.obs.clock=fail
                elif point=='tids':self.reader.tids=fail
                elif point=='stat':self.reader.thread_stat=fail
                else:validator=fail
                with self.assertRaises(type(error)) as caught:self.obs.observe([self.candidate],validator)
                self.assertIs(caught.exception,error)
    def test_normal_unexpected_exception_propagates_for_integration_containment(self):
        error=RuntimeError('ordinary')
        def fail(*a):raise error
        self.reader.tids=fail
        with self.assertRaises(RuntimeError) as caught:self.sample()
        self.assertIs(caught.exception,error)
    def test_invalid_units(self):
        for ticks in [0,-1,None,True,float('inf')]:
            with self.assertRaises(ValueError):t.ThreadObserver(ticks=ticks)


class ProcTests(unittest.TestCase):
    def stat(self,tid,name='private ) name',start=100):
        fields=['S']+['0']*21;fields[11]='4';fields[12]='5';fields[19]=str(start)
        return f'{tid} ({name}) '+' '.join(fields)
    def test_only_task_stat_paths_and_parentheses(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);task=root/'40'/'task'/'41';task.mkdir(parents=True)
            (task/'stat').write_text(self.stat(41));reader=t.ThreadProcReader(root)
            self.assertEqual(reader.tids(40),[41]);row=reader.thread_stat(40,41)
            self.assertEqual(row,dict(pid=41,start=100,ticks=9,name='private ) name'))
    def test_short_bad_identity_negative_overflow_stat(self):
        for raw in ['bad',self.stat(42),self.stat(41,start=-1),'a'*4097]:
            with tempfile.TemporaryDirectory() as directory:
                task=Path(directory)/'40'/'task'/'41';task.mkdir(parents=True);(task/'stat').write_text(raw)
                with self.assertRaises(ValueError):t.ThreadProcReader(directory).thread_stat(40,41)
    def test_real_directory_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory)/'40'/'task';task.mkdir(parents=True)
            for i in range(129):(task/str(i+1)).mkdir()
            with self.assertRaises(OverflowError):t.ThreadProcReader(directory).tids(40)


if __name__=='__main__':unittest.main()
