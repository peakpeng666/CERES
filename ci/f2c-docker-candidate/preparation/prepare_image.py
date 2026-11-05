"""Trusted future preparation controller. No operation on import; explicit execution gate.
The Docker daemon and reviewed setup actions are bootstrap trust roots. Workload
namespace evidence is collected separately; this module does not assert isolation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import posixpath
import urllib.request
import stat
import subprocess
import sys
import importlib.util

_diagnostic_spec=importlib.util.spec_from_file_location("preparation_diagnostic",Path(__file__).resolve().parents[1]/"preparation_diagnostic.py")
_diagnostic=importlib.util.module_from_spec(_diagnostic_spec);_diagnostic_spec.loader.exec_module(_diagnostic)
PREPARATION_STAGE="entry"

BASE='docker.io/library/ubuntu@sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04'
APPLICATION='3acf840d1a0c16ab1cfcca15744e4ea6609ddb35'
PROTOCOL='urp.f2c.docker-engine-netnone/1'
ENV={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','HOME':'/nonexistent','DOCKER_BUILDKIT':'1'}

def require(value,code):
    if not value:raise RuntimeError(code)

def digest(data):return hashlib.sha256(data).hexdigest()

def read_file(path,limit=256*1024*1024):
    p=Path(path)
    for item in [p,*p.parents]: require(not item.is_symlink(),'SOURCE_ALIAS')
    before=p.lstat();require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=limit,'SOURCE_TYPE_SIZE')
    raw=p.read_bytes();after=p.lstat()
    # Reading may legitimately update atime. Retain identity, permissions and
    # mutation timestamps (nanoseconds), not access bookkeeping, in the guard.
    fields=('st_dev','st_ino','st_mode','st_nlink','st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns')
    require(tuple(getattr(after,k) for k in fields)==tuple(getattr(before,k) for k in fields)
            and len(raw)==before.st_size,'SOURCE_CHANGED')
    return raw

def unique(pairs):
    out={}
    for k,v in pairs:require(k not in out,'DUPLICATE_KEY');out[k]=v
    return out

def safe_relative(name):
    require(isinstance(name,str) and name and not name.startswith('/') and all(p not in ('','.','..') for p in name.split('/')),'SOURCE_PATH')
    return name

def write(path,raw,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(raw)
    path.chmod(mode)

def record_buildkit_args(out, command):
    """Best-effort diagnostic binding from the exact already-constructed argv.

    Never changes command selection or gates, and never prints argument values.
    """
    try:
        rows = {}
        for i, value in enumerate(command):
            if value == '--build-arg':
                key, raw = command[i + 1].split('=', 1)
                if key in rows:
                    return False
                rows[key] = raw
        if set(rows) != {'WORKLOAD_UID', 'WORKLOAD_GID', 'INPUT_SOURCE_SHA', 'BOOTSTRAP_SHA'}:
            return False
        for key in ('WORKLOAD_UID', 'WORKLOAD_GID'):
            raw = rows[key]
            if not re.fullmatch('[1-9][0-9]{0,9}', raw) or int(raw) > 4294967294:
                return False
            rows[key] = int(raw)
        if any(not re.fullmatch('[0-9a-f]{64}', rows[key]) for key in ('INPUT_SOURCE_SHA', 'BOOTSTRAP_SHA')):
            return False
        write(out / 'buildkit-args.json', json.dumps(rows, sort_keys=True).encode('ascii') + b'\n', 0o600)
        return True
    except Exception:
        return False


def anchor_copy(source,target,expected):
    require(re.fullmatch('[0-9a-f]{64}',expected),'SOURCE_ANCHOR_FORMAT')
    raw=read_file(source/'source-index.json');require(digest(raw)==expected,'SOURCE_ANCHOR')
    idx=json.loads(raw,object_pairs_hook=unique)
    require(idx['schema']==1 and idx['status']=='reviewed-source' and type(idx['files']) is dict,'SOURCE_SCHEMA')
    for name,row in idx['files'].items():
        safe_relative(name);data=read_file(source/name)
        require(digest(data)==row['sha256'],'SOURCE_BYTES');write(target/name,data)
    write(target/'source-index.json',raw)
    return idx

def git_bytes(source,args):
    return subprocess.check_output(['/usr/bin/git','-C',str(source),*args],env=ENV,stdin=subprocess.DEVNULL,stderr=subprocess.PIPE)

def stage_application(source,target):
    require(git_bytes(source,['rev-parse','HEAD']).decode().strip()==APPLICATION,'APPLICATION_HEAD')
    require(not git_bytes(source,['status','--porcelain','--untracked-files=no']),'APPLICATION_DIRTY')
    rows={};total=0
    # Compiler-only source: never stage unrelated repository experiments/results.
    for item in git_bytes(source,['ls-tree','-r','-z','--full-tree',APPLICATION,'--','src/lab/pyproject.toml','src/lab/uv.lock']).split(b'\0'):
        if not item:continue
        attrs,name=item.split(b'\t',1);mode,kind,oid=attrs.decode().split();name=name.decode();safe_relative(name)
        require(name in ('src/lab/pyproject.toml','src/lab/uv.lock') and name not in rows,'APPLICATION_SOURCE_SCOPE')
        require(mode in ('100644','100755') and kind=='blob' and re.fullmatch('[0-9a-f]{40}',oid),'APPLICATION_ENTRY')
        raw=git_bytes(source,['cat-file','blob',oid]);require(len(raw)<64*1024*1024,'APPLICATION_FILE_SIZE')
        total+=len(raw);require(total<=128*1024*1024 and len(rows)<10000,'APPLICATION_SOURCE_BOUND')
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==oid,'APPLICATION_BLOB')
        write(target/name,raw,0o755 if mode=='100755' else 0o644);rows[name]={'sha256':digest(raw),'git_blob':oid}
    require(set(rows)=={'src/lab/pyproject.toml','src/lab/uv.lock'},'APPLICATION_EMPTY')
    require(git_bytes(source,['rev-parse','HEAD']).decode().strip()==APPLICATION,'APPLICATION_CHANGED')
    return rows

def input_seed(control,application,destination):
    seed=control/'preparation/inputs'
    for p in sorted(seed.rglob('*')):
        if p.is_dir():continue
        name=p.relative_to(seed).as_posix();safe_relative(name)
        require(name!='source-index.json','SEED_PREMATURE_ANCHOR')
        write(destination/name,read_file(p))
    app=stage_application(application,destination/'application')
    tree=git_bytes(application,['rev-parse',APPLICATION+'^{tree}']).decode().strip()
    require(re.fullmatch('[0-9a-f]{40}',tree),'APPLICATION_TREE_ID')
    app_receipt={'schema':1,'commit':APPLICATION,'git_root_tree':tree,'scope':'compiler-selected-source-only','selected_scopes':['src/lab/pyproject.toml','src/lab/uv.lock'],'files':app,'whole_repository_materialized':False}
    write(destination/'application-source.json',(json.dumps(app_receipt,sort_keys=True,indent=2)+'\n').encode())
    for name in ('engine_gate.py','offline_build.py','build_plan.py','freeze_input_index.py'):
        write(destination/name,read_file(control/name))
    for item in sorted((control/'validation').rglob('*')):
        if item.is_file():write(destination/'validation'/item.relative_to(control/'validation'),read_file(item))
    rows={}
    for item in destination.rglob('*'):
        name=item.relative_to(destination).as_posix()
        if item.is_symlink():
            require(name.startswith('application/'),'NONAPPLICATION_LINK')
            target=os.readlink(item);rows[name]={'sha256':digest(target.encode()),'symlink':target}
        elif item.is_file():rows[name]={'sha256':digest(read_file(item))}
    result={'schema':1,'status':'reviewed-source','application_commit':APPLICATION,'application_source_scope':['src/lab/pyproject.toml','src/lab/uv.lock'],'files':rows}
    raw=(json.dumps(result,sort_keys=True,indent=2)+'\n').encode();write(destination/'source-index.json',raw)
    return digest(raw)

def run_private(command,log):
    with log.open('xb') as f:subprocess.run(command,env=ENV,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,check=True)

def execute(source,application,uv,bootstrap_lock,bootstrap_sha,source_sha,out):
    global PREPARATION_STAGE
    PREPARATION_STAGE="entry"
    require(os.getuid()>0 and os.getgid()>0,'NONROOT_CONTROLLER')
    require(out.is_absolute() and not out.exists() and not out.is_symlink(),'FRESH_OUTPUT')
    require(out.resolve()==out,'OUTPUT_ALIAS')
    out.mkdir(mode=0o700);context=out/'context';context.mkdir(mode=0o700)
    docker_config=out/'docker-client';docker_config.mkdir(mode=0o700);write(docker_config/'config.json',b'{}\n',0o600)
    docker=['/usr/bin/docker','--host','unix:///var/run/docker.sock','--config',str(docker_config)]
    PREPARATION_STAGE="source_verification"
    control=context/'control';anchor_copy(source,control,source_sha)
    PREPARATION_STAGE="bootstrap_bytes"
    raw=read_file(bootstrap_lock);require(digest(raw)==bootstrap_sha,'BOOTSTRAP_LOCK_ANCHOR')
    # Load only independently anchored preparation helper bytes.
    namespace={'__name__':'anchored_bootstrap_tools'}
    exec(compile(read_file(control/'preparation/bootstrap_tools.py'),'<anchored-bootstrap>','exec'),namespace)
    lock=namespace['validate_lock'](json.loads(raw,object_pairs_hook=unique))
    data=read_file(uv);row=lock['tools']['uv']
    require(len(data)==row['bytes'] and digest(data)==row['sha256'],'BOOTSTRAP_BYTES')
    write(context/'bootstrap/uv',data,0o755)
    write(context/'bootstrap/bootstrap-lock.json',raw)
    PREPARATION_STAGE="seed_retrieval"
    ca_url='https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/c/ca-certificates/ca-certificates_20260816ubuntu0.22.04.1_all.deb'
    with urllib.request.urlopen(ca_url,timeout=180) as response:
        require(response.url.startswith('https://'),'CA_TRANSPORT');ca=response.read(139541)
    require(len(ca)==139540 and digest(ca)=='29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b','CA_ARCHIVE_BYTES')
    write(context/'bootstrap/ca-certificates.deb',ca)
    for name,url,size,expected in (
        ('jammy-security.InRelease','https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/dists/jammy-security/InRelease',128927,'eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9'),
        ('jammy-security.main.amd64.Packages.xz','https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/dists/jammy-security/main/binary-amd64/Packages.xz',3643160,'193e21d53e0d7fbf97fed37314da94d75223d4575b769aa95891d8ea1592471a')):
        with urllib.request.urlopen(url,timeout=180) as response:
            require(response.url.startswith('https://'),'SEED_TRANSPORT');data=response.read(size+1)
        require(len(data)==size and digest(data)==expected,'SEED_BYTES');write(context/'bootstrap'/name,data)
    PREPARATION_STAGE="input_staging"
    input_sha=input_seed(control,application,context/'inputs')
    write(context/'Dockerfile',read_file(control/'preparation/Dockerfile'))
    iid=out/'prepared-image.id'
    # Build context contains only anchored helpers, verified Git blobs and the
    # declared UV bootstrap executable. No credentials, checkout .git or host libs.
    cmd=docker+['build','--platform','linux/amd64','--pull','--no-cache','--progress=plain',
         '--build-arg','WORKLOAD_UID='+str(os.getuid()),'--build-arg','WORKLOAD_GID='+str(os.getgid()),
         '--build-arg','INPUT_SOURCE_SHA='+input_sha,'--build-arg','BOOTSTRAP_SHA='+bootstrap_sha,
         '--iidfile',str(iid),str(context)]
    PREPARATION_STAGE="image_build"
    record_buildkit_args(out, cmd)
    run_private(cmd,out/'preparation.log')
    PREPARATION_STAGE="image_inspection"
    image=read_file(iid,128).decode().strip();require(re.fullmatch('sha256:[0-9a-f]{64}',image),'IMAGE_ID')
    inspect=json.loads(subprocess.check_output(docker+['image','inspect',image],env=ENV,stderr=subprocess.PIPE))
    require(len(inspect)==1 and inspect[0]['Id']==image and inspect[0]['Architecture']=='amd64' and inspect[0]['Os']=='linux','IMAGE_IDENTITY')
    base=json.loads(subprocess.check_output(docker+['image','inspect',BASE],env=ENV,stderr=subprocess.PIPE))
    require(len(base)==1 and re.fullmatch('sha256:[0-9a-f]{64}',base[0]['Id']) and base[0]['Architecture']=='amd64' and base[0]['Os']=='linux','BASE_IDENTITY')
    require(inspect[0]['RootFS']['Layers'][:len(base[0]['RootFS']['Layers'])]==base[0]['RootFS']['Layers'],'IMAGE_ANCESTRY')
    require(inspect[0]['Config']['User']==f'{os.getuid()}:{os.getgid()}','IMAGE_USER')
    # docker create/cp/export are controller operations, not workload execution.
    PREPARATION_STAGE="input_export"
    cid=subprocess.check_output(docker+['create','--network','none','--entrypoint','/bin/false',image],env=ENV,stderr=subprocess.PIPE).decode().strip()
    require(re.fullmatch('[0-9a-f]{64}',cid),'EXPORT_CONTAINER_ID')
    exported=out/'inputs'
    try:run_private(docker+['cp',cid+':/inputs',str(exported)],out/'export.log')
    finally:subprocess.run(docker+['rm',cid],env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
    PREPARATION_STAGE="artifact_verification"
    anchor=json.loads(read_file(exported/'prepared-anchor.json',1024*1024),object_pairs_hook=unique)
    artifact=read_file(exported/'artifact-index.json',8*1024*1024)
    require(digest(artifact)==anchor['artifact_index_sha256'],'PREPARED_ANCHOR')
    files={}
    for p in sorted(exported.rglob('*')):
        if p.is_symlink():
            require(p.resolve().is_relative_to(exported),'EXPORTED_ALIAS_ESCAPE')
            files[p.relative_to(exported).as_posix()]={'symlink':os.readlink(p)}
        elif p.is_file():files[p.relative_to(exported).as_posix()]={'sha256':digest(read_file(p))}
        else:require(p.is_dir(),'EXPORTED_SPECIAL')
    manifest={'schema':1,'protocol':PROTOCOL,'base_manifest':BASE,'base_image_id':base[0]['Id'],'prepared_image_id':image,'application_commit':APPLICATION,
              'source_index_sha256':source_sha,'input_source_index_sha256':input_sha,'bootstrap_lock_sha256':bootstrap_sha,
              'artifact_index_sha256':anchor['artifact_index_sha256'],'input_index_sha256':digest(read_file(exported/'input-index.json',32*1024*1024)),'scope':'compiler-build-only','application_source_scope':['src/lab/pyproject.toml','src/lab/uv.lock'],'whole_repository_materialized':False,'runtime_image_prepared':False,'uid':os.getuid(),'gid':os.getgid(),
              'inputs':files,'native_build_executed':False,'bit_reproducible_claim':False}
    write(out/'prepared-image.json',(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode())
    return {'prepared_image_id':image,'input_index_sha256':manifest['input_index_sha256'],'manifest_sha256':digest(read_file(out/'prepared-image.json'))}

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute-trusted-preparation',action='store_true')
    for name in ('source','application','uv','bootstrap-lock','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('expected-source-index-sha256','expected-bootstrap-sha256'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    try:
        require(a.execute_trusted_preparation,'EXECUTION_NOT_AUTHORIZED')
        result=execute(a.source,a.application,a.uv,a.bootstrap_lock,a.expected_bootstrap_sha256,a.expected_source_index_sha256,a.output)
    except Exception as error:
        print(json.dumps(_diagnostic.project_exception(error,PREPARATION_STAGE),sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result))
if __name__=='__main__':main()
