"""Bind the authenticated bootstrap transaction to the later full signed closure."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


# Failure-only finite projection. No exception string or external value is emitted.
DIAGNOSTIC_STAGE = 'closure_verify'
DIAGNOSTIC_SUBSTAGES = frozenset(['entry', 'receipt_read', 'receipt_schema', 'roots_read', 'transaction_binding', 'system_ca', 'evidence_snapshot', 'receipt_write'])
DIAGNOSTIC_MESSAGES = {'BOOTSTRAP_EVIDENCE_ALIAS': 'BOOTSTRAP_EVIDENCE_ALIAS', 'BOOTSTRAP_EVIDENCE_SPECIAL': 'BOOTSTRAP_EVIDENCE_SPECIAL', 'BOOTSTRAP_PACKAGE_OUTSIDE_CLOSURE': 'BOOTSTRAP_PACKAGE_OUTSIDE_CLOSURE', 'BOOTSTRAP_PROVIDER_CHANGED': 'BOOTSTRAP_PROVIDER_CHANGED', 'BOOTSTRAP_RECEIPT': 'BOOTSTRAP_RECEIPT', 'BOOTSTRAP_ROOTS': 'BOOTSTRAP_ROOTS', 'BOOTSTRAP_ROOT_DUPLICATE': 'BOOTSTRAP_ROOT_DUPLICATE', 'BOOTSTRAP_ROOT_OUTSIDE_CLOSURE': 'BOOTSTRAP_ROOT_OUTSIDE_CLOSURE', 'BOOTSTRAP_ROOT_SCHEMA': 'BOOTSTRAP_ROOT_SCHEMA', 'BOOTSTRAP_ROOT_VERSION_CHANGED': 'BOOTSTRAP_ROOT_VERSION_CHANGED', 'BOOTSTRAP_SCOPE': 'BOOTSTRAP_SCOPE', 'BOOTSTRAP_TRANSACTION_ACTION': 'BOOTSTRAP_TRANSACTION_ACTION', 'BOOTSTRAP_TRANSACTION_SCHEMA': 'BOOTSTRAP_TRANSACTION_SCHEMA', 'DUPLICATE_PACKAGE': 'DUPLICATE_PACKAGE', 'FULL_CLOSURE_REQUIRED': 'FULL_CLOSURE_REQUIRED', 'SNAPSHOT_BINDING': 'SNAPSHOT_BINDING'}
DIAGNOSTIC_PREFIXES = {}
DIAGNOSTIC_CLASSES = frozenset(('SystemCAFailure',) + ('RuntimeError', 'CalledProcessError', 'FileNotFoundError', 'PermissionError', 'FileExistsError', 'OSError', 'ValueError', 'TypeError', 'KeyError', 'JSONDecodeError', 'UnicodeDecodeError', 'ImportError', 'ModuleNotFoundError', 'AttributeError', 'TimeoutError', 'OtherError'))
_DIAGNOSTIC_SUBSTAGE = 'entry'
_DIAGNOSTIC_CA_FAILURE_TYPE = None
DIAGNOSTIC_CA_CODES = {71: 'SYSTEM_CA_LAYOUT', 72: 'SYSTEM_CA_BUNDLE', 73: 'SYSTEM_CA_USABLE', 74: 'SYSTEM_CA_DEFAULT_PATH', 75: 'SYSTEM_CA_ALIAS_CONFLICT', 76: 'SYSTEM_CA_ALIAS_WRITE', 77: 'SYSTEM_CA_RECORD'}

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
    if _DIAGNOSTIC_CA_FAILURE_TYPE is not None and type(error) is _DIAGNOSTIC_CA_FAILURE_TYPE:
        if type(error.code) is int and error.code in DIAGNOSTIC_CA_CODES:
            code = DIAGNOSTIC_CA_CODES[error.code]
            name = 'SystemCAFailure'
    return {'schema': 1, 'stage': DIAGNOSTIC_STAGE,
            'substage': _DIAGNOSTIC_SUBSTAGE if _DIAGNOSTIC_SUBSTAGE in DIAGNOSTIC_SUBSTAGES else 'entry',
            'code': code, 'exception_class': name if name in DIAGNOSTIC_CLASSES else 'OtherError'}

def emit_diagnostic(error):
    try:
        print('SAFE_PREPARATION_DIAGNOSTIC:' + json.dumps(diagnostic_detail(error), sort_keys=True, separators=(',', ':')), file=sys.stderr)
    except Exception:
        pass  # Diagnostic failure never replaces the original unsuccessful outcome.


def require(value,code):
    if not value:raise RuntimeError(code)

def verify_system_ca():
    global _DIAGNOSTIC_CA_FAILURE_TYPE
    # Exact source-indexed sibling; -I remains enabled and no search path changes.
    path=Path(__file__).parent/'bootstrap'/'provision_system_ca.py'
    spec=importlib.util.spec_from_file_location('urp_verified_system_ca',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    _DIAGNOSTIC_CA_FAILURE_TYPE = module.Failure
    return module.verify()

def verify(bootstrap,packages):
    diagnostic_phase('receipt_read')
    receipt=json.loads((bootstrap/'bootstrap.json').read_bytes())
    diagnostic_phase('receipt_schema')
    require(receipt['status']=='bootstrap-installed-transaction-verified' and receipt['transaction_authenticated'] is True,'BOOTSTRAP_RECEIPT')
    require(receipt['snapshot']=='20261021T000000Z' and receipt['full_closure_verified'] is False,'BOOTSTRAP_SCOPE')
    require(packages['installed'] is True and packages['signed_metadata_verified'] is True and packages['closure_complete'] is True,'FULL_CLOSURE_REQUIRED')
    require(packages['snapshot']=={'kind':'immutable-signed-snapshot','identifier':'20261021T000000Z'},'SNAPSHOT_BINDING')
    resolved={}
    for r in packages['resolved']:
        require(r['name'] not in resolved,'DUPLICATE_PACKAGE');resolved[r['name']]=r
    diagnostic_phase('roots_read')
    root_rows=[line.split('\t') for line in (bootstrap/'roots.tsv').read_text().splitlines()]
    require(all(len(row)==2 for row in root_rows),'BOOTSTRAP_ROOT_SCHEMA')
    roots={row[0] for row in root_rows}
    require(len(root_rows)==len(roots),'BOOTSTRAP_ROOT_DUPLICATE')
    require(roots=={'python3','python3-apt','ca-certificates'},'BOOTSTRAP_ROOTS')
    selected=set(roots)
    diagnostic_phase('transaction_binding')
    transaction=[]
    for line in (bootstrap/'transaction.tsv').read_text().splitlines():
        row=line.split('\t');require(len(row)==10,'BOOTSTRAP_TRANSACTION_SCHEMA')
        name,arch,version,old,action,filename,size,digest,suite,component=row
        require(name not in transaction and action in ('install','upgrade'),'BOOTSTRAP_TRANSACTION_ACTION')
        transaction.append(name);selected.add(name)
        require(name in resolved,'BOOTSTRAP_PACKAGE_OUTSIDE_CLOSURE')
        target=resolved[name]
        # The snapshot remains identical: no silently changed bootstrap provider.
        require(target['architecture']==arch and target['version']==version and target['archive_filename']==filename and target['archive_sha256']==digest and target['archive_size']==int(size),'BOOTSTRAP_PROVIDER_CHANGED')
    require(selected.issubset(resolved),'BOOTSTRAP_ROOT_OUTSIDE_CLOSURE')
    require(all(resolved[name]['version']==version for name,version in root_rows),'BOOTSTRAP_ROOT_VERSION_CHANGED')
    diagnostic_phase('system_ca')
    system_ca=verify_system_ca()
    diagnostic_phase('evidence_snapshot')
    rows={}
    for p in sorted(bootstrap.rglob('*')):
        require(not p.is_symlink(),'BOOTSTRAP_EVIDENCE_ALIAS')
        if p.is_file():rows[p.relative_to(bootstrap).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
        else:require(p.is_dir(),'BOOTSTRAP_EVIDENCE_SPECIAL')
    return {'schema':1,'status':'bootstrap-transaction-bound-to-full-closure','bootstrap_packages':sorted(selected),'bootstrap_evidence':rows,'derived_system_ca':system_ca,'compiler_build_executed':False}

def main():
    bootstrap=Path('/opt/urp-bootstrap-evidence');root=Path('/inputs')
    result=verify(bootstrap,json.loads((root/'package-preparation.json').read_bytes()))
    diagnostic_phase('receipt_write')
    with (root/'bootstrap-closure.json').open('x') as f:json.dump(result,f,sort_keys=True,indent=2);f.write('\n')
if __name__=='__main__':
    try:
        main()
    except Exception as error:
        emit_diagnostic(error)
        raise
