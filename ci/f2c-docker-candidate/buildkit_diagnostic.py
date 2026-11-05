"""Bounded, diagnostic-only projection of private BuildKit plain-progress logs.

An exact vertex header scopes an allowlisted marker, but log text is not an
independent authentication channel. Nothing returned here proves the emitting
process identity, successful preparation, package provenance, or native
acceptance. Never use this result to change a gate or retry policy.
"""
import json
import importlib.util
import os
from pathlib import Path
import re
import stat

# Pure, source-indexed schema shared with the diagnostic producer; no path search.
_reference_spec = importlib.util.spec_from_file_location(
    'urp_package_reference_projection', Path(__file__).resolve().with_name('package_reference.py'))
_reference = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(_reference)
_stage_spec = importlib.util.spec_from_file_location(
    'urp_preparation_stage_projection', Path(__file__).resolve().with_name('preparation_stage_diagnostic.py'))
_stage = importlib.util.module_from_spec(_stage_spec)
_stage_spec.loader.exec_module(_stage)


MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_MARKER_BYTES = 1024
MAX_VERTICES = 4096

# Reviewed Dockerfile RUN commands, with continuation whitespace normalized.
# Do not derive these from log text or load executable preparation code.
RUN_COMMANDS = {
    'bootstrap': '/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent /bin/sh /usr/local/lib/urp-bootstrap/bootstrap.sh --install-bootstrap',
    'package': '/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C HOME=/nonexistent /usr/bin/python3 -I -B /opt/urp-control/preparation/prepare_packages.py --root /inputs --install',
    'closure_verify': '/usr/bin/python3 -I -B /opt/urp-control/preparation/verify_bootstrap_closure.py',
    'python_provision': '/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C LANG=C HOME=/root /usr/bin/python3 -I -B /opt/urp-control/preparation/bootstrap_tools.py install --lock /opt/urp-bootstrap-seed/bootstrap-lock.json --expected-sha256 "$BOOTSTRAP_SHA"',
    'input_ownership': '{ test "$WORKLOAD_UID" -gt 0 || { rc=$?; printf \'%s\\n\' \'SAFE_PREPARATION_DIAGNOSTIC:{"schema":1,"stage":"input_ownership","substage":"uid_check","code":"UID_CHECK_FAILED","exception_class":"ShellExit"}\' >&2 || :; exit "$rc"; }; } && { test "$WORKLOAD_GID" -gt 0 || { rc=$?; printf \'%s\\n\' \'SAFE_PREPARATION_DIAGNOSTIC:{"schema":1,"stage":"input_ownership","substage":"gid_check","code":"GID_CHECK_FAILED","exception_class":"ShellExit"}\' >&2 || :; exit "$rc"; }; } && { chown -R --no-dereference "$WORKLOAD_UID:$WORKLOAD_GID" /inputs || { rc=$?; printf \'%s\\n\' \'SAFE_PREPARATION_DIAGNOSTIC:{"schema":1,"stage":"input_ownership","substage":"ownership_change","code":"OWNERSHIP_COMMAND_FAILED","exception_class":"ShellExit"}\' >&2 || :; exit "$rc"; }; }',
    'inputs_prepare': '/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C.UTF-8 LANG=C.UTF-8 HOME=/inputs /opt/urp-python/bin/python3.12 -I -B -S /inputs/recipe/prepare_inputs.py --root /inputs --python /opt/urp-python/bin/python3.12 --package-lock /inputs/package-preparation.json --expected-source-index-sha256 "$INPUT_SOURCE_SHA"',
    'inputs_freeze': '/opt/urp-python/bin/python3.12 -I -B -S /inputs/freeze_input_index.py --root /inputs --phase build',

}
BUILD_ARG_KEYS = frozenset({'WORKLOAD_UID', 'WORKLOAD_GID', 'INPUT_SOURCE_SHA', 'BOOTSTRAP_SHA'})
_BUILD_ARG_TOKEN = re.compile(r'\$(WORKLOAD_UID|WORKLOAD_GID|INPUT_SOURCE_SHA|BOOTSTRAP_SHA)\b')


