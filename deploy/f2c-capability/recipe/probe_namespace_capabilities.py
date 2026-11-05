"""Diagnostic only: authenticated existing-tool, private namespace capability proof.

No install, rootfs, package maintainer script, build, persistent allocation, or
launcher fallback exists here. The public receipt contains two finite enums.
The only ownership changes occur on a bounded namespace-local tmpfs after a
private mount namespace has been proved. Native paths are not exercised by the
stdlib fixture suite. Run only with explicit execution authorization.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types

PROBE_CODES = frozenset({
    'OK', 'EXECUTION_NOT_REQUESTED', 'INVALID_ARGUMENTS', 'SOURCE_INVALID',
    'LOCK_INVALID', 'HOST_CHANGED', 'TOOL_CHANGED', 'HELPER_PRIVILEGE_MISSING',
    'NSS_UNSUPPORTED', 'LOGINUID_UNSUPPORTED', 'CALLER_UNSUPPORTED', 'SUBID_OWNER_UNSUPPORTED', 'SUBID_INVALID', 'SUBUID_MISSING', 'SUBGID_MISSING', 'SUBUID_TOO_SHORT',
    'SUBGID_TOO_SHORT', 'NAMESPACE_IDENTITY_INVALID', 'USER_NAMESPACE_FAILED',
    'MAPPING_FAILED', 'GROUPS_CAPABILITY_FAILED', 'MOUNT_NAMESPACE_FAILED', 'TMPFS_FAILED',
    'OFFLINE_NETWORK_FAILED', 'OWNERSHIP_FAILED', 'CHILD_PROTOCOL_FAILED',
    'CHILD_EXIT_FAILED', 'TIMEOUT', 'CLEANUP_FAILED', 'INTERNAL_FAILURE',
})
NS_GET_NSTYPE = 0xB703
CLONE_NEWNS = 0x00020000
CLONE_NEWUSER = 0x10000000
CLONE_NEWNET = 0x40000000
MAP_COUNT = 65535
TIMEOUT_SECONDS = 30
MAX_MESSAGE = 65536
TOOL_NAMES = ('python', 'unshare', 'newuidmap', 'newgidmap')
HOST_PATHS = ('/etc/passwd', '/etc/group', '/etc/nsswitch.conf', '/etc/subuid', '/etc/subgid')
NSCD_SOCKET = '/var/run/nscd/socket'
UINT32_MAX = (1 << 32) - 1
CLEAN_ENV = {'PATH': '/nonexistent', 'LANG': 'C', 'LC_ALL': 'C'}
SOURCE_NAME = 'recipe/probe_namespace_capabilities.py'
PREP_NAME = 'recipe/prepare_capability_inputs.py'


class ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code if code in PROBE_CODES else 'INTERNAL_FAILURE'
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise ProbeFailure(code)


def _integer(value, minimum=0, maximum=(1 << 32) - 2):
    return type(value) is int and minimum <= value <= maximum


def _unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'CHILD_PROTOCOL_FAILED')
        value[key] = item
    return value


def _json(data):
    require(len(data) <= MAX_MESSAGE, 'CHILD_PROTOCOL_FAILED')
    return json.loads(data, object_pairs_hook=_unique)


def _safe_root(root):
    root = Path(root)
    require(root.is_absolute() and str(root) == os.path.abspath(root), 'SOURCE_INVALID')
    require(not any(c.isspace() for c in str(root)), 'SOURCE_INVALID')
    for item in (root, *root.parents):
        require(not item.is_symlink(), 'SOURCE_INVALID')
        ancestor = item.stat()
        trusted_sticky = item != root and ancestor.st_uid == 0 and bool(ancestor.st_mode & stat.S_ISVTX)
        require(stat.S_ISDIR(ancestor.st_mode) and (not ancestor.st_mode & 0o022 or trusted_sticky), 'SOURCE_INVALID')
    info = root.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid(), 'SOURCE_INVALID')
    require(not info.st_mode & 0o022, 'SOURCE_INVALID')
    return root


def _sha_fd(fd):
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    for block in iter(lambda: os.read(fd, 1024 * 1024), b''):
        digest.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def verify_source_index(root, expected):
    """Authenticate helper bytes before importing the preparation verifier."""
    root = _safe_root(root)
    require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'SOURCE_INVALID')
    index_path = root / 'source-index.json'
    require(not index_path.is_symlink() and index_path.is_file(), 'SOURCE_INVALID')
    raw = index_path.read_bytes()
    require(len(raw) <= 4 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == expected, 'SOURCE_INVALID')
    index = json.loads(raw, object_pairs_hook=_unique)
    require(index.get('schema') == 1 and index.get('status') == 'reviewed-source', 'SOURCE_INVALID')
    files = index.get('files')
    require(isinstance(files, dict) and {SOURCE_NAME, PREP_NAME}.issubset(files), 'SOURCE_INVALID')
    require(2 <= len(files) <= 1024, 'SOURCE_INVALID')
    verifier_bytes = None
    for name, entry in files.items():
        require(isinstance(name, str) and not name.startswith('/'), 'SOURCE_INVALID')
        require(all(p not in ('', '.', '..') for p in name.split('/')), 'SOURCE_INVALID')
        path = root
        for part in name.split('/'):
            path = path / part
            require(not path.is_symlink(), 'SOURCE_INVALID')
            require(not path.stat().st_mode & 0o022, 'SOURCE_INVALID')
        require(path.is_file(), 'SOURCE_INVALID')
        require(isinstance(entry, dict) and re.fullmatch('[0-9a-f]{64}', entry.get('sha256', '')), 'SOURCE_INVALID')
        with path.open('rb') as stream:
            source_bytes = stream.read(4 * 1024 * 1024 + 1)
        require(len(source_bytes) <= 4 * 1024 * 1024 and
                hashlib.sha256(source_bytes).hexdigest() == entry['sha256'], 'SOURCE_INVALID')
        if name == PREP_NAME:
            verifier_bytes = source_bytes
    require(Path(__file__).resolve() == root / SOURCE_NAME, 'SOURCE_INVALID')
    return index, verifier_bytes


def _load_verifier(root, verified_source):
    # Execute only the exact byte string authenticated above, never a loader's
    # second path read. Later source/lock checks still reject any file mutation.
    require(isinstance(verified_source, bytes) and 0 < len(verified_source) <= 4 * 1024 * 1024, 'LOCK_INVALID')
    name = '_authenticated_capability_preparation'
    module = types.ModuleType(name)
    module.__file__ = str(root / PREP_NAME)
    module.__package__ = ''
    # dataclasses and other stdlib helpers may require module registration.
    sys.modules[name] = module
    try:
        exec(compile(verified_source, module.__file__, 'exec', dont_inherit=True), module.__dict__)
        return module.verify_lock
    except BaseException:
        sys.modules.pop(name, None)
        raise


def _record_info(st):
    return {'uid': st.st_uid, 'gid': st.st_gid, 'mode': stat.S_IMODE(st.st_mode),
            'device': st.st_dev, 'inode': st.st_ino, 'size': st.st_size}


def _hold_record(record, code, root_owned=False):
    require(isinstance(record, dict), code)
    path = Path(record['path'])
    resolved = Path(record.get('resolved_path', record['path']))
    require(path.is_absolute() and resolved.is_absolute() and path.resolve(strict=True) == resolved, code)
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), code)
        require(not before.st_mode & 0o022 and (not root_owned or before.st_uid == 0), code)
        require(_record_info(before) == {k: record[k] for k in _record_info(before)}, code)
        require(_sha_fd(fd) == record['sha256'], code)
        after = os.fstat(fd)
        require(_record_info(after) == _record_info(before) and
                (after.st_nlink, after.st_mtime_ns, after.st_ctime_ns) ==
                (before.st_nlink, before.st_mtime_ns, before.st_ctime_ns), code)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_host_records(lock):
    require(set(HOST_PATHS[:3]).issubset(lock['host_files']), 'LOCK_INVALID')
    texts = {}
    for path in HOST_PATHS:
        if path not in lock['host_files']:
            require(path in HOST_PATHS[3:] and path in lock.get('absent_paths', []), 'LOCK_INVALID')
            require(not os.path.lexists(path), 'HOST_CHANGED')
            texts[path] = ''
            continue
        record = lock['host_files'][path]
        require(record['path'] == path, 'LOCK_INVALID')
        fd = _hold_record(record, 'HOST_CHANGED', root_owned=True)
        try:
            require(os.fstat(fd).st_size <= 4 * 1024 * 1024, 'HOST_CHANGED')
            texts[path] = os.read(fd, 4 * 1024 * 1024 + 1).decode('utf-8', errors='strict')
        finally:
            os.close(fd)
    return texts


def _nss_files_first(text):
    databases = {}
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        require(':' in line, 'NSS_UNSUPPORTED')
        name, body = line.split(':', 1)
        name = name.strip()
        require(name not in databases, 'NSS_UNSUPPORTED')
        databases[name] = body.split()
    for name in ('passwd', 'group'):
        tokens = databases.get(name, [])
        # Only the exact source-reviewed grammar is allowed. systemd is an
        # authenticated error-fallback closure, never an arbitrary NSS provider.
        require(tokens in (['files'], ['files', 'systemd']), 'NSS_UNSUPPORTED')
    require('subid' not in databases or databases['subid'] == ['files'], 'NSS_UNSUPPORTED')
    return {name: databases[name] for name in ('passwd', 'group')}


def _records(text, columns, code):
    rows = []
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        row = line.split(':')
        require(len(row) == columns, code)
        rows.append(row)
    return rows


def _local_accounts(texts):
    # Reject ambiguity between our parser and glibc's files parser before any
    # helper can ask NSS to resolve a name or UID. GECOS may remain Unicode.
    for path in ('/etc/passwd', '/etc/group'):
        require(all(c == '\n' or ord(c) >= 32 and ord(c) != 127 for c in texts[path]), 'CALLER_UNSUPPORTED')
    users = _records(texts['/etc/passwd'], 7, 'CALLER_UNSUPPORTED')
    groups = _records(texts['/etc/group'], 4, 'CALLER_UNSUPPORTED')
    for rows, number_fields in ((users, (2, 3)), (groups, (2,))):
        require(rows, 'CALLER_UNSUPPORTED')
        for row in rows:
            require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*\$?', row[0]) and len(row[0]) <= 255,
                    'CALLER_UNSUPPORTED')
            for field in number_fields:
                require(re.fullmatch(r'(0|[1-9][0-9]{0,9})', row[field]) and int(row[field]) < UINT32_MAX,
                        'CALLER_UNSUPPORTED')
        require(len({row[0] for row in rows}) == len(rows) and
                len({int(row[2]) for row in rows}) == len(rows), 'CALLER_UNSUPPORTED')
    return users, groups


def _parse_loginuid(raw):
    # glibc2.35-0ubuntu3.15 Linux getlogin{,_r}.c consumes the whole raw
    # decimal input. UINT32_MAX yields positive ENXIO, so getlogin returns
    # NULL without the tty/utmp fallback. Missing/malformed/newline input is
    # NOT equivalent to that safe sentinel and is terminal here.
    require(isinstance(raw, bytes) and re.fullmatch(rb'(0|[1-9][0-9]{0,9})', raw) and
            int(raw) <= UINT32_MAX, 'LOGINUID_UNSUPPORTED')
    return raw.decode('ascii')


def _read_loginuid():
    try:
        with Path('/proc/self/loginuid').open('rb') as stream:
            return _parse_loginuid(stream.read(12))
    except OSError:
        raise ProbeFailure('LOGINUID_UNSUPPORTED') from None


def _validate_loginuid(host, users):
    value = host.get('loginuid')
    require(isinstance(value, str), 'LOGINUID_UNSUPPORTED')
    require(_parse_loginuid(value.encode('utf-8')) == value, 'LOGINUID_UNSUPPORTED')
    require(int(value) == UINT32_MAX or int(value) in {int(row[2]) for row in users}, 'LOGINUID_UNSUPPORTED')


def _require_nss_runtime(lock, texts):
    providers = _nss_files_first(texts['/etc/nsswitch.conf'])
    policy = lock.get('nss')
    require(isinstance(policy, dict) and set(policy) == {'providers', 'fallback_paths', 'absent_paths'} and
            policy['providers'] == providers, 'LOCK_INVALID')
    paths, absent = policy['fallback_paths'], policy['absent_paths']
    require(isinstance(paths, list) and isinstance(absent, list) and
            all(isinstance(path, str) and path.startswith('/') for path in paths + absent), 'LOCK_INVALID')
    require(len(set(paths)) == len(paths) and len(set(absent)) == len(absent) and
            not set(paths).intersection(absent), 'LOCK_INVALID')
    uses_systemd = any('systemd' in values for values in providers.values())
    require(bool(paths) == uses_systemd and (uses_systemd or not absent), 'LOCK_INVALID')
    for path in paths:
        # The preparation verifier has already rederived the fixed candidate
        # set and authenticated each provider and its complete ELF closure.
        # This consumer must not invent a more permissive provider search.
        require(path in lock['runtime_files'] and path in lock['runtime_elf_paths'] and
                lock['runtime_files'][path].get('package') == 'libnss-systemd', 'LOCK_INVALID')
    require(NSCD_SOCKET in lock.get('absent_paths', []), 'LOCK_INVALID')


def _guard_helper_identity(lock):
    require(_read_loginuid() == lock['host']['loginuid'], 'HOST_CHANGED')
    # Fresh helper exec has no inherited nscd mapping. glibc tries this fixed
    # endpoint before files, including in setuid helpers: it must stay absent.
    for path in (NSCD_SOCKET, *lock['nss']['absent_paths']):
        require(not os.path.lexists(path), 'HOST_CHANGED')


def _range(text, name, local_names, occupied, short_code):
    ranges = []
    for owner, start, count in _records(text, 3, 'SUBID_INVALID'):
        # shadow1:4.8.1-2ubuntu2.2 find_range may resolve an unrelated owner
        # whose range overlaps the caller-ID mapping. Validate EVERY owner,
        # not just the grant we select. Numeric owner fallback is unsupported.
        require(owner in local_names and not owner.isdecimal(), 'SUBID_OWNER_UNSUPPORTED')
        require(re.fullmatch('[0-9]+', start) and re.fullmatch('[0-9]+', count), 'SUBID_INVALID')
        start, count = int(start), int(count)
        require(_integer(start, 1) and _integer(count, 1) and start + count <= UINT32_MAX, 'SUBID_INVALID')
        if owner == name:
            ranges.append((start, count))
    require(ranges, short_code.replace('_TOO_SHORT', '_MISSING'))
    # Existing allocations only. Do not splice small allocations or allocate more.
    for start, count in sorted(ranges):
        if count >= MAP_COUNT and not any(start <= x < start + MAP_COUNT for x in occupied):
            return start
    raise ProbeFailure(short_code)


def select_mappings(texts, host):
    uid, gid = host['uid'], host['gid']
    require(_integer(uid, 1) and _integer(gid, 1), 'CALLER_UNSUPPORTED')
    _nss_files_first(texts['/etc/nsswitch.conf'])
    users, groups = _local_accounts(texts)
    _validate_loginuid(host, users)
    matching = [r for r in users if int(r[2]) == uid]
    require(len(matching) == 1 and int(matching[0][3]) == gid, 'CALLER_UNSUPPORTED')
    name = matching[0][0]
    require(len([r for r in groups if int(r[2]) == gid]) == 1, 'CALLER_UNSUPPORTED')
    require(isinstance(host['groups'], list) and all(_integer(v) for v in host['groups']), 'CALLER_UNSUPPORTED')
    occupied_uids = {int(r[2]) for r in users} | {uid, 0}
    occupied_gids = {int(r[2]) for r in groups} | set(host['groups']) | {gid, 0}
    local_names = {r[0] for r in users}
    uid_start = _range(texts['/etc/subuid'], name, local_names, occupied_uids, 'SUBUID_TOO_SHORT')
    gid_start = _range(texts['/etc/subgid'], name, local_names, occupied_gids, 'SUBGID_TOO_SHORT')
    return {'uid': [[0, uid, 1], [1, uid_start, MAP_COUNT]],
            'gid': [[0, gid, 1], [1, gid_start, MAP_COUNT]]}


def namespace_identity(fd, expected_type):
    require(fcntl.ioctl(fd, NS_GET_NSTYPE) == expected_type, 'NAMESPACE_IDENTITY_INVALID')
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino]


def _open_namespace(name, expected_type):
    fd = os.open('/proc/self/ns/' + name, os.O_RDONLY | os.O_CLOEXEC)
    try:
        return fd, namespace_identity(fd, expected_type)
    except BaseException:
        os.close(fd)
        raise


def _boot():
    value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', value), 'HOST_CHANGED')
    return value


def _remaining(deadline):
    left = deadline - time.monotonic()
    require(left > 0, 'TIMEOUT')
    return left


def _send(fd, message, deadline):
    data = (json.dumps(message, sort_keys=True, separators=(',', ':')) + '\n').encode()
    require(len(data) <= MAX_MESSAGE, 'CHILD_PROTOCOL_FAILED')
    view = memoryview(data)
    os.set_blocking(fd, False)
    while view:
        _, writable, _ = select.select([], [fd], [], _remaining(deadline))
        require(writable, 'TIMEOUT')
        try:
            count = os.write(fd, view[:4096])
        except BlockingIOError:
            continue
        require(count > 0, 'CHILD_PROTOCOL_FAILED')
        view = view[count:]


def _receive(fd, deadline, eof_code='CHILD_PROTOCOL_FAILED'):
    # Read one byte at a time: messages are small; never consume the next frame.
    data = bytearray()
    while len(data) < MAX_MESSAGE:
        readable, _, _ = select.select([fd], [], [], _remaining(deadline))
        require(readable, 'TIMEOUT')
        chunk = os.read(fd, 1)
        require(chunk, eof_code)
        if chunk == b'\n':
            value = _json(bytes(data))
            require(isinstance(value, dict), 'CHILD_PROTOCOL_FAILED')
            if value.get('event') == 'failure':
                raise ProbeFailure(value.get('code', 'CHILD_PROTOCOL_FAILED'))
            return value
        data.extend(chunk)
    raise ProbeFailure('CHILD_PROTOCOL_FAILED')


def _python_args(context):
    return ['/proc/self/fd/' + str(context['tool_fds']['python']), '-I', '-B', '-S',
            '-X', 'pycache_prefix=' + context['pycache_prefix'],
            '/proc/self/fd/' + str(context['script_fd'])]


def _read_maps(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        values = line.split()
        require(len(values) == 3 and all(re.fullmatch('[0-9]+', x) for x in values), 'MAPPING_FAILED')
        rows.append([int(x) for x in values])
    return rows


def _verify_maps(context, inner=False):
    expected_uid = [[0, 0, 1]] if inner else context['maps']['uid']
    expected_gid = [[0, 0, 1]] if inner else context['maps']['gid']
    require(_read_maps('/proc/self/uid_map') == expected_uid, 'MAPPING_FAILED')
    require(_read_maps('/proc/self/gid_map') == expected_gid, 'MAPPING_FAILED')
    require((os.getuid(), os.geteuid(), os.getgid(), os.getegid()) == (0, 0, 0, 0), 'MAPPING_FAILED')
    expected_groups = 'deny' if inner else 'allow'
    require(Path('/proc/self/setgroups').read_text().strip() == expected_groups, 'GROUPS_CAPABILITY_FAILED')


def _outer_groups_proof():
    # These group changes affect this ephemeral outer child only, never host or
    # parent credentials. Prove future package `_apt` group drop is feasible.
    try:
        os.setgroups([1, 65534])
        require(sorted(os.getgroups()) == [1, 65534], 'GROUPS_CAPABILITY_FAILED')
        os.setgroups([])
        require(os.getgroups() == [], 'GROUPS_CAPABILITY_FAILED')
    except OSError:
        raise ProbeFailure('GROUPS_CAPABILITY_FAILED') from None


def _process_starttime(raw):
    # comm may contain spaces or ')'; starttime is field22 after final ')'.
    tail = raw.rsplit(')', 1)[-1].split()
    require(len(tail) >= 20 and tail[19].isdigit(), 'USER_NAMESPACE_FAILED')
    return tail[19]


def _live_child(pidfd, procfd, starttime):
    # Do not call Popen.poll()/waitpid here: an unreaped direct child cannot have
    # its integer PID recycled even if externally killed during a helper call.
    require(not select.select([pidfd], [], [], 0)[0], 'USER_NAMESPACE_FAILED')
    fd = os.open('stat', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=procfd)
    try:
        current = _process_starttime(os.read(fd, 8192).decode())
    finally:
        os.close(fd)
    require(current == starttime, 'USER_NAMESPACE_FAILED')


def _kill_and_reap(process):
    # Each spawned process is its own process-group leader. Reap only after the
    # last helper; kill the entire group, not just the direct shell/launcher.
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _run_helper(fd, name, pid, rows, deadline):
    args = [name, str(pid)] + [str(v) for row in rows for v in row]
    process = subprocess.Popen(args, executable='/proc/self/fd/' + str(fd),
                               pass_fds=(fd,), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=CLEAN_ENV, close_fds=True, start_new_session=True)
    try:
        require(process.wait(timeout=_remaining(deadline)) == 0, 'MAPPING_FAILED')
    except subprocess.TimeoutExpired:
        raise ProbeFailure('TIMEOUT')
    finally:
        _kill_and_reap(process)


def _assert_private_mounts(raw):
    rows = [line.split() for line in raw.splitlines() if line.strip()]
    require(rows, 'MOUNT_NAMESPACE_FAILED')
    for fields in rows:
        require(len(fields) >= 10 and '-' in fields, 'MOUNT_NAMESPACE_FAILED')
        end = fields.index('-')
        require(end >= 6, 'MOUNT_NAMESPACE_FAILED')
        require(not any(v.startswith(('shared:', 'master:', 'propagate_from:')) for v in fields[6:end]), 'MOUNT_NAMESPACE_FAILED')


def _mount_tmpfs(path):
    """Actual native syscall. Deliberately not called by any local fixture."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount
    mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                      ctypes.c_ulong, ctypes.c_void_p]
    mount.restype = ctypes.c_int
    # MS_NOSUID | MS_NODEV | MS_NOEXEC; unshare already made propagation private.
    options = b'size=1048576,nr_inodes=32,mode=0700'
    result = mount(b'capability-probe', os.fsencode(path), b'tmpfs', 2 | 4 | 8, options)
    require(result == 0, 'TMPFS_FAILED')


