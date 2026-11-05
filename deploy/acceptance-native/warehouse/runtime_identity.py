"""Bounded, observational installed metadata; never a loaded-library attestation.

The fixed ROS share paths follow the retained Humble TB4/Create3 source trees
(a6ee13b6, 0ea4a60c, 7d7a4f69) and exact application 3acf840d. No discovery,
package changes, native simulation, sockets, devices, process maps or environment
inspection is performed. File hashes describe installed bytes, not loaded code.

Rendering6 OgreRenderEngine.cc (ignition-rendering6_6.6.3, lines 327-339)
uses common::env(IGN_HOMEDIR, ...). On Linux that *compile-time macro* is HOME,
not the environment key IGN_HOMEDIR. The runner HOME is private_dir.parent/home.
Only its exact .ignition/rendering/ogre.log is inspected. A new log is associated
with this invocation, never attributed to a particular process. Preexisting logs
are unusable even if replaced later. Ogre v1-9-0 OgreGLSupport.cpp lines 50-71 and v1.12.10
OgreGLRenderSystem.cpp lines 3247-3258 define the GL_VERSION / GL_VENDOR /
GL_RENDERER markers. Older Ogre logs the full GL version string; newer Ogre may
log only its parsed version. This projection retains only the API number, never
a Mesa package-version inference. No unverified Ogre banner is parsed.

Source definitions (not proof of the installed/loaded implementation):
https://raw.githubusercontent.com/gazebosim/gz-rendering/ignition-rendering6_6.6.3/ogre/src/OgreRenderEngine.cc
https://gazebosim.org/api/common/4/include_2gz_2common_2Util_8hh.html
https://github.com/OGRECave/ogre/blob/v1.12.10/RenderSystems/GL/src/OgreGLRenderSystem.cpp
https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-23.2.1/src/gallium/drivers/llvmpipe/lp_screen.c
https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-23.2.1/src/gallium/drivers/softpipe/sp_screen.c
"""
import errno
import hashlib
import os
from pathlib import Path
import re
import selectors
import stat
import sys
import subprocess
import time

PACKAGES = (
    'libgl1-mesa-dri', 'libglx-mesa0', 'libegl-mesa0',
    'libignition-gazebo6', 'libignition-gazebo6-plugins',
    'libignition-rendering6', 'libignition-rendering6-ogre1',
    'libignition-rendering6-ogre2', 'libogre-1.9.0v5', 'libogrenextmain2.2.5',
    'libignition-sensors6', 'libignition-sensors6-rendering',
    'libignition-sensors6-gpu-lidar', 'libignition-sensors6-rgbd-camera',
    'libignition-sensors6-camera', 'libignition-sensors6-depth-camera',
    'ros-humble-irobot-create-description', 'ros-humble-turtlebot4-description',
    'ros-humble-turtlebot4-ignition-bringup', 'ros-humble-ros-gz-sim',
    'ros-humble-ros-gz-bridge',
)
_SHARE = Path('/opt/ros/humble/share')
FILES = (
    ('tb4_standard_xacro', 'turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro'),
    ('tb4_rplidar_xacro', 'turtlebot4_description/urdf/sensors/rplidar.urdf.xacro'),
    ('tb4_oakd_xacro', 'turtlebot4_description/urdf/sensors/oakd.urdf.xacro'),
    ('create3_xacro', 'irobot_create_description/urdf/create3.urdf.xacro'),
    ('create3_properties_xacro', 'irobot_create_description/urdf/common_properties.urdf.xacro'),
    ('create3_cliff_xacro', 'irobot_create_description/urdf/sensors/cliff_sensor.urdf.xacro'),
    ('create3_ir_intensity_xacro', 'irobot_create_description/urdf/sensors/ir_intensity.urdf.xacro'),
    ('create3_ir_opcode_xacro', 'irobot_create_description/urdf/sensors/ir_opcode_receivers.urdf.xacro'),
    ('tb4_description_launch', 'turtlebot4_description/launch/robot_description.launch.py'),
    ('tb4_ignition_launch', 'turtlebot4_ignition_bringup/launch/turtlebot4_ignition.launch.py'),
    ('tb4_spawn_launch', 'turtlebot4_ignition_bringup/launch/turtlebot4_spawn.launch.py'),
    ('tb4_ignition_engine_launch', 'turtlebot4_ignition_bringup/launch/ignition.launch.py'),
    ('tb4_bridge_launch', 'turtlebot4_ignition_bringup/launch/ros_ign_bridge.launch.py'),
    ('warehouse_world', 'turtlebot4_ignition_bringup/worlds/warehouse.sdf'),
)
FILE_IDS = tuple(row[0] for row in FILES)
PACKAGE_BYTES = 65536
FILE_BYTES = 262144
LOG_BYTES = 131072
QUERY_SECONDS = 3.0
_FORMAT = '${Package}\t${Architecture}\t${Version}\t${db:Status-Status}\t${source:Package}\t${source:Version}\n'
QUERY_ARGV = ('/usr/bin/dpkg-query', '--admindir=/var/lib/dpkg', '-W', '-f=' + _FORMAT) + PACKAGES
_ARCHES = frozenset(('amd64', 'arm64', 'all', 'i386', 'armhf', 'ppc64el', 's390x', 'riscv64'))
_SOURCES = frozenset(PACKAGES + ('mesa', 'ignition-gazebo6', 'ignition-rendering6',
    'ignition-sensors6', 'ogre-1.9', 'ogre-next', 'ogre-next-2.2'))
