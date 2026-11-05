"""Portable, standard-library-only checks. No provisioning or native execution."""
import hashlib
import json
import os
import pathlib
import re
import stat

REQUIRED_INDEX = {
    'recipe/build_offline.sh', 'recipe/guardlib.py', 'recipe/build_guard.py',
    'recipe/verify_offline_inputs.py', 'recipe/run_build.py', 'reviewed-inputs.lock.json',
}
OUTPUTS = ('build-venv', 'build', 'wheels', 'home', 'tmp', 'cache', 'evidence')
REQUIRED_PACKAGES = {
    'gcc-11', 'g++-11', 'binutils', 'pkg-config', 'libgdal-dev', 'libgeos-dev',
    'libtinyxml2-dev', 'libeigen3-dev', 'libboost-dev', 'libtbb-dev',
    'zlib1g-dev', 'libbz2-dev', 'ca-certificates',
}
REQUIRED_COMMANDS = {
    'gcc', 'gxx', 'ld', 'as', 'ar', 'ranlib', 'strip', 'readelf',
    'pkg_config', 'gdal_config', 'bash', 'env', 'unshare',
}
REQUIRED_HOST_SCOPES = {
    'python-prefix', 'system-headers', 'compiler-support', 'system-libraries',
    'system-cmake', 'system-pkgconfig', 'system-executables', 'installed-package-metadata',
}
REQUIRED_INPUT_SCOPES = {
    'fields2cover', 'steering_functions', 'matplot', 'json', 'ortools-interface',
    'ortools-wheel-extracted', 'build-wheelhouse', 'input-archives',
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
        require(sha256(regular_under(root, name)) == digest(entry['sha256']), 'Indexed artifact mismatch: ' + name)
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

def fresh_outputs(root, create=False):
    """One fresh output parent; never resume, remove, overwrite, or auto-retry."""
    root = root_guard(root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.stat('output', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError('Output already exists; select a separately reviewed fresh candidate root')
        if create:
            os.mkdir('output', 0o700, dir_fd=fd)
            out = os.open('output', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                seen = set()
                for name in OUTPUTS:
                    os.mkdir(name, 0o700, dir_fd=out)
                    st = os.stat(name, dir_fd=out, follow_symlinks=False)
                    key = (st.st_dev, st.st_ino)
                    require(st.st_uid == os.geteuid() and st.st_dev == os.fstat(fd).st_dev and key not in seen, 'Output alias/owner/filesystem mismatch')
                    seen.add(key)
            finally:
                os.close(out)
    finally:
        os.close(fd)

def require_complete(lock):
    require(lock.get('schema') == 1 and lock.get('status') == 'reviewed-complete', 'Jammy preparation incomplete')
    for section in ('host', 'packages', 'inputs', 'runtime', 'interface', 'network', 'build'):
        require(isinstance(lock.get(section), dict) and lock[section], 'Missing prepared section: ' + section)
    approvals = lock.get('review', {})
    for flag in ('host_closure_complete', 'input_closure_complete', 'package_provenance_verified',
                 'interface_metadata_reviewed', 'single_wheel_runtime_provider', 'dependency_link_flags_reviewed'):
        require(approvals.get(flag) is True, 'Missing independent review: ' + flag)
    require(isinstance(approvals.get('evidence'), list) and approvals['evidence'], 'Missing review evidence')
