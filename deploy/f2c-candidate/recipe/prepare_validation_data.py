#!/usr/bin/env python3
"""Future runner only: materialize locked Lab dependencies and official maps/data.
No native Fields2Cover import, test or acceptance occurs here. No publication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import urllib.request

SOURCE = '18557cfc61f291e8ac5fa4a80d9df212e1df0030'
MAP_COMMIT = 'a6ee13b63cbd524500cc6a68cd20dbef69f326a9'
MAPS = {
 'depot.pgm':'df4cf00eda944c6ab259449400451ac837afc0642110bcaadf45c27402f5819c',
 'depot.yaml':'5aca76bc788429c8d2b5372551e659e62baef3590b0aee72932d0868fcf25cff',
 'maze.pgm':'02f070e256b7f1f3842b99340a6c5a49866d1289ac4a78ca08da5f47745830cf',
 'maze.yaml':'bf03c81310d17daec4703c1f6f3243cb683264ebe63544c377e684824355081d',
 'warehouse.pgm':'934ce2ca1feacc2216b68142349c6ebb3e46cc7c76fd80a9c2d2a6fb85ba7ff7',
 'warehouse.yaml':'ec00d3d25912028e3debed9ded3d6471f70a3bf0f0d1a3f9470458f3068cc585',
}
DATA = {
 'wkt.zip':(211710,'d00d0c9574212e29f7f18be775975f6b1ffd38a7dcd3501d7c05d2f63b7b26f7'),
 'README.md':(5734,'e0d347ebe8f9711fc738014de0dc09feaaff2b667a65156b29fbea662c51637f'),
 'LICENSE':(20131,'7abe19ec9bb73b36141b999b861d24ad855e808bafe0f81e84cce28556f6c297'),
}

def require(ok, message):
    if not ok: raise RuntimeError(message)

def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def check_tree(root):
    root = root.resolve(strict=True)
    rows = {}
    for p in sorted(root.rglob('*')):
        mode = p.lstat().st_mode
        if p.is_symlink():
            require(p.resolve(strict=True).is_relative_to(root),'Dependency symlink escapes tree')
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeError('Special dependency entry')
        if p.is_file(): rows[p.relative_to(root).as_posix()] = sha(p)
    require(rows,'Empty dependency tree')
    return rows

def fetch(url, destination, expected, size=None):
    require(url.startswith('https://'),'HTTPS input required')
    require(not destination.exists() and not destination.is_symlink(),'Refuse existing download')
    request = urllib.request.Request(url,headers={'User-Agent':'URP-candidate-input-verification/1'})
    with urllib.request.urlopen(request,timeout=180) as response:
        require(response.url.startswith('https://'),'Non-HTTPS redirect')
        data = response.read(128 * 1024 * 1024 + 1)
    require(len(data)<=128*1024*1024,'Oversized official input')
    require(size is None or len(data)==size,'Input size mismatch')
    require(hashlib.sha256(data).hexdigest()==expected,'Input SHA256 mismatch')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as stream: stream.write(data)
    return {'source':url,'bytes':len(data),'sha256':expected}

def source_clean(source):
    head=subprocess.check_output(['/usr/bin/git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    require(head==SOURCE,'Wrong application source revision')
    subprocess.run(['/usr/bin/git','-C',str(source),'diff','--exit-code','HEAD','--'],check=True,stdout=subprocess.DEVNULL)

def materialize(source, root, python, uv):
    require(not root.exists() and not root.is_symlink(),'Validation input root must be fresh')
    source_clean(source)
    version=subprocess.check_output([str(uv),'--version'],text=True).strip()
    require(version.split()[:2]==['uv','0.12.23'],'Wrong uv version')
    pyversion=subprocess.check_output([str(python),'-I','-c','import platform;print(platform.python_version())'],text=True).strip()
    require(pyversion=='3.12.13','Wrong Python version')
    root.mkdir(parents=True,mode=0o700)
    env={'PATH':str(uv.parent)+':/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
         'HOME':str(root/'home'),'UV_NO_CACHE':'1','UV_PYTHON_DOWNLOADS':'never',
         'UV_PROJECT_ENVIRONMENT':str(root/'dependency-venv'), 'PYTHONDONTWRITEBYTECODE':'1',
         'UV_NO_PROGRESS':'1','UV_NO_ENV_FILE':'1'}
    (root/'home').mkdir()
    with (root/'uv-sync.log').open('xb') as log:
        subprocess.run([str(uv),'sync','--frozen','--no-build','--no-install-project','--group','dev',
                        '--project',str(source/'src/lab'),'--python',str(python)],
                       env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,check=True)
    site=root/'dependency-venv/lib/python3.12/site-packages'
    check_tree(site)
    require(not (site/'fields2cover').exists(),'Historical or unexpected Fields2Cover prohibited')
    # uv's own venv-startup hook is not a Lab dependency and must not execute
    # in the relocated validation environment. Record its bytes, then exclude
    # only these two exact top-level uv-generated files from the copied tree.
    excluded={name:sha(site/name) for name in ('_virtualenv.pth','_virtualenv.py') if (site/name).is_file()}
    def ignore(base,names):
        return set(names)&set(excluded) if Path(base)==site else set()
    shutil.copytree(site,root/'dependency-site',symlinks=False,ignore=ignore)
    dependencies=check_tree(root/'dependency-site')
    require(not any(name.endswith('.pth') or Path(name).name in ('sitecustomize.py','usercustomize.py')
                    for name in dependencies),'Unexpected dependency startup hook')
    receipts={}
    for name,digest in MAPS.items():
        url=f'https://raw.githubusercontent.com/turtlebot/turtlebot4/{MAP_COMMIT}/turtlebot4_navigation/maps/{name}'
        receipts['maps/'+name]=fetch(url,root/'maps'/name,digest)
    for name,(size,digest) in DATA.items():
        url=f'https://zenodo.org/api/records/14524735/files/{name}/content'
        receipts['runtime/library/fields2benchmark/'+name]=fetch(url,root/'runtime/library/fields2benchmark'/name,digest,size)
    source_clean(source)
    result={'schema':1,'status':'inputs_materialized_not_native_validated','application_commit':SOURCE,
            'uv_version':version,'uv_sha256':sha(uv),'python_version':pyversion,'python_sha256':sha(python),
            'lab_lock_sha256':sha(source/'src/lab/uv.lock'), 'dependency_files':dependencies, 'excluded_uv_venv_bootstrap_files':excluded,
            'official_inputs':receipts,'images_downloaded':False,
            'license_note':'Original LICENSE/README preserved; country-specific source restrictions apply.',
            'paths':{'dependency_site':'dependency-site','maps':'maps','runtime':'runtime'}}
    (root/'materialization.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'validation_inputs_materialized':True,'native_validated':False,
                      'dependency_file_count':len(dependencies),'official_file_count':len(receipts)}))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--root',required=True,type=Path)
    p.add_argument('--python',required=True,type=Path);p.add_argument('--uv',required=True,type=Path)
    a=p.parse_args();materialize(a.source.resolve(strict=True),a.root.absolute(),a.python.resolve(strict=True),a.uv.resolve(strict=True))

if __name__=='__main__': main()
