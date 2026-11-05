#!/usr/bin/env python3
"""Reviewed same-runner preparer. Explicit future-runner entrypoint; no build here."""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import platform
import re
import sys

SOURCE_REQUIRED = {
    'official-source-inputs.json', 'build-requirements.lock', 'recipe/build-wheel-inputs.json',
    'recipe/prepare_live.py', 'recipe/live_inputs.py', 'recipe/runtime_prepare.py',
    'recipe/prepare_jammy_packages.py', 'recipe/guardlib.py', 'recipe/build_guard.py',
    'recipe/build_offline.sh', 'recipe/verify_offline_inputs.py', 'recipe/run_build.py',
}

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def source_anchor(root, expected):
    """No local helper import before this independent reviewed source anchor."""
    def need(value, message):
        if not value:
            raise RuntimeError(message)
    def unique(pairs):
        value = {}
        for key, entry in pairs:
            need(key not in value, 'Duplicate source-index key')
            value[key] = entry
        return value
    need(re.fullmatch('[0-9a-f]{64}', expected), 'Invalid source review digest')
    index_path = root / 'source-index.json'
    need(not index_path.is_symlink(), 'Source index symlink refused')
    raw = index_path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == expected, 'Reviewed source index anchor mismatch')
    index = json.loads(raw, object_pairs_hook=unique)
    need(index.get('schema') == 1 and index.get('status') == 'reviewed-source', 'Invalid reviewed source index')
    need(SOURCE_REQUIRED.issubset(index['files']), 'Missing reviewed preparer/source input')
    for name, entry in index['files'].items():
        need(isinstance(name, str) and not name.startswith('/') and all(x not in ('', '.', '..') for x in name.split('/')), 'Unsafe source-index path')
        path = root
        for component in name.split('/'):
            path /= component
            need(not path.is_symlink() and not path.stat().st_mode & 0o022, 'Writable/symlink source input')
        need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], 'Reviewed source input changed: ' + name)
    return index

def measure_scope(g, root, prefix, name, path, host=False):
    live = sys.modules['live_inputs']
    allowed = root if not host else (prefix if path.is_relative_to(prefix) else pathlib.Path('/'))
    return {'name': name, 'ref': live.path_ref(path, root, prefix), 'host_aliases': host,
            'entries': g.snapshot(path, allowed, host_aliases=host)}