_VERSION = re.compile(r'(?:[0-9]{1,10}:)?[0-9][A-Za-z0-9.+~\-]{0,95}\Z', re.ASCII)
_API_VERSION = re.compile(r'[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}\Z', re.ASCII)
_DB_STATUS = frozenset(('not-installed', 'config-files', 'half-installed', 'unpacked',
                       'half-configured', 'triggers-awaited', 'triggers-pending', 'installed'))
_READ_STATUS = frozenset(('available', 'missing', 'permission_denied', 'symlink',
    'not_regular', 'parent_not_directory', 'owner_mismatch', 'changed', 'too_large',
    'read_error', 'invalid_path', 'unavailable'))
_PACKAGE_STATUS = frozenset(('available', 'missing', 'not_installed', 'invalid', 'conflicting', 'unavailable'))
_QUERY_STATUS = frozenset(('complete', 'partial', 'invalid', 'unavailable', 'timeout', 'output_limit', 'not_collected'))
_LOG_STATUS = _READ_STATUS | frozenset(('not_collected', 'preexisting', 'preflight_failed',
    'markers_missing', 'partial', 'invalid', 'conflicting', 'truncated'))
_VENDORS = frozenset(('mesa', 'unknown', 'unavailable'))
_RENDERERS = frozenset(('llvmpipe', 'softpipe', 'unknown', 'unavailable'))


def _enum(value, allowed, default):
    return value if type(value) is str and len(value) <= 128 and value in allowed else default


def _version(value):
    if type(value) is not str or len(value) > 96 or _VERSION.fullmatch(value) is None:
        return None
    if '-' in value and re.fullmatch(r'[A-Za-z0-9.+~]{1,64}', value.rsplit('-', 1)[1], re.ASCII) is None:
        return None
    return value


def _integer(value, maximum):
    return value if type(value) is int and 0 <= value <= maximum else None


def _rows(raw, ids, maximum):
    """Only examine a bounded list; duplicates never silently select a winner."""
    if type(raw) is not list or len(raw) > maximum:
        return {}, False
    found = {}
    for row in raw:
        if type(row) is not dict or type(row.get('id')) is not str or row['id'] not in ids:
            continue
        key = row['id']
        found[key] = row if key not in found else {'id': key, 'status': 'conflicting'}
    return found, True


