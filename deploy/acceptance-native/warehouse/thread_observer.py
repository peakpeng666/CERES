"""Read-only thread CPU accounting for one verified, observed dominant Ruby.

Names identify source-defined conventions, not installed library/version/role or
causality. Raw task names and process/thread IDs never enter the projection.
Official Mesa mesa-23.2.1 provenance (retrieved 2026-10-22):
https://gitlab.freedesktop.org/mesa/mesa/-/raw/mesa-23.2.1/src/gallium/drivers/llvmpipe/lp_rast.c
sha256 18626617502cc60b0ae100f9b137d50c7f9f11fae59047c68168a5144f5b39a3
https://gitlab.freedesktop.org/mesa/mesa/-/raw/mesa-23.2.1/src/gallium/drivers/llvmpipe/lp_limits.h
sha256 dd9b814c86655d44f57c7400763548804879179fc437d94cf8c99a55561feede
https://gitlab.freedesktop.org/mesa/mesa/-/raw/mesa-23.2.1/src/util/u_thread.c
sha256 f9a35268a333633c6cba8f3800ff7e58678771a8685456a60b2e5b9e91485295
lp_rast names llvmpipe-%u, LP_MAX_THREADS is32, and Linux u_thread_setname
uses pthread_setname_np. Unknown names remain unknown; this does not establish
which Mesa version or Gazebo role is running. Same-image exec is undetectable.
Process timing is after validation: its counter read precedes the remaining
identity checks. This non-atomic skew is not a utilization estimate or equality
guarantee against summed per-thread read-completion intervals.
"""
import math
import os
from pathlib import Path
import time

START, END = 30., 38.
MAX_THREADS = MAX_LIFETIMES = 128
MAX_SAMPLES, MAX_PUBLIC = 9, 16
CATEGORIES = ('leader', 'named_llvmpipe_worker', 'unknown_worker')
MESA_NAMES = frozenset('llvmpipe-%d' % i for i in range(32))
STATUSES = {'not_selected', 'sampling', 'partial', 'complete', 'unavailable'}
REASONS = {'none', 'window_complete', 'cleanup_started', 'phase_left',
           'identity_mismatch', 'clock_invalid', 'read_error', 'scan_limit',
           'target_identity_lost', 'target_exec_changed'}
COUNTS = ('snapshots', 'read_omissions', 'scan_overflows', 'lifetime_omissions',
          'new_identities', 'lost_identities', 'name_changes', 'counter_regressions',
          'selection_ties', 'nonruby_leaders', 'no_positive_intervals',
          'discarded_batches', 'continuity_breaks')
READ_ERRORS = (OSError, ValueError, IndexError, KeyError, TypeError)


def number(value, high=1e12, integer=False):
    return value if type(value) in ((int,) if integer else (int, float)) and 0 <= value <= high and math.isfinite(value) else None


def classify(pid, tid, name):
    if tid == pid: return 'leader'
    return 'named_llvmpipe_worker' if name in MESA_NAMES else 'unknown_worker'


def sanitize(raw):
    raw = raw if type(raw) is dict else {}
    out = dict(policy='dominant_ruby_thread_cpu_v1', installed_mesa_version='unverified',
               application_role='unverified', same_image_exec_detection='unavailable',
               clock_basis='monotonic', interval_clock_sampling='per_thread_read_completion',
               scope='one_frozen_verified_gateway_descendant',
               selection_rule='unique_highest_positive_matched_delta_among_bounded_verified_candidates_then_ruby',
               counter_semantics='matched_adjacent_identity_name_intervals',
               target_process_timing='post_batch_validation_completion',
               process_counter_timestamp_skew='includes_remaining_identity_validation_work',
               thread_process_sum_equality='not_guaranteed_nonatomic_reads',
               status=raw.get('status') if type(raw.get('status')) is str and raw['status'] in STATUSES else 'unavailable',
               end_reason=raw.get('end_reason') if type(raw.get('end_reason')) is str and raw['end_reason'] in REASONS else 'none',
               target_process_lifetime_id=number(raw.get('target_process_lifetime_id'),128,True),
               first_offset_s=number(raw.get('first_offset_s'),END),
               last_offset_s=number(raw.get('last_offset_s'),END),
               process_cpu_delta_s=number(raw.get('process_cpu_delta_s'),1000000),
               process_covered_wall_s=number(raw.get('process_covered_wall_s'),END-START),
               process_intervals=number(raw.get('process_intervals'),MAX_SAMPLES-1,True),
               rows=[], categories=[])
    for key in COUNTS: out[key]=number(raw.get(key),1000000,True)
    categories=raw.get('categories')
    if type(categories) is list and len(categories)==len(CATEGORIES):
        for cat in CATEGORIES:
            matching=[r for r in categories if type(r) is dict and r.get('category')==cat]
            row=matching[0] if len(matching)==1 else {}
            out['categories'].append(dict(category=cat,cpu_delta_s=number(row.get('cpu_delta_s'),1000000),
                matched_intervals=number(row.get('matched_intervals'),MAX_LIFETIMES*(MAX_SAMPLES-1),True)))
    else:
        out['categories']=[dict(category=c,cpu_delta_s=None,matched_intervals=None) for c in CATEGORIES]
    rows=raw.get('rows')
    if type(rows) is list and len(rows)<=MAX_LIFETIMES:
        for row in rows:
            if type(row) is not dict or type(row.get('category')) is not str or row['category'] not in CATEGORIES:continue
            values={k:number(row.get(k),h,i) for k,h,i in (
                ('thread_lifetime_id',MAX_LIFETIMES,True),('cpu_delta_s',1000000,False),
                ('covered_wall_s',END-START,False),('intervals',MAX_SAMPLES-1,True),('samples',MAX_SAMPLES,True))}
            if any(v is None for v in values.values()):continue
            values['category']=row['category'];out['rows'].append(values)
    out['rows'].sort(key=lambda r:(-r['cpu_delta_s'],r['thread_lifetime_id']))
    out['public_rows_omitted']=min(MAX_LIFETIMES,(number(raw.get('public_rows_omitted'),MAX_LIFETIMES,True) or 0)+max(0,len(out['rows'])-MAX_PUBLIC))
    out['rows']=out['rows'][:MAX_PUBLIC]
    return out


