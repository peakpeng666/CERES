"""Data-only private public-keyring input. Never imports from the candidate path.

Loaded by the capability adapter only after external source-index anchoring.
The caller/interpreter/kernel and source materialization remain trusted. Private
permissions do not defend against malicious same-UID or privileged processes;
sealing binds the exact checked bytes consumed by the one verifier child.
"""
import fcntl
import hashlib
import os
import re
import stat
from pathlib import Path

PROTOCOL = 'private-public-keyring-v1'
SOURCE_PATH = 'inputs/ubuntu-archive-keyring.gpg'
PRIVATE_DIRECTORY = 'private-trust-anchor'
PRIVATE_PATH = PRIVATE_DIRECTORY + '/ubuntu-archive-keyring.gpg'
SHA256 = '80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
SIZE = 3607
SIGNER = 'F6ECB3762474EDA9D21B7022871920D1991BC93C'
SNAPSHOT = '20261021T000000Z'
SUITES = ('jammy', 'jammy-updates', 'jammy-security')
CODES = frozenset({'CAP_KEYRING_SOURCE_REJECTED', 'CAP_KEYRING_PATH_REJECTED',
    'CAP_KEYRING_STATE_CHANGED', 'CAP_KEYRING_SEAL_REJECTED',
    'CAP_KEYRING_FD_REJECTED', 'CAP_KEYRING_COMMAND_REJECTED'})
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class KeyringError(RuntimeError):
    def __init__(self, code):
        if type(code) is not str or code not in CODES:
            raise ValueError('Unknown keyring input code')
        self.code = code
        super().__init__(code)


def require(value, code):
    if not value:
        raise KeyringError(code)


def seal_capabilities():
    """Resolve build-dependent APIs before input writes; never invent constants."""
    try:
        names = ('F_ADD_SEALS', 'F_GET_SEALS', 'F_SEAL_SEAL', 'F_SEAL_SHRINK',
                 'F_SEAL_GROW', 'F_SEAL_WRITE')
        values = {name: getattr(fcntl, name) for name in names}
        flags = (os.MFD_CLOEXEC, os.MFD_ALLOW_SEALING)
        create, control = os.memfd_create, fcntl.fcntl
    except AttributeError:
        raise KeyringError('CAP_KEYRING_SEAL_REJECTED') from None
    require(all(type(value) is int and value > 0 for value in (*values.values(), *flags)) and
            callable(create) and callable(control), 'CAP_KEYRING_SEAL_REJECTED')
    return {'create': create, 'control': control,
            'add': values['F_ADD_SEALS'], 'get': values['F_GET_SEALS'],
            'seals': values['F_SEAL_SEAL'] | values['F_SEAL_SHRINK'] |
                     values['F_SEAL_GROW'] | values['F_SEAL_WRITE'],
            'flags': flags[0] | flags[1]}


def _identity(info, directory=False):
    value = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
             'device': info.st_dev, 'inode': info.st_ino}
    if not directory:
        value.update(size=info.st_size, nlink=info.st_nlink,
                     mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
    return value


def _private_directory(fd):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            info.st_gid == os.getgid() and stat.S_IMODE(info.st_mode) == 0o700,
            'CAP_KEYRING_PATH_REJECTED')
    return _identity(info, True)


def _root(root):
    """Walk each component with no-follow directory fds; never resolve aliases."""
    raw = os.fspath(root)
    require(type(raw) is str and raw.startswith('/') and raw != '/' and
            raw == os.path.normpath(raw) and not raw.startswith('//') and
            all(part not in ('', '.', '..') for part in raw.split('/')[1:]),
            'CAP_KEYRING_PATH_REJECTED')
    current = -1
    try:
        current = os.open('/', DIRECTORY_FLAGS)
        for part in raw.split('/')[1:]:
            next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = next_fd
        identity = _private_directory(current)
        return current, identity
    except OSError:
        if current >= 0:
            os.close(current)
        raise KeyringError('CAP_KEYRING_PATH_REJECTED') from None
    except BaseException:
        if current >= 0:
            os.close(current)
        raise