def sanitize(raw):
    """Closed, bounded and idempotent public projection. Never echo exceptions."""
    raw = raw if type(raw) is dict else {}
    packages = raw.get('package_database')
    packages = packages if type(packages) is dict else {}
    rows, bounded = _rows(packages.get('packages'), PACKAGES, 24)
    package_rows = []
    for name in PACKAGES:
        row = rows.get(name, {})
        status = _enum(row.get('status'), _PACKAGE_STATUS, 'unavailable')
        arch = _enum(row.get('architecture'), _ARCHES, None)
        version = _version(row.get('version'))
        if status == 'available' and (arch is None or version is None):
            status = 'invalid'
        source = _enum(row.get('source_package'), _SOURCES, None)
        source_version = _version(row.get('source_version'))
        source_status = 'available' if status == 'available' and source and source_version else 'unavailable'
        package_rows.append(dict(id=name, status=status,
            architecture=arch if status == 'available' else None,
            version=version if status == 'available' else None,
            source_status=source_status,
            source_package=source if source_status == 'available' else None,
            source_version=source_version if source_status == 'available' else None))
    files = raw.get('installed_file_bytes')
    files = files if type(files) is dict else {}
    file_rows, files_bounded = _rows(files.get('files'), FILE_IDS, 16)
    public_files = []
    for name in FILE_IDS:
        row = file_rows.get(name, {})
        status = _enum(row.get('status'), _READ_STATUS | {'conflicting'}, 'unavailable')
        size = _integer(row.get('size_bytes'), FILE_BYTES)
        digest = row.get('sha256')
        digest = digest if type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) else None
        if status == 'available' and (size is None or digest is None):
            status = 'unavailable'
        public_files.append(dict(id=name, status=status,
            size_bytes=size if status == 'available' else None,
            sha256=digest if status == 'available' else None))
    log = raw.get('renderer_log_claim')
    log = log if type(log) is dict else {}
    log_status = _enum(log.get('status'), _LOG_STATUS, 'unavailable')
    attributed = _enum(log.get('attribution'), {'invocation_associated_not_pid_bound'}, None) is not None
    if log.get('truncated') is True and log_status in ('available', 'partial'):
        log_status = 'truncated'
    trusted = attributed and log_status in ('available', 'partial') and log.get('truncated') is False
    api = log.get('gl_api_version')
    api = api if trusted and type(api) is str and _API_VERSION.fullmatch(api) else None
    vendor = _enum(log.get('vendor_family'), _VENDORS, 'unavailable') if trusted else 'unavailable'
    renderer = _enum(log.get('renderer_family'), _RENDERERS, 'unavailable') if trusted else 'unavailable'
    if not attributed and log_status in ('available', 'partial'):
        log_status = 'unavailable'
    package_status = _enum(packages.get('status'), _QUERY_STATUS, 'unavailable') if bounded else 'unavailable'
    if package_status in ('complete', 'partial'):
        if any(row['status'] in ('invalid', 'conflicting') for row in package_rows):
            package_status = 'invalid'
        elif any(row['status'] != 'available' for row in package_rows):
            package_status = 'partial'
    file_status = _enum(files.get('status'), {'complete', 'partial', 'unavailable', 'not_collected'}, 'unavailable') if files_bounded else 'unavailable'
    if file_status == 'complete' and any(row['status'] != 'available' for row in public_files):
        file_status = 'partial'
    if log_status == 'available' and (api is None or vendor == 'unavailable' or renderer == 'unavailable'):
        log_status = 'partial' if trusted else 'unavailable'
    return dict(schema_version=1,
        policy='fixed_installed_metadata_and_fresh_ogre_log_v1',
        query_cleanup_was_uncertain=raw.get('query_cleanup_was_uncertain') is True,
        query_cleanup_status=_enum(raw.get('query_cleanup_status'), {'not_started', 'not_observed', 'pending', 'reaped', 'uncertain'}, 'not_observed'),
        status=_enum(raw.get('status'), {'not_started', 'collected', 'complete', 'unavailable'}, 'unavailable'),
        package_database=dict(status=package_status,
            provenance='installed_dpkg_database_not_loaded_libraries', packages=package_rows),
        installed_file_bytes=dict(status=file_status,
            provenance='installed_file_bytes_not_loaded_files', files=public_files),
        renderer_log_claim=dict(status=log_status,
            attribution='invocation_associated_not_pid_bound' if attributed else 'unavailable',
            truncated=log.get('truncated') is True,
            gl_api_version=api, vendor_family=vendor, renderer_family=renderer,
            mesa_version='unavailable', ogre_version='unavailable'),
        loaded_library_identity=dict(status='unavailable', reason='not_observed'))


