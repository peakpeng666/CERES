#!/usr/bin/env python3
"""Authenticate EXISTING tools for a separate future namespace capability probe.

No APT transaction, helper invocation, namespace operation, package script, or
rootfs creation. The initial system Python and read-only dpkg/gpgv/dpkg-deb are
explicit bootstrap trust; this proof cannot authenticate them retroactively.
"""
import argparse
import hashlib
import types
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import struct
import subprocess
import sys
import tarfile

SNAPSHOT = '20261021T000000Z'
PROTOCOL = 'private-public-keyring-v1'
LOCK_SCHEMA = 3
LOCK_SIZE_LIMIT = 16 * 1024 * 1024
MEMBER_PROTOCOL = 'signed-package-hop-attribution-v1'
USRMERGE_LAYOUT_POLICY = 'root-usrmerge-v1'
KEYRING_SOURCE_PATH = 'inputs/ubuntu-archive-keyring.gpg'
KEYRING_PROVENANCE_PATH = 'inputs/ubuntu-archive-keyring.provenance.json'
KEYRING_SOURCE_SHA256 = '80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
KEYRING_SOURCE_SIZE = 3607
KEYRING_SIGNER = 'F6ECB3762474EDA9D21B7022871920D1991BC93C'
KEYRING_CODES = frozenset({'CAP_KEYRING_SOURCE_REJECTED', 'CAP_KEYRING_PATH_REJECTED',
    'CAP_KEYRING_STATE_CHANGED', 'CAP_KEYRING_SEAL_REJECTED',
    'CAP_KEYRING_FD_REJECTED', 'CAP_KEYRING_COMMAND_REJECTED'})
METADATA_SOURCE_SHA256 = '3ef3c611be036f06331e4c2f0567fa75db27072270b075c72dc1231a4644f558'
TOOLS = {'python': '/usr/bin/python3.10', 'unshare': '/usr/bin/unshare',
         'newuidmap': '/usr/bin/newuidmap', 'newgidmap': '/usr/bin/newgidmap'}
TOOL_PACKAGES = {'python': 'python3.10-minimal', 'unshare': 'util-linux',
                 'newuidmap': 'uidmap', 'newgidmap': 'uidmap'}
BOOTSTRAP_TOOLS = {'gpgv': '/usr/bin/gpgv', 'dpkg_query': '/usr/bin/dpkg-query', 'dpkg_deb': '/usr/bin/dpkg-deb'}
HOST_FILES = ('/etc/passwd', '/etc/group', '/etc/nsswitch.conf',
              '/etc/ld.so.cache', '/usr/lib/os-release', '/var/lib/dpkg/status')
OPTIONAL_HOST_FILES = ('/etc/subuid', '/etc/subgid')
PYTHON_PATHS = ['/usr/lib/python310.zip', '/usr/lib/python3.10', '/usr/lib/python3.10/lib-dynload']
PYTHON_PACKAGES = ('libpython3.10-minimal', 'libpython3.10-stdlib')
PYTHON_OPTIONAL_PACKAGES = {name: 'python3.10' for name in
    ('libpython3.10-dev', 'python3.10-dev', 'libpython3.10-testsuite', 'python3.10-examples')}
PYTHON_OPTIONAL_PACKAGES.update({'python3-gdbm': 'python3-stdlib-extensions',
                                 'python3-tk': 'python3-stdlib-extensions',
                                 'python3-distutils': 'python3-stdlib-extensions',
                                 'python3-lib2to3': 'python3-stdlib-extensions',
                                 'python3.10-venv': 'python3.10'})
PREPARATION_CODES = frozenset({
    'CAP_INPUTS_VERIFIED', 'CAP_SOURCE_REJECTED', 'CAP_HOST_UNSUPPORTED',
    'CAP_INTERPRETER_REJECTED', 'CAP_TOOL_MISSING', 'CAP_METADATA_REJECTED',
    'CAP_PACKAGE_UNAUTHENTICATED', 'CAP_OWNER_REJECTED', 'CAP_ARCHIVE_REJECTED',
    'CAP_BYTES_REJECTED', 'CAP_MODE_REJECTED', 'CAP_RUNTIME_UNSUPPORTED',
    'CAP_RUNTIME_UNAUTHENTICATED', 'CAP_CONFIGURATION_REJECTED',
    'CAP_STATE_CHANGED', 'CAP_LOCK_REJECTED', 'CAP_IO_FAILED', 'CAP_UNEXPECTED_ERROR',
    'CAP_LOGINUID_UNSUPPORTED', 'CAP_NSCD_PRESENT', 'CAP_NSS_UNSUPPORTED', 'CAP_NSS_PROVIDER_UNAUTHENTICATED',
} | KEYRING_CODES | {'CAP_' + name.upper() + suffix for name in TOOLS for suffix in
     ('_MISSING', '_PACKAGE_UNAUTHENTICATED', '_OWNER_REJECTED', '_BYTES_REJECTED', '_MODE_REJECTED')})
FILE_KEYS = frozenset('path resolved_path sha256 uid gid mode device inode size nlink aliases'.split())
LIBDIRS = ('/lib/x86_64-linux-gnu', '/usr/lib/x86_64-linux-gnu', '/lib', '/usr/lib')
NSCD_SOCKET = '/var/run/nscd/socket'
NSS_SONAMES = ('libnss_systemd.so.2', 'libnss_systemd-2.35.so')
# Public diagnostic values are closed reviewed labels, never measured values.
MODE_FIXED_SUBJECTS = {
    '/etc/passwd': 'PASSWD', '/etc/group': 'GROUP',
    '/etc/nsswitch.conf': 'NSSWITCH', '/etc/ld.so.cache': 'LOADER_CACHE',
    '/usr/lib/os-release': 'OS_RELEASE', '/var/lib/dpkg/status': 'DPKG_STATUS',
    '/usr/share/keyrings/ubuntu-archive-keyring.gpg': 'ARCHIVE_KEYRING',
    '/etc/subuid': 'SUBUID', '/etc/subgid': 'SUBGID',
    '/etc/ld.so.preload': 'LOADER_PRELOAD',
    '/usr/bin/python3.10': 'PYTHON', '/usr/bin/unshare': 'UNSHARE',
    '/usr/bin/newuidmap': 'NEWUIDMAP', '/usr/bin/newgidmap': 'NEWGIDMAP',
    '/usr/bin/gpgv': 'GPGV', '/usr/bin/dpkg-query': 'DPKG_QUERY',
    '/usr/bin/dpkg-deb': 'DPKG_DEB',
}
MODE_SUBJECTS = frozenset(MODE_FIXED_SUBJECTS.values()) | {'SOURCE_INPUT', 'RUNTIME_INPUT', 'UNKNOWN'}
MODE_CHECKS = frozenset({
    'COMPONENT_OWNER', 'COMPONENT_WRITABLE', 'SYMLINK_LIMIT', 'INTERMEDIATE_TYPE',
    'FILE_TYPE', 'FILE_SIZE', 'FILE_OWNER', 'FILE_WRITABLE', 'EXECUTABLE', 'SETUID', 'UNKNOWN',
})
MODE_CODES = frozenset({'CAP_MODE_REJECTED'} | {'CAP_' + name.upper() + '_MODE_REJECTED' for name in TOOLS})


