#!/usr/bin/env python3
"""Prepare authenticated packages on a live Ubuntu 22.04 amd64 runner only.
Default: native APT resolve + download + verify, never install. --install explicitly
permits the future runner's verified transaction. No launching/publishing occurs.
Requires native /usr/bin/python3 with python3-apt, gpgv, apt-get, dpkg-deb.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import shlex
import ssl
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import urllib.error

SNAPSHOT = '20261021T000000Z'
ORIGIN = 'https://snapshot.ubuntu.com/ubuntu/' + SNAPSHOT + '/'
SIGNER = 'F6ECB3762474EDA9D21B7022871920D1991BC93C'
RELEASE_HASHES = {
    'jammy': 'c14060cd8c6d625874dfcb9523a35a395bf4865c28b6b6a82569ef326fe92dc6',
    'jammy-updates': '34beb69f860de8eb98f2315b4c38c2542b1b005153652e399d898c892431adda',
    'jammy-security': 'eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9',
}
ROOTS = {
    # Clean image has no hosted preinstalled CMake/Python-APT. Signed snapshot roots.
    'cmake': '3.22.1-1ubuntu1.22.04.2', 'python3-apt': '2.4.0ubuntu4.1',
    'gcc-11': '11.4.0-1ubuntu1~22.04.3', 'g++-11': '11.4.0-1ubuntu1~22.04.3',
    'binutils': '2.38-4ubuntu2.12', 'pkg-config': '0.29.2-1ubuntu3',
    'libgdal-dev': '3.4.1+dfsg-1build4', 'libgeos-dev': '3.10.2-1',
    'libtinyxml2-dev': '9.0.0+dfsg-3', 'libeigen3-dev': '3.4.0-2ubuntu2',
    'libboost-dev': '1.74.0.3ubuntu7', 'libtbb-dev': '2021.5.0-7ubuntu2',
    'zlib1g-dev': '1:1.2.11.dfsg-2ubuntu9.2', 'libbz2-dev': '1.0.8-5ubuntu0.1',
    'ca-certificates': '20260816ubuntu0.22.04.1',
}
RELEASE_SIZES = {'jammy': 270087, 'jammy-updates': 128049, 'jammy-security': 128927}
KEYRING = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')

# Diagnostic-only output. Never include exception text, command arguments,
# filenames, environment values, response headers, or unreviewed URL strings.
DIAGNOSTIC_PREFIX = 'SAFE_PACKAGE_DIAGNOSTIC:'
TARGET_REFERENCE_PREFIX = 'SAFE_PACKAGE_TARGET_REFERENCE:'
INDEX_STANZA_COUNTS = {
    ('jammy', 'main'): 6090, ('jammy', 'universe'): 58923,
    ('jammy-updates', 'main'): 21642, ('jammy-updates', 'universe'): 5960,
    ('jammy-security', 'main'): 20377, ('jammy-security', 'universe'): 5017,
}
PROVIDER_SELECTION_ERRORS = {
    'APT_PROVIDER_COEXISTENCE_REJECTED': 'Both pkgconf and pkg-config are installed; refuse destructive repair',
    'APT_PROVIDER_VERSION_UNAUTHENTICATED': 'Installed pkgconf is absent from authenticated snapshot; stop instead of remove',
    'APT_PROVIDER_CAPABILITY_MISSING': 'Installed pkgconf does not authentically provide pkg-config',
    'APT_PROVIDER_OWNER_MISMATCH': 'pkg-config executable is not owned by selected signed pkgconf provider',
    'APT_PROVIDER_EXECUTABLE_MISSING': 'Provider executable missing',
}
CLOSURE_PROVENANCE_ERRORS = {
    'APT_CLOSURE_PACKAGE_UNAVAILABLE': 'No authenticated Jammy archive for relevant dependency',
    'APT_CLOSURE_DOWNGRADE_REFUSED': 'Snapshot would downgrade a relevant installed package',
    'APT_CLOSURE_PIN_UNRESOLVED': 'Cannot close pinned dependency provenance',
    'APT_CLOSURE_NO_PROGRESS': 'Native closure did not converge',
}
DIAGNOSTIC_CODES = frozenset({
    'APT_HTTP_ERROR', 'APT_TLS_ERROR', 'APT_NETWORK_ERROR', 'APT_TIMEOUT',
    'APT_PREREQUISITE_FAILED', 'APT_SUBPROCESS_FAILED', 'APT_METADATA_PARSE_ERROR',
    'APT_FILE_ACCESS_ERROR', 'APT_INVARIANT_REJECTED', 'APT_UNEXPECTED_ERROR',
} | set(PROVIDER_SELECTION_ERRORS) | set(CLOSURE_PROVENANCE_ERRORS))
DIAGNOSTIC_OPERATIONS = frozenset({
    'bootstrap', 'host-check', 'prerequisites', 'base-inventory', 'release-download',
    'signature-verify', 'release-parse', 'index-download', 'index-parse',
    'provider-selection', 'apt-config', 'apt-update', 'native-solver', 'solver-result',
    'closure-provenance', 'archive-download', 'archive-control', 'transaction-plan',
    'transaction-simulate', 'installation', 'post-install-check',
    'post-status-read', 'post-inventory-parse', 'post-inventory-delta', 'post-closure-verify', 'post-inventory-write', 'post-apt-check', 'post-delta-match', 'post-delta-missing', 'post-delta-unexpected', 'post-delta-version', 'post-delta-held', 'post-delta-other',
    'provider-verification', 'preparation-receipt',
})
DIAGNOSTIC_CLASSES = frozenset({
    'RuntimeError', 'CalledProcessError', 'ProcessFailure', 'HTTPError', 'URLError',
    'SSLError', 'TimeoutError', 'FileNotFoundError', 'PermissionError',
    'FileExistsError', 'OSError', 'UnicodeDecodeError', 'LZMAError', 'KeyError',
    'ValueError', 'ModuleNotFoundError', 'ImportError', 'AssertionError', 'TypeError',
    'JSONDecodeError', 'AttributeError', 'AptPkgError', 'OtherError', 'ProviderSelectionError',
    'ClosureProvenanceError',
})
DIAGNOSTIC_ERRNOS = frozenset({-3, -2, 1, 2, 5, 11, 13, 17, 22, 28, 30, 32,
                              36, 40, 95, 101, 104, 110, 111, 113})
DIAGNOSTIC_CONTEXT = {'operation': 'bootstrap', 'suite': None, 'component': None}
DIAGNOSTIC_ROOT = None


def phase(operation, suite=None, component=None):
    """Set only caller-supplied static logical names, never paths or package names."""
    DIAGNOSTIC_CONTEXT.update(operation=operation, suite=suite, component=component)


class ProcessFailure(RuntimeError):
    def __init__(self, returncode, child_receipt=None):
        super().__init__('Native subprocess returned nonzero status')
        self.returncode = returncode
        self.child_receipt = child_receipt


class ProviderSelectionError(RuntimeError):
    """A finite reason for an unchanged provider-selection acceptance condition."""
    def __init__(self, code):
        if code not in PROVIDER_SELECTION_ERRORS:
            raise ValueError('Unknown provider selection code')
        super().__init__(PROVIDER_SELECTION_ERRORS[code])
        self.code = code


def require_provider(value, code):
    if not value:
        raise ProviderSelectionError(code)


class ClosureProvenanceError(RuntimeError):
    """A finite reason for an unchanged closure-provenance acceptance condition."""
    def __init__(self, code, returncode=None, reference=None):
        if code not in CLOSURE_PROVENANCE_ERRORS:
            raise ValueError('Unknown closure provenance code')
        super().__init__(CLOSURE_PROVENANCE_ERRORS[code])
        self.code = code
        self.returncode = returncode
        self.reference = reference


def require_closure(value, code):
    if not value:
        raise ClosureProvenanceError(code)


def validated_child_receipt(receipt):
    """Only the exact, closed native-solver schema can cross the process boundary."""
    try:
        if type(receipt) is not dict or set(receipt) != {
                'schema', 'code', 'operation', 'exception_class', 'http_status',
                'errno', 'returncode', 'suite', 'component'}:
            return None
        if type(receipt['schema']) is not int or receipt['schema'] != 1:
            return None
        if receipt['code'] not in DIAGNOSTIC_CODES or receipt['exception_class'] not in DIAGNOSTIC_CLASSES:
            return None
        if receipt['operation'] != 'native-solver' or receipt['suite'] is not None or receipt['component'] is not None:
            return None
        for key, lower, upper in (('http_status', 100, 599), ('returncode', -255, 255)):
            value = receipt[key]
            if value is not None and (type(value) is not int or not lower <= value <= upper):
                return None
        value = receipt['errno']
        if value is not None and (type(value) is not int or value not in DIAGNOSTIC_ERRNOS):
            return None
        return dict(receipt)
    except (TypeError, KeyError):
        return None


def parse_child_receipt(output):
    """Inspect only the bounded final line, not a child log or earlier marker."""
    try:
        line = output[-4096:].splitlines()[-1]
        if not line.startswith(DIAGNOSTIC_PREFIX) or len(line) > 1024 or not line.isascii():
            return None
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate diagnostic key')
                result[key] = value
            return result
        return validated_child_receipt(json.loads(line[len(DIAGNOSTIC_PREFIX):], object_pairs_hook=unique_object))
    except (ValueError, TypeError, IndexError):
        return None


def failure_diagnostic(error):
    """Project errors to a bounded public schema without ever calling str(error)."""
    operation = DIAGNOSTIC_CONTEXT.get('operation')
    if operation not in DIAGNOSTIC_OPERATIONS:
        operation = 'bootstrap'
    name = type(error).__name__
    exception_class = name if name in DIAGNOSTIC_CLASSES else 'OtherError'
    if name == 'Error' and type(error).__module__ == 'apt_pkg':
        exception_class = 'AptPkgError'
    code, status, number, returncode = 'APT_UNEXPECTED_ERROR', None, None, None
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(error, urllib.error.HTTPError):
        code = 'APT_HTTP_ERROR'
        status = error.code if type(error.code) is int and 100 <= error.code <= 599 else None
    elif isinstance(reason, ssl.SSLError):
        code = 'APT_TLS_ERROR'
    elif isinstance(reason, TimeoutError):
        code = 'APT_TIMEOUT'
    elif isinstance(error, urllib.error.URLError):
        code = 'APT_NETWORK_ERROR'
    elif isinstance(error, (ProcessFailure, subprocess.CalledProcessError)):
        code = 'APT_PREREQUISITE_FAILED' if operation == 'prerequisites' else 'APT_SUBPROCESS_FAILED'
        rc = error.returncode
        returncode = rc if type(rc) is int and -255 <= rc <= 255 else None
    elif isinstance(error, OSError):
        code = 'APT_FILE_ACCESS_ERROR'
    elif operation in ('release-parse', 'index-parse') and isinstance(
            error, (RuntimeError, ValueError, KeyError, lzma.LZMAError)):
        code = 'APT_METADATA_PARSE_ERROR'
    elif isinstance(error, (ImportError, AssertionError)) and operation == 'prerequisites':
        code = 'APT_PREREQUISITE_FAILED'
    elif isinstance(error, ProviderSelectionError):
        code = error.code if error.code in PROVIDER_SELECTION_ERRORS else 'APT_INVARIANT_REJECTED'
    elif isinstance(error, ClosureProvenanceError):
        code = error.code if error.code in CLOSURE_PROVENANCE_ERRORS else 'APT_INVARIANT_REJECTED'
        if code == 'APT_CLOSURE_DOWNGRADE_REFUSED':
            rc = error.returncode
            returncode = rc if type(rc) is int and -255 <= rc <= 255 else None
    elif isinstance(error, RuntimeError):
        code = 'APT_INVARIANT_REJECTED'
    if isinstance(reason, OSError):
        value = reason.errno
        number = value if type(value) is int and value in DIAGNOSTIC_ERRNOS else None
    if isinstance(error, ProcessFailure) and operation == 'native-solver':
        child = validated_child_receipt(error.child_receipt)
        if child is not None:
            code, exception_class = child['code'], child['exception_class']
            status, number = child['http_status'], child['errno']
    suite, component = DIAGNOSTIC_CONTEXT.get('suite'), DIAGNOSTIC_CONTEXT.get('component')
    return {'schema': 1, 'code': code, 'operation': operation,
            'exception_class': exception_class, 'http_status': status, 'errno': number,
            'returncode': returncode,
            'suite': suite if suite in RELEASE_HASHES else None,
            'component': component if component in ('main', 'universe') else None}


def canonical_target_reference(record):
    """Reference only authenticated-index coordinates, never installed strings."""
    suite, component, stanza = record.get('_suite'), record.get('_component'), record.get('_stanza')
    if (type(suite) is not str or type(component) is not str or
            (suite, component) not in INDEX_STANZA_COUNTS or type(stanza) is not int or
            not 0 <= stanza < INDEX_STANZA_COUNTS[(suite, component)]):
        return None
    return {'schema': 1, 'snapshot': SNAPSHOT, 'suite': suite, 'component': component, 'stanza': stanza}


def write_target_reference(error, receipt):
    """Failure-only sidecar. If unavailable, retain the original closed receipt."""
    if (DIAGNOSTIC_ROOT is None or not isinstance(error, ClosureProvenanceError) or
            receipt['code'] != 'APT_CLOSURE_DOWNGRADE_REFUSED' or
            receipt['operation'] != 'closure-provenance'):
        return None
    try:
        ref = error.reference
        if type(ref) is not dict or set(ref) != {'schema', 'snapshot', 'suite', 'component', 'stanza'}:
            return None
        if type(ref['schema']) is not int or ref['schema'] != 1 or ref['snapshot'] != SNAPSHOT:
            return None
        checked = canonical_target_reference({'_suite': ref['suite'], '_component': ref['component'], '_stanza': ref['stanza']})
        if checked != ref:
            return None
        data = (json.dumps(checked, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
        fd = os.open(DIAGNOSTIC_ROOT / 'package-target-reference.json',
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        return hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, TypeError, KeyError):
        return None


def load_package_reference():
    """Load only the exact bundled helper, including under isolated Python -I."""
    path = Path(__file__).resolve().parents[1] / 'package_reference.py'
    spec = importlib.util.spec_from_file_location('_package_reference_v2', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def attach_post_delta_reference(error, before, after, transaction, records):
    """Attach only a closed diagnostic to the original, unchanged rejection."""
    try:
        if type(error) is not RuntimeError or DIAGNOSTIC_CONTEXT.get('operation') != 'post-delta-version':
            return
        helper = load_package_reference()
        reference = helper.validate_reference(helper.make_reference(before, after, transaction, records))
        if reference is not None:
            error.package_target_reference_v2 = reference
    except Exception:
        # Missing or faulty diagnostic code cannot replace the original error.
        pass


def post_delta_reference_marker(error, receipt):
    """Validate again at the output boundary; never interpolate private inputs."""
    try:
        if (type(error) is not RuntimeError or receipt['code'] != 'APT_INVARIANT_REJECTED' or
                receipt['operation'] != 'post-delta-version' or
                receipt['exception_class'] != 'RuntimeError' or
                any(receipt[key] is not None for key in
                    ('http_status', 'errno', 'returncode', 'suite', 'component'))):
            return None
        helper = load_package_reference()
        reference = helper.validate_reference(getattr(error, 'package_target_reference_v2', None))
        if reference is None:
            return None
        encoded = json.dumps(reference, sort_keys=True, separators=(',', ':'))
        marker = helper.PREFIX + encoded
        if not encoded.isascii() or len(encoded.encode('ascii')) > helper.MAX_REFERENCE_BYTES:
            return None
        return marker
    except Exception:
        return None


def emit_failure(error):
    receipt = failure_diagnostic(error)
    encoded = json.dumps(receipt, sort_keys=True, separators=(',', ':'))
    reference_hash = write_target_reference(error, receipt)
    # Do not overwrite a previous cohort, follow aliases, or let diagnostics mask
    # the original failure. A missing file never means preparation succeeded.
    if DIAGNOSTIC_ROOT is not None:
        try:
            fd = os.open(DIAGNOSTIC_ROOT / 'package-failure.json',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(encoded + '\n')
        except OSError:
            pass
    if reference_hash is not None:
        print(TARGET_REFERENCE_PREFIX + reference_hash, file=sys.stderr)
    marker = post_delta_reference_marker(error, receipt)
    if marker is not None:
        try:
            print(marker, file=sys.stderr)
        except Exception:
            # A failed optional diagnostic write must not suppress the original marker.
            pass
    print(DIAGNOSTIC_PREFIX + encoded, file=sys.stderr, flush=True)
    return receipt


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def fields(text):
    out, key = {}, None
    for line in text.splitlines():
        if not line:
            key = None
            continue
        if line.startswith((' ', '\t')):
            require(key is not None, 'Orphan control continuation')
            out[key] += '\n' + line
        else:
            require(':' in line, 'Malformed control field')
            key, value = line.split(':', 1)
            require(bool(key) and key[0] not in '#-' and
                    all(33 <= ord(c) <= 126 and c != ':' for c in key),
                    'Malformed control field name')
            require(key not in out, 'Duplicate control field: ' + key)
            out[key] = value.strip(' \t')
    return out


def package_index_rows(compressed):
    """Parse multiple signed package stanzas; a single-control parser is not enough."""
    for stanza in lzma.decompress(compressed).decode('utf-8').split('\n\n'):
        row = fields(stanza)
        if 'Package' in row:
            package_identity(row)
            yield row


def identity(row):
    return row['name'], row['version'], row['architecture']


def package_identity(row):
    return row['Package'], row['Version'], row['Architecture']


def cleartext(raw):
    require(raw.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n'), 'Not an InRelease')
    body = raw.split('\n\n', 1)[1].split('-----BEGIN PGP SIGNATURE-----', 1)[0]
    return '\n'.join(line[2:] if line.startswith('- ') else line for line in body.splitlines()) + '\n'


def hash_entries(payload):
    require('SHA256:\n' in payload, 'Release lacks SHA256')
    result = {}
    for line in payload.split('SHA256:\n', 1)[1].splitlines():
        parts = line.split()
        if len(parts) == 3 and re.fullmatch('[0-9a-f]{64}', parts[0]):
            require(parts[2] not in result, 'Duplicate SHA256 entry')
            result[parts[2]] = (parts[0], int(parts[1]))
    return result


def safe_archive_path(value):
    p = PurePosixPath(value)
    require(value.startswith('pool/') and not p.is_absolute() and
            all(part not in ('', '.', '..') for part in value.split('/')) and
            not any(c.isspace() or c in '?%#\\' for c in value), 'Unsafe archive filename')
    require(value.endswith('.deb'), 'Not a Debian archive')
    return value


def file_row(root, path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Evidence is not a regular file')
    require(path.resolve().is_relative_to(root), 'Evidence escapes candidate')
    ref = {'base': 'candidate', 'path': path.relative_to(root).as_posix()}
    return {'ref': ref, 'resolved_ref': dict(ref), 'symlink_target': None, 'sha256': sha(path)}


def os_values(text):
    return {k: v.strip('"') for line in text.splitlines() if '=' in line
            for k, v in [line.split('=', 1)]}


def check_host(values, machine, architecture):
    require(values.get('ID') == 'ubuntu' and values.get('VERSION_ID') == '22.04',
            'Run only on native Ubuntu 22.04; no Debian or other-release preparation')
    require(machine == 'x86_64' and architecture == 'amd64', 'Requires native amd64 runner')


def inventory(status):
    result = {}
    for stanza in status.split('\n\n'):
        row = fields(stanza)
        if row.get('Status', '').endswith(' ok installed'):
            key = row['Package'] + ':' + row['Architecture']
            require(key not in result, 'Duplicate installed package')
            result[key] = {'name': row['Package'], 'version': row['Version'],
                           'architecture': row['Architecture'], 'held': row['Status'].startswith('hold ')}
    return result


def choose_roots(installed, records):
    roots, providers = dict(ROOTS), {}
    pc = installed.get('pkg-config:amd64')
    conf = installed.get('pkgconf:amd64')
    require_provider(not (pc and conf), 'APT_PROVIDER_COEXISTENCE_REJECTED')
    if conf:
        candidates = records.get(identity(conf), [])
        require_provider(candidates, 'APT_PROVIDER_VERSION_UNAUTHENTICATED')
        require_provider(any(re.search(r'(^|,\s*)pkg-config(?:\s*\([^)]*\))?(?:,|$)',
                              row.get('Provides', '')) for row in candidates),
                         'APT_PROVIDER_CAPABILITY_MISSING')
        del roots['pkg-config']
        roots['pkgconf'] = conf['version']
        providers['pkg-config'] = 'pkgconf'
    return roots, providers


def validate_selected_provider(owner, executable):
    # dpkg-query -S reports package-owned diversions as well as actual owners.
    # These exact diversion paths are declared by the authenticated Jammy
    # pkgconf preinst; arbitrary text or other diversion forms are not evidence.
    lines = owner.splitlines()
    direct = {'pkgconf: /usr/bin/pkg-config', 'pkgconf:amd64: /usr/bin/pkg-config'}
    diverted = {'diversion by pkgconf from: /usr/bin/pkg-config',
                'diversion by pkgconf to: /usr/bin/pkg-config.real'}
    observed = set(lines)
    owners = observed & direct
    require_provider(len(owner) <= 1024 and len(lines) == len(observed)
                     and len(owners) == 1 and observed - owners in (set(), diverted),
                     'APT_PROVIDER_OWNER_MISMATCH')
    require_provider(executable.is_file(), 'APT_PROVIDER_EXECUTABLE_MISSING')


def validate_transaction(plan, base):
    seen = set()
    for item in plan:
        require(item['name'] not in seen, 'Duplicate transaction package')
        seen.add(item['name'])
        require(item['action'] in ('install', 'upgrade'), 'Removal/downgrade/reinstall transaction refused')
        old = base.get(item['name'] + ':' + item['architecture'])
        require(not old or not old['held'], 'Transaction changes held package')
        require(item.get('old_version') == (old['version'] if old else None), 'Base version changed')


def validate_inventory_delta(before, after, transaction):
    expected = {k: dict(v) for k, v in before.items()}
    for row in transaction:
        key = row['name'] + ':' + row['architecture']
        expected[key] = {'name': row['name'], 'version': row['version'],
                         'architecture': row['architecture'], 'held': False}
    require(expected == after, 'Unexpected installed inventory change; no readiness lock emitted')


def post_install_delta_operation(before, after, transaction):
    """Failure-only finite projection; never emits package identity or changes acceptance."""
    try:
        expected = {k: dict(v) for k, v in before.items()}
        for row in transaction:
            key = row['name'] + ':' + row['architecture']
            expected[key] = {'name': row['name'], 'version': row['version'],
                             'architecture': row['architecture'], 'held': False}
        if expected == after:
            return 'post-delta-match'
        if set(expected) - set(after):
            return 'post-delta-missing'
        if set(after) - set(expected):
            return 'post-delta-unexpected'
        if any(expected[k].get('version') != after[k].get('version') for k in expected):
            return 'post-delta-version'
        if any(expected[k].get('held') != after[k].get('held') for k in expected):
            return 'post-delta-held'
    except Exception:
        # Diagnostic failure must not mask the original rejection.
        pass
    return 'post-delta-other'


def parse_simulation(text):
    result = {}
    for line in text.splitlines():
        require(not line.startswith(('Remv ', 'Purg ')), 'APT simulation proposes removal')
        if line.startswith('Inst '):
            m = re.match(r'^Inst ([a-z0-9][a-z0-9+.:\-]*) (?:\[[^]]+\] )?\((\S+) ', line)
            require(m, 'Unrecognized APT simulation line: ' + line)
            name = m.group(1).split(':', 1)[0]
            require(name not in result, 'Duplicate APT simulation package')
            result[name] = m.group(2)
    return result


def parse_archive_uris(text):
    result = []
    for line in text.splitlines():
        if line.startswith("'"):
            parts = shlex.split(line)
            require(len(parts) == 4 and parts[2].isdigit(), 'Unrecognized APT archive URI row')
            uri, name, size, checksum = parts
            require(Path(name).name == name and name.endswith('.deb') and
                    not any(x in name for x in ('/', '\\', '..')), 'Unsafe APT cache filename')
            require(uri.startswith('file:'), 'APT attempted a source outside isolated local snapshot')
            result.append({'uri': uri, 'cache_name': name, 'size': int(size)})
    return result


def verify_provider_member(archive_bytes, system_root):
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r:') as archive:
        members = {m.name.removeprefix('./'): m for m in archive.getmembers()}
        name = 'usr/bin/pkg-config'
        require(name in members, 'Provider archive lacks pkg-config executable')
        member = members[name]
        actual = system_root / name
        if member.issym():
            require(actual.is_symlink() and os.readlink(actual) == member.linkname,
                    'Installed pkg-config alias differs from authenticated provider archive')
            require('/' not in member.linkname and member.linkname not in ('.', '..'),
                    'Unexpected provider link layout')
            name = 'usr/bin/' + member.linkname
            require(name in members, 'Provider symlink target missing from archive')
            member = members[name]
            actual = system_root / name
        require(member.isfile() and actual.is_file() and not actual.is_symlink(),
                'Provider executable is not a regular package-owned file')
        require(actual.read_bytes() == archive.extractfile(member).read(),
                'Installed pkg-config executable differs from authenticated archive')


def closure_versions(selected, seeds):
    """Walk native apt_pkg's actual satisfied targets, not textual dependency guesses."""
    queue = list(seeds)
    closure = {}
    while queue:
        version = queue.pop()
        key = version.parent_pkg.id
        if key in closure:
            continue
        closure[key] = version
        groups = []
        for kind in ('Depends', 'PreDepends', 'Pre-Depends'):
            groups += version.depends_list.get(kind, [])
        for group in groups:
            targets = []
            for dep in group:
                for target in dep.all_targets():
                    actual = selected.get(target.parent_pkg.id)
                    if actual and actual.ver_str == target.ver_str and actual.arch == target.arch:
                        targets.append(actual)
            require(targets, 'Native solved state has an unsatisfied dependency group')
            queue.extend(targets)  # Conservative superset when multiple installed alternatives satisfy it.
    return list(closure.values())