UNAVAILABLE_OBSERVATION=sanitize({'status':'unavailable','end_reason':'read_error'})


class ThreadProcReader:
    def __init__(self,root='/proc'):self.root=Path(root)
    def tids(self,pid):
        values=[]
        with os.scandir(self.root/str(pid)/'task') as entries:
            for entry in entries:
                if entry.name.isdigit():
                    values.append(int(entry.name))
                    if len(values)>MAX_THREADS:raise OverflowError('thread_scan_limit')
        return sorted(values)
    def thread_stat(self,pid,tid):
        with (self.root/str(pid)/'task'/str(tid)/'stat').open() as f:raw=f.read(4097)
        if len(raw)>4096:raise ValueError('stat_limit')
        prefix,sep,tail=raw.rpartition(')');head,opening,name=prefix.partition('(')
        if not sep or not opening or int(head)!=tid:raise ValueError('stat_identity')
        fields=tail.split()
        if len(fields)<20:raise ValueError('stat_short')
        values=[int(fields[i]) for i in (11,12,19)]
        if any(number(v,2**63-1,True) is None for v in values):raise ValueError('stat_number')
        ticks=values[0]+values[1]
        if number(ticks,2**63-1,True) is None:raise ValueError('stat_number')
        return dict(pid=tid,start=values[2],ticks=ticks,name=name)