def mode_diagnostic_valid(code, subject, check):
    """Closed labels and the producer's fixed tool-code/subject relationship."""
    if (type(code) is not str or code not in MODE_CODES or
            type(subject) is not str or subject not in MODE_SUBJECTS or
            type(check) is not str or check not in MODE_CHECKS):
        return False
    if code == 'CAP_MODE_REJECTED' or (subject, check) == ('UNKNOWN', 'UNKNOWN'):
        return True
    return any(code == 'CAP_' + name.upper() + '_MODE_REJECTED' and
               subject == MODE_FIXED_SUBJECTS[path] for name, path in TOOLS.items())


class PreparationError(RuntimeError):
    def __init__(self, code, subject=None, check=None):
        if type(code) is not str or code not in PREPARATION_CODES or code == 'CAP_INPUTS_VERIFIED':
            raise ValueError('Unknown preparation code')
        if code in MODE_CODES:
            if subject is None and check is None:
                subject, check = 'UNKNOWN', 'UNKNOWN'
            if not mode_diagnostic_valid(code, subject, check):
                raise ValueError('Unknown mode diagnostic')
        elif subject is not None or check is not None:
            raise ValueError('Unexpected mode diagnostic')
        super().__init__(code)
        self.code, self.subject, self.check = code, subject, check


def need(value, code, subject=None, check=None):
    if not value:
        raise PreparationError(code, subject, check)


def preparation_status(error=None):
    """Revalidate diagnostics at the receipt boundary, without exception text."""
    if error is None:
        return {'schema': 1, 'status': 'PASS', 'code': 'CAP_INPUTS_VERIFIED'}
    if not isinstance(error, PreparationError):
        raise ValueError('Invalid preparation error')
    checked = PreparationError(error.code, error.subject, error.check)
    status = {'schema': 1, 'status': 'FAIL', 'code': checked.code}
    if checked.code in MODE_CODES:
        status.update(subject=checked.subject, check=checked.check)
    return status


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'CAP_LOCK_REJECTED')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def measure(path, limit=64 * 1024 * 1024, root_owned=True, subject='UNKNOWN'):
    """Measure root-owned aliases and a stable regular file, never special files."""
    if type(subject) is not str or subject not in MODE_SUBJECTS:
        raise ValueError('Unknown mode subject')
    path = Path(path).absolute()
    need('..' not in path.parts, 'CAP_IO_FAILED')
    aliases, todo, current, hops = [], list(path.parts[1:]), Path('/'), 0
    while todo:
        current /= todo.pop(0)
        before = current.lstat()
        if root_owned:
            need(before.st_uid == 0, 'CAP_MODE_REJECTED', subject, 'COMPONENT_OWNER')
            need(stat.S_ISLNK(before.st_mode) or not before.st_mode & 0o022,
                 'CAP_MODE_REJECTED', subject, 'COMPONENT_WRITABLE')
        if stat.S_ISLNK(before.st_mode):
            hops += 1
            need(hops <= 16, 'CAP_MODE_REJECTED', subject, 'SYMLINK_LIMIT')
            target = os.readlink(current)
            aliases.append({'path': str(current), 'target': target, 'uid': before.st_uid,
                            'gid': before.st_gid, 'mode': stat.S_IMODE(before.st_mode),
                            'device': before.st_dev, 'inode': before.st_ino})
            expanded = Path(os.path.normpath(str(current.parent / target)))
            todo = list(expanded.parts[1:]) + todo
            current = Path('/')
        elif todo:
            need(stat.S_ISDIR(before.st_mode), 'CAP_MODE_REJECTED', subject, 'INTERMEDIATE_TYPE')
    fd = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode), 'CAP_MODE_REJECTED', subject, 'FILE_TYPE')
        need(0 <= before.st_size <= limit, 'CAP_MODE_REJECTED', subject, 'FILE_SIZE')
        if root_owned:
            need(before.st_uid == 0, 'CAP_MODE_REJECTED', subject, 'FILE_OWNER')
            need(not before.st_mode & 0o022, 'CAP_MODE_REJECTED', subject, 'FILE_WRITABLE')
        chunks, total = [], 0
        while True:
            block = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not block:
                break
            total += len(block)
            need(total <= limit, 'CAP_IO_FAILED')
            chunks.append(block)
        after = os.fstat(fd)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'CAP_STATE_CHANGED')
        data = b''.join(chunks)
    finally:
        os.close(fd)
    return {'path': str(path), 'resolved_path': str(current), 'sha256': digest(data),
            'uid': before.st_uid, 'gid': before.st_gid, 'mode': stat.S_IMODE(before.st_mode),
            'device': before.st_dev, 'inode': before.st_ino, 'size': before.st_size,
            'nlink': before.st_nlink, 'aliases': aliases}, data


def source_anchor(root, expected):
    need(re.fullmatch('[0-9a-f]{64}', expected or ''), 'CAP_SOURCE_REJECTED')
    measured, raw = measure(root / 'source-index.json', 1024 * 1024, False, subject='SOURCE_INPUT')
    need(not measured['aliases'] and measured['nlink'] == 1 and digest(raw) == expected, 'CAP_SOURCE_REJECTED')
    index = unique_json(raw)
    need(type(index.get('schema')) is int and index.get('schema') == 1 and index.get('status') == 'reviewed-source' and
         type(index.get('files')) is dict, 'CAP_SOURCE_REJECTED')
    required = {'recipe/prepare_capability_inputs.py', 'recipe/jammy_metadata.py',
                'recipe/probe_namespace_capabilities.py', 'recipe/keyring_input.py',
                KEYRING_SOURCE_PATH, KEYRING_PROVENANCE_PATH}
    need(required.issubset(index['files']), 'CAP_SOURCE_REJECTED')
    result = {'source-index.json': measured}
    for name, entry in index['files'].items():
        need(type(name) is str and not name.startswith('/') and
             all(p not in ('', '.', '..') for p in name.split('/')), 'CAP_SOURCE_REJECTED')
        row, data = measure(root / name, 4 * 1024 * 1024, False, subject='SOURCE_INPUT')
        need(not row['aliases'] and row['nlink'] == 1 and not row['mode'] & 0o022 and
             row['sha256'] == entry['sha256'], 'CAP_SOURCE_REJECTED')
        result[name] = row
    need(result['recipe/jammy_metadata.py']['sha256'] == METADATA_SOURCE_SHA256, 'CAP_SOURCE_REJECTED')
    need(result[KEYRING_SOURCE_PATH]['sha256'] == KEYRING_SOURCE_SHA256 and
         result[KEYRING_SOURCE_PATH]['size'] == KEYRING_SOURCE_SIZE, 'CAP_KEYRING_SOURCE_REJECTED')
    return result