def extend_provenance_roots(missing, records, roots):
    """Retain the original authenticated upgrade policy; only refine rejection codes."""
    progress = False
    for item in missing:
        choices = [k for k in records if k[0] == item['name'] and k[2] == item['architecture']]
        require_closure(choices, 'APT_CLOSURE_PACKAGE_UNAVAILABLE')
        best = choices[0][1]
        for choice in choices[1:]:
            if subprocess.run(['/usr/bin/dpkg', '--compare-versions', choice[1], 'gt', best]).returncode == 0:
                best = choice[1]
        comparison = subprocess.run(['/usr/bin/dpkg', '--compare-versions', best, 'ge', item['version']])
        if comparison.returncode != 0:
            record = records[(item['name'], best, item['architecture'])][0]
            raise ClosureProvenanceError('APT_CLOSURE_DOWNGRADE_REFUSED', comparison.returncode,
                                         canonical_target_reference(record))
        require_closure(item['name'] not in ROOTS and item['name'] not in roots,
                        'APT_CLOSURE_PIN_UNRESOLVED')
        roots[item['name']] = best; progress = True
    require_closure(progress, 'APT_CLOSURE_NO_PROGRESS')


def native_solve(input_path, output_path):
    phase('native-solver')
    import apt_pkg  # Only the runner's /usr/bin/python3, never imported by fixture tests.
    request = json.loads(Path(input_path).read_text())
    apt_pkg.init_config()
    apt_pkg.read_config_file(apt_pkg.config, request['config'])
    apt_pkg.init_system()
    cache = apt_pkg.Cache(None)
    depcache = apt_pkg.DepCache(cache)
    resolver = apt_pkg.ProblemResolver(depcache)
    root_objects = []
    for name, version in request['roots'].items():
        pkg = cache[name]
        matches = [v for v in pkg.version_list if v.ver_str == version and v.arch in ('amd64', 'all')]
        require(matches, 'Exact root version unavailable in native cache: ' + name + '=' + version)
        depcache.set_candidate_ver(pkg, matches[0])
        depcache.mark_install(pkg, True, True)
        resolver.protect(pkg)
        root_objects.append(pkg)
    resolver.resolve(True)
    require(not depcache.broken_count, 'Native APT resolver left broken packages')
    selected, changed, essential = {}, [], []
    for pkg in cache.packages:
        require(not depcache.marked_delete(pkg), 'Native APT proposes removal: ' + pkg.name)
        require(not depcache.marked_downgrade(pkg), 'Native APT proposes downgrade: ' + pkg.name)
        marked = depcache.marked_install(pkg) or depcache.marked_upgrade(pkg) or depcache.marked_reinstall(pkg)
        version = depcache.get_candidate_ver(pkg) if marked else pkg.current_ver
        if version is None:
            continue
        selected[pkg.id] = version
        if marked and (not pkg.current_ver or version.ver_str != pkg.current_ver.ver_str):
            changed.append({'name': pkg.name, 'version': version.ver_str, 'architecture': version.arch,
                            'old_version': pkg.current_ver.ver_str if pkg.current_ver else None,
                            'action': 'upgrade' if pkg.current_ver else 'install'})
        if pkg.essential:
            essential.append(version)
    seeds = [selected[pkg.id] for pkg in root_objects] + essential
    seeds += [version for version in selected.values()
              if any(x['name'] == version.parent_pkg.name for x in changed)]
    closure = closure_versions(selected, seeds)
    rows = [{'name': v.parent_pkg.name, 'version': v.ver_str, 'architecture': v.arch} for v in closure]
    for pkg in root_objects:
        require(selected[pkg.id].ver_str == request['roots'][pkg.name], 'Solver changed exact root version')
    write_json(output_path, {'apt_pkg_version': apt_pkg.VERSION,
                            'transaction': sorted(changed, key=lambda x: x['name']),
                            'closure': sorted(rows, key=identity)})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Archive redirected; stop for review: ' + newurl)