def validate_build_args(value):
    """Validate separately recorded actual inputs; never learn values from logs."""
    if type(value) is not dict or set(value) != BUILD_ARG_KEYS:
        return None
    for name in ('WORKLOAD_UID', 'WORKLOAD_GID'):
        if type(value[name]) is not int or not 1 <= value[name] <= 4294967294:
            return None
    for name in ('INPUT_SOURCE_SHA', 'BOOTSTRAP_SHA'):
        if type(value[name]) is not str or re.fullmatch('[0-9a-f]{64}', value[name]) is None:
            return None
    return dict(value)



def read_build_args(path):
    """Read one owned bounded binding file without exposing its values."""
    fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
              'st_size', 'st_mtime_ns', 'st_ctime_ns')
    def identity(value):
        return tuple(getattr(value, field) for field in fields)
    try:
        path = Path(path)
        if any(item.is_symlink() for item in (path, *path.parents)):
            return None
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4096:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if identity(before) != identity(opened):
                return None
            raw = stream.read(4097)
            after = os.fstat(stream.fileno())
            current = path.lstat()
            if (len(raw) != opened.st_size or len(raw) > 4096 or
                    identity(opened) != identity(after) or identity(after) != identity(current) or
                    any(item.is_symlink() for item in (path, *path.parents))):
                return None
        return validate_build_args(json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object))
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        return None


def render_run_commands(build_args=None):
    """Render only known tokens in reviewed templates, not arbitrary shell text.

    BuildKit's display-name ProcessWord expands known ARGs while retaining quotes,
    escapes and unknown variables. These fixed templates use the four known ARGs
    only as unescaped double-quoted references; $rc and $? remain unknown. This is
    intentionally not a general shell renderer. None retains the legacy literal
    mode; production callers must pass actual validated inputs, or {} to refuse
    variable-containing bindings. Invalid inputs permit only immutable commands
    without known ARG references, never variable-containing literal fallback.
    """
    if build_args is None:
        return dict(RUN_COMMANDS)
    checked = validate_build_args(build_args)
    if checked is None:
        return {stage: command for stage, command in RUN_COMMANDS.items()
                if _BUILD_ARG_TOKEN.search(command) is None}
    return {stage: _BUILD_ARG_TOKEN.sub(lambda m: str(checked[m[1]]), command)
            for stage, command in RUN_COMMANDS.items()}


