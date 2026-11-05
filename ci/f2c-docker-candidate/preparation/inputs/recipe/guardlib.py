"""Portable, standard-library-only checks. No provisioning or native execution."""
import hashlib
import json
import os
import pathlib
import re
import stat

REQUIRED_INDEX = {
    'recipe/prepare_inputs.py', 'recipe/guardlib.py', 'recipe/live_inputs.py',
    'recipe/runtime_prepare.py', 'recipe/verify_prepared_inputs.py', 'reviewed-inputs.lock.json',
}
OUTPUTS = ('build-venv', 'build', 'wheels', 'home', 'tmp', 'cache', 'evidence')
REQUIRED_PACKAGES = {
    'gcc-11', 'g++-11', 'binutils', 'pkg-config', 'libgdal-dev', 'libgeos-dev',
    'libtinyxml2-dev', 'libeigen3-dev', 'libboost-dev', 'libtbb-dev',
    'zlib1g-dev', 'libbz2-dev', 'ca-certificates',
}
REQUIRED_COMMANDS = {
    'gcc', 'gxx', 'ld', 'as', 'ar', 'ranlib', 'strip', 'readelf',
    'pkg_config', 'gdal_config', 'bash', 'env', 'dpkg_deb',
}
REQUIRED_HOST_SCOPES = {
    'python-prefix', 'system-headers', 'compiler-support', 'system-libraries',
    'system-cmake', 'system-pkgconfig', 'system-executables', 'installed-package-metadata',
    'uv-prefix',
}
REQUIRED_INPUT_SCOPES = {
    'fields2cover', 'steering_functions', 'matplot', 'json', 'ortools-interface',
    'ortools-wheel-extracted', 'build-wheelhouse', 'input-archives', 'application',
}

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)

def digest(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'Invalid SHA256')
    return value

def relative_path(value):
    require(isinstance(value, str) and value and not value.startswith('/'), 'Expected relative path')
    require(all(x not in ('', '.', '..') for x in value.split('/')), 'Unsafe relative path')
    require('\x00' not in value, 'NUL in path')
    return pathlib.PurePosixPath(value)

def regular_under(root, relative):
    relative_path(relative)
    p = root
    for part in relative.split('/'):
        p = p / part
        require(not p.is_symlink(), 'Symlink in indexed path: ' + relative)
        require(not p.stat().st_mode & 0o022, 'Group/world writable indexed path: ' + relative)
    require(p.is_file(), 'Missing regular file: ' + relative)
    return p

def verify_index_entry(root, relative, entry):
    require(isinstance(entry, dict) and set(entry) == {'sha256'}, 'Only regular indexed compiler inputs are allowed')
    path = regular_under(root, relative)
    require(sha256(path) == digest(entry['sha256']), 'Indexed artifact mismatch: ' + relative)
    return path


def root_guard(root):
    root = pathlib.Path(root)
    require(root.is_absolute() and root == pathlib.Path(os.path.abspath(root)), 'Root must be canonical/absolute')
    require(not any(c.isspace() or c in ';,$' for c in str(root)), 'Root contains unsupported CMake/linker path delimiters')
    for p in (root, *root.parents):
        require(not p.is_symlink(), 'Symlink root/ancestor')
    st = root.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid(), 'Root must be caller-owned directory')
    require(not st.st_mode & 0o022, 'Root is group/world writable')
    return root

def verify_index(root, expected):
    root = root_guard(root)
    path = regular_under(root, 'artifact-index.json')
    require(sha256(path) == digest(expected), 'Artifact index anchor mismatch')
    index = read_json(path)
    require(index.get('schema') == 1 and index.get('status') == 'reviewed-complete', 'Incomplete index')
    files = index.get('files', {})
    require(isinstance(files, dict) and REQUIRED_INDEX.issubset(files), 'Missing indexed helper/lock')
    for name, entry in files.items():
        verify_index_entry(root, name, entry)
    return index

def resolve_ref(root, python_prefix, ref):
    """Paths use three explicit bases, never environment expansion or shell text."""
    require(isinstance(ref, dict) and set(ref) == {'base', 'path'}, 'Invalid path reference')
    bases = {'candidate': root, 'python': python_prefix, 'system': pathlib.Path('/')}
    require(ref['base'] in bases, 'Unknown path base')
    if ref['path'] == '.' and ref['base'] == 'python':
        return python_prefix
    return bases[ref['base']] / relative_path(ref['path'])