def keyring_module(root, source_files):
    # No importlib/path loader or candidate sys.path entry: execute only held
    # bytes with the exact already anchored measurement.
    name = 'recipe/keyring_input.py'
    need(type(source_files) is dict and name in source_files, 'CAP_SOURCE_REJECTED')
    row, raw = measure(root / name, 4 * 1024 * 1024, False, subject='SOURCE_INPUT')
    need(row == source_files[name], 'CAP_SOURCE_REJECTED')
    module = types.ModuleType('capability_private_keyring')
    module.__file__ = str(root / name)
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    need(module.PROTOCOL == PROTOCOL and module.SHA256 == KEYRING_SOURCE_SHA256 and
         module.SIZE == KEYRING_SOURCE_SIZE and module.SIGNER == KEYRING_SIGNER and
         module.CODES == KEYRING_CODES, 'CAP_SOURCE_REJECTED')
    return module


def keyring_checked(module, operation, *args):
    try:
        return operation(*args)
    except module.KeyringError as error:
        raise PreparationError(error.code) from None


def metadata_module(root):
    path = root / 'recipe/jammy_metadata.py'
    raw = measure(path, 4 * 1024 * 1024, False, subject='SOURCE_INPUT')[1]
    need(digest(raw) == METADATA_SOURCE_SHA256, 'CAP_SOURCE_REJECTED')
    module = types.ModuleType('capability_jammy_metadata')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def runtime_flags(root):
    prefix = sys.pycache_prefix
    need(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode and
         isinstance(prefix, str) and Path(prefix).is_absolute() and
         prefix == str(Path(root).absolute() / 'unused-pycache') and not os.path.lexists(prefix) and
         sys.path == PYTHON_PATHS and not os.path.lexists(PYTHON_PATHS[0]),
         'CAP_INTERPRETER_REJECTED')
    return prefix


def command(argv, out, label, limit=256 * 1024 * 1024):
    """Only the declared, read-only bootstrap programs may be invoked here."""
    allowed = (len(argv) == 3 and argv[0] == '/usr/bin/dpkg-query' and argv[1] == '--search' or
               len(argv) == 3 and argv[0] == '/usr/bin/dpkg-deb' and argv[1] in ('--field', '--fsys-tarfile'))
    need(allowed and type(argv[-1]) is str and argv[-1].startswith('/') and
         re.fullmatch('[a-z0-9-]{1,64}', label), 'CAP_RUNTIME_UNSUPPORTED')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LANG': 'C', 'HOME': str(out)}
    result = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                            close_fds=True, pass_fds=())
    need(len(result.stdout) <= limit and len(result.stderr) <= 1024 * 1024, 'CAP_IO_FAILED')
    with (out / (label + '.stderr')).open('xb') as stream:
        stream.write(result.stderr)
    return result.returncode, result.stdout


def signature_command(module, held, suite, out):
    argv, pass_fds = keyring_checked(module, held.command, suite)
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LANG': 'C', 'HOME': str(out)}
    result = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=120, close_fds=True, pass_fds=pass_fds)
    keyring_checked(module, held.command_fds, argv)
    need(len(result.stdout) <= 1024 * 1024 and len(result.stderr) <= 1024 * 1024,
         'CAP_METADATA_REJECTED')
    with (out / (suite + '.stderr')).open('xb') as stream:
        stream.write(result.stderr)
    return result.returncode, result.stdout


def valid_signature_status(raw):
    # A real machine-status line and the direct signing fingerprint are required.
    # Optional primary-fingerprint fields do not broaden this signer policy.
    if type(raw) is not bytes or len(raw) > 1024 * 1024:
        return False
    signatures = []
    failures = {b'BADSIG', b'ERRSIG', b'NO_PUBKEY', b'NODATA', b'FAILURE'}
    for line in raw.splitlines():
        if len(line) > 4096 or not line.startswith(b'[GNUPG:] '):
            return False
        fields = line[9:].split(b' ')
        if fields[0] in failures:
            return False
        if fields[0] == b'VALIDSIG':
            if (len(fields) not in (10, 11) or
                    re.fullmatch(b'[0-9A-F]{40}', fields[1]) is None or
                    re.fullmatch(b'[0-9]{4}-[0-9]{2}-[0-9]{2}', fields[2]) is None or
                    any(re.fullmatch(b'[0-9]+', value) is None for value in fields[3:9]) or
                    re.fullmatch(b'[0-9A-Fa-f]{2}', fields[9]) is None or
                    (len(fields) == 11 and re.fullmatch(b'[0-9A-F]{40}', fields[10]) is None)):
                return False
            signatures.append(fields[1].decode('ascii'))
    return signatures == [KEYRING_SIGNER]


def load_metadata(root, m, out, keyring, held):
    directory = root / 'metadata' / ('snapshot-' + SNAPSHOT)
    directory.mkdir(parents=True, mode=0o700)
    records, evidence = {}, {}
    for suite, expected in m.RELEASE_HASHES.items():
        release = directory / (suite + '.InRelease')
        m.download_verified(m.ORIGIN + 'dists/' + suite + '/InRelease', release, m.RELEASE_SIZES[suite], expected)
        rc, result = signature_command(keyring, held, suite, out)
        need(rc == 0 and m.SIGNER == KEYRING_SIGNER and valid_signature_status(result),
             'CAP_METADATA_REJECTED')
        payload = m.cleartext(release.read_text())
        header = m.fields(payload.split('MD5Sum:', 1)[0])
        need(header.get('Origin') == 'Ubuntu' and header.get('Suite') == suite and
             header.get('Codename') == 'jammy', 'CAP_METADATA_REJECTED')
        entries = m.hash_entries(payload)
        evidence[str(release.relative_to(root))] = measure(release, root_owned=False, subject='SOURCE_INPUT')[0]
        for component in ('main', 'universe'):
            index = directory / (suite + '.' + component + '.amd64.Packages.xz')
            relative = component + '/binary-amd64/Packages.xz'
            expected_hash, size = entries[relative]
            need(0 < size <= 16 * 1024 * 1024, 'CAP_METADATA_REJECTED')
            m.download_verified(m.ORIGIN + 'dists/' + suite + '/' + relative, index, size, expected_hash)
            evidence[str(index.relative_to(root))] = measure(index, root_owned=False, subject='SOURCE_INPUT')[0]
            for ordinal, row in enumerate(m.package_index_rows(index.read_bytes())):
                row.update(_suite=suite, _component=component, _stanza=ordinal)
                records.setdefault(m.package_identity(row), []).append(row)
    return records, evidence


def select_record(name, installed, records):
    matches = [value for value in installed.values() if value['name'] == name and value['architecture'] in ('amd64', 'all')]
    need(len(matches) == 1, 'CAP_PACKAGE_UNAUTHENTICATED')
    value = matches[0]
    rows = records.get((name, value['version'], value['architecture']), [])
    need(bool(rows), 'CAP_PACKAGE_UNAUTHENTICATED')
    row = rows[0]
    need(re.fullmatch('[a-z0-9][a-z0-9+.-]{1,59}', row['Package']) and
         re.fullmatch('[0-9][0-9A-Za-z.+:~-]{0,53}', row['Version']), 'CAP_METADATA_REJECTED')
    return row


