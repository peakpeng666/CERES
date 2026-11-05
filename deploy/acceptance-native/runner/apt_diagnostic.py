#!/usr/bin/env python3
"""Read-only APT solver after install failure; stdout must remain private."""
import json, os, pathlib, re, subprocess, sys
TARGET='3acf840d1a0c16ab1cfcca15744e4ea6609ddb35'
BASE=['python3','curl','ca-certificates','gnupg','git','zstd']

def solver_command(source):
    source=pathlib.Path(source)
    def git(*args):return subprocess.check_output(['git','-C',str(source),*args],stderr=subprocess.DEVNULL)
    if git('rev-parse','HEAD').decode().strip()!=TARGET:raise ValueError('source_revision_mismatch')
    raw=(source/'deploy/ros-packages.txt').read_bytes()
    if raw!=git('show',TARGET+':deploy/ros-packages.txt'):raise ValueError('tracked_blob_mismatch')
    packages=[x for x in raw.decode().splitlines() if not re.match(r'^\s*(#|$)',x)]
    if not all(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*',x) for x in packages):raise ValueError('invalid_package_list')
    return ['apt-get','--simulate','-o','Debug::pkgProblemResolver=yes','install','--no-install-recommends',*BASE,*packages]

def projection_run(source,private,env):
    private=pathlib.Path(private);output=private/'apt-dependencies.json'
    command=['/usr/bin/python3',str(pathlib.Path(__file__).with_name('apt_projection.py')),
             '--source',str(source),'--log',str(private/'apt-solver.log'),'--output',str(output)]
    def failure(error,phase='subprocess'):
        result={'status':'metadata_unavailable','dependencies':[],'metadata_phase':phase,'metadata_error':error}
        output.write_text(json.dumps(result));return result
    def attempt():
        try:
            rc=subprocess.run(command,env=env,timeout=90,check=False).returncode
            if rc:return failure('subprocess_exit')
            if output.is_symlink() or output.stat().st_size>32768:return failure('invalid_output')
            result=json.loads(output.read_text())
            if not isinstance(result,dict):return failure('invalid_output')
            return result
        except subprocess.TimeoutExpired:
            phase='subprocess'
            try:
                previous=json.loads(output.read_text())
                if previous.get('metadata_phase') in ('module_import','source_identity','log_read','cache_open','metadata_project'):phase=previous['metadata_phase']
            except Exception:pass
            return failure('subprocess_timeout',phase)
        except OSError:return failure('subprocess_os')
        except (ValueError,TypeError):return failure('invalid_output')
    result=attempt()
    if result.get('status')!='verified':
        result=membership_fallback(source,private,env,result)
        output.write_text(json.dumps(result))
    return result


def membership_fallback(source,private,env,result):
    # This evidence is explicitly cache membership, NOT a verified official edge.
    if __package__:
        from .apt_projection import selected_edges,NAME
    else:
        from apt_projection import selected_edges,NAME
    result=dict(result);result['evidence']='apt_cache_membership';result['fallback_error']='none'
    result['dependencies']=[]
    try:
        solver_command(source)  # fixed public manifest and HEAD gate, no execution
        cache=subprocess.run(['apt-cache','pkgnames'],env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30,check=False)
        if cache.returncode:raise RuntimeError('cache_exit')
        if len(cache.stdout)>16777216:raise ValueError('cache_too_large')
        names={x for x in cache.stdout.decode('ascii').splitlines() if re.fullmatch(NAME,x)}
        log=pathlib.Path(private)/'apt-solver.log'
        if log.is_symlink():raise ValueError('unsafe_log')
        with log.open('rb') as f:
            f.seek(0,2);f.seek(max(0,f.tell()-65536));text=f.read(65536).decode('utf-8',errors='replace')
        edges=[{'package':p,'relation':r,'dependency':d} for p,r,d in selected_edges(text) if p in names and d in names][:20]
        result['dependencies']=edges;result['status']='membership_only' if edges else 'no_verified_edges'
    except subprocess.TimeoutExpired:result['fallback_error']='timeout'
    except RuntimeError:result['fallback_error']='exit'
    except OSError:result['fallback_error']='unavailable'
    except Exception:result['fallback_error']='output_invalid'
    return result



def diagnose(source, private):
    # No sudo, no update/download/install action: simulation explicitly disables locks.
    env=dict(os.environ,LC_ALL='C',LANG='C')
    private=pathlib.Path(private)
    log=private/'apt-solver.log'
    with log.open('wb') as stream:
        try:
            result=subprocess.run(solver_command(source),env=env,timeout=90,check=False,stdout=stream,stderr=subprocess.STDOUT)
            print('URP_APT_SOLVER_EXIT_'+str(result.returncode),flush=True)
        except subprocess.TimeoutExpired:print('URP_APT_SOLVER_TIMEOUT',flush=True)
    projection_run(source,private,env)


if __name__=='__main__':diagnose(sys.argv[1],sys.argv[2])