def _validate_routes(ipv4, ipv6):
    lines = ipv4.splitlines()
    require(lines and lines[0].split() and lines[0].split()[0] == 'Iface', 'OFFLINE_NETWORK_FAILED')
    require(not any(x.strip() for x in lines[1:]), 'OFFLINE_NETWORK_FAILED')
    for row in ipv6.splitlines():
        values = row.split()
        require(not values or (len(values) == 10 and values[-1] == 'lo'), 'OFFLINE_NETWORK_FAILED')


def _network_proof(context):
    hostfd = context['host_net_fd']
    require(namespace_identity(hostfd, CLONE_NEWNET) == context['baseline']['net'], 'NAMESPACE_IDENTITY_INVALID')
    require(_boot() == context['baseline']['boot'], 'HOST_CHANGED')
    currentfd, current = _open_namespace('net', CLONE_NEWNET)
    try:
        require(current != context['baseline']['net'], 'OFFLINE_NETWORK_FAILED')
        try:
            initfd = os.open('/proc/1/ns/net', os.O_RDONLY | os.O_CLOEXEC)
        except PermissionError:
            initfd = None
        if initfd is not None:
            try:
                require(current != namespace_identity(initfd, CLONE_NEWNET), 'OFFLINE_NETWORK_FAILED')
            finally:
                os.close(initfd)
    finally:
        os.close(currentfd)
    interfaces = [x.split(':', 1)[0].strip() for x in Path('/proc/net/dev').read_text().splitlines()[2:] if x.strip()]
    require(interfaces == ['lo'], 'OFFLINE_NETWORK_FAILED')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        flags = struct.unpack('16sH14x', fcntl.ioctl(sock, 0x8913, struct.pack('16sH14x', b'lo', 0)))[1]
    require(not flags & 1, 'OFFLINE_NETWORK_FAILED')
    _validate_routes(Path('/proc/net/route').read_text(), Path('/proc/net/ipv6_route').read_text())
    for fd in Path('/proc/self/fd').iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        require(not target.startswith('socket:'), 'OFFLINE_NETWORK_FAILED')
    # This proof FD is never available to anything after the trusted guard.
    os.close(hostfd)
    context['host_net_fd'] = -1