def download_verified(url, destination, expected_size, expected_hash):
    require(url.startswith(ORIGIN), 'Download outside fixed official Ubuntu snapshot')
    require(re.fullmatch('[0-9a-f]{64}', expected_hash), 'Invalid expected SHA256')
    require(expected_size > 0, 'Invalid expected size')
    if destination.exists():
        require(not destination.is_symlink() and destination.is_file(), 'Unsafe existing download path')
        require(destination.stat().st_size == expected_size and sha(destination) == expected_hash,
                'Existing artifact differs from authenticated hash/size')
        return
    partial = destination.with_name(destination.name + '.partial')
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(url, timeout=90) as response:
        require(response.status == 200, 'Official snapshot request not successful')
        with partial.open('xb') as target:
            count = 0
            while block := response.read(1024 * 1024):
                count += len(block)
                require(count <= expected_size, 'Artifact exceeds authenticated size')
                target.write(block)
    require(partial.stat().st_size == expected_size and sha(partial) == expected_hash,
            'Artifact hash/size mismatch: ' + url)
    partial.rename(destination)


def download_payload(row, destination):
    filename = safe_archive_path(row['Filename'])
    download_verified(ORIGIN + urllib.parse.quote(filename, safe='/+'), destination,
                      int(row['Size']), row['SHA256'])


