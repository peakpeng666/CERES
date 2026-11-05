"""Bounded shutdown evidence only. No inference of PASS or arbitrary text output."""
import json
import math
import os
import stat
import errno
import re
from pathlib import Path

GROUPS={'sim','truth','nav','localization','foxglove','gazebo_web','gui','rviz'}
PHASES={'starting','undocking','localizing','nav2_starting','ready','executing','stopping','stopped','failed'}
SIGNALS={'SIGINT','SIGINT_LEADER','SIGTERM','SIGKILL'}
READS={'unavailable','missing','invalid','unreadable','too_large','available'}
ERRORS={'session_exit_nonzero','session_cleanup_failed','session_report_missing','forced_leftovers','bridge_crash','unknown'}
EVENTS={'shutdown_requested','exit_observed','wait_exhausted','signal_int','signal_term','signal_kill','forced_leftovers','bridge_crash','unknown'}
MAX_BYTES=16*1024*1024

def obj(v):return v if isinstance(v,dict) else {}
def rows(v):return v if isinstance(v,list) else []
def enum(v,allowed,default='unknown'):return v if isinstance(v,str) and v in allowed else default
def boolean(v):return v if type(v) is bool else None
def num(v,lo=0,hi=10000,integer=False):
    if type(v) not in ((int,) if integer else (int,float)):return None
    if not lo<=v<=hi or not math.isfinite(v):return None
    return v
def count(v):return min(len(v),1000000) if isinstance(v,list) else None
def codes(v):return [x for x in (num(x,-255,255,True) for x in rows(v)[:32]) if x is not None]
def error_code(v):
    if not isinstance(v,str):return 'unknown'
    for marker,code in [('会话关闭失败：真实退出码','session_exit_nonzero'),('会话清理未通过：','session_cleanup_failed'),('缺少会话关闭报告','session_report_missing'),('会话关闭后仍有残留进程','forced_leftovers'),('检测到桥接堆损坏/异常退出','bridge_crash')]:
        if marker in v:return code
    return 'unknown'
def event(v):
    if not isinstance(v,str):return 'unknown'
    for marker,code in [('POST /shutdown','shutdown_requested'),('内未退出，开始逐级发送信号','wait_exhausted'),('发送 SIGINT','signal_int'),('发送 SIGTERM','signal_term'),('发送 SIGKILL 到会话进程组','signal_kill'),('会话退出后仍有','forced_leftovers'),('检测到桥接堆损坏','bridge_crash'),('内退出，返回码','exit_observed')]:
        if marker in v:return code
    return 'unknown'
def outcome(v):return 'succeeded' if v is True else 'failed' if v is False else 'timeout' if isinstance(v,str) and v=='ROS 响应超时' else 'error' if isinstance(v,str) else 'unavailable'
def read_file(path):
    # Only regular, singly-linked files below unchanged, non-symlink directories.
    # Nonblocking open prevents FIFO/device traps before fstat validates the type.
    fds=[];chain=[]
    def identity(st):return (st.st_dev,st.st_ino,st.st_mode)
    def version(st):return (identity(st),st.st_size,st.st_mtime_ns,st.st_ctime_ns,st.st_nlink)
    try:
        path=Path(path)
        if '..' in path.parts:return 'invalid',{}
        path=Path(os.path.abspath(path));parts=path.parts[1:]
        if not parts or len(parts)>256:return 'invalid',{}
        flags=os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW
        parent=os.open('/',flags|os.O_DIRECTORY);fds.append(parent)
        for name in parts[:-1]:
            child=os.open(name,flags|os.O_DIRECTORY,dir_fd=parent);fds.append(child)
            chain.append((parent,name,identity(os.fstat(child))))
            parent=child
        fd=os.open(parts[-1],flags|os.O_NONBLOCK,dir_fd=parent);fds.append(fd)
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1:return 'invalid',{}
        if before.st_size>MAX_BYTES:return 'too_large',{}
        data=bytearray()
        while len(data)<=MAX_BYTES:
            chunk=os.read(fd,min(65536,MAX_BYTES+1-len(data)))
            if not chunk:break
            data.extend(chunk)
        if len(data)>MAX_BYTES:return 'too_large',{}
        if version(before)!=version(os.fstat(fd)) or version(before)!=version(os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)):
            return 'invalid',{}
        for ancestor,name,expected in chain:
            if identity(os.stat(name,dir_fd=ancestor,follow_symlinks=False))!=expected:return 'invalid',{}
        if len(data)!=before.st_size:return 'invalid',{}
        value=json.loads(data)
        return ('available',value) if isinstance(value,dict) else ('invalid',{})
    except FileNotFoundError:return 'missing',{}
    except (ValueError,UnicodeError,RecursionError):return 'invalid',{}
    except OSError as exc:return ('invalid' if exc.errno in (errno.ELOOP,errno.ENOTDIR) else 'unreadable'),{}
    finally:
        for fd in reversed(fds):
            try:os.close(fd)
            except OSError:pass

