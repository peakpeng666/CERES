"""Bounded read-only gateway-descendant accounting; never an acceptance input.

No cmdline, environment, maps or signaling. Optional bounded thread stat sampling
is restricted to one verified dominant Ruby lifetime. Process identity is
PID/start_ticks within this invocation; executable identity changes split CPU
intervals. Same-image exec cannot be distinguished by this interface. CPU
seconds are observed matched-interval consumption, not utilization or causality.
"""
import math
import os
from pathlib import Path
import re
import time

from startup_observer import is_cancellation
from resource_uri_diagnostics import LAUNCH_GRAPH_PROCESSES
import thread_observer as thread_diag

MAX_SCAN = 4096
MAX_OWNED = 128
MAX_LIFETIMES = 128
MAX_SAMPLES = 9
MAX_PUBLIC = 32
START = 30.0
END = 38.0
CATEGORIES = {'gazebo_native', 'ros_bridge', 'robot_state_publisher',
              'controller_manager', 'python_runtime', 'ruby_runtime',
              'other_owned', 'unavailable'}
# Reuse only exact executable names from the existing pinned public Humble
# launch catalogue. Never use its log-command aliases or infer script identity.
NATIVE_NAMES = {}
NATIVE_NAMES.update({name:name for name in LAUNCH_GRAPH_PROCESSES})
NATIVE_NAMES.update({'create':'entity_create','image_bridge':'image_bridge',
                     'turtlebot4_node':'turtlebot4_node','create3_nodes':'create3_nodes'})
CATEGORIES.update(NATIVE_NAMES.values())
STATUSES = {'not_started', 'sampling', 'complete', 'partial', 'unavailable'}
REASONS = {'none', 'window_complete', 'cleanup_started', 'phase_left',
           'identity_mismatch', 'clock_invalid', 'read_error', 'scan_limit'}
COUNTS = ('snapshots', 'read_omissions', 'ancestry_omissions', 'scan_overflows',
          'owned_overflows', 'lifetime_omissions', 'new_identities',
          'lost_identities', 'exec_changes', 'counter_regressions', 'api_read_errors')


def number(v, high=1e12, integer=False):
    return v if type(v) in ((int,) if integer else (int, float)) and 0 <= v <= high and math.isfinite(v) else None


def category(exe):
    name = exe.rsplit('/', 1)[-1]
    if name in ('gzserver', 'ign-gazebo-server', 'gz-sim-server'):
        return 'gazebo_native'
    if name == 'parameter_bridge': return 'ros_bridge'
    if name == 'robot_state_publisher': return 'robot_state_publisher'
    if name == 'ros2_control_node': return 'controller_manager'
    if name in NATIVE_NAMES:return NATIVE_NAMES[name]
    if re.fullmatch(r'python(?:[23](?:\.[0-9]+)?)?', name): return 'python_runtime'
    if re.fullmatch(r'ruby(?:[0-9]+(?:\.[0-9]+)*)?', name): return 'ruby_runtime'
    return 'other_owned'


def sanitize(raw):
    raw = raw if type(raw) is dict else {}
    result = {'policy': 'gateway_descendant_cpu_v1', 'clock_basis': 'monotonic', 'interval_clock_sampling': 'snapshot_start',
              'scope': 'verified_gateway_descendants_in_cached_startup',
              'counter_semantics': 'matched_adjacent_identity_intervals',
              'same_image_exec_detection': 'unavailable',
              'status': raw.get('status') if type(raw.get('status')) is str and raw['status'] in STATUSES else 'unavailable',
              'end_reason': raw.get('end_reason') if type(raw.get('end_reason')) is str and raw['end_reason'] in REASONS else 'none',
              'first_offset_s': number(raw.get('first_offset_s'), END),
              'last_offset_s': number(raw.get('last_offset_s'), END),
              'snapshot_span_s': number(raw.get('snapshot_span_s'), END-START),
              'rows': []}
    for key in COUNTS: result[key] = number(raw.get(key), 1000000, True)
    rows = raw.get('rows')
    if type(rows) is list and len(rows) <= MAX_LIFETIMES:
        for row in rows:
            if type(row) is not dict: continue
            cat = row.get('category')
            values = {k:number(row.get(k), high, integer) for k,high,integer in (
                ('lifetime_id', MAX_LIFETIMES, True), ('cpu_delta_s', 1000000, False),
                ('covered_wall_s', END-START, False), ('intervals', MAX_SAMPLES-1, True),
                ('samples', MAX_SAMPLES, True), ('peak_rss_kib', 128*1024*1024, True),
                ('max_threads', 100000, True))}
            if type(cat) is not str or cat not in CATEGORIES or any(v is None for v in values.values()): continue
            state=row.get('last_state')
            values.update(category=cat,last_state=state if type(state) is str and state in {'R','S','D','Z','T','t','X','I','P'} else 'unknown')
            result['rows'].append(values)
        result['rows'].sort(key=lambda r:(-r['cpu_delta_s'],r['lifetime_id']))
    result['public_rows_omitted']=min(MAX_LIFETIMES,(number(raw.get('public_rows_omitted'), MAX_LIFETIMES, True) or 0)+max(0,len(result['rows'])-MAX_PUBLIC))
    result['rows']=result['rows'][:MAX_PUBLIC]
    try:result['thread_observation'] = thread_diag.sanitize(raw.get('thread_observation'))
    except BaseException as exc:
        if is_cancellation(exc):raise
        result['thread_observation'] = thread_diag.UNAVAILABLE_OBSERVATION
    return result