def main():
    global DIAGNOSTIC_ROOT
    phase('bootstrap')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', '--candidate-root', required=True, type=Path)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    require(root.is_dir() and not args.root.is_symlink(), 'Candidate root missing or symlinked')
    require(not any(c.isspace() or c in '\";$' for c in str(root)), 'Unsupported candidate path')
    if not (root / 'package-preparation').exists() and not (root / 'package-preparation.json').exists():
        DIAGNOSTIC_ROOT = root
    phase('host-check')
    arch = subprocess.check_output(['/usr/bin/dpkg', '--print-architecture'], text=True).strip()
    check_host(os_values(Path('/etc/os-release').read_text()), platform.machine(), arch)
    phase('prerequisites')
    require(KEYRING.is_file(), 'Trusted Ubuntu keyring is missing; no bootstrap installation attempted')
    subprocess.run(['/usr/bin/python3', '-I', '-B', '-c',
                    'import apt_pkg; assert apt_pkg.VERSION.startswith("2.4.")'], check=True)
    for tool in ('/usr/bin/gpgv', '/usr/bin/apt-get', '/usr/bin/dpkg-deb'):
        require(Path(tool).is_file(), 'Missing native tool: ' + tool)
    out = root / 'package-preparation'
    require(not out.exists() and not (root / 'package-preparation.json').exists(), 'Fresh preparation directory required')
    out.mkdir(mode=0o700)
    logs = out / 'logs'; logs.mkdir()
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'LANG': 'C',
           'HOME': str(out), 'DEBIAN_FRONTEND': 'noninteractive'}
    def run(argv, label, privileged=False, separate_stderr=False):
        command = list(argv)
        if privileged and os.geteuid() != 0:
            command = ['/usr/bin/sudo', '-n', '/usr/bin/env'] + [k + '=' + v for k, v in env.items()] + command
        result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT)
        (logs / (label + '.log')).write_text(result.stdout)
        if separate_stderr:
            (logs / (label + '.stderr.log')).write_text(result.stderr)
        if result.returncode != 0:
            child = parse_child_receipt(result.stdout) if label == 'native-solver' else None
            raise ProcessFailure(result.returncode, child)
        return result.stdout
    phase('base-inventory')
    base_status = Path('/var/lib/dpkg/status').read_bytes()
    (out / 'base-status').write_bytes(base_status)
    base = inventory(base_status.decode())
    write_json(out / 'base-inventory.json', base)
    write_json(out / 'host.json', {'os_release': os_values(Path('/etc/os-release').read_text()),
        'machine': platform.machine(), 'architecture': arch, 'image_os': os.environ.get('ImageOS'),
        'image_version': os.environ.get('ImageVersion'), 'apt_version': run(['/usr/bin/apt-get', '--version'], 'apt-version')})
    repo = out / 'repo'; repo.mkdir()
    metadata = root / 'metadata' / ('snapshot-' + SNAPSHOT)
    metadata.mkdir(parents=True, exist_ok=True)
    require(metadata.resolve() == metadata, 'Symlinked metadata directory refused')
    records, signed_files = {}, []
    for suite, expected in RELEASE_HASHES.items():
        phase('release-download', suite)
        release = metadata / (suite + '.InRelease')
        download_verified(ORIGIN + 'dists/' + suite + '/InRelease', release, RELEASE_SIZES[suite], expected)
        require(sha(release) == expected, 'Pinned InRelease changed: ' + suite)
        phase('signature-verify', suite)
        result = run(['/usr/bin/gpgv', '--status-fd=1', '--keyring', str(KEYRING), str(release)], suite + '-signature')
        require('[GNUPG:] VALIDSIG ' + SIGNER + ' ' in result, 'Unexpected Ubuntu release signer')
        phase('release-parse', suite)
        payload = cleartext(release.read_text())
        header = fields(payload.split('MD5Sum:', 1)[0])
        require(header.get('Origin') == 'Ubuntu' and header.get('Suite') == suite and header.get('Codename') == 'jammy', 'Wrong signed distribution')
        entries = hash_entries(payload)
        directory = repo / 'dists' / suite; directory.mkdir(parents=True)
        shutil.copyfile(release, directory / 'InRelease')
        signed_files.append(file_row(root, release))
        for component in ('main', 'universe'):
            phase('index-download', suite, component)
            index = metadata / (suite + '.' + component + '.amd64.Packages.xz')
            relative = component + '/binary-amd64/Packages.xz'
            # The signed Release is already authenticated before even fetching this index.
            expected_hash, expected_size = entries[relative]
            download_verified(ORIGIN + 'dists/' + suite + '/' + relative, index, expected_size, expected_hash)
            require((sha(index), index.stat().st_size) == entries[relative], 'Signed package-index mismatch')
            target = directory / relative; target.parent.mkdir(parents=True)
            shutil.copyfile(index, target)
            signed_files.append(file_row(root, index))
            phase('index-parse', suite, component)
            for stanza, row in enumerate(package_index_rows(index.read_bytes())):
                row.update(_suite=suite, _component=component, _stanza=stanza)
                records.setdefault(package_identity(row), []).append(row)
    phase('provider-selection')
    roots, providers = choose_roots(base, records)
    if providers:
        owner = run(['/usr/bin/dpkg-query', '-S', '/usr/bin/pkg-config'], 'pkg-config-owner', separate_stderr=True)
        validate_selected_provider(owner, Path('/usr/bin/pkg-config'))
    phase('apt-config')
    for path in ('lists/partial', 'archives/partial', 'empty-conf', 'empty-sources', 'empty-prefs'):
        (out / path).mkdir(parents=True)
    sources = out / 'sources.list'
    sources.write_text(''.join(f'deb [arch=amd64 signed-by={KEYRING} by-hash=no] {repo.as_uri()} {suite} main universe\n' for suite in RELEASE_HASHES))
    conf = out / 'apt.conf'
    options = {
        'Dir::Etc::main': '/dev/null', 'Dir::Etc::parts': str(out / 'empty-conf'),
        'Dir::Etc::sourcelist': str(sources), 'Dir::Etc::sourceparts': str(out / 'empty-sources'),
        'Dir::Etc::preferences': '/dev/null', 'Dir::Etc::preferencesparts': str(out / 'empty-prefs'),
        'Dir::State::lists': str(out / 'lists'), 'Dir::State::status': '/var/lib/dpkg/status',
        'Dir::State::extended_states': str(out / 'extended_states'), 'Dir::Cache::archives': str(out / 'archives'),
        'Dir::Cache::pkgcache': str(out / 'pkgcache.bin'), 'Dir::Cache::srcpkgcache': str(out / 'srcpkgcache.bin'),
        'Dir::Log': str(logs), 'APT::Architecture': 'amd64', 'APT::Install-Recommends': 'false',
        'APT::Install-Suggests': 'false', 'APT::Get::Always-Include-Phased-Updates': 'true',
        'APT::Get::AllowUnauthenticated': 'false', 'Acquire::AllowInsecureRepositories': 'false',
        'Acquire::AllowDowngradeToInsecureRepositories': 'false', 'Acquire::Languages': 'none',
        'Acquire::Retries': '0', 'APT::Get::AutomaticRemove': 'false',
    }
    conf.write_text(''.join(f'{key} "{value}";\n' for key, value in options.items()))
    env['APT_CONFIG'] = str(conf)
    phase('apt-update')
    run(['/usr/bin/apt-get', 'update'], 'apt-update')
    # Older relevant preinstalled versions may no longer be in the fixed snapshot.
    # Upgrade them only to authenticated versions, re-solving until provenance closes.
    while True:
        phase('native-solver')
        write_json(out / 'solver-input.json', {'config': str(conf), 'roots': roots})
        run(['/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), '--native-solve',
             str(out / 'solver-input.json'), str(out / 'solver-output.json')], 'native-solver')
        phase('solver-result')
        solution = json.loads((out / 'solver-output.json').read_text())
        validate_transaction(solution['transaction'], base)
        missing = [r for r in solution['closure'] if identity(r) not in records]
        if not missing:
            break
        phase('closure-provenance')
        extend_provenance_roots(missing, records, roots)
    validate_transaction(solution['transaction'], base)
    write_json(out / 'transaction.json', solution)
    payloads = out / 'payloads'; payloads.mkdir()
    resolved = []
    for item in solution['closure']:
        require(item['architecture'] in ('amd64', 'all'), 'Foreign architecture in relevant closure')
        phase('archive-download')
        record = records[identity(item)][0]
        path = payloads / (record['SHA256'] + '.deb')
        if not path.exists():
            download_payload(record, path)
        phase('archive-control')
        control = run(['/usr/bin/dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'], 'control-' + item['name'])
        require(package_identity(fields(control)) == identity(item), 'Archive control identity mismatch')
        resolved.append({**item, 'suite': record['_suite'], 'component': record['_component'],
                         'archive': file_row(root, path), 'archive_filename': record['Filename'],
                         'archive_size': int(record['Size']), 'archive_sha256': record['SHA256']})
    actual = {r['name']: r for r in resolved}
    if providers:
        # Ownership text remains provisional until the complete closure has
        # authenticated payload hashes and exact control identities. No earlier
        # step invokes pkgconf. Prove the live alias and binary before planning
        # or applying any transaction; keep the independent post-install proof.
        phase('provider-verification')
        provider = actual[providers['pkg-config']]
        provider_archive = root / provider['archive']['ref']['path']
        data_tar = subprocess.check_output(['/usr/bin/dpkg-deb', '--fsys-tarfile', str(provider_archive)])
        verify_provider_member(data_tar, Path('/'))
    phase('transaction-plan')
    pins = [row['name'] + '=' + row['version'] for row in solution['transaction']]
    require(Path('/var/lib/dpkg/status').read_bytes() == base_status, 'Package database changed during preparation')
    install_command = ['/usr/bin/apt-get', '--no-install-recommends', '--no-remove', '--no-download', 'install'] + pins
    if pins:
        # Ask APT for its cache filenames but NEVER fetch using these URIs. Cache
        # only separately SHA256-authenticated payload bytes under those names.
        uri_command = ['/usr/bin/apt-get', '-o', 'Debug::NoLocking=true', '--print-uris', '--download-only',
                       '--no-install-recommends', '--no-remove', 'install'] + pins
        uri_rows = parse_archive_uris(run(uri_command, 'apt-archive-uris'))
        remaining = {actual[row['name']]['archive_filename']: actual[row['name']]
                     for row in solution['transaction']}
        for uri in uri_rows:
            suffix = urllib.parse.unquote(urllib.parse.urlparse(uri['uri']).path)
            matches = [name for name in remaining if suffix.endswith('/' + name)]
            require(len(matches) == 1, 'APT URI differs from authenticated transaction archive')
            row = remaining.pop(matches[0])
            require(uri['size'] == row['archive_size'], 'APT URI size disagrees with signed metadata')
            destination = out / 'archives' / uri['cache_name']
            require(not destination.exists(), 'Unexpected cached package collision')
            shutil.copyfile(root / row['archive']['ref']['path'], destination)
            require(sha(destination) == row['archive_sha256'], 'Cached payload copy mismatch')
        require(not remaining, 'APT did not enumerate every exact transaction archive')
        phase('transaction-simulate')
        simulation = run(install_command[:1] + ['--simulate'] + install_command[1:], 'verified-payload-simulation')
        require(parse_simulation(simulation) == {r['name']: r['version'] for r in solution['transaction']},
                'Verified-payload APT transaction differs from native solution')
    installed = False
    if args.install:
        phase('installation')
        require(Path('/var/lib/dpkg/status').read_bytes() == base_status,
                'Package database changed before installation')
        if pins:
            # No archive can be fetched during this step. No removal/downgrade option is granted.
            run(install_command[:1] + ['--assume-yes'] + install_command[1:], 'install', privileged=True)
        phase('post-install-check')
        phase('post-status-read')
        post_status = Path('/var/lib/dpkg/status').read_bytes()
        (out / 'post-status').write_bytes(post_status)
        phase('post-inventory-parse')
        after = inventory(post_status.decode())
        phase('post-inventory-delta')
        try:
            validate_inventory_delta(base, after, solution['transaction'])
        except RuntimeError as error:
            operation = post_install_delta_operation(base, after, solution['transaction'])
            phase(operation)
            if operation == 'post-delta-version':
                try:
                    attach_post_delta_reference(error, base, after, solution['transaction'], records)
                except Exception:
                    # Even diagnostic argument/helper failures preserve this error.
                    pass
            raise
        phase('post-closure-verify')
        for row in resolved:
            require(after.get(row['name'] + ':' + row['architecture'], {}).get('version') == row['version'],
                    'Post-install closure mismatch: ' + row['name'])
        phase('post-inventory-write')
        write_json(out / 'post-inventory.json', after)
        phase('post-apt-check')
        run(['/usr/bin/apt-get', 'check'], 'post-apt-check', privileged=True)
        phase('provider-verification')
        provider = actual[providers.get('pkg-config', 'pkg-config')]
        provider_archive = root / provider['archive']['ref']['path']
        data_tar = subprocess.check_output(['/usr/bin/dpkg-deb', '--fsys-tarfile', str(provider_archive)])
        verify_provider_member(data_tar, Path('/'))
        installed = True
    phase('preparation-receipt')
    evidence = [file_row(root, path) for path in sorted(out.rglob('*'))
                if path.is_file() and not path.is_symlink() and (path.parent == logs or path.parent == out)]
    result = {'schema': 1, 'distribution': 'ubuntu', 'release': '22.04',
              'status': 'installed-and-verified' if installed else 'resolved-and-payloads-verified-not-installed',
              'signed_metadata_verified': True, 'signed_metadata': signed_files,
              'snapshot': {'kind': 'immutable-signed-snapshot', 'identifier': SNAPSHOT},
              'requested_root_provider': providers, 'resolved': resolved, 'evidence': evidence,
              'installation_verified': installed, 'installed': installed,
              'closure_complete': True, 'provider_compatibility_verified': installed,
              'closure_scope': 'Native apt_pkg Depends/Pre-Depends closure of requested roots, all transaction packages, and Essential base packages; all satisfied installed alternatives conservatively included.',
              'transaction': file_row(root, out / 'transaction.json'),
              'base_inventory': file_row(root, out / 'base-inventory.json')}
    if installed:
        result['post_inventory'] = file_row(root, out / 'post-inventory.json')
    write_json(root / 'package-preparation.json', result)
    print(json.dumps({'status': result['status'], 'resolved_packages': len(resolved),
                      'transaction_packages': len(solution['transaction']), 'output': str(root / 'package-preparation.json')}))


if __name__ == '__main__':
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--native-solve':
            native_solve(sys.argv[2], sys.argv[3])
        else:
            main()
    except Exception as error:
        # This failure-only diagnostic path never changes acceptance or retries.
        # Raw subprocess logs remain private; the final marker is a projection.
        emit_failure(error)
        sys.exit(2)
