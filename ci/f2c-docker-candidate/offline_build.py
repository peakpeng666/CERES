"""New-protocol offline builder. Reuses exact old input checks/compiler settings,
not its old namespace launcher. Entry only via anchored engine controller.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
ROOT=Path('/inputs'); OUT=Path('/out/build')

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module

def main():
    gate=load('engine_gate',ROOT/'engine_gate.py');receipt=gate.verify_runtime_receipt({},'build-preflight')
    g=load('guardlib',ROOT/'recipe/guardlib.py')
    # Full signed-input/host/provider proof remains mandatory, unchanged.
    anchor=json.loads((ROOT/'prepared-anchor.json').read_text())['artifact_index_sha256']
    g.verify_index(ROOT,anchor)
    verifier=load('offline_verifier',ROOT/'recipe/verify_prepared_inputs.py')
    lock=g.read_json(ROOT/'reviewed-inputs.lock.json')
    prefix,commands=verifier.verify_prepared(ROOT,sys.executable,lock,g)
    g.require(Path(sys.executable)==Path('/opt/urp-python/bin/python3.12'),'wrong Python selection')
    g.require(sys.version_info[:3]==(3,12,13),'wrong Python ABI patch')
    g.require(not OUT.exists() and not OUT.is_symlink(),'never reuse build output')
    g.require(shutil.disk_usage(OUT.parent).free>=lock['build']['minimum_free_bytes'],'insufficient bounded build storage')
    OUT.mkdir(mode=0o700)
    for name in ('home','tmp','cache','evidence','wheels'): (OUT/name).mkdir(mode=0o700)
    env={'PATH':str(OUT/'build-venv/bin')+':/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
        'HOME':str(OUT/'home'),'TMPDIR':str(OUT/'tmp'),'XDG_CACHE_HOME':str(OUT/'cache'),
        'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PIP_CONFIG_FILE':'/dev/null',
        'PIP_REQUIRE_VIRTUALENV':'1','PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1',
        'CC':str(commands['gcc']),'CXX':str(commands['gxx']),
        'SOURCE_DATE_EPOCH':str(lock['build']['source_date_epoch']),
        'CMAKE_BUILD_PARALLEL_LEVEL':str(lock['build']['parallel_jobs'])}
    def invoke(argv,label):
        gate.verify_runtime_receipt({},label)
        with (OUT/'evidence'/f'{label}.log').open('xb') as log:
            result=subprocess.run(argv,cwd=OUT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True)
        (OUT/'evidence'/f'{label}.exit-code').write_text(str(result.returncode)+'\n')
        g.require(result.returncode==0,'offline command failed: '+label)
    invoke([sys.executable,'-I','-B','-m','venv','--copies',str(OUT/'build-venv')],'create-venv')
    wh=next(r for r in lock['inputs']['trees'] if r['name']=='build-wheelhouse')
    invoke([str(OUT/'build-venv/bin/python'),'-I','-B','-m','pip','install','--no-index','--find-links',
        str(g.resolve_ref(ROOT,prefix,wh['ref'])),'--require-hashes','--only-binary=:all:',
        '-r',str(g.resolve_ref(ROOT,prefix,lock['inputs']['build_requirements']['ref']))],'install-build-tools')
    plan=load('build_plan',ROOT/'build_plan.py')
    invoke(plan.build_command(ROOT,prefix,commands,lock,OUT),'compile')
    wheels=list((OUT/'wheels').glob('*.whl'));g.require(len(wheels)==1,'expected exactly one wheel')
    with zipfile.ZipFile(wheels[0]) as z:
        member='fields2cover/_fields2cover_python.so';g.require(member in z.namelist(),'unexpected extension member')
        extension_sha=hashlib.sha256(z.read(member)).hexdigest()
    # Do not replace original frozen evidence after compiler execution.
    g.verify_index(ROOT,anchor);verifier.verify_prepared(ROOT,sys.executable,lock,g)
    gate.verify_runtime_receipt({},'build-post-integrity')
    result={'schema':1,'protocol':gate.PROTOCOL,'status':'built_not_accepted',
        'application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','python_version':'3.12.13',
        'wheel':{'path':'build/wheels/'+wheels[0].name,'sha256':g.sha256(wheels[0]),'extension_sha256':extension_sha},
        'build_image_id':receipt['image_id'],'input_manifest_sha256':receipt['input_anchor'],
        'runtime_interface_sha256':g.sha256(ROOT/'preparation-evidence/runtime-interface.json'),
        'pending':['ELF/provider closure','fresh runtime image','relocated imports','geometry','current full suite']}
    with Path('/out/build-result.json').open('x') as f:json.dump(result,f,indent=2)

if __name__=='__main__':main()
