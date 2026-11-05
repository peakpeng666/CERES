#!/usr/bin/env python3
"""Opt-in repair for a disposable GitHub hosted VM, never a default local action."""
import argparse, hashlib, json, os, pathlib, platform, re, subprocess
TARGET='3acf840d1a0c16ab1cfcca15744e4ea6609ddb35'
ALLOW=frozenset(('pkgconf','r-base-dev','libunwind-14-dev','libc++-14-dev','libc++abi-14-dev','libc++-dev','libc++abi-dev','libmagickwand-dev','libmagickwand-6.q16-dev','libmagickcore-6.q16-dev','libmagickcore-dev'))
ARGS=['install','--no-install-recommends','pkg-config','libunwind-dev','pkgconf-','libunwind-14-dev-']
NEEDED={('pkgconf','pkg-config'),('libunwind-14-dev','libunwind-dev')}
class Refuse(Exception):pass

def host_gate(env,os_release,machine,architecture,setup_exit):
    if env.get('URP_HOST_APT_REPAIR')!='1':raise Refuse('opt_in_missing')
    if env.get('GITHUB_ACTIONS')!='true' or env.get('RUNNER_ENVIRONMENT')!='github-hosted':raise Refuse('host_not_allowed')
    if os_release.get('ID')!='ubuntu' or os_release.get('VERSION_ID')!='22.04' or machine!='x86_64' or architecture!='amd64':raise Refuse('platform_not_allowed')
    if setup_exit!=100:raise Refuse('setup_exit_not_allowed')

def evidence_gate(evidence):
    if evidence.get('status') not in ('membership_only','verified'):raise Refuse('conflict_not_confirmed')
    if evidence['status']=='membership_only' and evidence.get('evidence')!='apt_cache_membership':raise Refuse('conflict_not_confirmed')
    edges=evidence.get('dependencies')
    if not isinstance(edges,list) or len(edges)>20:raise Refuse('conflict_not_confirmed')
    pairs={(e.get('package'),e.get('dependency')) for e in edges if isinstance(e,dict) and e.get('relation') in ('conflicts','breaks')}
    if not NEEDED.issubset(pairs):raise Refuse('conflict_not_confirmed')

def valid_partial_verified(evidence):
    if not isinstance(evidence,dict) or evidence.get('status')!='verified':return False
    edges=evidence.get('dependencies')
    if not isinstance(edges,list) or not 1<=len(edges)<=20:return False
    keys={'package','package_version','relation','dependency','version_relation','dependency_version'}
    for edge in edges:
        if not isinstance(edge,dict) or set(edge)!=keys or not all(isinstance(x,str) for x in edge.values()):return False
        if not all(re.fullmatch(r'[a-z0-9][a-z0-9+.-]{0,119}',edge[k]) for k in ('package','dependency')):return False
        if not re.fullmatch(r'[A-Za-z0-9.+:~\-]{1,120}',edge['package_version']):return False
        if edge['dependency_version'] and not re.fullmatch(r'[A-Za-z0-9.+:~\-]{1,120}',edge['dependency_version']):return False
        if edge['relation'] not in ('depends','predepends','conflicts','breaks') or edge['version_relation'] not in ('any','eq','ge','le','gt','lt'):return False
        if (edge['version_relation']=='any') != (edge['dependency_version']==''):return False
    return True


def select_evidence(primary,source,private,env,fallback=None):
    try:
        evidence_gate(primary)
        return 'official_metadata' if primary['status']=='verified' else 'apt_cache_membership'
    except Refuse:
        if not valid_partial_verified(primary):raise
    if fallback is None:
        if __package__:
            from .apt_diagnostic import membership_fallback
        else:
            from apt_diagnostic import membership_fallback
        fallback=membership_fallback
    secondary=fallback(source,private,env,primary)
    # Never replace or merge the original official metadata evidence.
    (pathlib.Path(private)/'host-repair-evidence.json').write_text(json.dumps(secondary,sort_keys=True))
    if secondary.get('status')!='membership_only' or secondary.get('evidence')!='apt_cache_membership' or secondary.get('fallback_error')!='none':raise Refuse('conflict_not_confirmed')
    evidence_gate(secondary)
    return 'apt_cache_membership'


