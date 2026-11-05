"""Stage only the checked wheel/result from private bounded output tar.
Never extract arbitrary build members, paths or symlinks into the runtime inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
from engine_gate import PROTOCOL,require

def stage(archive, inputs, expected_tar_sha256):
    with archive.open('rb') as f:
        require(hashlib.file_digest(f,'sha256').hexdigest()==expected_tar_sha256,'output export digest mismatch')
    require(not (inputs/'build-result.json').exists(),'never overwrite build result')
    with tarfile.open(archive,'r:') as tar:
        selected={}
        for m in tar:
            name=m.name[2:] if m.name.startswith('./') else m.name
            if name.rstrip('/')=='build/wheels' and m.isdir():continue
            if name=='build-result.json' or name.startswith('build/wheels/'):
                require(m.isfile() and not m.issym() and not m.islnk(),'artifact must be regular')
                require(name not in selected,'duplicate artifact')
                require(not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')),'unsafe artifact name')
                require(m.size<=1024**3,'oversized artifact')
                selected[name]=m
        require('build-result.json' in selected,'build did not produce result')
        require(selected['build-result.json'].size<1024*1024,'oversized result')
        result=json.load(tar.extractfile(selected['build-result.json']))
        require(result['status']=='built_not_accepted' and result['protocol']==PROTOCOL,'wrong build state')
        require(result['application_commit']=='3acf840d1a0c16ab1cfcca15744e4ea6609ddb35' and result['python_version']=='3.12.13','wrong build target')
        name=result['wheel']['path'];require(set(selected)=={'build-result.json',name} and name.endswith('.whl'),'ambiguous wheel artifacts')
        data=tar.extractfile(selected[name]).read();require(hashlib.sha256(data).hexdigest()==result['wheel']['sha256'],'wheel bytes mismatch')
        target=inputs/name
        require(target.resolve().is_relative_to(inputs.resolve()) and not target.exists(),'unsafe/existing wheel destination')
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write(data)
        with (inputs/'build-result.json').open('x') as f:json.dump(result,f,indent=2)
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--expected-tar-sha256',required=True)
    a=p.parse_args();stage(a.archive,a.inputs,a.expected_tar_sha256)