def capture(runtime,sid,report):
    if not isinstance(sid,str) or not re.fullmatch(r'sim_[A-Za-z0-9_]{1,124}',sid):return snapshot({})
    status,s=read_file(Path(runtime)/'sim'/sid/'session-report.json')
    gs,g=read_file(Path(runtime)/'sim'/sid/'gateway-stop.json')
    sh=obj(obj(report).get('shutdown'));lc=obj(s.get('lifecycle_shutdown'));gaz=obj(s.get('gazebo_shutdown'))
    raw={'session_read':status,'gateway_read':gs,'session_identity_matches':s.get('session_id')==sid if status=='available' else None,
      'gateway_identity_matches':g.get('session_id')==sid if gs=='available' else None,'session_status':s.get('status'),
      'full_loop_verified':sh.get('verified'),'full_loop_phase':sh.get('phase'),'full_loop_error_count':count(sh.get('errors')),
      'full_loop_error_codes':list(dict.fromkeys(error_code(v) for v in rows(sh.get('errors'))[:32])),
      'session_error_count':count(s.get('errors')),'final_state_error_count':count(obj(s.get('final_state')).get('errors')),'remaining_owned_count':count(s.get('remaining_owned_processes')),
      'abnormal_count':count(s.get('abnormal_exits')),'cleanup_count':count(s.get('cleanup')),
      'lifecycle':{k:outcome(lc.get(k)) for k in ('navigation','localization')},
      'gazebo':{'accepted':gaz.get('accepted'),'returncode':gaz.get('returncode'),'error_present':('error' in gaz) if isinstance(s.get('gazebo_shutdown'),dict) else None},
      'gateway_returncode':g.get('returncode'),'gateway_events':list(dict.fromkeys(event(obj(v).get('text')) for v in rows(g.get('steps'))[:32])),
      'leaves':{k:{'signaled_count':count(obj(s.get(k)).get('signaled')),'still_running_count':count(obj(s.get(k)).get('still_running'))} for k in ('robot_node_shutdown','bridge_shutdown')},
      'cleanup':[],'abnormal':[]}
    for c in rows(s.get('cleanup'))[:16]:
        c=obj(c)
        raw['cleanup'].append({'component':c.get('name'),'complete':c.get('complete'),'signals':c.get('signals'),'seconds':c.get('seconds'),
          'returncodes':[obj(x).get('returncode') for x in rows(c.get('reaped'))[:32]],'reaped_count':count(c.get('reaped'))})
    for a in rows(s.get('abnormal_exits'))[:32]:
        a=obj(a);raw['abnormal'].append({'component':a.get('group'),'lifecycle':a.get('lifecycle_shutdown_failed'),
          'forced_or_incomplete':a.get('forced_or_incomplete'),'returncodes':[a['returncode']] if 'returncode' in a else a.get('codes')})
    return snapshot(raw)