def _read_file(directory, name):
    fd = -1
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=directory)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and
                before.st_gid == os.getgid() and stat.S_IMODE(before.st_mode) == 0o600 and
                before.st_nlink == 1 and before.st_size == SIZE,
                'CAP_KEYRING_PATH_REJECTED')
        raw = os.pread(fd, SIZE + 1, 0)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        require(_identity(before) == _identity(after) == _identity(named) and
                stat.S_ISREG(named.st_mode), 'CAP_KEYRING_STATE_CHANGED')
        require(len(raw) == SIZE and hashlib.sha256(raw).hexdigest() == SHA256,
                'CAP_KEYRING_SOURCE_REJECTED')
        return _identity(before), raw
    except OSError:
        raise KeyringError('CAP_KEYRING_PATH_REJECTED') from None
    finally:
        if fd >= 0:
            os.close(fd)


def _read(root, relative):
    require(relative in (SOURCE_PATH, PRIVATE_PATH), 'CAP_KEYRING_PATH_REJECTED')
    root_fd, root_identity = _root(root)
    directory = -1
    try:
        parent, name = relative.split('/')
        directory = os.open(parent, DIRECTORY_FLAGS, dir_fd=root_fd)
        directory_identity = _private_directory(directory)
        file_identity, raw = _read_file(directory, name)
        # A retained fd prevents traversal redirection; these checks also reject
        # replacement of any named directory during the bounded read.
        require(_identity(os.stat(parent, dir_fd=root_fd, follow_symlinks=False), True) ==
                directory_identity and _private_directory(root_fd) == root_identity,
                'CAP_KEYRING_STATE_CHANGED')
        current, current_identity = _root(root)
        os.close(current)
        require(current_identity == root_identity, 'CAP_KEYRING_STATE_CHANGED')
        return root_identity, directory_identity, file_identity, raw
    except OSError:
        raise KeyringError('CAP_KEYRING_PATH_REJECTED') from None
    finally:
        if directory >= 0:
            os.close(directory)
        os.close(root_fd)


def _manifest(root, root_identity, directory_identity, file_identity):
    return {'schema': 1, 'kind': 'public-keyring-data', 'protocol': PROTOCOL,
            'root': os.fspath(root), 'source_path': SOURCE_PATH, 'path': PRIVATE_PATH,
            'sha256': SHA256, 'size': SIZE, 'signer': SIGNER,
            'root_identity': root_identity, 'directory_identity': directory_identity,
            'file_identity': file_identity}


def _same_manifest(value, expected):
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(_same_manifest(value[key], item)
                                                  for key, item in expected.items())
    return value == expected


def verify_manifest(root, manifest):
    """Recheck private data without creating a verifier or inherited descriptor."""
    source_root, unused_directory, unused_file, unused = _read(root, SOURCE_PATH)
    root_identity, directory_identity, file_identity, raw = _read(root, PRIVATE_PATH)
    expected = _manifest(root, root_identity, directory_identity, file_identity)
    require(_same_manifest(manifest, expected) and source_root == root_identity,
            'CAP_KEYRING_STATE_CHANGED')
    return raw


def _write_all(fd, raw):
    done = 0
    while done < len(raw):
        count = os.write(fd, raw[done:])
        require(count > 0, 'CAP_KEYRING_STATE_CHANGED')
        done += count


