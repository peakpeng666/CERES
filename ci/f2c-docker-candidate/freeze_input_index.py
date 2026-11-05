"""Trusted preparation final step. Measures inputs; does not execute them.
Caller must anchor the reviewed source/preparer before using this derived index.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import stat
# Safe under Python -I: load only this reviewed sibling, never cwd/site modules.
import importlib.util
_spec=importlib.util.spec_from_file_location('engine_gate',Path(__file__).resolve().with_name('engine_gate.py'))
_gate=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_gate)
PROTOCOL,require=_gate.PROTOCOL,_gate.require

# Failure-only finite projection. No exception string or external value is emitted.
DIAGNOSTIC_STAGE = 'inputs_freeze'
DIAGNOSTIC_SUBSTAGES = frozenset(['entry', 'root_identity', 'input_walk', 'entrypoint_verify', 'index_write', 'index_measure'])
DIAGNOSTIC_MESSAGES = {'absolute source alias': 'ABSOLUTE_SOURCE_ALIAS', 'alias escapes root': 'ALIAS_ESCAPES_ROOT', 'canonical input root': 'CANONICAL_INPUT_ROOT', 'never replace an input index': 'NEVER_REPLACE_AN_INPUT_INDEX', 'nonregular input member': 'NONREGULAR_INPUT_MEMBER', 'phase entrypoint missing': 'PHASE_ENTRYPOINT_MISSING', 'writable input member': 'WRITABLE_INPUT_MEMBER'}
DIAGNOSTIC_PREFIXES = {}
DIAGNOSTIC_CLASSES = frozenset(('RuntimeError', 'CalledProcessError', 'FileNotFoundError', 'PermissionError', 'FileExistsError', 'OSError', 'ValueError', 'TypeError', 'KeyError', 'JSONDecodeError', 'UnicodeDecodeError', 'ImportError', 'ModuleNotFoundError', 'AttributeError', 'TimeoutError', 'OtherError'))
_DIAGNOSTIC_SUBSTAGE = 'entry'

def diagnostic_phase(value):
    global _DIAGNOSTIC_SUBSTAGE
    _DIAGNOSTIC_SUBSTAGE = value if value in DIAGNOSTIC_SUBSTAGES else 'entry'

def diagnostic_detail(error):
    code = 'UNKNOWN_FAILURE'
    if type(error) is RuntimeError and len(error.args) == 1 and type(error.args[0]) is str:
        code = DIAGNOSTIC_MESSAGES.get(error.args[0], code)
        if code == 'UNKNOWN_FAILURE':
            for prefix in sorted(DIAGNOSTIC_PREFIXES, key=len, reverse=True):
                if error.args[0].startswith(prefix):
                    code = DIAGNOSTIC_PREFIXES[prefix]
                    break
    name = type(error).__name__
    return {'schema': 1, 'stage': DIAGNOSTIC_STAGE,
            'substage': _DIAGNOSTIC_SUBSTAGE if _DIAGNOSTIC_SUBSTAGE in DIAGNOSTIC_SUBSTAGES else 'entry',
            'code': code, 'exception_class': name if name in DIAGNOSTIC_CLASSES else 'OtherError'}

def emit_diagnostic(error):
    try:
        print('SAFE_PREPARATION_DIAGNOSTIC:' + json.dumps(diagnostic_detail(error), sort_keys=True, separators=(',', ':')), file=sys.stderr)
    except Exception:
        pass  # Diagnostic failure never replaces the original unsuccessful outcome.


def freeze(root,phase):
    diagnostic_phase('root_identity')
    require(root.is_absolute() and root==root.resolve(),'canonical input root')
    destination=root/'input-index.json';require(not destination.exists() and not destination.is_symlink(),'never replace an input index')
    diagnostic_phase('input_walk')
    files={}
    for p in sorted(root.rglob('*')):
        name=str(p.relative_to(root));mode=p.lstat().st_mode
        require(stat.S_ISLNK(mode) or not mode & 0o022,'writable input member')
        if stat.S_ISDIR(mode):continue
        require(p.resolve().is_relative_to(root),'alias escapes root')
        if p.is_symlink():
            target=p.readlink().as_posix()
            require(not target.startswith('/'),'absolute source alias')
            row={'symlink':target,'sha256':hashlib.sha256(target.encode()).hexdigest()}
        else:
            require(p.is_file() and stat.S_ISREG(mode),'nonregular input member')
            row={'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        files[name]=row
    diagnostic_phase('entrypoint_verify')
    entry='offline_build.py' if phase=='build' else 'validation/run_pipeline.py'
    require(entry in files and 'engine_gate.py' in files,'phase entrypoint missing')
    index={'schema':1,'protocol':PROTOCOL,'application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35',
        'python':'/opt/urp-python/bin/python3.12','files':files,
        'commands':{phase:['/opt/urp-python/bin/python3.12','-I','-B','/inputs/'+entry]}}
    diagnostic_phase('index_write')
    with destination.open('x') as f:json.dump(index,f,sort_keys=True,indent=2);f.write('\n')
    diagnostic_phase('index_measure')
    return hashlib.sha256(destination.read_bytes()).hexdigest()
if __name__=='__main__':
    try:
        p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--phase',choices=['build','validate'],required=True)
        a=p.parse_args();print(freeze(a.root,a.phase))
    except Exception as error:
        emit_diagnostic(error)
        raise
