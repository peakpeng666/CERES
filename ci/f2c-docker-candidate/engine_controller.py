"""Explicit future Docker execution controller; import/pure tests perform no calls.
Only --execute-authorized-run can invoke Docker. Requires locally prepared image
ID and externally anchored input manifest; never pulls or repairs the host.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import selectors
import shutil
import subprocess
import sys
import time
import importlib.util
_spec=importlib.util.spec_from_file_location('engine_gate',Path(__file__).resolve().with_name('engine_gate.py'))
_gate=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_gate)
PROTOCOL,require,validate_inside=_gate.PROTOCOL,_gate.require,_gate.validate_inside

DIGEST=re.compile(r'sha256:[0-9a-f]{64}')
CLIENT_CONFIG=None

def docker_argv(argv):
    require(CLIENT_CONFIG is not None and argv[0]=='docker','controller client not initialized')
    return ['/usr/bin/docker','--host','unix:///var/run/docker.sock','--config',str(CLIENT_CONFIG),*argv[1:]]

def client_env():
    return {'PATH':'/usr/bin:/bin','HOME':str(CLIENT_CONFIG),'LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}

def canonical(path):
    path=Path(path)
    require(path.is_absolute() and str(path)==str(path.resolve()) and ',' not in str(path),'canonical mount path required')
    for part in (path,*path.parents): require(not part.is_symlink(),'mount ancestor symlink')
    return path

def verify_inputs(root,expected):
    require(re.fullmatch('[0-9a-f]{64}',expected),'input anchor required')
    require(not (root/'input-index.json').is_symlink() and (root/'input-index.json').is_file(),'regular input index required')
    raw=(root/'input-index.json').read_bytes();require(hashlib.sha256(raw).hexdigest()==expected,'input anchor mismatch')
    def unique(pairs):
        out={}
        for key,value in pairs:
            require(key not in out,'duplicate input key');out[key]=value
        return out
    index=json.loads(raw,object_pairs_hook=unique)
    require(index['protocol']==PROTOCOL and index['application_commit']=='3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','wrong input protocol/source')
    require(index['python']=='/opt/urp-python/bin/python3.12','wrong selected interpreter')
    require(isinstance(index['files'],dict) and 'engine_gate.py' in index['files'],'incomplete input manifest')
    root_mode=root.lstat().st_mode
    require(stat.S_ISDIR(root_mode) and not root_mode & 0o022,'unsafe input root')
    actual=set()
    for member in root.rglob('*'):
        mode=member.lstat().st_mode
        if stat.S_ISDIR(mode):
            require(not mode & 0o022,'writable input directory')
        elif stat.S_ISLNK(mode):
            actual.add(str(member.relative_to(root)))
        elif stat.S_ISREG(mode):
            require(not mode & 0o022,'writable input member')
            actual.add(str(member.relative_to(root)))
        else:
            raise RuntimeError('unsupported input member')
    require(actual==set(index['files'])|{'input-index.json'},'input membership changed')
    for relative,row in index['files'].items():
        require(relative and not relative.startswith('/') and all(x not in ('','.','..') for x in relative.split('/')),'unsafe input path')
        path=root/relative
        require(path.resolve().is_relative_to(root),'input alias escapes root')
        if path.is_symlink():
            target=os.readlink(path)
            require(not target.startswith('/') and row.get('symlink')==target,'input alias changed')
            require(hashlib.sha256(target.encode()).hexdigest()==row['sha256'],'input alias bytes changed')
        else:
            require(not path.stat().st_mode & 0o022,'writable input member')
            require('symlink' not in row,'input type changed')
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],'input bytes changed')
    return index

def create_argv(image,inputs,control,uid,gid,out_bytes,memory_bytes):
    require(DIGEST.fullmatch(image),'prepared image ID required, never mutable tag')
    require(type(uid) is int and uid>0 and type(gid) is int and gid>0,'nonroot required')
    require(0<out_bytes<memory_bytes and memory_bytes<=16*1024**3,'bounded resource policy')
    return ['docker','create','--pull=never','--platform=linux/amd64','--network=none',
        '--user',f'{uid}:{gid}','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--log-driver=local','--log-opt=max-size=1m','--log-opt=max-file=1','--read-only','--ipc=private','--cgroupns=private','--pids-limit=512','--cpus=2',
        '--memory',str(memory_bytes),'--memory-swap',str(memory_bytes),'--ulimit','fsize=1073741824:1073741824',
        '--tmpfs',f'/out:rw,nosuid,nodev,size={out_bytes},uid={uid},gid={gid},mode=0700',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=268435456,mode=1777',
        '--mount',f'type=bind,src={inputs},dst=/inputs,readonly',
        '--mount',f'type=bind,src={control},dst=/control,readonly',
        '--workdir','/tmp','--entrypoint','/usr/bin/env',image,'-i','PATH=/usr/bin:/bin',
        'LANG=C.UTF-8','LC_ALL=C.UTF-8','/opt/urp-python/bin/python3.12','-I','-B','/inputs/engine_gate.py','--hold']

def check_inspect(info,launch,inputs,control):
    h=info['HostConfig'];c=info['Config']
    require(info['Image']==launch['image_id'] and info['State']['Running'],'container image/state mismatch')
    require(h['NetworkMode']=='none' and not h['Privileged'] and h['ReadonlyRootfs'],'engine isolation policy')
    require(h['CapDrop']==['ALL'] and not h.get('CapAdd') and h['SecurityOpt']==['no-new-privileges'],'engine security options')
    require(h.get('LogConfig')=={'Type':'local','Config':{'max-size':'1m','max-file':'1'}},'bounded daemon logs required')
    require(h.get('RestartPolicy',{}).get('Name') in ('no',''),'restart policy prohibited')
    require(h.get('Ulimits')==[{'Name':'fsize','Soft':1073741824,'Hard':1073741824}],'file size limit missing')
    require(h['PidMode']=='' and h['IpcMode']=='private' and h['UTSMode']=='' and h['CgroupnsMode']=='private','host namespace sharing')
    require(not h.get('Devices') and not h.get('DeviceRequests') and not h.get('PortBindings') and not h.get('VolumesFrom') and not h.get('Links') and not h.get('Sysctls'),'extra host access')
    require(h['Memory']==launch['memory_bytes'] and h['MemorySwap']==launch['memory_bytes'] and h['PidsLimit']==512 and h['NanoCpus']==2_000_000_000,'resource limits')
    expected_cmd=['-i','PATH=/usr/bin:/bin','LANG=C.UTF-8','LC_ALL=C.UTF-8','/opt/urp-python/bin/python3.12','-I','-B','/inputs/engine_gate.py','--hold']
    require(c['Cmd']==expected_cmd and c['WorkingDir']=='/tmp','entry command changed')
    require(c['User']==f"{launch['uid']}:{launch['gid']}" and c['Entrypoint']==['/usr/bin/env'],'entrypoint/user mismatch')
    require(h['Tmpfs']=={'/out':f"rw,nosuid,nodev,size={launch['out_bytes']},uid={launch['uid']},gid={launch['gid']},mode=0700",'/tmp':'rw,nosuid,nodev,size=268435456,mode=1777'},'output tmpfs bounds')
    binds={m['Destination']:(m['Source'],m['RW']) for m in info['Mounts'] if m['Type']=='bind'}
    require(binds=={'/inputs':(str(inputs),False),'/control':(str(control),False)},'bind mount policy')
    require(all(m['Type']=='bind' or (m['Type']=='tmpfs' and m['Destination'] in ('/out','/tmp')) for m in info['Mounts']),'unexpected mount')

def call(argv):
    try:
        p=subprocess.run(docker_argv(argv),env=client_env(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,close_fds=True,timeout=60)
    except subprocess.TimeoutExpired as error:
        record_call_failure(argv,'timeout',error.stdout,error.stderr)
        raise
    if p.returncode:
        record_call_failure(argv,p.returncode,p.stdout,p.stderr)
        raise RuntimeError('Docker controller command failed; see private controller-calls.jsonl')
    return p.stdout

def record_call_failure(argv,code,stdout,stderr):
    row={'argv':argv,'returncode':code,'stdout':(stdout or b'')[:1024*1024].decode('utf-8','replace'),'stderr':(stderr or b'')[:1024*1024].decode('utf-8','replace')}
    with (CLIENT_CONFIG.parent/'controller-calls.jsonl').open('a') as target:target.write(json.dumps(row)+'\n')

def start_identity(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]

def run(args):
    inputs=canonical(args.inputs);out=canonical(args.output)
    require(not out.exists(),'fresh output required');out.mkdir(mode=0o700)
    control=out/'control';control.mkdir(mode=0o700)
    global CLIENT_CONFIG
    CLIENT_CONFIG=out/'docker-client';CLIENT_CONFIG.mkdir(mode=0o700)
    index=verify_inputs(inputs,args.expected_input_sha256)
    require(args.phase in index['commands'],'unknown anchored phase')
    require(DIGEST.fullmatch(args.image),'prepared local image ID required')
    require(shutil.disk_usage(out).free>args.out_bytes*2+1024**3,'insufficient bounded export space')
    engine=json.loads(call(['docker','info','--format','{{json .}}']))
    require(any(x in engine.get('SecurityOptions',[]) for x in ('name=seccomp,profile=builtin','name=seccomp,profile=default')),'default seccomp unavailable')
    require(engine.get('MemTotal',0)>=args.memory_bytes+512*1024**2,'insufficient engine memory headroom')
    image=json.loads(call(['docker','image','inspect',args.image]))[0]
    require(image['Id']==args.image and image['Os']=='linux' and image['Architecture']=='amd64','local image identity/platform')
    launch=dict(protocol=PROTOCOL,nonce=secrets.token_hex(32),image_id=args.image,input_anchor=args.expected_input_sha256,
        uid=os.getuid(),gid=os.getgid(),host_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        host_netns=os.readlink('/proc/self/ns/net'),command=index['commands'][args.phase],out_bytes=args.out_bytes,memory_bytes=args.memory_bytes,timeout_s=args.timeout)
    (control/'launch.json').write_text(json.dumps(launch));(out/'engine-info.json').write_text(json.dumps(engine))
    cid=call(create_argv(args.image,inputs,control,launch['uid'],launch['gid'],args.out_bytes,args.memory_bytes)).decode().strip()
    require(re.fullmatch('[0-9a-f]{64}',cid),'container ID invalid')
    try:
        (out/'container-id').write_text(cid)
        call(['docker','start',cid]);deadline=time.monotonic()+120;ready=None
        while time.monotonic()<deadline:
            logs=call(['docker','logs',cid]);require(len(logs)<1024*1024,'unexpected controller log volume')
            rows=[json.loads(x) for x in logs.splitlines()]
            ready=next((r for r in rows if r.get('kind')=='ready'),None)
            if ready: break
            require(json.loads(call(['docker','inspect',cid]))[0]['State']['Running'],'entrypoint exited before readiness')
            time.sleep(.5)
        require(ready is not None and ready['nonce']==launch['nonce'],'entrypoint readiness absent')
        info=json.loads(call(['docker','inspect',cid]))[0];check_inspect(info,launch,inputs,control)
        pid=info['State']['Pid'];born=start_identity(pid);net=os.readlink(f'/proc/{pid}/ns/net')
        validate_inside(ready['observed'],launch)
        require(net==ready['observed']['netns'],'engine PID/kernel mismatch')
        again=json.loads(call(['docker','inspect',cid]))[0]
        require(again['State']['Running'] and again['State']['Pid']==pid and start_identity(pid)==born,'engine PID reused')
        approval=dict(protocol=PROTOCOL,nonce=launch['nonce'],image_id=args.image,input_anchor=args.expected_input_sha256,
            container_id=cid,engine_pid=pid,engine_starttime=born,engine_pid_netns=net,inside_netns=net,host_boot_id=launch['host_boot_id'],engine_policy_verified=True)
        (out/'inspect-approved.json').write_text(json.dumps(info));temp=control/'approval.tmp';temp.write_text(json.dumps(approval));temp.rename(control/'approval.json')
        deadline=time.monotonic()+args.timeout+30;finished=None
        while time.monotonic()<deadline:
            logs=call(['docker','logs',cid]);require(len(logs)<1024*1024,'unexpected controller log volume')
            finished=next((json.loads(x) for x in logs.splitlines() if json.loads(x).get('kind')=='finished'),None)
            if finished:break
            require(json.loads(call(['docker','inspect',cid]))[0]['State']['Running'],'entrypoint exited before result/export')
            time.sleep(1)
        require(finished is not None and finished['nonce']==launch['nonce'],'stage timeout or missing result')
        # Container remains alive so its hard-bounded tmpfs evidence still exists.
        export_output(cid,out,args.out_bytes)
        verify_inputs(inputs,args.expected_input_sha256)
        with (out/'private-output.tar').open('rb') as f: tar_sha256=hashlib.file_digest(f,'sha256').hexdigest()
        (out/'result.json').write_text(json.dumps({**finished,'protocol':PROTOCOL,'output_tar_sha256':tar_sha256}))
        (control/'release').write_text('release\n')
        return finished['exit_code']
    except BaseException as exc:
        # Evidence is best effort on a failed run; never mask its original error/cancellation.
        try:
            (out/'controller-failure.json').write_text(json.dumps({'status':'failed','type':type(exc).__name__,'reason':str(exc)}))
            if not (out/'private-output.tar').exists() and not (out/'private-output.partial.tar').exists():
                try: export_output(cid,out,args.out_bytes)
                except BaseException as export_error:
                    (out/'export-failure.json').write_text(json.dumps({'type':type(export_error).__name__,'reason':str(export_error)}))
        except BaseException: pass
        raise
    finally:
        # Only this exact newly-created ephemeral container is removed. Evidence remains.
        had_failure=sys.exc_info()[0] is not None
        try:call(['docker','rm','-f',cid])
        except BaseException as cleanup_error:
            try:
                (out/'cleanup-failure.json').write_text(json.dumps({'container_id':cid,'reason':str(cleanup_error),'removed':False}))
            except BaseException: pass
            if not had_failure:raise

def export_output(cid,out,out_bytes):
    """Bound export bytes and time; preserve partial evidence on errors."""
    partial=out/'private-output.partial.tar'
    with (out/'export-stderr.log').open('xb') as error:
        process=subprocess.Popen(docker_argv(['docker','cp',cid+':/out/.','-']),env=client_env(),stdout=subprocess.PIPE,stderr=error,stdin=subprocess.DEVNULL,close_fds=True)
        selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
        total=0;deadline=time.monotonic()+120
        try:
            with partial.open('xb') as target:
                while True:
                    require(time.monotonic()<deadline,'output export deadline')
                    if not selector.select(1):continue
                    chunk=os.read(process.stdout.fileno(),1024*1024)
                    if not chunk:break
                    total+=len(chunk);require(total<=out_bytes+512*1024**2,'export exceeds evidence bound');target.write(chunk)
            require(process.wait(timeout=5)==0,'output export failed')
            partial.rename(out/'private-output.tar')
        finally:
            selector.close();process.stdout.close()
            if process.poll() is None:process.kill();process.wait()

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute-authorized-run',action='store_true',required=True)
    p.add_argument('--inputs',required=True);p.add_argument('--output',required=True);p.add_argument('--image',required=True)
    p.add_argument('--expected-input-sha256',required=True);p.add_argument('--phase',choices=['build','validate'],required=True)
    p.add_argument('--out-bytes',type=int,default=6*1024**3);p.add_argument('--memory-bytes',type=int,default=8*1024**3);p.add_argument('--timeout',type=int,default=3600)
    a=p.parse_args();require(0<a.timeout<=3600,'bounded stage timeout');raise SystemExit(run(a))
if __name__=='__main__':main()