def snapshot(value):
    v=obj(value);out={k:enum(v.get(k),READS,'unavailable') for k in ('session_read','gateway_read')}
    out.update({k:boolean(v.get(k)) for k in ('session_identity_matches','gateway_identity_matches','full_loop_verified')})
    out.update(session_status=enum(v.get('session_status'),{'PASS','FAIL'},'unavailable'),full_loop_phase=enum(v.get('full_loop_phase'),PHASES))
    out.update({k:num(v.get(k),0,1000000,True) for k in ('full_loop_error_count','session_error_count','final_state_error_count','remaining_owned_count','abnormal_count','cleanup_count')})
    out['full_loop_error_codes']=list(dict.fromkeys(enum(x,ERRORS) for x in rows(v.get('full_loop_error_codes'))[:32]))
    out['gateway_returncode']=num(v.get('gateway_returncode'),-255,255,True)
    out['gateway_events']=list(dict.fromkeys(enum(x,EVENTS) for x in rows(v.get('gateway_events'))[:32]))
    out['lifecycle']={k:enum(obj(v.get('lifecycle')).get(k),{'succeeded','failed','timeout','error','unavailable'},'unavailable') for k in ('navigation','localization')}
    g=obj(v.get('gazebo'));out['gazebo']={'accepted':boolean(g.get('accepted')),'returncode':num(g.get('returncode'),-255,255,True),'error_present':boolean(g.get('error_present'))}
    out['leaves']={k:{f:num(obj(obj(v.get('leaves')).get(k)).get(f),0,1000000,True) for f in ('signaled_count','still_running_count')} for k in ('robot_node_shutdown','bridge_shutdown')}
    out['cleanup']=[]
    for c in rows(v.get('cleanup'))[:16]:
        c=obj(c);out['cleanup'].append({'component':enum(c.get('component'),GROUPS),'complete':boolean(c.get('complete')),
          'signals':[enum(x,SIGNALS) for x in rows(c.get('signals'))[:8]],'seconds':num(c.get('seconds')),
          'returncodes':codes(c.get('returncodes')),'reaped_count':num(c.get('reaped_count'),0,1000000,True)})
    out['abnormal']=[]
    for a in rows(v.get('abnormal'))[:32]:
        a=obj(a);out['abnormal'].append({'component':enum(a.get('component'),GROUPS),'lifecycle':enum(a.get('lifecycle'),{'navigation','localization'},'unavailable'),
          'forced_or_incomplete':boolean(a.get('forced_or_incomplete')),'returncodes':codes(a.get('returncodes'))})
    return out

def sanitize(value):
    v=obj(value)
    return {'schema':1,'scope':'shutdown_observation_only','at_full_loop_return':snapshot(v.get('at_full_loop_return')),'after_gateway_shutdown':snapshot(v.get('after_gateway_shutdown'))}

# Pure adaptation of the existing session-report fields for identity-verified
# in-memory consumers. Unlike capture(), these functions perform no I/O.
DETAIL_GROUP_LIMIT = 8
DETAIL_REAPED_LIMIT = 256
DETAIL_STATUSES = frozenset(('available', 'missing', 'invalid', 'truncated', 'unavailable'))
DETAIL_RETURN_CLASSES = ('normal', 'sigint', 'sigterm', 'sigkill', 'other')
_DETAIL_MISSING = object()


def _detail_status(v):
    return v if type(v) is str and len(v) <= 16 and v in DETAIL_STATUSES else 'unavailable'


def _detail_count(v):
    return v if type(v) is int and 0 <= v <= DETAIL_REAPED_LIMIT else None


def _detail_list_count(v):
    if v is _DETAIL_MISSING:return {'status': 'missing', 'count': None}
    if type(v) is not list:return {'status': 'invalid', 'count': None}
    return {'status': 'truncated' if len(v) > DETAIL_REAPED_LIMIT else 'available',
            'count': min(len(v), DETAIL_REAPED_LIMIT)}


def _detail_signals(v):
    if v is _DETAIL_MISSING:return 'missing', None
    if type(v) is not list:return 'invalid', None
    if any(type(x) is not str or len(x) > 16 or x not in SIGNALS for x in v[:3]):return 'invalid', None
    return ('truncated' if len(v) > 3 else 'available'), v[:3]


def _detail_returncodes(v):
    if v is _DETAIL_MISSING:return 'missing', None
    if type(v) is not list:return 'invalid', None
    counts = dict.fromkeys(DETAIL_RETURN_CLASSES, 0)
    for row in v[:DETAIL_REAPED_LIMIT]:
        rc = row.get('returncode') if type(row) is dict else None
        if type(rc) is not int or not -255 <= rc <= 255:return 'invalid', None
        category = 'normal' if rc == 0 else 'sigint' if rc in (-2, 130) else 'sigterm' if rc == -15 else 'sigkill' if rc == -9 else 'other'
        counts[category] += 1
    return ('truncated' if len(v) > DETAIL_REAPED_LIMIT else 'available'), counts