def _ownership_proof(path):
    before = os.stat(path, follow_symlinks=False)
    require(stat.S_ISDIR(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o700, 'TMPFS_FAILED')
    _mount_tmpfs(path)
    after = os.stat(path, follow_symlinks=False)
    require(after.st_dev != before.st_dev and after.st_uid == 0 and after.st_gid == 0, 'TMPFS_FAILED')
    mounts = Path('/proc/self/mountinfo').read_text()
    _assert_private_mounts(mounts)
    matching = [line.split() for line in mounts.splitlines() if len(line.split()) >= 10 and line.split()[4] == path]
    require(len(matching) == 1, 'TMPFS_FAILED')
    fields = matching[0]
    require(fields[fields.index('-') + 1] == 'tmpfs', 'TMPFS_FAILED')
    require({'rw', 'nosuid', 'nodev', 'noexec'}.issubset(fields[5].split(',')), 'TMPFS_FAILED')
    # Open only inside the just-proved private tmpfs; no host-file fchown exists.
    dirfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open('ownership-proof', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dirfd)
        try:
            for uid in (1, 65534, 0):
                try:
                    os.fchown(fd, uid, uid)
                except OSError:
                    raise ProbeFailure('OWNERSHIP_FAILED') from None
                info = os.fstat(fd)
                require((info.st_uid, info.st_gid) == (uid, uid), 'OWNERSHIP_FAILED')
        finally:
            os.close(fd)
            os.unlink('ownership-proof', dir_fd=dirfd)
    finally:
        os.close(dirfd)


def _check_child_runtime(context):
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode, 'LOCK_INVALID')
    require(sys.pycache_prefix == context['pycache_prefix'] and not Path(sys.pycache_prefix).exists(), 'LOCK_INVALID')
    require(_boot() == context['baseline']['boot'], 'HOST_CHANGED')