def archive_members(data):
    result = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        for member in archive:
            name = member.name.removeprefix('./')
            if name in ('', '.') or member.isdir():
                continue
            need(not name.startswith('/') and all(x not in ('', '.', '..') for x in name.split('/')) and
                 name not in result, 'CAP_ARCHIVE_REJECTED')
            if member.isfile():
                need(member.size <= 64 * 1024 * 1024, 'CAP_ARCHIVE_REJECTED')
                content = archive.extractfile(member).read()
                result[name] = {'kind': 'file', 'sha256': digest(content), 'size': member.size,
                                'mode': member.mode, 'uid': member.uid, 'gid': member.gid}
            elif member.issym():
                result[name] = {'kind': 'symlink', 'target': member.linkname,
                                'mode': member.mode, 'uid': member.uid, 'gid': member.gid}
            elif member.islnk():
                result[name] = {'kind': 'hardlink', 'target': member.linkname.removeprefix('./')}
            else:
                raise PreparationError('CAP_ARCHIVE_REJECTED')
    return result


def canonical_path(path):
    need(type(path) is str and 1 < len(path) <= 4096 and path.startswith('/') and
         '\0' not in path and all(x not in ('', '.', '..') for x in path[1:].split('/')),
         'CAP_BYTES_REJECTED')
    return path


def member_names(path):
    """Only the existing four usrmerge spellings, not a general path fallback."""
    canonical_path(path)
    names = [path[1:]]
    if path.startswith(('/usr/lib/', '/usr/lib64/', '/usr/bin/', '/usr/sbin/')):
        names.append(path[5:])
    return names


def resolved_alias(path, target, suffix):
    need(type(target) is str and 0 < len(target) <= 4096 and '\0' not in target,
         'CAP_BYTES_REJECTED')
    parts = [] if target.startswith('/') else path[1:].split('/')[:-1]
    for part in target.split('/') + suffix:
        if part in ('', '.'):
            continue
        if part == '..':
            need(parts, 'CAP_BYTES_REJECTED')
            parts.pop()
        else:
            parts.append(part)
    return canonical_path('/' + '/'.join(parts))


def member_chain(row):
    """Replay the measured order, rejecting unconsumed hops, cycles and escapes."""
    current = canonical_path(row['path'])
    canonical_path(row['resolved_path'])
    aliases = row['aliases']
    need(type(aliases) is list and len(aliases) <= 16, 'CAP_BYTES_REJECTED')
    seen = {current}
    for alias in aliases:
        path = canonical_path(alias['path'])
        need(current == path or current.startswith(path + '/'), 'CAP_BYTES_REJECTED')
        need(all(type(alias.get(k)) is int and alias[k] >= 0 for k in
                 ('uid', 'gid', 'mode', 'device', 'inode')), 'CAP_BYTES_REJECTED')
        suffix = current[len(path):].strip('/').split('/') if current != path else []
        current = resolved_alias(path, alias['target'], suffix)
        need(current not in seen, 'CAP_BYTES_REJECTED')
        seen.add(current)
        if path in ('/bin', '/sbin', '/lib', '/lib64'):
            # A fixed host-layout policy, deliberately NOT signed ownership.
            need(alias['target'] == 'usr' + path, 'CAP_BYTES_REJECTED')
    need(current == row['resolved_path'], 'CAP_BYTES_REJECTED')
    return aliases


def verified_archive_member(path, observed, kind, members):
    names = [name for name in member_names(path) if name in members]
    need(len(names) == 1, 'CAP_BYTES_REJECTED')
    name = names[0]
    signed = members[name]
    keys = ('sha256', 'size', 'mode', 'uid', 'gid') if kind == 'file' else ('target', 'mode', 'uid', 'gid')
    need(type(signed) is dict and set(signed) == {'kind'} | set(keys) and
         signed.get('kind') == kind and all(k in signed and k in observed and
         signed[k] == observed[k] and type(signed[k]) is type(observed[k]) for k in keys),
         'CAP_BYTES_REJECTED')
    return name


def package_reference(package):
    return {key: package[key] for key in
            ('name', 'version', 'architecture', 'source', 'suite', 'component', 'stanza')} | {
            'archive_sha256': package['archive']['sha256'], 'archive_size': package['archive']['size']}


def attributed_members(row, bind):
    hops = []
    for alias in member_chain(row):
        if alias['path'] in ('/bin', '/sbin', '/lib', '/lib64'):
            hops.append({'kind': 'layout-policy', 'path': alias['path'], 'target': alias['target'],
                         'layout_policy': USRMERGE_LAYOUT_POLICY})
        else:
            hops.append(bind(alias['path'], alias, 'symlink'))
    return {'hops': hops, 'target': bind(row['resolved_path'], row, 'file')}


def verify_member(row, members):
    """Single-archive pure checker; cross-package use must supply attribution."""
    def bind(path, observed, kind):
        return verified_archive_member(path, observed, kind, members)
    attributed_members(row, bind)


def verify_member_attributions(lock):
    """Replay the hash-anchored ledger; do not execute archive or host programs.

    Member projections and ownership observations are outputs of preparation,
    authenticated by the externally supplied lock digest. They are not a second
    signature scheme. verify_state separately remeasures every pinned archive,
    dpkg status, runtime file and alias before accepting this ledger on reuse.
    """
    need(len(json.dumps(lock, sort_keys=True, separators=(',', ':')).encode()) + 1 <= LOCK_SIZE_LIMIT,
         'CAP_LOCK_REJECTED')
    need(lock.get('member_protocol') == MEMBER_PROTOCOL and
         type(lock.get('member_attributions')) is dict and
         type(lock.get('packages')) is dict and 0 < len(lock['packages']) <= 96 and
         set(lock['member_attributions']) == set(lock['runtime_files']), 'CAP_LOCK_REJECTED')
    owners, used_members, used_paths = {}, {}, set()
    for name, package in lock['packages'].items():
        need(package.get('name') == name and type(name) is str and
             re.fullmatch('[a-z0-9][a-z0-9+.-]{1,59}', name) and
             type(package.get('version')) is str and re.fullmatch('[0-9][0-9A-Za-z.+:~-]{0,53}', package['version']) and
             package.get('architecture') in ('amd64', 'all') and
             type(package.get('source')) is str and 0 < len(package['source']) <= 256 and
             package.get('suite') in ('jammy', 'jammy-updates', 'jammy-security') and
             package.get('component') in ('main', 'universe') and
             type(package.get('stanza')) is int and package['stanza'] >= 0 and
             type(package.get('members')) is dict and type(package.get('owner_paths')) is list and
             all(type(path) is str for path in package['owner_paths']) and
             package['owner_paths'] == sorted(set(package['owner_paths'])), 'CAP_LOCK_REJECTED')
        need(type(package.get('archive')) is dict and
             type(package['archive'].get('sha256')) is str and
             re.fullmatch('[0-9a-f]{64}', package['archive']['sha256']) and
             type(package['archive'].get('size')) is int and 0 < package['archive']['size'] <= 64 * 1024 * 1024,
             'CAP_LOCK_REJECTED')
        used_members[name] = set()
        for path in package['owner_paths']:
            canonical_path(path)
            need(path not in owners, 'CAP_LOCK_REJECTED')
            owners[path] = name
    for path, name in owners.items():
        for equivalent in member_names(path):
            need('/' + equivalent not in owners or owners['/' + equivalent] == name,
                 'CAP_LOCK_REJECTED')
    def owner(path):
        need(path in owners, 'CAP_LOCK_REJECTED')
        used_paths.add(path)
        return owners[path]
    def bind(path, observed, kind):
        name = owner(path)
        package = lock['packages'][name]
        member = verified_archive_member(path, observed, kind, package['members'])
        used_members[name].add(member)
        return {'kind': 'signed-package-member', 'path': path, 'member': member,
                'package': name, 'package_record': package_reference(package)}
    for path, row in lock['runtime_files'].items():
        need(row['path'] == path and row.get('package') == owner(path), 'CAP_LOCK_REJECTED')
        need(attributed_members(row, bind) == lock['member_attributions'][path], 'CAP_LOCK_REJECTED')
    need(used_paths == set(owners) and all(used_members[name] and used_members[name] == set(package['members'])
         for name, package in lock['packages'].items()), 'CAP_LOCK_REJECTED')
    for mapping in (lock['tools'], lock['bootstrap_files']):
        need(all(row == lock['runtime_files'].get(row['path']) for row in mapping.values()), 'CAP_LOCK_REJECTED')
    for name, path in TOOLS.items():
        need(lock['runtime_files'][path]['package'] == TOOL_PACKAGES[name], 'CAP_LOCK_REJECTED')
    for path in lock['python_source_paths']:
        need(path in lock['member_attributions'], 'CAP_LOCK_REJECTED')
        attribution = lock['member_attributions'][path]
        names = {lock['runtime_files'][path]['package']} | {part['package'] for part in
            attribution['hops'] + [attribution['target']] if part['kind'] == 'signed-package-member'}
        for name in names:
            python_package_policy(name, lock['packages'][name]['source'])