UNAVAILABLE = sanitize({'status': 'unavailable'})


class QueryCleanupUncertain(RuntimeError):
    """The owned metadata child could not be confirmed exited/reaped in time."""


def _bounded_capture(argv, *, timeout=QUERY_SECONDS, max_bytes=PACKAGE_BYTES,
                     popen=None, selector_factory=None, clock=None,
                     on_child=None, on_reaped=None):
    """No communicate(): stdout is capped while the single child is running.

    Callbacks retain the actual owned handle, never a recycled PID. An uncertain
    cleanup aborts preflight; cancellation identity always outranks this condition.
    """
    popen = subprocess.Popen if popen is None else popen
    selector_factory = selectors.DefaultSelector if selector_factory is None else selector_factory
    clock = time.monotonic if clock is None else clock
    proc = None
    selector = None
    error = None
    result = None
    confirmed_reaped = False

    def poll():
        nonlocal confirmed_reaped
        code = proc.poll()
        if type(code) is int:
            confirmed_reaped = True  # Popen.poll performs a nonblocking waitpid.
        return code

    try:
        proc = popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                     cwd='/', env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        if on_child is not None:
            on_child(proc)
        selector = selector_factory()
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = clock() + timeout
        data = bytearray()
        eof = False
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                result = {'status': 'timeout', 'stdout': b'', 'returncode': None}
                break
            if not eof:
                for key, _ in selector.select(min(0.05, remaining)):
                    try:
                        chunk = os.read(key.fileobj.fileno(), min(4096, max_bytes + 1 - len(data)))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        eof = True
                        selector.unregister(proc.stdout)
                        break
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        result = {'status': 'output_limit', 'stdout': b'', 'returncode': None}
                        break
                if result is not None:
                    break
            code = poll()
            if eof and type(code) is int:
                result = {'status': 'complete' if code in (0, 1) else 'unavailable',
                          'stdout': bytes(data) if code in (0, 1) else b'', 'returncode': code}
                break
            if eof:
                try:
                    code = proc.wait(timeout=min(0.05, remaining))
                    confirmed_reaped = confirmed_reaped or type(code) is int
                except subprocess.TimeoutExpired:
                    pass
    except BaseException as exc:
        error = exc
    if proc is None:
        raise error
    # Each cleanup operation is attempted even if an earlier operation failed.
    try:
        if poll() is None:
            proc.kill()
    except BaseException as exc:
        error = _prefer_error(error, exc)
    try:
        code = proc.wait(timeout=1.0)
        confirmed_reaped = confirmed_reaped or type(code) is int
    except BaseException as exc:
        error = _prefer_error(error, exc)
    for action in (lambda: selector.close() if selector is not None else None,
                   lambda: proc.stdout.close()):
        try:
            action()
        except BaseException as exc:
            error = _prefer_error(error, exc)
    if confirmed_reaped and on_reaped is not None:
        try:
            on_reaped(proc)
        except BaseException as exc:
            error = _prefer_error(error, exc)
    if not confirmed_reaped and (error is None or not _is_cancellation(error)):
        raise QueryCleanupUncertain('metadata_query_cleanup_uncertain') from None
    if error is not None:
        raise error
    return result


