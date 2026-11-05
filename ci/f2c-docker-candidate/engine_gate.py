"""Docker-engine-netnone/1: live entrypoint handshake and repeated kernel checks.
Only a trusted outside controller writes /control; it is read-only in the workload.
Not a rootless/user-namespace claim. No Docker API/socket is available here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import stat
import subprocess
import sys
import time

PROTOCOL='urp.f2c.docker-engine-netnone/1'
CONTROL=Path('/control')
OUTPUT=Path('/out')

def require(value, message):
    if not value: raise RuntimeError(message)

def read_json(path):
    def unique(pairs):
        out={}
        for key,value in pairs:
            require(key not in out, 'duplicate JSON key'); out[key]=value
        return out
    require(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), 'regular receipt required')
    require(path.stat().st_size<1024*1024, 'receipt too large')
    return json.loads(path.read_bytes(),object_pairs_hook=unique)

def observe():
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    ipv4=[line.split() for line in Path('/proc/net/route').read_text().splitlines()[1:]]
    ipv6=[line.split() for line in Path('/proc/net/ipv6_route').read_text().splitlines()]
    external=[r for r in ipv4 if r and r[0]!='lo']+[r for r in ipv6 if r and r[-1]!='lo']
    sockets=[]
    for item in Path('/proc/self/fd').iterdir():
        try: target=os.readlink(item)
        except FileNotFoundError: continue
        if target.startswith('socket:'): sockets.append(int(item.name))
    return {'protocol':PROTOCOL,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'netns':os.readlink('/proc/self/ns/net'),'uids':[int(x) for x in status['Uid'].split()],
        'gids':[int(x) for x in status['Gid'].split()], 'pid':os.getpid(),
        'interfaces':sorted(name for _,name in socket.if_nameindex()),'external_routes':external,
        'caps':{k:int(status[k].strip(),16) for k in ('CapEff','CapPrm','CapInh','CapBnd','CapAmb')},
        'no_new_privs':int(status['NoNewPrivs']), 'seccomp':int(status['Seccomp']),
        'socket_fds':sockets,'docker_socket':any(Path(x).exists() for x in ('/var/run/docker.sock','/run/docker.sock'))}

def validate_inside(observed, launch, approval=None):
    require(launch['protocol']==PROTOCOL and observed['protocol']==PROTOCOL,'wrong isolation protocol')
    require(type(launch['uid']) is int and launch['uid']>0 and type(launch['gid']) is int and launch['gid']>0,'nonroot required')
    require(observed['uids']==[launch['uid']]*4 and observed['gids']==[launch['gid']]*4,'kernel identity changed')
    require(set(observed['caps'])=={'CapEff','CapPrm','CapInh','CapBnd','CapAmb'} and not any(observed['caps'].values()),'capabilities present')
    require(observed['seccomp']==2 and observed['no_new_privs']==1,'kernel security state')
    require(observed['interfaces']==['lo'] and not observed['external_routes'],'external network state')
    require(not observed['socket_fds'] and not observed['docker_socket'],'socket exposure')
    require(observed['boot_id']==launch['host_boot_id'] and observed['netns']!=launch['host_netns'],'host namespace reuse')
    if approval is not None:
        require(approval['protocol']==PROTOCOL and approval['nonce']==launch['nonce'],'approval identity')
        require(approval['image_id']==launch['image_id'] and approval['input_anchor']==launch['input_anchor'],'approval source/image binding')
        require(approval['inside_netns']==approval['engine_pid_netns']==observed['netns'],'engine/kernel namespace mismatch')
        require(approval['host_boot_id']==observed['boot_id'],'engine boot identity')
        require(approval['engine_policy_verified'] is True,'outside engine policy missing')
    return observed

def verify_runtime_receipt(config, phase):
    # Namespace policy is never accepted from caller config, old host locks or env.
    launch=read_json(CONTROL/'launch.json'); approval=read_json(CONTROL/'approval.json')
    validate_inside(observe(),launch,approval)
    require(phase and isinstance(phase,str),'phase required')
    return {'protocol':PROTOCOL,'phase':phase,'image_id':launch['image_id'],'input_anchor':launch['input_anchor'],'nonce':launch['nonce']}

def hold():
    launch=read_json(CONTROL/'launch.json'); observed=validate_inside(observe(),launch)
    destination=OUTPUT/'inside-ready.json'
    ready={'nonce':launch['nonce'],'observed':observed}
    with destination.open('x') as f: json.dump(ready,f)
    print(json.dumps({'kind':'ready',**ready}),flush=True)
    # Host keeps this process live and checks /proc/<State.Pid> before approval.
    deadline=time.monotonic()+120
    while not (CONTROL/'approval.json').exists():
        require(time.monotonic()<deadline,'outside controller approval timeout')
        time.sleep(.1)
    verify_runtime_receipt({},'before-exec')
    command=launch['command']
    require(isinstance(command,list) and command and all(isinstance(x,str) and '\x00' not in x for x in command),'invalid anchored command')
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','HOME':'/out/home','TMPDIR':'/tmp'}
    code=125
    try:
        with (OUTPUT/'job.log').open('xb') as log:
            process=subprocess.Popen(command,env=env,cwd='/out',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True,start_new_session=True)
            try:
                code=process.wait(timeout=launch['timeout_s'])
            finally:
                # Kill the entire workload group, including orphaned compiler children.
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
        verify_runtime_receipt({},'after-command')
    except subprocess.TimeoutExpired:
        code=124
    except BaseException as error:
        code=125
        (OUTPUT/'job-error.json').write_text(json.dumps({'type':type(error).__name__,'reason':str(error)}))
    result={'kind':'finished','nonce':launch['nonce'],'exit_code':code}
    (OUTPUT/'job-result.json').write_text(json.dumps(result)+'\n')
    print(json.dumps(result),flush=True)
    deadline=time.monotonic()+300
    while not (CONTROL/'release').exists():
        require(time.monotonic()<deadline,'output export controller timeout')
        time.sleep(.1)
    raise SystemExit(code)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--hold',action='store_true',required=True);parser.parse_args();hold()