def _child_user(context, control, report):
    _check_child_runtime(context)
    fd, current = _open_namespace('user', CLONE_NEWUSER)
    os.close(fd)
    require(current != context['baseline']['user'], 'USER_NAMESPACE_FAILED')
    # The first stage must still be in the parent's actual captured netns.
    fd, current = _open_namespace('net', CLONE_NEWNET)
    os.close(fd)
    require(current == context['baseline']['net'], 'NAMESPACE_IDENTITY_INVALID')
    require(namespace_identity(context['host_net_fd'], CLONE_NEWNET) == current, 'NAMESPACE_IDENTITY_INVALID')
    # Preserve allow in this new outer userns; privileged existing newgidmap
    # can install the already-authorized ranges without disabling setgroups.
    require(Path('/proc/self/setgroups').read_text().strip() == 'allow', 'GROUPS_CAPABILITY_FAILED')
    _send(report, {'event': 'ready'}, context['deadline'])
    require(_receive(control, context['deadline']) == {'event': 'mapped'}, 'CHILD_PROTOCOL_FAILED')
    _verify_maps(context)
    _outer_groups_proof()
    # First isolate mounts while keeping the full outer UID/GID allocation.
    args = ['unshare', '--mount', '--propagation', 'private', '--']
    args += _python_args(context) + ['--_child-mounted', str(control), str(report)]
    _send(report, {'event': 'mounting'}, context['deadline'])
    os.execve(context['tool_fds']['unshare'], args, CLEAN_ENV)