def removal_plan(text):
    removed=set()
    for line in text.splitlines():
        if line.startswith('Remv '):
            match=re.match(r'^Remv ([a-z0-9][a-z0-9+.-]*)(?::amd64)?(?:\s|$)',line)
            if not match or match[1] not in ALLOW:raise Refuse('removal_not_allowed')
            removed.add(match[1])
    if not {'pkgconf','libunwind-14-dev'}.issubset(removed):raise Refuse('replacement_plan_incomplete')
    return removed

def essential_gate(text,removed):
    checked=set()
    for line in text.splitlines():
        fields=line.split('\t')
        if len(fields)!=4:raise Refuse('package_safety_unknown')
        name=fields[0].removesuffix(':amd64')
        if name not in removed or fields[1] not in ('','no') or fields[2] not in ('','no') or fields[3] not in ('optional','extra','standard'):raise Refuse('package_safety_unknown')
        checked.add(name)
    if checked!=removed:raise Refuse('package_safety_unknown')

def state_digest():
    digest=hashlib.sha256()
    paths=[pathlib.Path('/var/lib/dpkg/status')]
    paths+=sorted(p for p in pathlib.Path('/var/lib/apt/lists').iterdir() if p.is_file() and p.name!='lock')
    for path in paths:
        if path.is_symlink():raise Refuse('package_state_changed')
        digest.update(str(path).encode())
        with path.open('rb') as stream:
            while block:=stream.read(1048576):digest.update(block)
    return digest.digest()

def repair(source,private,setup_exit,execute=subprocess.run):
    source=pathlib.Path(source);private=pathlib.Path(private)
    result={'state':'blocked','reason':'unknown','planned_removals':[],'planned_removal_count':0,'apply_exit_code':None,'evidence_used':'not_measured'}
    env=dict(os.environ,LC_ALL='C',LANG='C')
    def command(args,log,timeout=120):
        with (private/log).open('wb') as stream:
            rc=execute(args,stdout=stream,stderr=subprocess.STDOUT,env=env,timeout=timeout,check=False).returncode
        return rc
    try:
        # Cheap environment rejection occurs before any subprocess, including sudo.
        release={}
        for line in pathlib.Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                key,value=line.split('=',1);release[key]=value.strip('"')
        host_gate(env,release,platform.machine(),'amd64' if platform.machine()=='x86_64' else '',setup_exit)
        if (private/'host-apt-repair-attempted').exists():raise Refuse('already_attempted')
        head=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],stderr=subprocess.DEVNULL).decode().strip()
        if head!=TARGET:raise Refuse('source_identity')
        architecture=subprocess.check_output(['dpkg','--print-architecture'],stderr=subprocess.DEVNULL).decode().strip()
        if architecture!='amd64':raise Refuse('platform_not_allowed')
        evidence_path=private/'apt-dependencies.json'
        if evidence_path.is_symlink() or evidence_path.stat().st_size>32768:raise Refuse('conflict_not_confirmed')
        result['evidence_used']=select_evidence(json.loads(evidence_path.read_text()),source,private,env)
        (private/'host-apt-repair-attempted').touch(exist_ok=False)
        before=state_digest()
        if command(['apt-get','--simulate',*ARGS],'host-repair-plan.log'):raise Refuse('simulation_failed')
        log=private/'host-repair-plan.log'
        if log.stat().st_size>2097152:raise Refuse('plan_too_large')
        removed=removal_plan(log.read_text())
        safety=subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Essential}\t${Protected}\t${Priority}\n',*sorted(removed)],stderr=subprocess.DEVNULL).decode()
        essential_gate(safety,removed)
        if state_digest()!=before:raise Refuse('package_state_changed')
        result['planned_removals']=sorted(removed);result['planned_removal_count']=len(removed)
        rc=command(['sudo','env','DEBIAN_FRONTEND=noninteractive','apt-get','-y',*ARGS],'host-repair-apply.log',600)
        result['apply_exit_code']=rc
        if rc:raise Refuse('apply_failed')
        result.update(state='applied',reason='none')
    except Refuse as exc:result['reason']=str(exc)
    except subprocess.TimeoutExpired:result['reason']='command_timeout'
    except Exception:result['reason']='check_failed'
    (private/'host-apt-repair.json').write_text(json.dumps(result,sort_keys=True))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--private-dir',required=True);p.add_argument('--setup-exit',type=int,required=True);a=p.parse_args()
    result=repair(a.source,a.private_dir,a.setup_exit)
    raise SystemExit(0 if result['state']=='applied' else 1)