BOOTSTRAP_PREFIX = 'SAFE_BOOTSTRAP_DIAGNOSTIC:'
PACKAGE_PREFIX = 'SAFE_PACKAGE_DIAGNOSTIC:'
BOOTSTRAP_PHASES = frozenset({
    'prerequisites', 'public_ca_seed', 'signed_metadata', 'apt_metadata_check',
    'signed_root_selection', 'transaction_plan', 'archive_authentication',
    'install_bootstrap', 'post_install_verification', 'receipt',
})
BOOTSTRAP_SUBSTEPS = {
    'prerequisites': ('apt_configuration', 'apt_version', 'base_audit', 'base_inventory', 'base_keyring', 'base_tool_hashes', 'entry', 'evidence_workspace', 'helper_presence', 'platform_identity', 'tool_presence'),
    'public_ca_seed': ('archive_bytes', 'archive_decode', 'base_unchanged', 'bundle_hash', 'certificate_bundle', 'certificate_data', 'certificate_pem', 'entry', 'index_bytes', 'index_decode', 'index_identity', 'member_listing', 'member_selection', 'package_identity', 'package_parse', 'release_bytes', 'release_parse', 'release_signature', 'release_signer', 'seed_copy'),
    'signed_metadata': ('entry', 'index_decode', 'index_fetch', 'index_parse', 'release_fetch', 'release_parse', 'release_signature', 'release_signer', 'source_registration'),
    'apt_metadata_check': ('apt_update', 'base_unchanged', 'configuration_dump', 'entry'),
    'signed_root_selection': ('candidate_parse', 'candidate_policy', 'entry', 'root_record', 'signed_identity'),
    'transaction_plan': ('action_checks', 'archive_names', 'archive_names_match', 'archive_names_parse', 'entry', 'initial_simulation', 'installed_identity', 'pinned_simulation', 'signed_identity', 'simulation_compare', 'simulation_parse', 'transaction_record'),
    'archive_authentication': ('archive_control', 'archive_fetch', 'cache_copy', 'cache_hash', 'cache_name', 'control_identity', 'entry'),
    'install_bootstrap': ('base_unchanged', 'entry', 'offline_compare', 'offline_parse', 'offline_simulation', 'package_install'),
    'post_install_verification': ('apt_check', 'apt_version_check', 'apt_version_mismatch', 'audit_clean', 'ca_file_missing', 'ca_path_check', 'dpkg_audit', 'entry', 'final_snapshot', 'final_unchanged', 'python_execution', 'python_import_apt_pkg', 'python_import_ssl', 'python_import_sys', 'python_prerequisites', 'python_version_check', 'python_version_mismatch', 'root_versions', 'status_parse', 'status_snapshot', 'system_ca_alias_conflict', 'system_ca_alias_write', 'system_ca_bundle', 'system_ca_default_path', 'system_ca_defaults', 'system_ca_execution', 'system_ca_layout', 'system_ca_record', 'system_ca_unknown', 'system_ca_usable', 'timestamp', 'transaction_delta'),
    'receipt': ('entry', 'receipt_inventory', 'receipt_write'),
}
BOOTSTRAP_FAILURES = frozenset({'EXPLICIT_CONTAINER_INSTALL_MODE_REQUIRED'} | {
    'BLOCKED_' + phase for phase in BOOTSTRAP_PHASES
} | {'BLOCKED_' + phase + '__' + substep
     for phase, substeps in BOOTSTRAP_SUBSTEPS.items() for substep in substeps})
BOOTSTRAP_SUCCESS = 'BOOTSTRAP_TRANSACTION_VERIFIED_FULL_CLOSURE_PENDING'
PACKAGE_CODES = frozenset({
    'APT_HTTP_ERROR', 'APT_TLS_ERROR', 'APT_NETWORK_ERROR', 'APT_TIMEOUT',
    'APT_PREREQUISITE_FAILED', 'APT_SUBPROCESS_FAILED', 'APT_METADATA_PARSE_ERROR',
    'APT_FILE_ACCESS_ERROR', 'APT_INVARIANT_REJECTED', 'APT_UNEXPECTED_ERROR',
    'APT_PROVIDER_COEXISTENCE_REJECTED', 'APT_PROVIDER_VERSION_UNAUTHENTICATED',
    'APT_PROVIDER_CAPABILITY_MISSING', 'APT_PROVIDER_OWNER_MISMATCH',
    'APT_PROVIDER_EXECUTABLE_MISSING', 'APT_CLOSURE_PACKAGE_UNAVAILABLE',
    'APT_CLOSURE_DOWNGRADE_REFUSED', 'APT_CLOSURE_PIN_UNRESOLVED',
    'APT_CLOSURE_NO_PROGRESS',
})
PACKAGE_OPERATIONS = frozenset({
    'bootstrap', 'host-check', 'prerequisites', 'base-inventory', 'release-download',
    'signature-verify', 'release-parse', 'index-download', 'index-parse',
    'provider-selection', 'apt-config', 'apt-update', 'native-solver', 'solver-result',
    'closure-provenance', 'archive-download', 'archive-control', 'transaction-plan',
    'transaction-simulate', 'installation', 'post-install-check',
    'post-status-read', 'post-inventory-parse', 'post-inventory-delta', 'post-closure-verify', 'post-inventory-write', 'post-apt-check', 'post-delta-match', 'post-delta-missing', 'post-delta-unexpected', 'post-delta-version', 'post-delta-held', 'post-delta-other',
    'provider-verification', 'preparation-receipt',
})
PACKAGE_CLASSES = frozenset({
    'RuntimeError', 'CalledProcessError', 'ProcessFailure', 'HTTPError', 'URLError',
    'SSLError', 'TimeoutError', 'FileNotFoundError', 'PermissionError',
    'FileExistsError', 'OSError', 'UnicodeDecodeError', 'LZMAError', 'KeyError',
    'ValueError', 'ModuleNotFoundError', 'ImportError', 'AssertionError', 'TypeError',
    'JSONDecodeError', 'AttributeError', 'AptPkgError', 'OtherError',
    'ProviderSelectionError', 'ClosureProvenanceError',
})
PACKAGE_ERRNOS = frozenset({-3, -2, 1, 2, 5, 11, 13, 17, 22, 28, 30, 32,
                           36, 40, 95, 101, 104, 110, 111, 113})