def _child_mounted(context, control, report):
    _check_child_runtime(context)
    _verify_maps(context)
    userfd, userid = _open_namespace('user', CLONE_NEWUSER)
    os.close(userfd)
    require(userid == context['mapped_user'], 'USER_NAMESPACE_FAILED')
    mountfd, mountid = _open_namespace('mnt', CLONE_NEWNS)
    os.close(mountfd)
    require(mountid != context['baseline']['mount'], 'MOUNT_NAMESPACE_FAILED')
    netfd, netid = _open_namespace('net', CLONE_NEWNET)
    os.close(netfd)
    require(netid == context['baseline']['net'], 'NAMESPACE_IDENTITY_INVALID')
    _assert_private_mounts(Path('/proc/self/mountinfo').read_text())
    _ownership_proof(context['fixture'])
    # This is the actual proposed future offline-build composition: a second
    # user namespace, root-only remapping and a fresh network namespace inside
    # the outer multi-ID user/private mount context. No build is run.
    args = ['unshare', '--user', '--map-root-user', '--net', '--']
    args += _python_args(context) + ['--_child-inner', str(control), str(report)]
    _send(report, {'event': 'nesting'}, context['deadline'])
    os.execve(context['tool_fds']['unshare'], args, CLEAN_ENV)


def _child_inner(context, control, report):
    _check_child_runtime(context)
    _verify_maps(context, inner=True)
    userfd, userid = _open_namespace('user', CLONE_NEWUSER)
    os.close(userfd)
    require(userid not in (context['mapped_user'], context['baseline']['user']), 'USER_NAMESPACE_FAILED')
    mountfd, mountid = _open_namespace('mnt', CLONE_NEWNS)
    os.close(mountfd)
    require(mountid == context['outer_mount'] and mountid != context['baseline']['mount'], 'MOUNT_NAMESPACE_FAILED')
    _assert_private_mounts(Path('/proc/self/mountinfo').read_text())
    _network_proof(context)
    require(not Path(context['pycache_prefix']).exists(), 'HOST_CHANGED')
    _send(report, {'event': 'complete'}, context['deadline'])


