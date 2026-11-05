#!/usr/bin/env python3
"""Rebuild a bounded diagnostic from official public APT metadata, never raw text.

Does not update APT, install, mark or commit packages.
"""
import argparse, collections, importlib, json, pathlib, re, subprocess
LIMIT=65536
NAME=r'[a-z0-9][a-z0-9+.-]{0,119}'
RELATIONS={'Depends':'depends','PreDepends':'predepends','Conflicts':'conflicts','Breaks':'breaks'}
OPS={'':'any','=':'eq','>=':'ge','<=':'le','>>':'gt','<<':'lt','>':'gt','<':'lt'}
VERSION=re.compile(r'[A-Za-z0-9.+:~\-]{1,120}\Z')
METADATA_OPERATIONS=frozenset({'not_run','cache_membership','package_lookup',
    'version_selection','version_identity','origin_records','origin_validation',
    'dependency_groups','dependency_alternatives','dependency_fields','complete'})


def selected_edges(text):
    """Discard all text except syntactically recognized diagnostic edge identities."""
    found=[]; current=None
    for line in text.splitlines():
        debug=re.match(r'^Broken ('+NAME+r')(?::[a-z0-9]+)? (Depends|PreDepends|Conflicts|Breaks) on ('+NAME+r')(?::[a-z0-9]+)?(?:\s|$)',line)
        normal=re.match(r'^\s+('+NAME+r')(?:\:[a-z0-9]+)?\s*:\s*(Depends|PreDepends|Pre-Depends|Conflicts|Breaks):\s*('+NAME+r')(?::[a-z0-9]+)?(?:\s|$)',line)
        if debug or normal:
            p,r,d=(debug or normal).groups();r=r.replace('Pre-Depends','PreDepends')
            current=p;found.append((p,RELATIONS[r],d))
        else:
            continuation=re.match(r'^\s+(Depends|PreDepends|Pre-Depends|Conflicts|Breaks):\s*('+NAME+r')(?::[a-z0-9]+)?(?:\s|$)',line)
            if continuation and current:
                r,d=continuation.groups();found.append((current,RELATIONS[r.replace('Pre-Depends','PreDepends')],d))
            elif line.strip():current=None
        if len(found)>=200:break
    return list(dict.fromkeys(found))


def official(origin):
    site=getattr(origin,'site',None);suite=getattr(origin,'archive',None)
    if not isinstance(site,str) or not isinstance(suite,str):return False
    site=site.lower()
    return origin.trusted and ((origin.origin=='Ubuntu' and (site=='archive.ubuntu.com' or site.endswith('.archive.ubuntu.com') or site=='security.ubuntu.com') and suite in {'jammy','jammy-updates','jammy-security','jammy-backports'}) or (origin.origin=='ROS' and site=='packages.ros.org' and suite=='jammy'))


def metadata_lookup(cache,observe=None):
    # Only a fixed operation name crosses this callback, never an API value,
    # exception string, package name, source URI or stack frame.
    observe=observe or (lambda operation: None)
    memo={}
    def lookup(name):
        if name in memo:return memo[name]
        result=[];memo[name]=result
        if not re.fullmatch(NAME,name):return result
        observe('cache_membership')
        if name not in cache:return result
        observe('package_lookup')
        pkg=cache[name]
        # Select only current candidate/installed records with a public official origin.
        observe('version_selection')
        versions=[pkg.candidate,pkg.installed]
        seen=set()
        for v in versions:
            observe('version_identity')
            if v is None or not isinstance(v.version,str) or v.version in seen:continue
            seen.add(v.version)
            if not VERSION.fullmatch(v.version):continue
            observe('origin_records')
            origins=v.origins
            observe('origin_validation')
            if not any(official(o) for o in origins):continue
            for typ,relation in RELATIONS.items():
                observe('dependency_groups')
                for group in (v.get_dependencies(typ) or ()):
                    observe('dependency_alternatives')
                    for dep in (group.or_dependencies or ()):
                        observe('dependency_fields')
                        if not isinstance(dep.name,str) or not isinstance(dep.version,str) or not isinstance(dep.relation,str):continue
                        if not re.fullmatch(NAME,dep.name) or dep.relation not in OPS:continue
                        if dep.version and not VERSION.fullmatch(dep.version):continue
                        result.append({'package':name,'package_version':v.version,'relation':relation,'dependency':dep.name,'version_relation':OPS[dep.relation],'dependency_version':dep.version})
            # Enumeration membership must also work for packages without dependencies.
            result.append({'package':name,'package_version':v.version,'relation':'membership','dependency':'','version_relation':'any','dependency_version':''})
        return result
    return lookup