PACKAGE_SUITES = frozenset({'jammy', 'jammy-updates', 'jammy-security'})
PACKAGE_COMPONENTS = frozenset({'main', 'universe'})
PACKAGE_KEYS = frozenset({'schema', 'code', 'operation', 'exception_class',
                          'http_status', 'errno', 'returncode', 'suite', 'component'})

# Only anchored BuildKit records qualify. In particular, a process printing a
# nested '#N [...] RUN ...' behind '#M elapsed ' cannot establish a vertex.
_HEADER = re.compile(rb'#([1-9][0-9]{0,5}) \[([\x20-\x7e]{1,128})\] ([\x20-\x7e]{1,4096})')
_STEP = re.compile(rb' *([1-9][0-9]{0,3})/([1-9][0-9]{0,3})')
_OUTPUT = re.compile(rb'#([1-9][0-9]{0,5}) ([0-9]{1,6}\.[0-9]{1,3}) (.*)')


def _enum(value, choices):
    return type(value) is str and value in choices


def _package(value):
    if type(value) is not dict or set(value) != PACKAGE_KEYS:
        return None
    if type(value['schema']) is not int or value['schema'] != 1:
        return None
    for key, choices in (('code', PACKAGE_CODES), ('operation', PACKAGE_OPERATIONS),
                         ('exception_class', PACKAGE_CLASSES)):
        if not _enum(value[key], choices):
            return None
    for key, choices in (('suite', PACKAGE_SUITES), ('component', PACKAGE_COMPONENTS)):
        if value[key] is not None and not _enum(value[key], choices):
            return None
    for key, lower, upper in (('http_status', 100, 599), ('returncode', -255, 255)):
        number = value[key]
        if number is not None and (type(number) is not int or not lower <= number <= upper):
            return None
    number = value['errno']
    if number is not None and (type(number) is not int or number not in PACKAGE_ERRNOS):
        return None
    return dict(value)


def validate_projection(value):
    """Return a fresh closed projection or None; never echo an invalid field."""
    fields = {'schema', 'source', 'diagnostic_only', 'detail'}
    if type(value) is not dict or set(value) not in (fields, fields | {'reference'}):
        return None
    if type(value['schema']) is not int or value['schema'] != 1 or value['diagnostic_only'] is not True:
        return None
    source = value['source']
    if not _enum(source, RUN_COMMANDS):
        return None
    detail = value['detail']
    if source == 'bootstrap':
        if type(detail) is not dict or set(detail) != {'code'} or not _enum(detail['code'], BOOTSTRAP_FAILURES):
            return None
        detail = dict(detail)
    elif source == 'package':
        detail = _package(detail)
        if detail is None:
            return None
    else:
        detail = _stage.validate(detail)
        if detail is None or detail['stage'] != source:
            return None
    result = {'schema': 1, 'source': source, 'diagnostic_only': True, 'detail': detail}
    if 'reference' in value:
        if not _reference_failure(source, detail):
            return None
        reference = _reference.validate_reference(value['reference'])
        if reference is None:
            return None
        result['reference'] = reference
    return result