def _child_entry(mode, control, report):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        context = _receive(control, deadline)
        require(context.get('protocol') == 1, 'CHILD_PROTOCOL_FAILED')
        require(type(context['deadline']) in (int, float) and 0 < context['deadline'] - time.monotonic() <= TIMEOUT_SECONDS,
                'CHILD_PROTOCOL_FAILED')
        if mode == '--_child-user':
            _child_user(context, control, report)
        elif mode == '--_child-mounted':
            _child_mounted(context, control, report)
        else:
            _child_inner(context, control, report)
        return 0
    except BaseException as error:
        code = error.code if isinstance(error, ProbeFailure) else 'INTERNAL_FAILURE'
        try:
            _send(report, {'event': 'failure', 'code': code}, deadline)
        except BaseException:
            pass
        return 1


def _execute_locked(root, lock, expected_lock, expected_source, verifier):
    require(lock.get('schema') == 3 and lock.get('protocol') == 'private-public-keyring-v1' and
            lock.get('member_protocol') == 'signed-package-hop-attribution-v1' and
            lock.get('status') == 'authenticated-existing-tools', 'LOCK_INVALID')
    require(lock.get('scope') == 'namespace-capability-only' and lock['source_index_sha256'] == expected_source, 'LOCK_INVALID')
    require(threading.active_count() == 1 and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'CALLER_UNSUPPORTED')
    host = lock['host']
    require((os.getuid(), os.geteuid(), os.getgid(), os.getegid()) == (host['uid'], host['uid'], host['gid'], host['gid']), 'HOST_CHANGED')
    require(sorted(os.getgroups()) == sorted(host['groups']) and _boot() == host['boot_id'], 'HOST_CHANGED')
    require(not Path(host['pycache_prefix']).exists(), 'HOST_CHANGED')
    texts = _read_host_records(lock)
    _require_nss_runtime(lock, texts)
    maps = select_mappings(texts, host)
    _guard_helper_identity(lock)
    fds = []
    fixture = None
    process = None
    primary_error = None
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        tool_fds = {}
        for name in TOOL_NAMES:
            fd = _hold_record(lock['tools'][name], 'TOOL_CHANGED', root_owned=True)
            fds.append(fd)
            tool_fds[name] = fd
            require(os.fstat(fd).st_mode & 0o111, 'TOOL_CHANGED')
            if name in ('newuidmap', 'newgidmap'):
                require(os.fstat(fd).st_mode & stat.S_ISUID, 'HELPER_PRIVILEGE_MISSING')
        scriptfd = _hold_record(lock['source_files'][SOURCE_NAME], 'SOURCE_INVALID')
        fds.append(scriptfd)
        netfd, netid = _open_namespace('net', CLONE_NEWNET)
        fds.append(netfd)
        mountfd, mountid = _open_namespace('mnt', CLONE_NEWNS)
        fds.append(mountfd)
        userfd, userid = _open_namespace('user', CLONE_NEWUSER)
        fds.append(userfd)
        fixture = tempfile.mkdtemp(prefix='.namespace-probe-', dir=root)
        info = os.stat(fixture, follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == host['uid'], 'TMPFS_FAILED')
        control_r, control_w = os.pipe2(os.O_CLOEXEC)
        report_r, report_w = os.pipe2(os.O_CLOEXEC)
        fds += [control_r, control_w, report_r, report_w]
        context = {'protocol': 1, 'deadline': deadline, 'tool_fds': tool_fds, 'script_fd': scriptfd,
                   'host_net_fd': netfd, 'pycache_prefix': host['pycache_prefix'], 'fixture': fixture,
                   'maps': maps, 'baseline': {'boot': host['boot_id'], 'net': netid, 'mount': mountid, 'user': userid}}
        inherited = tuple(tool_fds.values()) + (scriptfd, netfd, control_r, report_w)
        args = ['unshare', '--user', '--'] + _python_args(context) + ['--_child-user', str(control_r), str(report_w)]
        process = subprocess.Popen(args, executable='/proc/self/fd/' + str(tool_fds['unshare']),
                                   pass_fds=inherited, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=CLEAN_ENV, close_fds=True, start_new_session=True)
        os.close(control_r); fds.remove(control_r)
        os.close(report_w); fds.remove(report_w)
        pidfd = os.pidfd_open(process.pid, 0)
        procfd = os.open('/proc/' + str(process.pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fds += [pidfd, procfd]
        statfd = os.open('stat', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=procfd)
        try:
            starttime = _process_starttime(os.read(statfd, 8192).decode())
        finally:
            os.close(statfd)
        _send(control_w, context, deadline)
        require(_receive(report_r, deadline, 'USER_NAMESPACE_FAILED') == {'event': 'ready'}, 'CHILD_PROTOCOL_FAILED')
        child_user_fd = os.open('ns/user', os.O_RDONLY | os.O_CLOEXEC, dir_fd=procfd)
        try:
            context['mapped_user'] = namespace_identity(child_user_fd, CLONE_NEWUSER)
            require(context['mapped_user'] != userid, 'USER_NAMESPACE_FAILED')
        finally:
            os.close(child_user_fd)
        for name, kind in (('newuidmap', 'uid'), ('newgidmap', 'gid')):
            _live_child(pidfd, procfd, starttime)
            # Recheck authenticated host/tool/source/runtime bytes immediately
            # before helper invocation. The child remains blocked and unreaped.
            verified = verifier(root, expected_lock)
            require(verified == lock, 'HOST_CHANGED')
            _guard_helper_identity(lock)
            try:
                _run_helper(tool_fds[name], name, process.pid, maps[kind], deadline)
            finally:
                _guard_helper_identity(lock)
            _live_child(pidfd, procfd, starttime)
        _send(control_w, {'event': 'mapped'}, deadline)
        require(_receive(report_r, deadline, 'MAPPING_FAILED') == {'event': 'mounting'}, 'CHILD_PROTOCOL_FAILED')
        # After each exec the same process reads a fresh bounded context, not env flags.
        _send(control_w, context, deadline)
        require(_receive(report_r, deadline, 'MOUNT_NAMESPACE_FAILED') == {'event': 'nesting'}, 'CHILD_PROTOCOL_FAILED')
        child_mount_fd = os.open('ns/mnt', os.O_RDONLY | os.O_CLOEXEC, dir_fd=procfd)
        try:
            context['outer_mount'] = namespace_identity(child_mount_fd, CLONE_NEWNS)
            require(context['outer_mount'] != mountid, 'MOUNT_NAMESPACE_FAILED')
        finally:
            os.close(child_mount_fd)
        _send(control_w, context, deadline)
        require(_receive(report_r, deadline, 'OFFLINE_NETWORK_FAILED') == {'event': 'complete'}, 'CHILD_PROTOCOL_FAILED')
        try:
            require(process.wait(timeout=_remaining(deadline)) == 0, 'CHILD_EXIT_FAILED')
        except subprocess.TimeoutExpired:
            raise ProbeFailure('TIMEOUT')
        require(verifier(root, expected_lock) == lock, 'HOST_CHANGED')
        require(_read_host_records(lock) == texts, 'HOST_CHANGED')
        _guard_helper_identity(lock)
        _remaining(deadline)
    except BaseException as error:
        primary_error = error
    finally:
        cleanup_ok = True
        if process is not None:
            try:
                _kill_and_reap(process)
            except BaseException:
                cleanup_ok = False
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                cleanup_ok = False
        if fixture is not None:
            try:
                # Never rmtree/chown/unmount a host path. The isolated tmpfs
                # disappears with namespace exit; only its empty mountpoint remains.
                os.rmdir(fixture)
            except OSError:
                cleanup_ok = False
        if primary_error is not None:
            try:
                _guard_helper_identity(lock)
            except BaseException as error:
                primary_error = error
        if not cleanup_ok:
            primary_error = ProbeFailure('CLEANUP_FAILED')
    if primary_error is not None:
        raise primary_error


def run_probe(root, expected_source, expected_lock):
    root = _safe_root(root)
    unused_index, verifier_bytes = verify_source_index(root, expected_source)
    verifier = _load_verifier(root, verifier_bytes)
    try:
        lock = verifier(root, expected_lock)
    except BaseException:
        raise ProbeFailure('LOCK_INVALID') from None
    _execute_locked(root, lock, expected_lock, expected_source, verifier)


def _write_status(root, code):
    require(code in PROBE_CODES, 'INTERNAL_FAILURE')
    data = (json.dumps({'schema': 1, 'status': 'PASS' if code == 'OK' else 'FAIL', 'code': code},
                       sort_keys=True, separators=(',', ':')) + '\n').encode()
    root = _safe_root(root)
    fd, path = tempfile.mkstemp(prefix='.capability-status-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        # Atomic, no overwrite or symlink following, and no retry/resume.
        os.link(path, root / 'capability-probe-status.json', follow_symlinks=False)
    finally:
        if fd >= 0:
            os.close(fd)
        os.unlink(path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] in ('--_child-user', '--_child-mounted', '--_child-inner'):
        try:
            require(all(re.fullmatch('[0-9]+', x) for x in argv[1:]), 'INVALID_ARGUMENTS')
            return _child_entry(argv[0], int(argv[1]), int(argv[2]))
        except BaseException:
            return 1
    root = None
    code = 'INVALID_ARGUMENTS'
    try:
        allowed = {'--root', '--expected-source-index-sha256', '--expected-lock-sha256'}
        options = {}
        execute = False
        while argv:
            key = argv.pop(0)
            if key == '--execute-capability-probe':
                require(not execute, 'INVALID_ARGUMENTS')
                execute = True
            else:
                require(key in allowed and key not in options and argv, 'INVALID_ARGUMENTS')
                options[key] = argv.pop(0)
        require(set(options) == allowed, 'INVALID_ARGUMENTS')
        root = _safe_root(options['--root'])
        require(execute, 'EXECUTION_NOT_REQUESTED')
        require(re.fullmatch('[0-9a-f]{64}', options['--expected-lock-sha256']), 'LOCK_INVALID')
        run_probe(root, options['--expected-source-index-sha256'], options['--expected-lock-sha256'])
        code = 'OK'
    except BaseException as error:
        code = error.code if isinstance(error, ProbeFailure) else 'INTERNAL_FAILURE'
    if root is not None:
        try:
            _write_status(root, code)
        except BaseException:
            code = 'CLEANUP_FAILED'
    # Only finite status and code leave the private execution context.
    print(json.dumps({'schema': 1, 'status': 'PASS' if code == 'OK' else 'FAIL', 'code': code}, sort_keys=True))
    return 0 if code == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