def cstring(data, offset, end=None):
    end = len(data) if end is None else min(len(data), end)
    need(0 <= offset < end, 'CAP_RUNTIME_UNSUPPORTED')
    stop = data.find(b'\0', offset, end)
    need(stop >= offset and stop - offset <= 4096, 'CAP_RUNTIME_UNSUPPORTED')
    return data[offset:stop].decode('ascii')


def elf_inputs(data):
    """Small data-only ELF64 little-endian x86-64 dependency reader."""
    need(len(data) >= 64 and data[:6] == b'\x7fELF\x02\x01' and
         struct.unpack_from('<H', data, 18)[0] == 62, 'CAP_RUNTIME_UNSUPPORTED')
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    need(size == 56 and 0 < count <= 128 and offset + size * count <= len(data), 'CAP_RUNTIME_UNSUPPORTED')
    loads, dynamic, interp = [], None, None
    for i in range(count):
        kind, flags, start, address, physical, length, memory, align = struct.unpack_from('<IIQQQQQQ', data, offset + i * size)
        need(start + length <= len(data), 'CAP_RUNTIME_UNSUPPORTED')
        if kind == 1:
            loads.append((address, start, length))
        elif kind == 2:
            need(dynamic is None and length <= 1024 * 1024, 'CAP_RUNTIME_UNSUPPORTED')
            dynamic = data[start:start + length]
        elif kind == 3:
            need(interp is None, 'CAP_RUNTIME_UNSUPPORTED')
            interp = cstring(data, start, start + length)
            need(interp == '/lib64/ld-linux-x86-64.so.2', 'CAP_RUNTIME_UNSUPPORTED')
    need(dynamic is not None and len(dynamic) % 16 == 0, 'CAP_RUNTIME_UNSUPPORTED')
    needed, table, table_size, terminated = [], None, None, False
    for i in range(0, len(dynamic), 16):
        tag, value = struct.unpack_from('<qQ', dynamic, i)
        if tag == 0:
            terminated = True
            break
        need(tag not in (15, 29, 0x6ffffefb, 0x6ffffefc, 0x7ffffffd, 0x7fffffff), 'CAP_RUNTIME_UNSUPPORTED')
        if tag == 1:
            needed.append(value)
        elif tag == 5:
            table = value
        elif tag == 10:
            table_size = value
    need(terminated and table is not None and table_size is not None and len(needed) <= 128, 'CAP_RUNTIME_UNSUPPORTED')
    positions = [(start + table - address, start + length) for address, start, length in loads
                 if address <= table and table + table_size <= address + length]
    need(len(positions) == 1, 'CAP_RUNTIME_UNSUPPORTED')
    start, end = positions[0]
    names = [cstring(data, start + value, start + table_size) for value in needed]
    need(all(re.fullmatch('[A-Za-z0-9_+.-]{1,128}', name) for name in names), 'CAP_RUNTIME_UNSUPPORTED')
    return interp, names


def loader_cache(data):
    need(len(data) >= 48 and data[:20] == b'glibc-ld.so.cache1.1' and data[28] & 3 in (0, 2), 'CAP_RUNTIME_UNSUPPORTED')
    count, strings = struct.unpack_from('<II', data, 20)
    start, end = 48 + count * 24, 48 + count * 24 + strings
    need(count <= 20000 and end <= len(data), 'CAP_RUNTIME_UNSUPPORTED')
    result = {}
    for i in range(count):
        flags, key, value, version, hwcap = struct.unpack_from('<iIIIQ', data, 48 + i * 24)
        if flags != 0x303:
            continue
        need(start <= key < end and start <= value < end, 'CAP_RUNTIME_UNSUPPORTED')
        name, path = cstring(data, key, end), cstring(data, value, end)
        need(path.startswith('/') and '..' not in Path(path).parts, 'CAP_RUNTIME_UNSUPPORTED')
        result.setdefault(name, set()).add(path)
    return result


