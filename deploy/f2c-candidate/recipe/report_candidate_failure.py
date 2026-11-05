#!/usr/bin/env python3
"""Bounded report wrapper, shared by indexed fixtures and actual workflow."""
import hashlib,json,os,pathlib,re,subprocess
details='code=UNKNOWN'
allowed_codes=['APT_ARCHIVE_MISMATCH', 'APT_CLOSURE_DOWNGRADE_REFUSED', 'APT_CLOSURE_NO_PROGRESS', 'APT_CLOSURE_PACKAGE_UNAVAILABLE', 'APT_CLOSURE_PIN_UNRESOLVED', 'APT_FILE_ACCESS_ERROR', 'APT_HOST_MISMATCH', 'APT_HTTP_ERROR', 'APT_INPUT_CHANGED', 'APT_INVARIANT_REJECTED', 'APT_METADATA_PARSE_ERROR', 'APT_NETWORK_ERROR', 'APT_PREREQUISITE_FAILED', 'APT_PROVIDER_CAPABILITY_MISSING', 'APT_PROVIDER_COEXISTENCE_REJECTED', 'APT_PROVIDER_CONFLICT', 'APT_PROVIDER_EXECUTABLE_MISSING', 'APT_PROVIDER_OWNER_MISMATCH', 'APT_PROVIDER_VERSION_UNAUTHENTICATED', 'APT_REMOVAL_REFUSED', 'APT_SIGNATURE_REJECTED', 'APT_SOLVER_BLOCKED', 'APT_SUBPROCESS_FAILED', 'APT_TIMEOUT', 'APT_TLS_ERROR', 'APT_UNEXPECTED_ERROR', 'BUILD_STAGE_FAILED', 'GUARD_INDEX_MISMATCH', 'GUARD_INPUT_MISMATCH', 'GUARD_NAMESPACE_REJECTED', 'GUARD_OUTPUT_EXISTS', 'HELPER_TESTS_FAILED', 'PREPARATION_ARCHIVE_REJECTED', 'PREPARATION_HOST_MISMATCH', 'PREPARATION_INPUT_INCOMPLETE', 'PREPARATION_PROVIDER_REJECTED', 'UNKNOWN', 'VALIDATION_ABI_REJECTED', 'VALIDATION_INPUT_REJECTED', 'VALIDATION_INTEGRITY_FAILED', 'VALIDATION_PROVIDER_REJECTED', 'VALIDATION_TEST_GATE_FAILED']
try:
    root=pathlib.Path(os.environ['CANDIDATE_ROOT'])
    raw=(root/'source-index.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==os.environ['REVIEWED_SOURCE_INDEX_SHA256']
    index=json.loads(raw)
    helper=root/'recipe/project_failure_code.py'
    helper_bytes=helper.read_bytes()
    assert not helper.is_symlink() and hashlib.sha256(helper_bytes).hexdigest()==index['files']['recipe/project_failure_code.py']['sha256']
    verified={'__name__':'verified_failure_projector','__file__':str(helper)}
    exec(compile(helper_bytes,str(helper),'exec'),verified)
    names={'helper_tests':['helper-tests.log'],'packages':['packages.log'],'validation_inputs':['validation-inputs.log'],'measured_lock':['preparation.log'],'offline_build':['offline-build.log'],'native_validation':['validation-run.log','validation-stage.log','validation-freeze.log']}
    stage=os.environ['FAILED_STAGE']
    for name in names.get(stage,[]):
        log=root/'private-workflow-evidence'/name
        if not log.exists(): continue
        projected=verified['project_details'](stage,str(log))
        if projected.get('code') in allowed_codes:
            details='code='+projected['code']
            if set(projected)==verified['PACKAGE_KEYS']:
                checked=verified['validated_package_receipt'](json.dumps(projected))
                if checked is None: raise ValueError('Invalid projection')
                for key in ('operation','exception_class','http_status','errno','returncode','suite','component'):
                    value=checked[key]
                    if value is not None: details+=' '+key+'='+str(value)
                if checked['code']=='APT_CLOSURE_DOWNGRADE_REFUSED':
                    target={}
                    try:
                        canonical_helper=root/'recipe/project_canonical_target.py'
                        canonical_bytes=canonical_helper.read_bytes()
                        assert not canonical_helper.is_symlink() and hashlib.sha256(canonical_bytes).hexdigest()==index['files']['recipe/project_canonical_target.py']['sha256']
                        canonical={'__name__':'verified_canonical_projector','__file__':str(canonical_helper)}
                        exec(compile(canonical_bytes,str(canonical_helper),'exec'),canonical)
                        target=canonical['project'](root,checked,str(log))
                        assert set(target)=={'canonical_package','canonical_version','canonical_architecture','target_snapshot','target_suite','target_component','target_stanza'}
                        assert re.fullmatch('[a-z0-9][a-z0-9+.-]{0,127}',target['canonical_package'])
                        assert re.fullmatch('[0-9][A-Za-z0-9.+:~-]{0,127}',target['canonical_version'])
                        assert target['canonical_architecture'] in ('amd64','all')
                        assert target['target_snapshot']=='20261021T000000Z'
                        assert target['target_suite'] in ('jammy','jammy-updates','jammy-security')
                        assert target['target_component'] in ('main','universe')
                        assert type(target['target_stanza']) is int and 0<=target['target_stanza']<200000
                    except Exception:
                        target={}
                    details+=' target_status='+('VERIFIED' if target else 'UNAVAILABLE')
                    for key in ('canonical_package','canonical_version','canonical_architecture','target_snapshot','target_suite','target_component','target_stanza'):
                        if target: details+=' '+key+'='+str(target[key])
        break
except Exception:
    details='code=UNKNOWN'
print(details)