def snapshot(root, allowed_root, host_aliases=False):
    """Exact membership, bytes, modes and symlink targets; no exclusions."""
    root, allowed_root = pathlib.Path(root), pathlib.Path(allowed_root).resolve()
    require(root.is_dir() and not root.is_symlink(), 'Tree root must be a real directory')
    require(root.resolve() == root and root.is_relative_to(allowed_root), 'Tree root has symlink ancestry or escapes allowed root')
    rows = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            p = pathlib.Path(base) / name
            st = p.lstat()
            row = {'path': p.relative_to(root).as_posix(), 'mode': stat.S_IMODE(st.st_mode)}
            if stat.S_ISLNK(st.st_mode):
                target = p.resolve(strict=not host_aliases)
                require(target.is_relative_to(allowed_root), 'Symlink escapes allowed root')
                row.update(kind='symlink', target=os.readlink(p), resolved=target.relative_to(allowed_root).as_posix())
                if target.is_file():
                    row['referent_sha256'] = sha256(target)
                elif not target.exists() and host_aliases:
                    row['dangling'] = True
                else:
                    require(target.is_dir(), 'Special symlink referent')
            elif stat.S_ISREG(st.st_mode):
                require(st.st_nlink == 1 or host_aliases, 'Hard-linked input is not immutable in isolation')
                row.update(kind='regular', sha256=sha256(p))
                if host_aliases:
                    row['link_count'] = st.st_nlink
            elif stat.S_ISDIR(st.st_mode):
                row['kind'] = 'directory'
            else:
                raise RuntimeError('Special file in input tree')
            require(not st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode), 'Group/world writable tree entry')
            rows.append(row)
    return sorted(rows, key=lambda x: x['path'])

def verify_file(root, prefix, entry):
    path = resolve_ref(root, prefix, entry['ref'])
    require(path.is_file(), 'Missing locked file: ' + str(path))
    require(sha256(path) == digest(entry['sha256']), 'Changed locked file: ' + str(path))
    alias = os.readlink(path) if path.is_symlink() else None
    require(alias == entry.get('symlink_target'), 'Changed file alias: ' + str(path))
    resolved = resolve_ref(root, prefix, entry['resolved_ref'])
    require(path.resolve(strict=True) == resolved, 'Changed file resolution: ' + str(path))
    require(not path.stat().st_mode & 0o022, 'Writable locked file: ' + str(path))
    return path

def verify_candidate_file(root, prefix, entry):
    require(entry['ref']['base'] == 'candidate' and entry['resolved_ref']['base'] == 'candidate', 'Input must be candidate-local')
    return verify_file(root, prefix, entry)

def verify_trees(root, prefix, scopes, required, candidate):
    require(isinstance(scopes, list), 'Tree scopes missing')
    names = [s['name'] for s in scopes]
    require(len(names) == len(set(names)) and required.issubset(names), 'Missing/duplicate closure scope')
    for scope in scopes:
        ref = scope['ref']
        require((ref['base'] == 'candidate') == candidate, 'Wrong closure base')
        path = resolve_ref(root, prefix, ref)
        allowed = root if candidate else (prefix if ref['base'] == 'python' else pathlib.Path('/'))
        require('exclude_parts' not in scope and 'excludes' not in scope, 'Closure exclusions forbidden')
        require(isinstance(scope.get('entries'), list) and scope['entries'], 'Empty/unprepared tree lock')
        require(scope.get('host_aliases', False) is (not candidate), 'Host alias policy must be explicit and host-only')
        require(snapshot(path, allowed, host_aliases=not candidate) == scope['entries'], 'Tree membership/content changed: ' + scope['name'])

PROTOCOL = 'urp.f2c.docker-engine-netnone/1'
APPLICATION_COMMIT = '3acf840d1a0c16ab1cfcca15744e4ea6609ddb35'
APPLICATION_FILES = {'application/src/lab/pyproject.toml', 'application/src/lab/uv.lock'}
PYTHON_PREFIX = pathlib.Path('/opt/urp-python')
PYTHON_EXECUTABLE = PYTHON_PREFIX / 'bin/python3.12'
LAYOUT = {'inputs': '/inputs', 'control': '/control', 'output': '/out',
          'build': '/out/build', 'python_prefix': str(PYTHON_PREFIX),
          'inputs_readonly': True, 'control_readonly': True,
          'image_root_readonly': True, 'toolchain_residence': 'image'}