class Authenticator:
    def __init__(self, root, m, out, installed, records):
        self.root, self.m, self.out = root, m, out
        self.installed, self.records = installed, records
        self.packages, self.members, self.files = {}, {}, {}
        self.member_attributions, self.owners = {}, {}
        self.serial = 0

    def package(self, name):
        if name in self.members:
            return self.members[name]
        need(len(self.members) < 96, 'CAP_RUNTIME_UNSUPPORTED')
        row = select_record(name, self.installed, self.records)
        need(0 < int(row['Size']) <= 64 * 1024 * 1024, 'CAP_ARCHIVE_REJECTED')
        archive = self.out / (str(len(self.members)) + '.deb')
        self.m.download_payload(row, archive)
        rc, control = command(['/usr/bin/dpkg-deb', '--field', str(archive)], self.out, 'control-' + str(len(self.members)))
        parsed = self.m.fields(control.decode())
        need(rc == 0 and self.m.package_identity(parsed) == self.m.package_identity(row), 'CAP_ARCHIVE_REJECTED')
        rc, data = command(['/usr/bin/dpkg-deb', '--fsys-tarfile', str(archive)], self.out, 'data-' + str(len(self.members)))
        need(rc == 0, 'CAP_ARCHIVE_REJECTED')
        members = archive_members(data)
        archive_row = measure(archive, root_owned=False, subject='SOURCE_INPUT')[0]
        need(archive_row['sha256'] == row['SHA256'] and archive_row['size'] == int(row['Size']),
             'CAP_ARCHIVE_REJECTED')
        self.members[name] = members
        self.packages[name] = {'name': row['Package'], 'version': row['Version'],
            'architecture': row['Architecture'], 'suite': row['_suite'], 'component': row['_component'],
            'stanza': row['_stanza'], 'source': row.get('Source', row['Package']),
            'archive': archive_row,
            'members': {}, 'owner_paths': []}
        return self.members[name]

    def owner(self, path):
        canonical_path(path)
        if path in self.owners:
            return self.owners[path]
        candidates = ['/' + name for name in member_names(path)]
        if path.startswith(('/lib/', '/lib64/', '/bin/', '/sbin/')):
            candidates.append('/usr' + path)
        owners = set()
        for candidate in candidates:
            self.serial += 1
            rc, output = command(['/usr/bin/dpkg-query', '--search', candidate], self.out, 'owner-' + str(self.serial), 65536)
            if rc == 1:
                need(not output, 'CAP_OWNER_REJECTED')
                continue
            need(rc == 0, 'CAP_OWNER_REJECTED')
            for line in output.decode().splitlines():
                match = re.fullmatch(r'([a-z0-9][a-z0-9+.-]*)(?::amd64|:all)?: (.*)', line)
                need(match and match[2] == candidate, 'CAP_OWNER_REJECTED')
                owners.add(match[1])
        need(len(owners) == 1, 'CAP_OWNER_REJECTED')
        self.owners[path] = next(iter(owners))
        return self.owners[path]

    def authenticate(self, path, expected=None, owner_policy=None):
        if path in self.files:
            row = self.files[path]
            need(expected is None or row['package'] == expected, 'CAP_OWNER_REJECTED')
            if owner_policy is not None:
                attribution = self.member_attributions[path]
                names = {row['package']} | {part['package'] for part in
                    attribution['hops'] + [attribution['target']] if part['kind'] == 'signed-package-member'}
                for name in names:
                    owner_policy(name)
            return row
        row, data = measure(path, subject=MODE_FIXED_SUBJECTS.get(path, 'RUNTIME_INPUT'))
        member_chain(row)
        def checked_owner(member_path):
            owner = self.owner(member_path)
            if owner_policy is not None:
                owner_policy(owner)
            self.package(owner)
            package = self.packages[owner]
            package['owner_paths'] = sorted(set(package['owner_paths']) | {member_path})
            return owner
        owner = self.owner(path)
        need(expected is None or owner == expected, 'CAP_OWNER_REJECTED')
        checked_owner(path)
        def bind(member_path, observed, kind):
            actual = checked_owner(member_path)
            member = verified_archive_member(member_path, observed, kind, self.members[actual])
            package = self.packages[actual]
            package['members'][member] = dict(self.members[actual][member])
            return {'kind': 'signed-package-member', 'path': member_path, 'member': member,
                    'package': actual, 'package_record': package_reference(package)}
        attribution = attributed_members(row, bind)
        row['package'] = owner
        self.member_attributions[path] = attribution
        self.files[path] = row
        return row


def library_paths(name, cache):
    paths = set(cache.get(name, ()))
    for directory in LIBDIRS:
        path = Path(directory) / name
        if path.exists():
            paths.add(str(path))
        base = Path(directory)
        for legacy in ('tls', 'haswell', 'x86_64', 'avx512_1'):
            need(not (base / legacy).exists(), 'CAP_RUNTIME_UNSUPPORTED')
        if (base / 'glibc-hwcaps').exists():
            for path in (base / 'glibc-hwcaps').glob('*/' + name):
                paths.add(str(path))
    need(paths and len(paths) <= 32, 'CAP_RUNTIME_UNSUPPORTED')
    return sorted(paths)


def loginuid_value():
    # glibc 2.35 Linux getlogin_r: malformed input returns -1 and enables tty
    # fallback, but UINT32_MAX returns positive ENXIO before any NSS lookup.
    with Path('/proc/self/loginuid').open('rb') as stream:
        raw = stream.read(12)
    need(re.fullmatch(b'(0|[1-9][0-9]{0,9})', raw) and int(raw) <= 4294967295,
         'CAP_LOGINUID_UNSUPPORTED')
    return raw.decode('ascii')


def nss_providers(text):
    databases = {}
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        need(':' in line, 'CAP_NSS_UNSUPPORTED')
        name, body = line.split(':', 1)
        name = name.strip()
        need(name not in databases, 'CAP_NSS_UNSUPPORTED')
        databases[name] = body.split()
    providers = {name: databases.get(name) for name in ('passwd', 'group')}
    need(all(value in (['files'], ['files', 'systemd']) for value in providers.values()) and
         ('subid' not in databases or databases['subid'] == ['files']), 'CAP_NSS_UNSUPPORTED')
    return providers


def nss_candidates(name, cache):
    # Enumerate both the generic NSS soname and Ubuntu's fixed glibc-2.35
    # local-nss-upgrade retry, including missing paths. No RPATH exemption.
    paths, absent = set(cache.get(name, ())), set()
    for directory in LIBDIRS:
        base = Path(directory)
        for legacy in ('tls', 'haswell', 'x86_64', 'avx512_1'):
            need(not (base / legacy).exists(), 'CAP_RUNTIME_UNSUPPORTED')
        candidates = [base / name]
        if (base / 'glibc-hwcaps').exists():
            candidates.extend(path / name for path in (base / 'glibc-hwcaps').glob('*') if path.is_dir())
        for path in candidates:
            if os.path.lexists(path):
                paths.add(str(path))
            else:
                absent.add(str(path))
    need(len(paths) <= 32 and len(absent) <= 128, 'CAP_RUNTIME_UNSUPPORTED')
    return paths, absent - paths


def nss_inputs(text, cache):
    providers = nss_providers(text)
    paths, absent = set(), set()
    if any('systemd' in value for value in providers.values()):
        for index, name in enumerate(NSS_SONAMES):
            found, missing = nss_candidates(name, cache)
            if index == 0:
                need(found, 'CAP_NSS_PROVIDER_UNAUTHENTICATED')
            paths.update(found)
            absent.update(missing)
    return {'providers': providers, 'fallback_paths': sorted(paths), 'absent_paths': sorted(absent)}


def authenticate_nss(auth, nss):
    try:
        for path in nss['fallback_paths']:
            auth.authenticate(path, 'libnss-systemd')
    except (PreparationError, OSError):
        raise PreparationError('CAP_NSS_PROVIDER_UNAUTHENTICATED') from None


def check_nss_state(lock, cache):
    need(nss_inputs(Path('/etc/nsswitch.conf').read_text(), cache) == lock['nss'], 'CAP_STATE_CHANGED')
    need(all(path in lock['runtime_files'] and path in lock['runtime_elf_paths'] and
             lock['runtime_files'][path].get('package') == 'libnss-systemd'
             for path in lock['nss']['fallback_paths']), 'CAP_LOCK_REJECTED')