UNAVAILABLE_OBSERVATION = sanitize({'status':'unavailable','end_reason':'read_error'})


class ProcReader:
    def __init__(self, root='/proc'):
        self.root=Path(root)
    def pids(self):
        values=[]
        with os.scandir(self.root) as entries:
            for entry in entries:
                if entry.name.isdigit():
                    values.append(int(entry.name))
                    if len(values)>MAX_SCAN: raise OverflowError('scan_limit')
        return sorted(values)
    def stat(self,pid):
        with (self.root/str(pid)/'stat').open() as f:
            raw=f.read(4097)
        if len(raw)>4096: raise ValueError('stat_limit')
        prefix,sep,tail=raw.rpartition(')')
        if not sep or int(prefix.split('(',1)[0])!=pid: raise ValueError('stat_identity')
        fields=tail.split()
        if len(fields)<22: raise ValueError('stat_short')
        if any(number(int(fields[i]),2**63-1,True) is None for i in (1,2,11,12,17,19,21)):
            raise ValueError('stat_number')
        row={'pid':pid,'ppid':int(fields[1]),'pgid':int(fields[2]),
             'start':int(fields[19]),'ticks':int(fields[11])+int(fields[12]),
             'rss':int(fields[21]),'threads':int(fields[17]),'state':fields[0]}
        if any(number(row[k], 2**63-1, True) is None for k in ('pid','ppid','pgid','start','ticks','rss','threads')):
            raise ValueError('stat_number')
        if row['state'] not in {'R','S','D','Z','T','t','X','I','P'}: raise ValueError('stat_state')
        return row
    def exe(self,pid):
        path=self.root/str(pid)/'exe'
        target=os.readlink(path)
        info=path.stat()
        return target,(info.st_dev,info.st_ino)


def identity(row): return row['pid'],row['start']
def ancestry(row): return row['pid'],row['start'],row['ppid'],row['pgid']