NETWORK = {'protocol': PROTOCOL, 'verification': 'outside-engine-and-live-kernel',
           'offline_execution_required': True, 'prepared_namespace_reuse': False}


def require_docker_layout(lock):
    require(lock.get('application_commit') == APPLICATION_COMMIT, 'Wrong application source revision')
    require(lock.get('layout') == LAYOUT, 'Wrong Docker input/control/output layout')
    require(lock.get('network') == NETWORK, 'Wrong Docker isolation protocol')
    preparation = lock.get('preparation', {})
    require(preparation.get('validation_inputs_prepared') is False and preparation.get('scope') == 'compiler-build-only', 'Compiler-only preparation required')
    require(preparation.get('same_live_runner_required') is False, 'Legacy same-runner preparation prohibited')
    require(preparation.get('image_environment_required') is True, 'Measured image environment required')
    require(preparation.get('structural_abi_proven') is False, 'Preparation cannot establish native ABI')
    require(preparation.get('native_build_executed') is False, 'Input preparation cannot build native sources')
    require(lock['host']['python']['prefix'] == str(PYTHON_PREFIX), 'Python must remain inside image')


def fresh_outputs(root=pathlib.Path('/out'), create=False):
    """Independent output mount; /inputs and /control are never written here.

    The engine protocol proves readonly mounts, image identity and kernel state.
    This helper only proves caller ownership and a fresh /out/build directory.
    """
    require(pathlib.Path(root) == pathlib.Path('/out'), 'Output must be /out')
    root = root_guard(root)
    target = root / 'build'
    require(not target.exists() and not target.is_symlink(), 'Build output already exists; use a fresh container')
    if create:
        target.mkdir(mode=0o700)
        for name in OUTPUTS:
            (target / name).mkdir(mode=0o700)
    return target

def require_complete(lock):
    require(lock.get('schema') == 1 and lock.get('status') == 'reviewed-complete', 'Docker image input preparation incomplete')
    for section in ('host', 'packages', 'inputs', 'runtime', 'interface', 'network', 'build', 'bootstrap'):
        require(isinstance(lock.get(section), dict) and lock[section], 'Missing prepared section: ' + section)
    approvals = lock.get('review', {})
    for flag in ('host_closure_complete', 'input_closure_complete', 'package_provenance_verified',
                 'interface_metadata_reviewed', 'single_wheel_runtime_provider', 'dependency_link_flags_reviewed'):
        require(approvals.get(flag) is True, 'Missing independent review: ' + flag)
    require(isinstance(approvals.get('evidence'), list) and approvals['evidence'], 'Missing review evidence')


BOOTSTRAP_PATHS = {'python': '/opt/urp-python/bin/python3.12', 'uv': '/opt/urp-uv/uv'}
BOOTSTRAP_PROVENANCE = {
    'python': {'kind': 'uv-pinned-download',
               'archive_sha256': '506191be3ee7bd190a8834dcdc1b3bc70aab50608deccc711935aa007239cabd',
               'key': 'cpython-3.12.13-linux-x86_64-gnu'},
    'uv': {'kind': 'pinned-github-action',
           'action': 'astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4'},
}


def verify_bootstrap(receipt):
    """Data-only receipt/byte checks; never execute UV or another Python.

    Action provenance is explicit bootstrap trust, NOT Ubuntu .deb provenance.
    The trusted root stage must authenticate and measure this receipt first.
    """
    require(receipt.get('schema') == 1 and set(receipt.get('tools', {})) == set(BOOTSTRAP_PATHS),
            'Incomplete bootstrap tool receipt')
    for name, expected in BOOTSTRAP_PATHS.items():
        row = receipt['tools'][name]
        require(set(row) == {'version', 'path', 'sha256', 'provenance'}, 'Unexpected bootstrap receipt fields')
        require(row['path'] == expected and row['provenance'] == BOOTSTRAP_PROVENANCE[name],
                'Wrong bootstrap tool path/provenance: ' + name)
        require(row['version'] == {'python': '3.12.13', 'uv': '0.12.23'}[name], 'Wrong bootstrap tool version')
        path = pathlib.Path(expected)
        for part in (path, *path.parents):
            require(not part.is_symlink(), 'Aliased bootstrap executable path')
        require(path.is_file() and sha256(path) == digest(row['sha256']), 'Bootstrap executable bytes changed: ' + name)
        require(not path.stat().st_mode & 0o022, 'Writable bootstrap executable')
    return receipt