def runtime_closure(auth, initial, cache):
    queue, seen = list(initial), set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        need(len(seen) < 512, 'CAP_RUNTIME_UNSUPPORTED')
        seen.add(path)
        auth.authenticate(path)
        row, data = measure(path, subject=MODE_FIXED_SUBJECTS.get(path, 'RUNTIME_INPUT'))
        interp, needed = elf_inputs(data)
        if interp:
            queue.append(interp)
        for name in needed:
            queue.extend(library_paths(name, cache))
    return sorted(seen)


def python_files():
    base = Path('/usr/lib/python3.10')
    need(base.is_dir(), 'CAP_INTERPRETER_REJECTED')
    files = []
    for path in base.rglob('*'):
        need(not (path.is_symlink() and path.is_dir()), 'CAP_RUNTIME_UNAUTHENTICATED')
        if path.suffix == '.pyc' and '__pycache__' not in path.parts:
            raise PreparationError('CAP_RUNTIME_UNAUTHENTICATED')
        if path.suffix in ('.py', '.so'):
            files.append(str(path))
    need(100 <= len(files) <= 10000, 'CAP_INTERPRETER_REJECTED')
    return sorted(files)


def python_package_policy(name, source):
    need(name in PYTHON_PACKAGES or name in PYTHON_OPTIONAL_PACKAGES, 'CAP_RUNTIME_UNAUTHENTICATED')
    expected = 'python3.10' if name in PYTHON_PACKAGES else PYTHON_OPTIONAL_PACKAGES[name]
    need(type(source) is str and source.split() and source.split()[0] == expected,
         'CAP_RUNTIME_UNAUTHENTICATED')


def authenticate_python(auth, paths):
    for package in PYTHON_PACKAGES:
        auth.package(package)
    def policy(owner):
        # Apply the same bounded Python source policy to EVERY hop and target.
        need(owner in PYTHON_PACKAGES or owner in PYTHON_OPTIONAL_PACKAGES,
             'CAP_RUNTIME_UNAUTHENTICATED')
        row = select_record(owner, auth.installed, auth.records)
        python_package_policy(owner, row.get('Source', owner))
    for path in paths:
        owner = auth.owner(path)
        policy(owner)
        auth.authenticate(path, expected=owner, owner_policy=policy)


def verify_interpreter(row):
    """A held executable FD is fine; a copied or different interpreter is not."""
    current = os.stat('/proc/self/exe')
    need(sys.version_info[:2] == (3, 10) and
         (current.st_dev, current.st_ino, current.st_size) ==
         (row['device'], row['inode'], row['size']) and
         digest(Path('/proc/self/exe').read_bytes()) == row['sha256'], 'CAP_INTERPRETER_REJECTED')


def check_runtime_paths(initial, cache, expected):
    queue, seen = list(initial), set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        need(path in expected and len(seen) < 512, 'CAP_STATE_CHANGED')
        seen.add(path)
        interp, names = elf_inputs(measure(path, subject=MODE_FIXED_SUBJECTS.get(path, 'RUNTIME_INPUT'))[1])
        if interp:
            queue.append(interp)
        for name in names:
            queue.extend(library_paths(name, cache))
    need(seen == set(expected), 'CAP_STATE_CHANGED')


