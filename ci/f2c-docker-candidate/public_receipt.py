"""Closed-vocabulary public outcome. Never emits raw errors, paths or native logs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import importlib.util
_spec=importlib.util.spec_from_file_location("preparation_diagnostic",Path(__file__).resolve().with_name("preparation_diagnostic.py"))
_diagnostic=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_diagnostic)
_buildkit_spec=importlib.util.spec_from_file_location("buildkit_diagnostic",Path(__file__).resolve().with_name("buildkit_diagnostic.py"))
_buildkit=importlib.util.module_from_spec(_buildkit_spec);_buildkit_spec.loader.exec_module(_buildkit)

_census_spec=importlib.util.spec_from_file_location("buildkit_census",Path(__file__).resolve().with_name("buildkit_census.py"))
_census=importlib.util.module_from_spec(_census_spec);_census_spec.loader.exec_module(_census)

PHASES=('bootstrap','preparation','isolation_and_build')
DIGEST=re.compile('[0-9a-f]{64}')
CATEGORIES={'bootstrap':'BOOTSTRAP_FAILED','preparation':'TRUSTED_PREPARATION_FAILED','isolation_and_build':'ISOLATED_BUILD_OR_PROTOCOL_FAILED'}

def read(path):
    if not path.exists():return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:raise ValueError('receipt shape')
    return json.loads(path.read_text())

def digest_field(value):
    return value if type(value) is str and DIGEST.fullmatch(value) else None

def project(state):
    terminal=read(state/'terminal.json') or {}
    phase=terminal.get('phase');code=terminal.get('exit_code')
    if phase not in PHASES or type(code) is not int or not 0<=code<=255:raise ValueError('terminal shape')
    preparation_detail=_diagnostic.read_projected(state/'preparation-stderr.log') if phase=='preparation' and code else None
    buildkit_detail=None
    buildkit_structure=None
    buildkit_census=None
    if preparation_detail is not None and preparation_detail['stage']=='image_build':
        args=_buildkit.read_build_args(state/'prepared/buildkit-args.json')
        args=args if args is not None else {}
        buildkit_detail=_buildkit.validate_projection(_buildkit.parse_log(state/'prepared/preparation.log', build_args=args))
        buildkit_structure=_buildkit.validate_structure(_buildkit.inspect_structure(state/'prepared/preparation.log', build_args=args))
        buildkit_census=_census.validate_census(_census.inspect_census(state/'prepared/preparation.log', build_args=args))
    reached=PHASES.index(phase)
    outcomes=[{'phase':p,'status':('passed' if i<reached or (i==reached and code==0) else 'failed' if i==reached else 'not_run'),'exit_code':(0 if i<reached else code if i==reached else None)} for i,p in enumerate(PHASES)]
    prepared=read(state/'prepared/prepared-image.json') or {}
    result=read(state/'build-evidence/result.json') or {}
    source_hash=digest_field(prepared.get('source_index_sha256'))
    supplied=state/'reviewed-source.sha256'
    if source_hash is None and supplied.is_file() and not supplied.is_symlink() and supplied.stat().st_size<128:source_hash=digest_field(supplied.read_text().strip())
    built=(phase=='isolation_and_build' and code==0 and type(result.get('exit_code')) is int and result['exit_code']==0
           and source_hash is not None and digest_field(prepared.get('input_index_sha256')) is not None
           and digest_field(result.get('output_tar_sha256')) is not None)
    return {'schema':1,'protocol':'urp.f2c.docker-engine-netnone/1','application_commit':'3acf840d1a0c16ab1cfcca15744e4ea6609ddb35','python':'3.12.13',
        'outcome':'built_not_accepted' if built else 'failed_or_not_run','stages':outcomes,
        'preparation_failure_detail':preparation_detail,
        'buildkit_failure_detail':buildkit_detail,
        'buildkit_structure':buildkit_structure,
        'buildkit_census':buildkit_census,
        'failure_category':None if built else CATEGORIES[phase] if code else 'RESULT_RECEIPT_MISSING',
        'source_index_sha256':source_hash,
        'input_index_sha256':digest_field(prepared.get('input_index_sha256')),
        'output_tar_sha256':digest_field(result.get('output_tar_sha256')),
        'raw_logs_durably_archived':False,'fresh_runtime_acceptance':'not_run','current_application_full_suite':'not_run'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,required=True);a=p.parse_args()
    result=project(a.state)
    print(json.dumps(result,sort_keys=True))
    if result['outcome'] != 'built_not_accepted':raise SystemExit(1)
if __name__=='__main__':main()