def session_detail(session):
    """Project only existing memory; caller must establish session identity."""
    if type(session) is not dict:return sanitize_session_detail({'status': 'invalid'})
    groups = session.get('cleanup', _DETAIL_MISSING)
    status = 'missing' if groups is _DETAIL_MISSING else 'invalid' if type(groups) is not list else 'truncated' if len(groups) > DETAIL_GROUP_LIMIT else 'available'
    out = {'status': status, 'groups': [], 'leaves': {}}
    if type(groups) is list:
        for child in groups[:DETAIL_GROUP_LIMIT]:
            child = child if type(child) is dict else {}
            signal_status, signals = _detail_signals(child.get('signals', _DETAIL_MISSING))
            reaped_status, classes = _detail_returncodes(child.get('reaped', _DETAIL_MISSING))
            out['groups'].append({'component': child.get('name'), 'complete': child.get('complete'),
                'signals_status': signal_status, 'signals': signals,
                'reaped_status': reaped_status, 'returncode_counts': classes})
    for name in ('robot_node_shutdown', 'bridge_shutdown'):
        leaf = session.get(name, _DETAIL_MISSING)
        value = leaf.get('still_running', _DETAIL_MISSING) if type(leaf) is dict else _DETAIL_MISSING
        out['leaves'][name] = _detail_list_count(value) if leaf is _DETAIL_MISSING or type(leaf) is dict else {'status': 'invalid', 'count': None}
    return sanitize_session_detail(out)


def sanitize_session_detail(value):
    """Closed, bounded, idempotent projection; unknown never means zero."""
    v = value if type(value) is dict else {}
    status = _detail_status(v.get('status'))
    rows_value = v.get('groups')
    groups = []
    if status in ('available', 'truncated'):
        if type(rows_value) is not list:status = 'invalid'
        else:
            if len(rows_value) > DETAIL_GROUP_LIMIT:status = 'truncated'
            for row in rows_value[:DETAIL_GROUP_LIMIT]:
                row = row if type(row) is dict else {}
                component = row.get('component')
                component = component if type(component) is str and len(component) <= 16 and component in GROUPS else 'unknown'
                complete = row.get('complete') if type(row.get('complete')) is bool else None
                ss = _detail_status(row.get('signals_status'))
                checked_ss, signals = _detail_signals(row.get('signals', _DETAIL_MISSING))
                if ss not in ('available', 'truncated'):signals = None
                elif checked_ss not in ('available', 'truncated'):ss, signals = 'invalid', None
                elif checked_ss == 'truncated':ss = 'truncated'
                if ss == 'truncated' and (signals is None or len(signals) != 3):ss, signals = 'invalid', None
                rs = _detail_status(row.get('reaped_status'))
                raw_counts = row.get('returncode_counts')
                counts = None
                if rs in ('available', 'truncated'):
                    if type(raw_counts) is dict:
                        candidate = {k: _detail_count(raw_counts.get(k)) for k in DETAIL_RETURN_CLASSES}
                        if all(x is not None for x in candidate.values()) and sum(candidate.values()) <= DETAIL_REAPED_LIMIT:counts = candidate
                    if counts is None:rs = 'invalid'
                    elif rs == 'truncated' and sum(counts.values()) != DETAIL_REAPED_LIMIT:rs, counts = 'invalid', None
                groups.append({'component': component, 'complete': complete,
                    'signals_status': ss, 'signals': signals,
                    'reaped_status': rs, 'returncode_counts': counts})
    leaves_value = v.get('leaves')
    leaves_value = leaves_value if type(leaves_value) is dict else {}
    leaves = {}
    for name in ('robot_node_shutdown', 'bridge_shutdown'):
        row = leaves_value.get(name)
        row = row if type(row) is dict else {}
        ls = _detail_status(row.get('status')); count = _detail_count(row.get('count'))
        if ls not in ('available', 'truncated'):count = None
        elif count is None:ls = 'invalid'
        elif ls == 'truncated' and count != DETAIL_REAPED_LIMIT:ls, count = 'invalid', None
        leaves[name] = {'status': ls, 'count': count}
    return {'status': status, 'groups': groups, 'leaves': leaves}