def _package_metadata(runner):
    try:
        result = runner(QUERY_ARGV, timeout=QUERY_SECONDS, max_bytes=PACKAGE_BYTES)
    except OSError:
        return {'status': 'unavailable', 'packages': []}
    if type(result) is not dict:
        return {'status': 'invalid', 'packages': []}
    status = _enum(result.get('status'), _QUERY_STATUS, 'unavailable')
    if status != 'complete':
        return {'status': status, 'packages': []}
    data = result.get('stdout')
    if type(data) is not bytes:
        return {'status': 'invalid', 'packages': []}
    if len(data) > PACKAGE_BYTES:
        return {'status': 'output_limit', 'packages': []}
    try:
        lines = data.decode('ascii').splitlines()
    except UnicodeDecodeError:
        return {'status': 'invalid', 'packages': []}
    if len(lines) > 24:
        return {'status': 'invalid', 'packages': []}
    found = {}
    bad = False
    for line in lines:
        fields = line.split('\t')
        name = fields[0]
        if name not in PACKAGES:
            bad = True
            continue
        if name in found:
            found[name] = {'id': name, 'status': 'conflicting'}
            bad = True
            continue
        row = {'id': name, 'status': 'invalid'}
        if len(fields) == 6:
            _, arch, version, db_status, source, source_version = fields
            if arch in _ARCHES and _version(version) and db_status in _DB_STATUS:
                row.update(status='available' if db_status == 'installed' else 'not_installed',
                    architecture=arch, version=version, source_package=source,
                    source_version=source_version)
        bad = bad or row['status'] == 'invalid'
        found[name] = row
    rows = [found.get(name, {'id': name, 'status': 'missing'}) for name in PACKAGES]
    return {'status': 'invalid' if bad else ('complete' if all(r['status'] == 'available' for r in rows) else 'partial'),
            'packages': rows}


def _is_cancellation(exc):
    # Keep exactly the same contract as startup_observer.is_cancellation.
    return not isinstance(exc, Exception) or (type(exc) is RuntimeError and exc.args == ('interrupted',))


def _prefer_error(primary, new):
    return new if primary is None or (_is_cancellation(new) and not _is_cancellation(primary)) else primary


def _close_fd(fd):
    primary = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as exc:
        if primary is None or (_is_cancellation(exc) and not _is_cancellation(primary)):
            raise


class _ReadFailure(Exception):
    def __init__(self, status):
        self.status = status


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode


def _stable(info):
    return _identity(info) + (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _os_status(exc):
    if exc.errno == errno.ENOENT:
        return 'missing'
    if exc.errno in (errno.EACCES, errno.EPERM):
        return 'permission_denied'
    if exc.errno == errno.ELOOP:
        return 'symlink'
    if exc.errno == errno.ENOTDIR:
        return 'parent_not_directory'
    return 'read_error'


class _Chain:
    """Anchored openat traversal, rejecting symlinks at every component."""
    def __init__(self, path, *, owned_below=None, record=None):
        path = Path(path)
        if not path.is_absolute() or '..' in path.parts or len(path.parts) > 64:
            raise _ReadFailure('invalid_path')
        self.path = path
        self.record = record
        self.fds = []
        self.links = []
        self.owned_below = Path(owned_below) if owned_below is not None else None

    def _check_owner(self, info, path):
        required = self.owned_below is not None and (path == self.owned_below or self.owned_below in path.parents)
        if info.st_uid not in ({os.geteuid()} if required else {0, os.geteuid()}):
            raise _ReadFailure('owner_mismatch')

    def __enter__(self):
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open('/', flags)
            self.fds.append(fd)
            root = os.fstat(fd)
            self._check_owner(root, Path('/'))
            self.root_identity = _identity(root)
            current = Path('/')
            for component in self.path.parts[1:-1]:
                current /= component
                before = os.stat(component, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode):
                    raise _ReadFailure('symlink')
                if not stat.S_ISDIR(before.st_mode):
                    raise _ReadFailure('parent_not_directory')
                self._check_owner(before, current)
                parent = fd
                fd = os.open(component, flags, dir_fd=parent)
                self.fds.append(fd)
                after = os.fstat(fd)
                if _identity(before) != _identity(after):
                    raise _ReadFailure('changed')
                self.links.append((parent, component, fd, _identity(after)))
                if self.record is not None and self.owned_below is not None and (current == self.owned_below or self.owned_below in current.parents):
                    self.record.append((current, _identity(after)))
            return self
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def verify(self):
        if _identity(os.stat('/', follow_symlinks=False)) != self.root_identity:
            raise _ReadFailure('changed')
        for parent, name, fd, identity in self.links:
            if (_identity(os.fstat(fd)) != identity or
                    _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity):
                raise _ReadFailure('changed')

    def __exit__(self, exc_type, exc, traceback):
        error = None
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except BaseException as failure:
                error = _prefer_error(error, failure)
        self.fds = []
        if error is not None and (exc is None or (_is_cancellation(error) and not _is_cancellation(exc))):
            raise error


def _probe(path, *, owned_below=None, record=None):
    try:
        with _Chain(path, owned_below=owned_below, record=record) as chain:
            try:
                info = os.stat(chain.path.name, dir_fd=chain.fds[-1], follow_symlinks=False)
            except FileNotFoundError:
                chain.verify()
                return 'missing'
            chain.verify()
            if stat.S_ISLNK(info.st_mode):
                return 'symlink'
            if not stat.S_ISREG(info.st_mode):
                return 'not_regular'
            chain._check_owner(info, chain.path)
            return 'preexisting'
    except _ReadFailure as exc:
        return exc.status
    except OSError as exc:
        return _os_status(exc)


def _read(path, *, maximum, head_tail=False, owned_below=None):
    try:
        with _Chain(path, owned_below=owned_below) as chain:
            parent = chain.fds[-1]
            before = os.stat(chain.path.name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise _ReadFailure('symlink')
            if not stat.S_ISREG(before.st_mode):
                raise _ReadFailure('not_regular')
            chain._check_owner(before, chain.path)
            if before.st_size > maximum and not head_tail:
                raise _ReadFailure('too_large')
            fd = os.open(chain.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            try:
                opened = os.fstat(fd)
                if _stable(before) != _stable(opened):
                    raise _ReadFailure('changed')
                if not stat.S_ISREG(opened.st_mode):
                    raise _ReadFailure('not_regular')
                truncated = opened.st_size > maximum
                regions = ((0, maximum // 2), (opened.st_size - maximum // 2, maximum // 2)) if truncated else ((0, opened.st_size),)
                parts = []
                for offset, length in regions:
                    data = bytearray()
                    while len(data) < length:
                        piece = os.pread(fd, min(16384, length - len(data)), offset + len(data))
                        if not piece:
                            raise _ReadFailure('changed')
                        data.extend(piece)
                    parts.append(bytes(data))
                after = os.fstat(fd)
                final = os.stat(chain.path.name, dir_fd=parent, follow_symlinks=False)
                chain.verify()
                if _stable(opened) != _stable(after) or _stable(opened) != _stable(final):
                    raise _ReadFailure('changed')
                return {'status': 'truncated' if truncated else 'available',
                        'size_bytes': opened.st_size, 'data': b''.join(parts)}
            finally:
                _close_fd(fd)
    except _ReadFailure as exc:
        return {'status': exc.status}
    except OSError as exc:
        return {'status': _os_status(exc)}


def _home_identity(home, *, owned_below=None):
    """The invocation HOME itself must exist and remain the same owned directory."""
    # Appending a fixed nonexistent child permits the same directory traversal;
    # this helper neither stats nor opens the synthetic leaf.
    with _Chain(home / '_identity_unused_leaf', owned_below=home if owned_below is None else owned_below) as chain:
        chain.verify()
        return _identity(os.fstat(chain.fds[-1]))


def _parse_log(data):
    if type(data) is not bytes or len(data) > LOG_BYTES:
        return {'status': 'invalid'}
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return {'status': 'invalid'}
    markers = {name: set() for name in ('VERSION', 'VENDOR', 'RENDERER')}
    invalid = False
    # Ogre records use LF or CRLF, not arbitrary Unicode/control separators.
    lines = text.split('\n')
    for index, line in enumerate(lines):
        if index < len(lines) - 1 and line.endswith('\r'):
            line = line[:-1]
        line = re.sub(r'^[0-9]{2}:[0-9]{2}:[0-9]{2}: ', '', line, count=1)
        for name in markers:
            prefix = 'GL_' + name + ' = '
            if line.startswith(prefix):
                value = line[len(prefix):]
                if not value or len(value) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                    invalid = True
                else:
                    markers[name].add(value)
    if any(len(values) > 1 for values in markers.values()):
        return {'status': 'conflicting'}
    if invalid:
        return {'status': 'invalid'}
    values = {key: next(iter(value)) if value else None for key, value in markers.items()}
    if not any(values.values()):
        return {'status': 'markers_missing'}
    api = None
    if values['VERSION'] is not None:
        match = re.match(r'^([0-9]{1,3}(?:\.[0-9]{1,3}){1,3})(?:\s|$)', values['VERSION'], re.ASCII)
        if match is None:
            return {'status': 'invalid'}
        api = match.group(1)
    vendor = 'unavailable' if values['VENDOR'] is None else ('mesa' if values['VENDOR'] == 'Mesa' else 'unknown')
    renderer = 'unavailable'
    if values['RENDERER'] is not None:
        # Mesa lp_screen.c (retained exact source) defines this precise format.
        # Do not guess other GPU families or accept arbitrary llvmpipe prefixes.
        # Mesa23.2.1 sp_screen.c:72–74 returns this exact literal.
        renderer = 'softpipe' if values['RENDERER'] == 'softpipe' else 'llvmpipe' if re.fullmatch(
            r'llvmpipe \(LLVM [0-9]{1,3}(?:\.[0-9]{1,3}){1,3}, [0-9]{1,4} bits\)',
            values['RENDERER'], re.ASCII) else 'unknown'
    return dict(status='available' if all(values.values()) else 'partial',
                gl_api_version=api, vendor_family=vendor, renderer_family=renderer)


class RuntimeIdentity:
    def __init__(self, private_dir, *, runner=None):
        self.private_dir = Path(private_dir)
        self.home = self.private_dir.parent / 'home'
        self.log = self.home / '.ignition/rendering/ogre.log'
        self.runner = runner
        self._query_child = None
        self._query_final_cleanup = False
        self._before = False
        self._after = False
        self._home = None
        self._ancestors = []
        self._preflight = 'unavailable'
        self.raw = {'status': 'not_started', 'query_cleanup_status': 'not_started',
                    'query_cleanup_was_uncertain': False}

    def _on_child(self, proc):
        self._query_child = proc
        self.raw['query_cleanup_status'] = 'pending'

    def _on_reaped(self, proc):
        if proc is self._query_child:
            self._query_child = None
            self.raw['query_cleanup_status'] = 'reaped'

    def _run_query(self, argv, *, timeout, max_bytes):
        if self.runner is not None:
            self.raw['query_cleanup_status'] = 'not_observed'
            return self.runner(argv, timeout=timeout, max_bytes=max_bytes)
        self.raw['query_cleanup_status'] = 'not_observed'
        try:
            return _bounded_capture(argv, timeout=timeout, max_bytes=max_bytes,
                                    on_child=self._on_child, on_reaped=self._on_reaped)
        finally:
            if self._query_child is not None:
                self.raw['query_cleanup_status'] = 'uncertain'
                self.raw['query_cleanup_was_uncertain'] = True

    def cleanup_query_child(self):
        """One final bounded kill/wait attempt, only on our retained child."""
        if self._query_child is None:
            return
        if self._query_final_cleanup:
            raise QueryCleanupUncertain('metadata_query_cleanup_uncertain')
        self._query_final_cleanup = True
        proc = self._query_child
        error = None
        confirmed = False
        try:
            code = proc.poll()
            confirmed = type(code) is int
            if code is None:
                proc.kill()
        except BaseException as exc:
            error = exc
        try:
            code = proc.wait(timeout=1.0)
            confirmed = confirmed or type(code) is int
        except BaseException as exc:
            error = _prefer_error(error, exc)
        if confirmed:
            self._on_reaped(proc)
        else:
            self.raw['query_cleanup_status'] = 'uncertain'
        if error is not None and _is_cancellation(error):
            raise error
        if not confirmed:
            raise QueryCleanupUncertain('metadata_query_cleanup_uncertain') from None
        if error is not None:
            raise error

    def before_gateway(self):
        if self._before or self._after:
            return
        self._before = True  # faults and cancellations cannot start a second query
        self.raw['status'] = 'unavailable'
        try:
            self._home = _home_identity(self.home)
            self._preflight = _probe(self.log, owned_below=self.home, record=self._ancestors)
        except _ReadFailure as exc:
            self._preflight = exc.status
        except OSError as exc:
            self._preflight = _os_status(exc)
        self.raw['renderer_log_claim'] = {'status': 'preexisting' if self._preflight == 'preexisting' else 'not_collected'}
        self.raw['package_database'] = _package_metadata(self._run_query)
        rows = []
        for name, relative in FILES:
            found = _read(_SHARE / relative, maximum=FILE_BYTES)
            row = {'id': name, 'status': found['status']}
            if found['status'] == 'available':
                row.update(size_bytes=found['size_bytes'], sha256=hashlib.sha256(found['data']).hexdigest())
            rows.append(row)
        self.raw['installed_file_bytes'] = {'status': 'complete' if all(r['status'] == 'available' for r in rows) else 'partial', 'files': rows}
        self.raw['status'] = 'collected'

    def after_cleanup(self):
        if self._after:
            return
        self._after = True
        if not self._before or self.raw.get('status') != 'collected':
            self.raw['renderer_log_claim'] = {'status': 'preflight_failed'}
            return
        if self._preflight != 'missing' or self._home is None:
            self.raw['renderer_log_claim'] = {'status': 'preexisting' if self._preflight == 'preexisting' else 'preflight_failed'}
            self.raw['status'] = 'complete'
            return
        try:
            if _home_identity(self.home) != self._home or any(_home_identity(path, owned_below=self.home) != identity for path, identity in self._ancestors):
                found = {'status': 'changed'}
            else:
                found = _read(self.log, maximum=LOG_BYTES, head_tail=True, owned_below=self.home)
                if _home_identity(self.home) != self._home or any(_home_identity(path, owned_below=self.home) != identity for path, identity in self._ancestors):
                    found = {'status': 'changed'}
        except _ReadFailure as exc:
            found = {'status': exc.status}
        except OSError as exc:
            found = {'status': _os_status(exc)}
        log = {'status': found['status'], 'truncated': found['status'] == 'truncated'}
        if found['status'] in ('available', 'truncated'):
            log['attribution'] = 'invocation_associated_not_pid_bound'
            if found['status'] == 'available':
                log.update(_parse_log(found['data']))
        self.raw['renderer_log_claim'] = log
        self.raw['status'] = 'complete'

    def result(self):
        return sanitize(self.raw)