def _reference_failure(source, detail):
    return (source == 'package' and detail.get('code') == 'APT_INVARIANT_REJECTED'
            and detail.get('operation') == 'post-delta-version'
            and detail.get('exception_class') == 'RuntimeError'
            and all(detail.get(key) is None for key in
                    ('http_status', 'errno', 'returncode', 'suite', 'component')))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate key')
        value[key] = item
    return value


def _parse_lines(stream, build_args=None):
    commands = render_run_commands(build_args)
    if commands is None:
        return None
    headers, sources, markers = {}, {}, set()
    header_bytes = {}
    failure = None
    pending_reference = None
    reference_seen = False
    total = 0
    while True:
        raw = stream.readline(MAX_LINE_BYTES + 1)
        if not raw:
            break
        total += len(raw)
        if total > MAX_LOG_BYTES or len(raw) > MAX_LINE_BYTES:
            return None
        terminated = raw.endswith(b'\n')
        line = raw[:-1] if terminated else raw
        match = _HEADER.fullmatch(line)
        if match:
            vertex, step, command = match.groups()
            source = None
            positions = _STEP.fullmatch(step)
            if positions and 1 <= int(positions[1]) <= int(positions[2]) <= MAX_VERTICES:
                # Only ASCII spaces are normalized; no shell parsing/evaluation.
                normalized = ' '.join(word for word in command.decode('ascii').split(' ') if word)
                for candidate, expected in commands.items():
                    if normalized == 'RUN ' + expected:
                        source = candidate
                        break
            if vertex in headers:
                # Plain BuildKit reprints a vertex title when switching back to
                # it. Only the exact original bytes may repeat; changed titles
                # and unterminated copies remain conflicts.
                if not terminated or header_bytes[vertex] != (step, command):
                    return None
                continue
            if source is not None:
                if source in sources or not terminated:
                    return None
                sources[source] = vertex
            headers[vertex] = source
            header_bytes[vertex] = (step, command)
            if len(headers) > MAX_VERTICES:
                return None
            continue
        match = _OUTPUT.fullmatch(line)
        if not match:
            continue
        vertex, _elapsed, payload = match.groups()
        source = headers.get(vertex)
        if source is None:
            continue
        if payload.startswith(_reference.PREFIX.encode('ascii')):
            if (source != 'package' or reference_seen or failure is not None or
                    not terminated or len(payload) > len(_reference.PREFIX) + _reference.MAX_REFERENCE_BYTES or
                    any(byte < 32 or byte > 126 for byte in payload)):
                return None
            try:
                raw_reference = json.loads(payload[len(_reference.PREFIX):], object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError, RecursionError):
                return None
            projected_reference = _reference.validate_reference(raw_reference)
            if projected_reference is None:
                return None
            pending_reference = (vertex, projected_reference)
            reference_seen = True
            continue
        if pending_reference is not None and pending_reference[0] == vertex:
            # Unrelated vertices may interleave, but the same vertex must emit
            # its one unchanged failure marker immediately after the reference.
            if not payload.startswith(PACKAGE_PREFIX.encode('ascii')):
                return None
        if not payload.startswith((BOOTSTRAP_PREFIX.encode('ascii'), PACKAGE_PREFIX.encode('ascii'), _stage.PREFIX.encode('ascii'))):
            continue
        if (not terminated or len(payload) > MAX_MARKER_BYTES or
                any(byte < 32 or byte > 126 for byte in payload)):
            return None
        if source == 'bootstrap':
            if not payload.startswith(BOOTSTRAP_PREFIX.encode('ascii')):
                return None
            code = payload[len(BOOTSTRAP_PREFIX):].decode('ascii')
            if code == BOOTSTRAP_SUCCESS:
                continue
            detail = {'code': code}
        else:
            prefix = PACKAGE_PREFIX if source == 'package' else _stage.PREFIX
            if not payload.startswith(prefix.encode('ascii')):
                return None
            try:
                detail = json.loads(payload[len(prefix):], object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError, RecursionError):
                return None
        if source in markers:
            return None
        markers.add(source)
        envelope = {'schema': 1, 'source': source, 'diagnostic_only': True, 'detail': detail}
        if pending_reference is not None:
            if pending_reference[0] != vertex:
                return None
            envelope['reference'] = pending_reference[1]
            pending_reference = None
        projected = validate_projection(envelope)
        if projected is None or failure is not None:
            return None
        failure = projected
    if pending_reference is not None:
        return None
    return failure