class ProcessObserver:
    def __init__(self, gateway, *, reader=None, clock=time.monotonic,
                 parent_pid=None, ticks=None, page=None, thread_reader=None):
        self.thread=None;self.thread_reader=thread_reader;self.thread_chain=None
        self.gateway=gateway  # Exact Popen object from this helper, never API metadata.
        self.reader=reader or ProcReader();self.clock=clock
        self.sid=self.origin=self.last_clock=None
        self.previous={};self.lifetimes={};self.closed=False
        self.raw=dict(status='not_started',end_reason='none',rows=[],**dict.fromkeys(COUNTS,0))
        try:
            self.ticks=os.sysconf('SC_CLK_TCK') if ticks is None else ticks
            self.page=os.sysconf('SC_PAGE_SIZE') if page is None else page
            root=self.reader.stat(gateway.pid)
            if gateway.returncode is not None or root['ppid']!=(os.getpid() if parent_pid is None else parent_pid) or root['pgid']!=gateway.pid:
                raise ValueError('root_identity')
            if number(self.ticks,1000000,True) is None or self.ticks==0 or number(self.page,1000000,True) is None or self.page==0:
                raise ValueError('system_units')
            self.root=ancestry(root)
        except (OSError,ValueError,IndexError,KeyError,TypeError):
            self.root=None;self.stop('identity_mismatch')

    def thread_call(self,name,*args):
        if self.thread is None:return
        try:return getattr(self.thread,name)(*args)
        except BaseException as exc:
            self.thread=None
            self.raw['thread_observation']=thread_diag.UNAVAILABLE_OBSERVATION
            if is_cancellation(exc):raise

    def stop(self,reason):
        if not self.closed:
            self.closed=True;self.raw['end_reason']=reason
            self.raw['status']='partial' if self.raw['snapshots'] else 'unavailable'
            if reason=='window_complete' and not any(self.raw[k] for k in COUNTS if k not in ('snapshots','new_identities','lost_identities','exec_changes')):
                self.raw['status']='complete' if self.raw['snapshots']>=2 else 'partial'
            self.thread_call('stop',reason)

    def bind(self,sid):
        if self.closed:return
        if type(sid) is not str or len(sid)>128 or not re.fullmatch(r'sim_[A-Za-z0-9_]+',sid) or self.sid not in (None,sid):
            self.stop('identity_mismatch');return
        if self.sid is None:
            self.sid=sid;self.origin=self.clock();self.last_clock=self.origin
            if number(self.origin) is None:self.stop('clock_invalid')
            else:
                try:self.thread=thread_diag.ThreadObserver(reader=self.thread_reader or thread_diag.ThreadProcReader(),clock=self.clock,origin=self.origin,ticks=self.ticks)
                except BaseException as exc:
                    self.raw['thread_observation']=thread_diag.UNAVAILABLE_OBSERVATION
                    if is_cancellation(exc):raise

    def safe(self,name,*args):
        try:return getattr(self,name)(*args)
        except BaseException as exc:
            try:self.stop('read_error')
            except BaseException as stopping:
                if is_cancellation(exc):raise exc
                if is_cancellation(stopping):raise
            if is_cancellation(exc):raise
            return None

    def wrap(self,api):
        def call(method,path,data=None,timeout=5):
            cancellation=None
            if method=='DELETE' and path=='/sim/session':
                try:self.safe('stop','cleanup_started')
                except BaseException as exc:cancellation=exc
            try:result=api(method,path,data,timeout)
            except BaseException as exc:
                # Bookkeeping must never replace the original API exception.
                try:
                    if not is_cancellation(exc) and method=='GET' and path=='/sim/state':
                        self.raw['api_read_errors']=min(1000000,self.raw['api_read_errors']+1)
                        self.previous={}  # Never bridge a missed startup observation.
                        self.thread_call('break_intervals')
                    else:self.stop('read_error')
                except BaseException:pass
                raise
            if cancellation is not None:raise cancellation
            if method=='GET' and path=='/sim/state':self.safe('observe',result)
            return result
        return call

    def observe(self,row):
        if self.closed or self.sid is None:return
        if type(row) is not dict or row.get('session_id')!=self.sid or row.get('world')!='warehouse':
            self.stop('identity_mismatch');return
        if row.get('phase')!='starting':self.stop('phase_left');return
        now=self.clock()
        if number(now) is None or now<self.last_clock:self.stop('clock_invalid');return
        self.last_clock=now;offset=now-self.origin
        if offset>END:self.stop('window_complete');return
        if offset<START:return
        last=self.raw.get('last_offset_s')
        if last is not None and offset-last<0.9:return
        if self.raw['snapshots']>=MAX_SAMPLES:self.stop('window_complete');return
        self.snapshot(offset)

    def snapshot(self,offset):
        if self.gateway.returncode is not None or ancestry(self.reader.stat(self.gateway.pid))!=self.root:
            self.stop('identity_mismatch');return
        table={}
        try:pids=self.reader.pids()
        except OverflowError:
            self.raw['scan_overflows']+=1;self.stop('scan_limit');return
        for pid in pids:
            try:table[pid]=self.reader.stat(pid)
            except (OSError,ValueError,IndexError,KeyError,TypeError):self.raw['read_omissions']+=1
        root=table.get(self.gateway.pid)
        if root is None or ancestry(root)!=self.root:self.stop('identity_mismatch');return
        current={};candidates=0
        for pid,row in table.items():
            chain=[];node=row;seen=set()
            while node['pid']!=self.gateway.pid and len(chain)<64 and node['pid'] not in seen:
                seen.add(node['pid']);chain.append(node)
                node=table.get(node['ppid'])
                if node is None:break
            if node is not None and node['pid']!=self.gateway.pid and (len(chain)>=64 or node['pid'] in seen):
                self.raw['ancestry_omissions']+=1
            if node is None or node['pid']!=self.gateway.pid:continue
            if pid==self.gateway.pid:continue  # anchor is not a descendant
            chain.append(node);candidates+=1
            if candidates>MAX_OWNED:self.raw['owned_overflows']+=1;continue
            try:
                # Validate chain on both sides of executable identity read.
                for link in chain:
                    if ancestry(self.reader.stat(link['pid']))!=ancestry(link):raise ValueError('chain_changed')
                exe,exe_id=self.reader.exe(pid)
                latest=self.reader.stat(pid)
                for link in chain:
                    if ancestry(self.reader.stat(link['pid']))!=ancestry(link):raise ValueError('chain_changed')
                exe_after,exe_id_after=self.reader.exe(pid)
                if (exe,exe_id)!=(exe_after,exe_id_after):raise ValueError('exec_race')
                for link in chain:
                    if ancestry(self.reader.stat(link['pid']))!=ancestry(link):raise ValueError('final_chain_changed')
            except (OSError,ValueError,IndexError,KeyError,TypeError):
                self.raw['ancestry_omissions']+=1;continue
            key=identity(row)
            current[key]=(latest,exe,exe_id,offset)
        if ancestry(self.reader.stat(self.gateway.pid))!=self.root:
            self.stop('identity_mismatch');return
        finished=self.clock()
        if number(finished) is None or finished<self.last_clock:
            self.stop('clock_invalid');return
        if finished-self.origin>END:
            self.stop('window_complete');return
        self.last_clock=finished
        self.raw['new_identities']+=len(current.keys()-self.previous.keys())
        self.raw['lost_identities']+=len(self.previous.keys()-current.keys())
        thread_candidates=[]
        for key,(row,exe,exe_id,at) in current.items():
            life=self.lifetimes.get(key)
            if life is None:
                if len(self.lifetimes)>=MAX_LIFETIMES:self.raw['lifetime_omissions']+=1;continue
                life=dict(lifetime_id=len(self.lifetimes)+1,category=category(exe),cpu_delta_s=0.,covered_wall_s=0.,intervals=0,samples=0,peak_rss_kib=0,max_threads=0,last_state=row['state'])
                self.lifetimes[key]=life
            life['samples']+=1;life['peak_rss_kib']=max(life['peak_rss_kib'],row['rss']*self.page//1024)
            life['max_threads']=max(life['max_threads'],row['threads']);life['last_state']=row['state']
            previous=self.previous.get(key)
            if previous is not None:
                old,old_exe,old_id,before=previous
                if (old_exe,old_id)!=(exe,exe_id):
                    self.raw['exec_changes']+=1;life['category']='unavailable'
                elif ancestry(row)!=ancestry(old):self.raw['ancestry_omissions']+=1
                elif row['ticks']<old['ticks']:self.raw['counter_regressions']+=1
                elif at>before:
                    life['cpu_delta_s']+=(row['ticks']-old['ticks'])/self.ticks
                    life['covered_wall_s']+=at-before;life['intervals']+=1
                    if life['category']!='unavailable':
                        thread_candidates.append(dict(row=row,exe=exe,exe_id=exe_id,lifetime_id=life['lifetime_id'],category=category(exe),delta_ticks=row['ticks']-old['ticks']))
            # Once a lifetime crosses exec, preserve ambiguity in its label.
        self.previous=current
        self.raw['snapshots']+=1;self.raw['status']='sampling'
        self.raw.setdefault('first_offset_s',offset);self.raw['last_offset_s']=offset
        self.raw['snapshot_span_s']=offset-self.raw['first_offset_s']
        self.raw['rows']=list(self.lifetimes.values())
        self.thread_call('observe',thread_candidates,self.validate_thread_target)

    def validate_thread_target(self,pid,start,exe,exe_id):
        """Fresh bounded ownership proof, never API PID or an asserted role."""
        if self.closed or self.gateway.returncode is not None:raise ValueError('target_closed')
        chain=[];seen=set();node=self.reader.stat(pid)
        if identity(node)!=(pid,start):raise ValueError('target_changed')
        while node['pid']!=self.gateway.pid:
            if len(chain)>=64 or node['pid'] in seen:raise ValueError('chain_limit')
            seen.add(node['pid']);chain.append(node)
            node=self.reader.stat(node['ppid'])
        if not chain or ancestry(node)!=self.root:raise ValueError('root_changed')
        chain.append(node)
        frozen=tuple(ancestry(link) for link in chain)
        if self.thread_chain is not None and frozen!=self.thread_chain:raise ValueError('target_chain_changed')
        for link in chain:
            if ancestry(self.reader.stat(link['pid']))!=ancestry(link):raise ValueError('chain_changed')
        if self.reader.exe(pid)!=(exe,exe_id):raise ValueError('exec_changed')
        latest=self.reader.stat(pid)
        for link in chain:
            if ancestry(self.reader.stat(link['pid']))!=ancestry(link):raise ValueError('chain_changed')
        if self.reader.exe(pid)!=(exe,exe_id):raise ValueError('exec_changed')
        for link in chain:
            if ancestry(self.reader.stat(link['pid']))!=ancestry(link):raise ValueError('chain_changed')
        self.thread_chain=frozen
        return latest,exe,exe_id

    def result(self):
        result=self.thread_call('result')
        if result is not None:self.raw['thread_observation']=result
        return sanitize(self.raw)