def project(text,lookup,roots):
    selected=selected_edges(text)
    reachable=set(roots);todo=collections.deque(roots)
    while todo:
        if len(reachable)>10000:return {'status':'tree_limit','dependencies':[]}
        for edge in lookup(todo.popleft()):
            if edge['relation'] in {'depends','predepends'} and edge['dependency'] not in reachable:
                reachable.add(edge['dependency']);todo.append(edge['dependency'])
    out=[];seen=set()
    for package,relation,dependency in selected:
        if package not in reachable and dependency not in reachable:continue
        if not lookup(dependency):continue
        for edge in lookup(package):
            if edge['relation']!=relation or edge['dependency']!=dependency:continue
            key=tuple(edge.values())
            if key not in seen:seen.add(key);out.append(dict(edge))
            if len(out)==20:return {'status':'verified','dependencies':out}
    return {'status':'verified' if out else 'no_verified_edges','dependencies':out}


def error_category(exc):
    if isinstance(exc,ModuleNotFoundError):return 'import_missing'
    if isinstance(exc,ImportError):return 'import_error'
    if isinstance(exc,AttributeError):return 'api_attribute'
    if isinstance(exc,TypeError):return 'api_type'
    if isinstance(exc,KeyError):return 'api_key'
    if isinstance(exc,subprocess.CalledProcessError):return 'command_failed'
    if isinstance(exc,ValueError):return 'validation'
    if isinstance(exc,OSError):return 'filesystem'
    if isinstance(exc,SystemError):return 'apt_system'
    if isinstance(exc,RuntimeError):return 'apt_runtime'
    return 'unknown'


def collect(source,log,output):
    result={'status':'metadata_unavailable','dependencies':[],'metadata_phase':'module_import','metadata_error':'none','metadata_operation':'not_run'}
    def phase(name):
        result['metadata_phase']=name
        pathlib.Path(output).write_text(json.dumps(result,sort_keys=True))
    def operation(name):
        # In-memory only to avoid per-edge filesystem work; the exception or
        # success path persists it once. A hard subprocess timeout stays unknown.
        result['metadata_operation']=name if name in METADATA_OPERATIONS else 'not_run'
    try:
        phase('module_import')
        apt=importlib.import_module('apt')
        phase('source_identity')
        if __package__:
            from .apt_diagnostic import solver_command
        else:
            from apt_diagnostic import solver_command
        roots=solver_command(source)[6:]
        phase('log_read')
        path=pathlib.Path(log)
        if path.is_symlink():raise ValueError('unsafe_log')
        with path.open('rb') as f:
            f.seek(0,2);f.seek(max(0,f.tell()-LIMIT));text=f.read(LIMIT).decode('utf-8',errors='replace')
        phase('cache_open')
        cache=apt.Cache()
        phase('metadata_project')
        result.update(project(text,metadata_lookup(cache,operation),roots))
        result['metadata_phase']='complete'
        operation('complete')
    except Exception as exc:
        result['metadata_error']=error_category(exc)
    pathlib.Path(output).write_text(json.dumps(result,sort_keys=True))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--log',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    collect(args.source,args.log,args.output)

if __name__=='__main__':main()