def parse_log(path, build_args=None):
    """Project one static, bounded regular file; absent/invalid evidence is None.

    Open without following a final symlink or blocking on a special file. Reject
    replacement or modification during parsing. Neither bytes nor paths escape
    into the result. Ordinary unrecognized log lines are never re-published.
    """
    try:
        path = Path(path)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_LOG_BYTES:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_LOG_BYTES or
                    (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                return None
            result = _parse_lines(stream) if build_args is None else _parse_lines(stream, build_args)
            after = os.fstat(stream.fileno())
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                return None
        return result
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return None

# Structural observability is separate from the unchanged safe-marker schema.
BASE_IMAGE = 'docker.io/library/ubuntu@sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04'
STATIC_STAGES = ('base_metadata', 'base_pull', 'bootstrap', 'package',
                 'closure_verify', 'python_provision', 'input_ownership',
                 'inputs_prepare', 'inputs_freeze')
LOG_STATES = frozenset({'read', 'empty', 'missing', 'unreadable', 'unsupported_file',
                        'oversized', 'invalid', 'changed'})
_ERROR = re.compile(rb'#([1-9][0-9]{0,5}) ERROR:')


def _structure(state, size=None, capped=False, frames=False, stages=(), failed='unknown'):
    return {'schema': 1, 'diagnostic_only': True, 'log_state': state,
            'log_bytes': size, 'size_capped': capped,
            'buildkit_frames_seen': frames, 'stages_reached': list(stages),
            'failed_stage': failed}


def validate_structure(value):
    if type(value) is not dict or set(value) != {
            'schema', 'diagnostic_only', 'log_state', 'log_bytes', 'size_capped',
            'buildkit_frames_seen', 'stages_reached', 'failed_stage'}:
        return None
    if type(value['schema']) is not int or value['schema'] != 1 or value['diagnostic_only'] is not True:
        return None
    if not _enum(value['log_state'], LOG_STATES):
        return None
    size=value['log_bytes']
    if size is not None and (type(size) is not int or not 0 <= size <= MAX_LOG_BYTES+1):
        return None
    if type(value['size_capped']) is not bool or type(value['buildkit_frames_seen']) is not bool:
        return None
    if value['size_capped'] != (size == MAX_LOG_BYTES+1):
        return None
    state=value['log_state']
    if state == 'read' and (size is None or not 1 <= size <= MAX_LOG_BYTES):
        return None
    if state == 'empty' and size != 0:
        return None
    if state == 'oversized' and size != MAX_LOG_BYTES+1:
        return None
    if state == 'invalid' and (size is None or size > MAX_LOG_BYTES):
        return None
    if state in {'missing','unreadable','unsupported_file','changed'} and size is not None:
        return None
    if state != 'read' and (value['buildkit_frames_seen'] or value['stages_reached'] or value['failed_stage'] != 'unknown'):
        return None
    stages=value['stages_reached']
    if type(stages) is not list or any(not _enum(stage,STATIC_STAGES) for stage in stages):
        return None
    if stages != [stage for stage in STATIC_STAGES if stage in stages]:
        return None
    if not _enum(value['failed_stage'], (*STATIC_STAGES,'unknown')):
        return None
    if value['failed_stage'] != 'unknown' and value['failed_stage'] not in stages:
        return None
    if (stages or value['failed_stage'] != 'unknown') and not value['buildkit_frames_seen']:
        return None
    return _structure(value['log_state'],size,value['size_capped'],value['buildkit_frames_seen'],stages,value['failed_stage'])


def _static_header(step, command, build_args=None):
    if step == b'internal' and command == ('load metadata for '+BASE_IMAGE).encode('ascii'):
        return 'base_metadata'
    positions=_STEP.fullmatch(step)
    if not positions or not 1 <= int(positions[1]) <= int(positions[2]) <= MAX_VERTICES:
        return None
    normalized=' '.join(word for word in command.decode('ascii').split(' ') if word)
    if normalized == 'FROM '+BASE_IMAGE:
        return 'base_pull'
    commands = render_run_commands(build_args)
    if commands is None:
        return None
    for stage, expected in commands.items():
        if normalized == 'RUN '+expected:
            return stage
    return None


def _inspect_lines(stream, size, build_args=None):
    headers={}; header_bytes={}; sources={}; reached=set(); failures=[]; frames=False; ambiguous=False; total=0
    while True:
        raw=stream.readline(MAX_LINE_BYTES+1)
        if not raw:break
        total+=len(raw)
        if total>MAX_LOG_BYTES or len(raw)>MAX_LINE_BYTES:
            return _structure('invalid',size)
        # A truncated final fragment cannot establish an event/source.
        if not raw.endswith(b'\n'):continue
        line=raw[:-1]
        match=_HEADER.fullmatch(line)
        if match:
            frames=True;vertex,step,command=match.groups()
            stage=_static_header(step,command,build_args)
            if stage is not None:reached.add(stage)
            if vertex in headers:
                if header_bytes[vertex] != (step,command):
                    ambiguous=True
                continue
            if stage is not None and stage in sources and sources[stage] != vertex:
                ambiguous=True
            if stage is not None:sources[stage]=vertex
            headers[vertex]=stage
            header_bytes[vertex]=(step,command)
            if stage is not None:reached.add(stage)
            if len(headers)>MAX_VERTICES:return _structure('invalid',size)
            continue
        match=_ERROR.match(line)
        if match:
            # Never decode, classify, retain or publish any ERROR payload.
            vertex=match[1]
            if vertex in headers:frames=True
            failures.append(headers.get(vertex))
            if len(failures)>MAX_VERTICES:return _structure('invalid',size)
            continue
        match=_OUTPUT.fullmatch(line)
        if match and match[1] in headers:frames=True
    failed='unknown'
    if not ambiguous and len(failures)==1 and failures[0] is not None:
        failed=failures[0]
    return _structure('empty' if size==0 else 'read',size,False,frames,
                      [stage for stage in STATIC_STAGES if stage in reached],failed)


def inspect_structure(path, build_args=None):
    """Finite log shape only. No free-text error classification or acceptance."""
    try:
        path=Path(path);before=path.lstat()
        if not stat.S_ISREG(before.st_mode):return _structure('unsupported_file')
        if before.st_size>MAX_LOG_BYTES:return _structure('oversized',MAX_LOG_BYTES+1,True)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as stream:
            opened=os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):return _structure('unsupported_file')
            if opened.st_size>MAX_LOG_BYTES:return _structure('oversized',MAX_LOG_BYTES+1,True)
            if (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):return _structure('changed')
            result=_inspect_lines(stream,opened.st_size) if build_args is None else _inspect_lines(stream,opened.st_size,build_args)
            after=os.fstat(stream.fileno());current=path.lstat()
            if (opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):return _structure('changed')
            if not stat.S_ISREG(current.st_mode) or (current.st_dev,current.st_ino)!=(opened.st_dev,opened.st_ino):return _structure('changed')
        return result
    except FileNotFoundError:return _structure('missing')
    except (OSError,ValueError,TypeError,UnicodeError,OverflowError):return _structure('unreadable')