class ThreadObserver:
    def __init__(self,*,reader=None,clock=time.monotonic,ticks=100,origin=None):
        if number(ticks,1000000,True) is None or ticks==0:raise ValueError('ticks')
        self.reader=reader or ThreadProcReader();self.clock=clock;self.ticks=ticks;self.origin=origin
        self.target=None;self.closed=False;self.previous={};self.process_previous=None
        self.lifetimes={};self.names={};self.last_clock=None
        self.raw=dict(status='not_selected',end_reason='none',rows=[],
            process_cpu_delta_s=0.,process_covered_wall_s=0.,process_intervals=0,
            categories=[dict(category=c,cpu_delta_s=0.,matched_intervals=0) for c in CATEGORIES],**dict.fromkeys(COUNTS,0))

    def break_intervals(self):
        self.previous={};self.process_previous=None
        self.raw['continuity_breaks']+=1

    def stop(self,reason):
        if self.closed:return
        self.closed=True;self.raw['end_reason']=reason if reason in REASONS else 'read_error'
        if self.target is None:self.raw['status']='not_selected';return
        faulty=any(self.raw[k] for k in COUNTS if k not in ('snapshots','new_identities','selection_ties','nonruby_leaders','no_positive_intervals'))
        self.raw['status']='complete' if reason=='window_complete' and self.raw['snapshots']>=2 and not faulty else 'partial'

    def _discard(self):
        self.raw['discarded_batches']+=1
        if self.closed and self.target is not None:self.raw['status']='partial'

    def _now(self):
        now=self.clock()
        if number(self.origin) is None or number(now) is None or (self.last_clock is not None and now<self.last_clock):
            self.stop('clock_invalid');return None
        self.last_clock=now
        if now-self.origin>END:self.stop('window_complete');return None
        if now-self.origin<START:return None
        return now

    def _validate(self,validate):
        pid,start,exe,exe_id=self.target
        row,new_exe,new_id=validate(pid,start,exe,exe_id)
        if (row['pid'],row['start'])!=(pid,start):raise ValueError('target_identity')
        if (new_exe,new_id)!=(exe,exe_id):raise ValueError('target_exec')
        if number(row['ticks'],2**63-1,True) is None:raise ValueError('target_ticks')
        return row

    def observe(self,candidates,validate):
        if self.closed or self._now() is None:return
        if self.raw['snapshots']>=MAX_SAMPLES:self.stop('window_complete');return
        if self.target is None:
            positive=[c for c in candidates if number(c.get('delta_ticks'),2**63-1,True) is not None and c['delta_ticks']>0]
            if not positive:self.raw['no_positive_intervals']+=1;return
            maximum=max(c['delta_ticks'] for c in positive);winners=[c for c in positive if c['delta_ticks']==maximum]
            if len(winners)!=1:self.raw['selection_ties']+=1;return
            selected=winners[0]
            if selected['category']!='ruby_runtime':self.raw['nonruby_leaders']+=1;return
            row=selected['row'];self.target=(row['pid'],row['start'],selected['exe'],selected['exe_id'])
            self.raw['target_process_lifetime_id']=selected['lifetime_id']
        try:
            if self._now() is None:return
            self._validate(validate)
        except READ_ERRORS:
            self.stop('target_identity_lost');return
        current={};pid=self.target[0]
        try:
            if self._now() is None:return
            tids=self.reader.tids(pid)
            if type(tids) is not list or len(tids)>MAX_THREADS:raise OverflowError('thread_scan_limit')
            if any(number(t,2**31-1,True) is None or t==0 for t in tids) or len(set(tids))!=len(tids):raise ValueError('thread_ids')
        except OverflowError:
            self.raw['scan_overflows']+=1;self.break_intervals();return
        except READ_ERRORS:
            self.raw['read_omissions']+=1;self.break_intervals();return
        for tid in tids:
            if self._now() is None:self._discard();return
            try:
                before=self.reader.thread_stat(pid,tid)
                if self._now() is None:self._discard();return
                after=self.reader.thread_stat(pid,tid)
                at=self._now()
                if at is None:self._discard();return
                for row in (before,after):
                    if row['pid']!=tid or number(row['start'],2**63-1,True) is None or number(row['ticks'],2**63-1,True) is None or type(row['name']) is not str or len(row['name'])>4096:raise ValueError('thread_row')
                if (before['pid'],before['start'],before['name'])!=(after['pid'],after['start'],after['name']):raise ValueError('thread_race')
                if after['ticks']<before['ticks']:raise ValueError('thread_counter_race')
                current[(tid,after['start'])]=(after,at)
            except READ_ERRORS:self.raw['read_omissions']+=1
        if self._now() is None:self._discard();return
        try:process=self._validate(validate)
        except READ_ERRORS:
            self._discard();self.stop('target_identity_lost');return
        finished=self._now()
        if finished is None:self._discard();return
        self.raw['new_identities']+=len(current.keys()-self.previous.keys())
        self.raw['lost_identities']+=len(self.previous.keys()-current.keys())
        for key,(row,at) in current.items():
            old=self.previous.get(key)
            name_changed=key in self.names and self.names[key]!=row['name']
            if name_changed:self.raw['name_changes']+=1
            # A name transition starts a new accounting lifetime, even if name later returns.
            life=self.lifetimes.get(key)
            if life is None or name_changed:
                if len(self.raw['rows'])>=MAX_LIFETIMES:self.raw['lifetime_omissions']+=1;self.lifetimes.pop(key,None);continue
                life=dict(thread_lifetime_id=len(self.raw['rows'])+1,category=classify(pid,key[0],row['name']),cpu_delta_s=0.,covered_wall_s=0.,intervals=0,samples=0)
                self.lifetimes[key]=life;self.names[key]=row['name'];self.raw['rows'].append(life)
            life['samples']+=1
            if old is not None and not name_changed:
                oldrow,before=old
                if row['ticks']<oldrow['ticks']:self.raw['counter_regressions']+=1
                elif at>before:
                    delta=(row['ticks']-oldrow['ticks'])/self.ticks
                    life['cpu_delta_s']+=delta;life['covered_wall_s']+=at-before;life['intervals']+=1
                    bucket=self.raw['categories'][CATEGORIES.index(life['category'])]
                    bucket['cpu_delta_s']+=delta;bucket['matched_intervals']+=1
        if self.process_previous is not None:
            previous,before=self.process_previous
            if process['ticks']<previous['ticks']:self.raw['counter_regressions']+=1
            elif finished>before:
                self.raw['process_cpu_delta_s']+=(process['ticks']-previous['ticks'])/self.ticks
                self.raw['process_covered_wall_s']+=finished-before;self.raw['process_intervals']+=1
        self.previous=current;self.process_previous=(process,finished)
        self.raw['snapshots']+=1;self.raw['status']='sampling'
        self.raw.setdefault('first_offset_s',finished-self.origin);self.raw['last_offset_s']=finished-self.origin

    def result(self):return sanitize(self.raw)