def write_new(path, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    partial = path.with_name(path.name + '.partial')
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    os.link(partial, path, follow_symlinks=False)
    partial.unlink()


def authenticate_tools(auth):
    tools = {}
    for name, path in TOOLS.items():
        try:
            tools[name] = auth.authenticate(path, TOOL_PACKAGES[name])
            need(tools[name]['mode'] & 0o111, 'CAP_MODE_REJECTED', MODE_FIXED_SUBJECTS[path], 'EXECUTABLE')
            if name in ('newuidmap', 'newgidmap'):
                need(tools[name]['mode'] & stat.S_ISUID, 'CAP_MODE_REJECTED', MODE_FIXED_SUBJECTS[path], 'SETUID')
                need(tools[name]['uid'] == 0, 'CAP_MODE_REJECTED', MODE_FIXED_SUBJECTS[path], 'FILE_OWNER')
        except PreparationError as error:
            code = 'CAP_' + name.upper() + '_' + error.code.removeprefix('CAP_')
            selected = code if code in PREPARATION_CODES else error.code
            if selected in MODE_CODES:
                raise PreparationError(selected, MODE_FIXED_SUBJECTS[path], error.check) from None
            raise PreparationError(selected) from None
    return tools


def prepare(root, expected):
    root = Path(root).absolute()
    source_files = source_anchor(root, expected)
    need(platform.machine() == 'x86_64' and sys.version_info[:2] == (3, 10) and
         sys.executable == TOOLS['python'], 'CAP_HOST_UNSUPPORTED')
    prefix = runtime_flags(root)
    m = metadata_module(root)
    keyring = keyring_module(root, source_files)
    release = m.os_values(Path('/usr/lib/os-release').read_text())
    need(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04', 'CAP_HOST_UNSUPPORTED')
    need(os.geteuid() == os.getuid() != 0 and os.getegid() == os.getgid(), 'CAP_HOST_UNSUPPORTED')
    for name, path in TOOLS.items():
        need(Path(path).is_file(), 'CAP_' + name.upper() + '_MISSING')
    need(not (root / 'capability-inputs.lock.json').exists(), 'CAP_LOCK_REJECTED')
    out = root / 'capability-input-evidence'
    out.mkdir(mode=0o700)
    host_files = {path: measure(path, subject=MODE_FIXED_SUBJECTS[path])[0] for path in HOST_FILES}
    need(not os.path.lexists(NSCD_SOCKET), 'CAP_NSCD_PRESENT')
    absent = [PYTHON_PATHS[0], NSCD_SOCKET]
    for path in OPTIONAL_HOST_FILES:
        if os.path.lexists(path):
            host_files[path] = measure(path, subject=MODE_FIXED_SUBJECTS[path])[0]
        else:
            absent.append(path)
    if os.path.lexists('/etc/ld.so.preload'):
        row, data = measure('/etc/ld.so.preload', 65536, subject='LOADER_PRELOAD')
        need(not any(line.split(b'#', 1)[0].strip() for line in data.splitlines()), 'CAP_CONFIGURATION_REJECTED')
        host_files['/etc/ld.so.preload'] = row
    else:
        absent.append('/etc/ld.so.preload')
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    need(re.fullmatch('[0-9a-f-]{36}', boot_id), 'CAP_HOST_UNSUPPORTED')
    host = {'uid': os.getuid(), 'gid': os.getgid(), 'groups': sorted(os.getgroups()),
            'boot_id': boot_id, 'pycache_prefix': prefix, 'python_path': list(sys.path),
            'loginuid': loginuid_value()}
    try:
        with keyring_checked(keyring, keyring.create, root) as held:
            records, metadata_files = load_metadata(root, m, out, keyring, held)
            trust_anchor = held.manifest
        # The keyring fd is closed before inventory, package authentication, or
        # any later helper/namespace-capability phase.
    except PreparationError:
        raise
    except Exception:
        raise PreparationError('CAP_METADATA_REJECTED') from None
    installed = m.inventory(Path('/var/lib/dpkg/status').read_text())
    auth = Authenticator(root, m, out, installed, records)
    tools = authenticate_tools(auth)
    stdlib = python_files()
    authenticate_python(auth, stdlib)
    bootstrap = {name: auth.authenticate(path) for name, path in BOOTSTRAP_TOOLS.items()}
    initial = list(TOOLS.values()) + list(BOOTSTRAP_TOOLS.values()) + [path for path in stdlib if path.endswith('.so')]
    cache = loader_cache(Path('/etc/ld.so.cache').read_bytes())
    nss = nss_inputs(Path('/etc/nsswitch.conf').read_text(), cache)
    authenticate_nss(auth, nss)
    initial.extend(nss['fallback_paths'])
    runtime = runtime_closure(auth, initial, cache)
    lock = {'schema': LOCK_SCHEMA, 'protocol': PROTOCOL, 'member_protocol': MEMBER_PROTOCOL,
        'member_attributions': auth.member_attributions, 'trust_anchor': trust_anchor,
        'status': 'authenticated-existing-tools', 'scope': 'namespace-capability-only',
        'source_index_sha256': expected, 'snapshot': SNAPSHOT, 'host': host, 'tools': tools,
        'host_files': host_files, 'source_files': source_files, 'runtime_files': auth.files,
        'bootstrap_files': bootstrap, 'nss': nss,
        'runtime_elf_paths': runtime, 'python_source_paths': stdlib, 'absent_paths': absent,
        'metadata_files': metadata_files, 'packages': auth.packages,
        'bootstrap_trust': 'initial-system-python-and-read-only-dpkg-gpgv-dpkg-deb'}
    verify_state(root, lock)
    write_new(root / 'capability-inputs.lock.json', lock)
    return lock


def verify_state(root, lock):
    need(type(lock.get('schema')) is int and lock.get('schema') == LOCK_SCHEMA and lock.get('protocol') == PROTOCOL and
         lock.get('member_protocol') == MEMBER_PROTOCOL and lock.get('status') == 'authenticated-existing-tools' and
         lock.get('scope') == 'namespace-capability-only' and lock.get('snapshot') == SNAPSHOT,
         'CAP_LOCK_REJECTED')
    need(set(lock['tools']) == set(TOOLS) and all(lock['tools'][name]['path'] == path for name, path in TOOLS.items()), 'CAP_LOCK_REJECTED')
    need(set(lock['bootstrap_files']) == set(BOOTSTRAP_TOOLS) and
         all(lock['bootstrap_files'][name]['path'] == path for name, path in BOOTSTRAP_TOOLS.items()), 'CAP_LOCK_REJECTED')
    optional = set(OPTIONAL_HOST_FILES) | {'/etc/ld.so.preload'}
    need(set(HOST_FILES).issubset(lock['host_files']) and
         set(lock['host_files']).issubset(set(HOST_FILES) | optional) and
         set(lock['absent_paths']).issubset(optional | {PYTHON_PATHS[0], NSCD_SOCKET}) and
         len(lock['absent_paths']) == len(set(lock['absent_paths'])) and
         not (set(lock['host_files']) & set(lock['absent_paths'])) and
         optional.issubset(set(lock['host_files']) | set(lock['absent_paths'])), 'CAP_LOCK_REJECTED')
    need(lock['host']['python_path'] == PYTHON_PATHS and
         {PYTHON_PATHS[0], NSCD_SOCKET}.issubset(lock['absent_paths']), 'CAP_LOCK_REJECTED')
    need(not os.path.lexists(NSCD_SOCKET), 'CAP_NSCD_PRESENT')
    need(loginuid_value() == lock['host']['loginuid'], 'CAP_STATE_CHANGED')
    verify_interpreter(lock['tools']['python'])
    need(runtime_flags(root) == lock['host']['pycache_prefix'], 'CAP_STATE_CHANGED')
    need((os.getuid(), os.getgid(), sorted(os.getgroups())) ==
         (lock['host']['uid'], lock['host']['gid'], lock['host']['groups']) and
         Path('/proc/sys/kernel/random/boot_id').read_text().strip() == lock['host']['boot_id'], 'CAP_STATE_CHANGED')
    source_files = source_anchor(root, lock['source_index_sha256'])
    need(source_files == lock['source_files'], 'CAP_SOURCE_REJECTED')
    keyring = keyring_module(root, source_files)
    keyring_checked(keyring, keyring.verify_manifest, root, lock.get('trust_anchor'))
    need('/usr/share/keyrings/ubuntu-archive-keyring.gpg' not in lock['runtime_files'] and
         str(Path(root) / keyring.PRIVATE_PATH) not in lock['runtime_files'], 'CAP_LOCK_REJECTED')
    for mapping, root_owned in ((lock['tools'], True), (lock['bootstrap_files'], True), (lock['runtime_files'], True),
                                (lock['host_files'], True), (lock['metadata_files'], False)):
        for row in mapping.values():
            measured, unused = measure(row['path'], root_owned=root_owned,
                                       subject=MODE_FIXED_SUBJECTS.get(row['path'], 'RUNTIME_INPUT' if root_owned else 'SOURCE_INPUT'))
            need(measured == {k: row[k] for k in FILE_KEYS}, 'CAP_STATE_CHANGED')
    for package in lock['packages'].values():
        row = package['archive']
        need(measure(row['path'], root_owned=False, subject='SOURCE_INPUT')[0] == row, 'CAP_STATE_CHANGED')
    verify_member_attributions(lock)
    need(all(not os.path.lexists(path) for path in lock['absent_paths']) and
         python_files() == lock['python_source_paths'], 'CAP_STATE_CHANGED')
    initial = list(TOOLS.values()) + list(BOOTSTRAP_TOOLS.values()) + [path for path in lock['python_source_paths'] if path.endswith('.so')]
    cache = loader_cache(Path('/etc/ld.so.cache').read_bytes())
    check_nss_state(lock, cache)
    initial.extend(lock['nss']['fallback_paths'])
    check_runtime_paths(initial, cache, lock['runtime_elf_paths'])


def verify_lock(root, expected_lock_sha256):
    root = Path(root).absolute()
    need(re.fullmatch('[0-9a-f]{64}', expected_lock_sha256 or ''), 'CAP_LOCK_REJECTED')
    row, raw = measure(root / 'capability-inputs.lock.json', LOCK_SIZE_LIMIT, False, subject='SOURCE_INPUT')
    need(not row['aliases'] and row['nlink'] == 1 and row['mode'] == 0o600 and
         digest(raw) == expected_lock_sha256, 'CAP_LOCK_REJECTED')
    lock = unique_json(raw)
    verify_state(root, lock)
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--expected-source-index-sha256', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        prepare(args.root, args.expected_source_index_sha256)
        status = preparation_status()
    except PreparationError as error:
        status = preparation_status(error)
    except OSError:
        status = preparation_status(PreparationError('CAP_IO_FAILED'))
    except Exception:
        status = preparation_status(PreparationError('CAP_UNEXPECTED_ERROR'))
    try:
        write_new(args.root / 'capability-preparation-status.json', status)
    except OSError:
        status = preparation_status(PreparationError('CAP_IO_FAILED'))
    print(json.dumps(status, sort_keys=True, separators=(',', ':')))
    return 0 if status['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