def prepare(root, python, package_lock, source_digest):
    index = source_anchor(root, source_digest)
    g = load('guardlib', root / 'recipe/guardlib.py')
    g.root_guard(root)
    g.require(sys.version_info[:3] == (3, 12, 13), 'Preparation requires exactly CPython 3.12.13')
    g.require(pathlib.Path(sys.executable) == python and sys.platform == 'linux' and platform.machine() == 'x86_64', 'Unexpected preparation interpreter/platform')
    release = dict(line.split('=', 1) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in line)
    g.require(release.get('ID', '').strip('"') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '22.04', 'Preparation requires actual Ubuntu22.04')
    for name in ('output', 'artifact-index.json', 'reviewed-inputs.lock.json', 'prepared-anchor.json', 'sources', 'provider', 'interface', 'preparation-evidence'):
        g.require(not (root / name).exists() and not (root / name).is_symlink(), 'Prepared state already exists: ' + name)
    g.require(package_lock.is_relative_to(root) and not package_lock.is_symlink(), 'Package lock must be candidate-local')
    live = load('live_inputs', root / 'recipe/live_inputs.py')
    runtime = load('runtime_prepare', root / 'recipe/runtime_prepare.py')
    prefix = pathlib.Path(sys.base_prefix).resolve()
    packages = g.read_json(package_lock)
    g.require(packages.get('distribution') == 'ubuntu' and packages.get('release') == '22.04' and packages.get('installed') is True, 'Package preparation did not verify installation')
    g.require(packages.get('signed_metadata_verified') is True and packages.get('resolved'), 'Package authentication/closure incomplete')
    if packages['snapshot']['kind'] == 'ubuntu-snapshot':
        packages['snapshot']['kind'] = 'immutable-signed-snapshot'
    for row in packages['signed_metadata']:
        g.verify_candidate_file(root, prefix, row)
    for row in packages['resolved']:
        g.verify_candidate_file(root, prefix, row['archive'])
    g.require(packages.get('closure_complete') is True, 'Package lock is transaction-only, not complete relevant dependency closure')
    (root / 'preparation-evidence').mkdir(mode=0o700)
    source_rows = g.read_json(root / 'official-source-inputs.json')['sources']
    expected_names = {'fields2cover', 'steering_functions', 'matplot', 'json', 'ortools-jammy-headers', 'ortools-python-runtime'}
    g.require(len(source_rows) == len(expected_names) and {row['name'] for row in source_rows} == expected_names, 'Unexpected source reference set')
    archives, source_locks = {}, {}
    for row in source_rows:
        name = row['name']
        filename = row.get('filename', name + '.tar.gz')
        g.relative_path(filename)
        g.require('/' not in filename, 'Source archive filename must be a basename')
        destination = root / 'inputs/archives' / filename
        live.fetch(row, destination)
        archives[name] = destination
        if name in ('fields2cover', 'steering_functions', 'matplot', 'json'):
            live.tar_data_only(destination, root / 'sources' / name)
            source_locks[name] = {'archive': live.file_row(destination, root, prefix), 'commit': row['commit'], 'source_url': row['url']}
    build_rows = g.read_json(root / 'recipe/build-wheel-inputs.json')['wheels']
    requirement_text = (root / 'build-requirements.lock').read_text()
    g.require(len(build_rows) == 9, 'Build wheel reference set changed')
    build_wheels = []
    for row in build_rows:
        g.require(row['sha256'] in requirement_text and row['name'] + '==' + row['version'] in requirement_text, 'Build wheel does not match reviewed requirements')
        g.relative_path(row['filename'])
        g.require('/' not in row['filename'], 'Build wheel filename must be a basename')
        destination = root / 'inputs/wheels' / row['filename']
        live.fetch(row, destination)
        build_wheels.append(live.file_row(destination, root, prefix))
    headers, provider = root / 'interface/upstream', root / 'provider'
    live.tar_data_only(archives['ortools-jammy-headers'], headers, interface_only=True)
    live.wheel_data_only(archives['ortools-python-runtime'], provider)
    shim, system_libraries, runtime_evidence = runtime.prepare_runtime(root, prefix, provider, headers)
    command_paths = {'gcc': '/usr/bin/gcc-11', 'gxx': '/usr/bin/g++-11', 'ld': '/usr/bin/ld',
                     'as': '/usr/bin/as', 'ar': '/usr/bin/ar', 'ranlib': '/usr/bin/ranlib',
                     'strip': '/usr/bin/strip', 'readelf': '/usr/bin/readelf', 'pkg_config': '/usr/bin/pkg-config',
                     'gdal_config': '/usr/bin/gdal-config', 'bash': '/usr/bin/bash', 'env': '/usr/bin/env',
                     'unshare': '/usr/bin/unshare'}
    commands = {name: live.file_row(pathlib.Path(path), root, prefix) for name, path in command_paths.items()}
    package_archives = [g.resolve_ref(root, prefix, row['archive']['ref']) for row in packages['resolved']]
    system_paths = [g.resolve_ref(root, prefix, row['ref']) for row in system_libraries]
    byte_proof = live.verify_system_archive_bytes(package_archives, [*map(pathlib.Path, command_paths.values()), *system_paths])
    byte_evidence = root / 'preparation-evidence/system-provider-bytes.json'
    live.write_json_new(byte_evidence, {'schema': 1, 'all_selected_commands_and_system_providers_match': True, 'files': byte_proof})
    cmake_roots = sorted(pathlib.Path('/usr/share').glob('cmake-*'))
    g.require(cmake_roots, 'Missing measured system CMake modules')
    host_paths = [('python-prefix', prefix), ('system-headers', pathlib.Path('/usr/include')),
                  ('compiler-support', pathlib.Path('/usr/lib/gcc')), ('system-libraries', pathlib.Path('/usr/lib/x86_64-linux-gnu')),
                  ('system-executables', pathlib.Path('/usr/bin')), ('installed-package-metadata', pathlib.Path('/var/lib/dpkg')),
                  ('system-cmake', cmake_roots[0]), ('system-pkgconfig', pathlib.Path('/usr/share/pkgconfig')),
                  ('eigen-cmake', pathlib.Path('/usr/share/eigen3'))]
    host_paths.extend(('extra-system-cmake-' + str(i), path) for i, path in enumerate(cmake_roots[1:]))
    host_trees = [measure_scope(g, root, prefix, name, path, host=True) for name, path in host_paths]
    configuration = [pathlib.Path(p) for p in ('/etc/ld.so.cache', '/etc/ld.so.conf', '/usr/lib/os-release')]
    configuration.extend(sorted(pathlib.Path('/etc/ld.so.conf.d').glob('*')))
    configuration.extend(path for path in (pathlib.Path('/usr/lib/libarmadillo.so'),) if path.exists())
    host_files = [live.file_row(path, root, prefix) for path in configuration if path.is_file()]
    host_files += system_libraries
    input_paths = [(name, root / 'sources' / name) for name in ('fields2cover', 'steering_functions', 'matplot', 'json')]
    input_paths += [('ortools-interface', root / 'interface'), ('ortools-wheel-extracted', provider),
                    ('build-wheelhouse', root / 'inputs/wheels'), ('input-archives', root / 'inputs/archives')]
    evidence = [live.file_row(runtime_evidence, root, prefix), live.file_row(byte_evidence, root, prefix), live.file_row(package_lock, root, prefix)]
    for name in ('base_inventory', 'post_inventory', 'transaction'):
        if packages.get(name):
            g.verify_candidate_file(root, prefix, packages[name])
            evidence.append(packages[name])
    lock = {
        'schema': 1, 'status': 'reviewed-complete',
        'preparation': {'mode': 'measured-by-externally-reviewed-preparer', 'source_index_sha256': source_digest,
                        'structural_abi_proven': False, 'same_live_runner_required': True},
        'review': dict.fromkeys(('host_closure_complete', 'input_closure_complete', 'package_provenance_verified',
                                'interface_metadata_reviewed', 'single_wheel_runtime_provider', 'dependency_link_flags_reviewed'), True),
        'host': {'os': {'id': 'ubuntu', 'version': '22.04', 'architecture': 'x86_64'},
                 'python': {'version': '3.12.13', 'prefix': str(prefix), 'executable': live.file_row(python, root, prefix)},
                 'os_release': live.file_row(pathlib.Path('/etc/os-release'), root, prefix),
                 'commands': commands, 'trees': host_trees, 'files': host_files},
        'network': {'kernel_boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    'netns': os.readlink('/proc/self/ns/net')},
        'packages': packages,
        'inputs': {'trees': [measure_scope(g, root, prefix, name, path) for name, path in input_paths],
                   'sources': source_locks, 'build_requirements': live.file_row(root / 'build-requirements.lock', root, prefix),
                   'build_wheels': build_wheels},
        'interface': {'archive': live.file_row(archives['ortools-jammy-headers'], root, prefix),
                      'cmake_ref': live.path_ref(shim, root, prefix)},
        'runtime': {'provider': 'ortools-python-wheel-only', 'wheel': live.file_row(archives['ortools-python-runtime'], root, prefix),
                    'extracted_ref': live.path_ref(provider, root, prefix),
                    'system_sonames': {'gdal': 'libgdal.so.30', 'tinyxml2': 'libtinyxml2.so.9'},
                    'system_libraries': system_libraries},
        'build': {'source_date_epoch': 1788454736, 'parallel_jobs': 2, 'minimum_free_bytes': 4294967296},
    }
    lock['review']['evidence'] = evidence
    # Validate installed versions, exact source identities, closures and layout
    # before issuing a measured anchor. This preflight does not import natives.
    verifier = load('offline_verifier', root / 'recipe/verify_offline_inputs.py')
    verifier.verify_prepared(root, str(python), lock, g)
    live.write_json_new(root / 'reviewed-inputs.lock.json', lock)
    files = dict(index['files'])
    files['source-index.json'] = {'sha256': source_digest}
    files['reviewed-inputs.lock.json'] = {'sha256': g.sha256(root / 'reviewed-inputs.lock.json')}
    live.write_json_new(root / 'artifact-index.json', {'schema': 1, 'status': 'reviewed-complete', 'files': files})
    measured = g.sha256(root / 'artifact-index.json')
    g.verify_index(root, measured)
    live.write_json_new(root / 'prepared-anchor.json', {'schema': 1, 'source_index_sha256': source_digest,
                        'artifact_index_sha256': measured, 'same_live_runner_required': True,
                        'native_build_executed': False, 'structural_abi_proven': False})
    return {'preparation_complete': True, 'artifact_index_sha256': measured, 'native_build_executed': False}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=pathlib.Path, required=True)
    p.add_argument('--python', type=pathlib.Path, required=True)
    p.add_argument('--package-lock', type=pathlib.Path, required=True)
    p.add_argument('--expected-source-index-sha256', required=True)
    args = p.parse_args()
    os.umask(0o077)
    print(json.dumps(prepare(args.root, args.python, args.package_lock, args.expected_source_index_sha256)))

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        # Caller redirects this private diagnostic stream. Public workflow emits
        # only its sanitized stage/status summary, never raw host paths or logs.
        print('Preparation refused: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