class HeldKeyring:
    """A single sealed, close-on-exec descriptor, valid only for exact gpgv argv."""
    def __init__(self, root, manifest, raw):
        self.root, self.manifest, self.fd, self.identity = root, manifest, -1, None
        require(type(raw) is bytes and len(raw) == SIZE and
                hashlib.sha256(raw).hexdigest() == SHA256, 'CAP_KEYRING_SOURCE_REJECTED')
        api = seal_capabilities()
        try:
            self.fd = api['create']('capability-public-keyring', api['flags'])
            require(self.fd > 2, 'CAP_KEYRING_FD_REJECTED')
            _write_all(self.fd, raw)
            api['control'](self.fd, api['add'], api['seals'])
            info = os.fstat(self.fd)
            self.identity = (info.st_dev, info.st_ino, info.st_uid, info.st_gid)
            self.check_fd()
        except (OSError, AttributeError):
            self.close()
            raise KeyringError('CAP_KEYRING_SEAL_REJECTED') from None
        except BaseException:
            self.close()
            raise

    def check_fd(self):
        require(type(self.fd) is int and self.fd > 2 and self.identity is not None,
                'CAP_KEYRING_FD_REJECTED')
        api = seal_capabilities()
        try:
            info = os.fstat(self.fd)
            require((info.st_dev, info.st_ino, info.st_uid, info.st_gid) == self.identity and
                    stat.S_ISREG(info.st_mode) and info.st_size == SIZE and
                    not os.get_inheritable(self.fd), 'CAP_KEYRING_FD_REJECTED')
            require(api['control'](self.fd, api['get']) == api['seals'],
                    'CAP_KEYRING_SEAL_REJECTED')
            raw = os.pread(self.fd, SIZE + 1, 0)
            require(len(raw) == SIZE and hashlib.sha256(raw).hexdigest() == SHA256,
                    'CAP_KEYRING_SOURCE_REJECTED')
            os.lseek(self.fd, 0, os.SEEK_SET)
        except OSError:
            raise KeyringError('CAP_KEYRING_FD_REJECTED') from None
        return self.fd

    def command(self, suite):
        require(type(suite) is str and suite in SUITES, 'CAP_KEYRING_COMMAND_REJECTED')
        argv = ['/usr/bin/gpgv', '--status-fd=1', '--keyring', '/proc/self/fd/' + str(self.fd),
                str(Path(self.root) / 'metadata' / ('snapshot-' + SNAPSHOT) / (suite + '.InRelease'))]
        return argv, self.command_fds(argv)

    def command_fds(self, argv):
        expected_releases = {str(Path(self.root) / 'metadata' / ('snapshot-' + SNAPSHOT) /
                                 (suite + '.InRelease')) for suite in SUITES}
        require(type(argv) is list and len(argv) == 5 and
                all(type(value) is str for value in argv) and
                argv[:3] == ['/usr/bin/gpgv', '--status-fd=1', '--keyring'] and
                re.fullmatch(r'/proc/self/fd/(?:[3-9]|[1-9][0-9]+)', argv[3]) and
                argv[3] == '/proc/self/fd/' + str(self.fd) and argv[4] in expected_releases,
                'CAP_KEYRING_COMMAND_REJECTED')
        verify_manifest(self.root, self.manifest)
        return (self.check_fd(),)

    def close(self):
        fd, self.fd, self.identity = self.fd, -1, None
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                # Descriptor replacement is detected before consumption. Close
                # must never leak a raw OS message or obscure an earlier error.
                pass

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


def create(root):
    """Exclusively create one private copy, then hold exact bytes in a memfd."""
    seal_capabilities()
    root = os.fspath(root)
    root_identity, unused_directory, unused_file, raw = _read(root, SOURCE_PATH)
    root_fd, current_identity = _root(root)
    directory, output = -1, -1
    try:
        require(current_identity == root_identity, 'CAP_KEYRING_STATE_CHANGED')
        os.mkdir(PRIVATE_DIRECTORY, 0o700, dir_fd=root_fd)
        directory = os.open(PRIVATE_DIRECTORY, DIRECTORY_FLAGS, dir_fd=root_fd)
        _private_directory(directory)
        output = os.open('ubuntu-archive-keyring.gpg',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
        _write_all(output, raw)
        os.close(output)
        output = -1
    except OSError:
        raise KeyringError('CAP_KEYRING_PATH_REJECTED') from None
    finally:
        if output >= 0:
            os.close(output)
        if directory >= 0:
            os.close(directory)
        os.close(root_fd)
    checked_root, directory_identity, file_identity, checked = _read(root, PRIVATE_PATH)
    require(checked_root == root_identity and checked == raw, 'CAP_KEYRING_STATE_CHANGED')
    manifest = _manifest(root, checked_root, directory_identity, file_identity)
    verify_manifest(root, manifest)
    return HeldKeyring(root, manifest, raw)
