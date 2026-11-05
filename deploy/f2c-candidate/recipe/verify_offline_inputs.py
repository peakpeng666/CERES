#!/usr/bin/env python3
"""Anchored read-only preflight, then optional explicitly selected offline build."""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import platform
import re
import sys
import zipfile

F2C_SHA = '756ed60234dd9b0b1ba19d3cf4f8e1ade18744754bba548c3742b2c30a37e640'
ORTOOLS_SHA = '033836c0eb33bc72697a299e0caedbb25fc9d1cee0b13832d69cb30405f57b3e'
INTERFACE_SHA = '0b30114d7c05f0596286bf3ef8d02adcf5f45be3b39273490e6bb74a2a9bd1ea'

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def anchored_import(root, anchor):
    # This also protects direct read-only invocation before importing local code.
    if not re.fullmatch('[0-9a-f]{64}', anchor):
        raise RuntimeError('Invalid external anchor')
    raw = (root / 'artifact-index.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != anchor:
        raise RuntimeError('Artifact index anchor mismatch')
    index = json.loads(raw)
    for name in ('guardlib', 'build_guard', 'run_build'):
        relative = 'recipe/' + name + '.py'
        path = root / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != index['files'][relative]['sha256']:
            raise RuntimeError('Helper anchor mismatch')
    return load('guardlib', root / 'recipe/guardlib.py')

def verify_prepared(root, python, lock, g):
    """No subprocesses, package resolution, downloads, or native imports."""
    g.require_complete(lock)
    host = lock['host']
    prefix = pathlib.Path(host['python']['prefix'])
    g.require(prefix.is_absolute() and prefix == prefix.resolve(), 'Python prefix must be canonical and absolute')
    g.require(host['python']['version'] == '3.12.13', 'Require exactly CPython 3.12.13')
    g.require(host['os'] == {'id': 'ubuntu', 'version': '22.04', 'architecture': 'x86_64'}, 'Not an Ubuntu Jammy x64 lock')
    executable = g.verify_file(root, prefix, host['python']['executable'])
    g.require(executable == pathlib.Path(python), 'Selected Python differs from reviewed executable')
    g.require(sys.version_info[:3] == (3, 12, 13), 'Interpreter patch version differs')
    g.require(pathlib.Path(sys.executable).resolve() == executable.resolve(), 'Verifier running with wrong Python')
    g.require(platform.machine() == 'x86_64' and sys.platform == 'linux', 'Wrong host architecture/OS')
    os_release = g.verify_file(root, prefix, host['os_release'])
    g.require(os_release == pathlib.Path('/etc/os-release'), 'Must verify actual OS release file')
    values = dict(line.split('=', 1) for line in os_release.read_text().splitlines() if '=' in line)
    g.require(values.get('ID', '').strip('"') == 'ubuntu' and values.get('VERSION_ID', '').strip('"') == '22.04', 'Actual host is not Jammy')
    commands = host['commands']
    g.require(g.REQUIRED_COMMANDS.issubset(commands), 'Incomplete host command lock')
    resolved_commands = {name: g.verify_file(root, prefix, entry) for name, entry in commands.items()}
    expected = {'gcc': '/usr/bin/gcc-11', 'gxx': '/usr/bin/g++-11', 'bash': '/usr/bin/bash', 'env': '/usr/bin/env', 'unshare': '/usr/bin/unshare'}
    for name, path in expected.items():
        g.require(resolved_commands[name] == pathlib.Path(path), 'Wrong measured command: ' + name)
    g.verify_trees(root, prefix, host['trees'], g.REQUIRED_HOST_SCOPES, candidate=False)
    expected_scopes = {'python-prefix': prefix, 'system-headers': pathlib.Path('/usr/include'),
                       'compiler-support': pathlib.Path('/usr/lib/gcc'),
                       'system-libraries': pathlib.Path('/usr/lib/x86_64-linux-gnu'),
                       'system-executables': pathlib.Path('/usr/bin'),
                       'installed-package-metadata': pathlib.Path('/var/lib/dpkg')}
    for scope in host['trees']:
        if scope['name'] in expected_scopes:
            g.require(g.resolve_ref(root, prefix, scope['ref']) == expected_scopes[scope['name']], 'Mislabelled host scope')
    for entry in host['files']:
        g.verify_file(root, prefix, entry)
    g.require(host['files'], 'Missing loader/configuration closure files')
    package = lock['packages']
    g.require(package['distribution'] == 'ubuntu' and package['release'] == '22.04', 'Foreign package closure')
    g.require(package['signed_metadata_verified'] is True and package['signed_metadata'], 'Missing authenticated package provenance')
    for entry in package['signed_metadata']:
        g.verify_candidate_file(root, prefix, entry)
    rows = package['resolved']
    names = [row['name'] for row in rows]
    required_packages = set(g.REQUIRED_PACKAGES)
    aliases = package.get('requested_root_provider', {})
    if aliases:
        g.require(aliases == {'pkg-config': 'pkgconf'} and package.get('provider_compatibility_verified') is True,
                  'Unreviewed package-root provider substitution')
        required_packages.remove('pkg-config')
        required_packages.add('pkgconf')
    g.require(len(names) == len(set(names)) and required_packages.issubset(names), 'Incomplete package closure')
    g.require(package.get('closure_complete') is True, 'Package provenance covers only transaction delta')
    installed = {}
    for paragraph in pathlib.Path('/var/lib/dpkg/status').read_text().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith((' ', '\t')))
        if fields.get('Status', '').endswith(' ok installed'):
            installed[(fields.get('Package'), fields.get('Architecture'))] = fields.get('Version')
    for row in rows:
        g.require(row['version'] and row['version'] != 'TODO' and row['suite'] in ('jammy', 'jammy-updates', 'jammy-security'), 'Unresolved or foreign package pin')
        g.require(row['architecture'] in ('amd64', 'all'), 'Unexpected package architecture')
        g.require(installed.get((row['name'], row['architecture'])) == row['version'], 'Installed package version differs: ' + row['name'])
        g.verify_candidate_file(root, prefix, row['archive'])
    g.require(package['snapshot']['kind'] in ('immutable-signed-snapshot', 'measured-live-repository'), 'Declare snapshot reproducibility scope')
    g.require(package['snapshot']['identifier'], 'Missing measured repository identity')
    inputs = lock['inputs']
    g.verify_trees(root, prefix, inputs['trees'], g.REQUIRED_INPUT_SCOPES, candidate=True)
    scope_refs = {row['name']: row['ref'] for row in inputs['trees']}
    sources = inputs['sources']
    g.require(set(sources) == {'fields2cover', 'steering_functions', 'matplot', 'json'}, 'Incomplete upstream source closure')
    for row in sources.values():
        g.verify_candidate_file(root, prefix, row['archive'])
        g.require(row['commit'] and row['source_url'].startswith('https://'), 'Missing source provenance')
    g.require(sources['fields2cover']['archive']['sha256'] == F2C_SHA and sources['fields2cover']['commit'] == '011e4159cd633ee3adf82122dd030153b94da0fa', 'Changed F2C source identity')
    requirement = g.verify_candidate_file(root, prefix, inputs['build_requirements'])
    # Pip's --require-hashes validates every resolved wheel again at installation.
    g.require('--hash=sha256:' in requirement.read_text(), 'Unhashed build requirements')
    g.require(inputs['build_wheels'], 'Missing complete build wheelhouse')
    for row in inputs['build_wheels']:
        g.verify_candidate_file(root, prefix, row)
    runtime = lock['runtime']
    g.require(scope_refs['ortools-wheel-extracted'] == runtime['extracted_ref'], 'Provider extraction is not exact-tree locked')
    g.require(runtime['provider'] == 'ortools-python-wheel-only', 'Multiple/foreign OR-Tools provider')
    wheel = g.verify_candidate_file(root, prefix, runtime['wheel'])
    g.require(runtime['wheel']['sha256'] == ORTOOLS_SHA, 'Wrong OR-Tools runtime wheel')
    g.require(runtime['system_sonames']['gdal'] == 'libgdal.so.30' and runtime['system_sonames']['tinyxml2'] == 'libtinyxml2.so.9', 'Wrong Jammy native SONAME contract')
    g.require(runtime['system_libraries'], 'Missing native system/provider closure')
    for entry in runtime['system_libraries']:
        g.require(entry['ref']['base'] == 'system', 'System library must not be bundled')
        g.verify_file(root, prefix, entry)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        g.require(len(names) == len(set(names)), 'Duplicate wheel members')
        g.require(any(x.startswith('ortools/.libs/') for x in names) and any(x.startswith('ortools.libs/') for x in names), 'Provider wheel layout changed')
        g.require('ortools/.libs/libz.so.1' in names, 'Reviewed wheel-owned zlib provider missing')
        extraction = g.resolve_ref(root, prefix, runtime['extracted_ref'])
        g.require(runtime['extracted_ref']['base'] == 'candidate', 'Provider extraction must be candidate-local')
        for name in names:
            g.relative_path(name.rstrip('/'))
            if name.endswith('/'):
                continue
            member = g.regular_under(extraction, name)
            g.require(g.sha256(member) == hashlib.sha256(archive.read(name)).hexdigest(), 'Extracted provider wheel differs')
    interface = lock['interface']
    interface_path = g.resolve_ref(root, prefix, interface['cmake_ref'])
    interface_tree = g.resolve_ref(root, prefix, scope_refs['ortools-interface'])
    g.require(interface_path.is_relative_to(interface_tree) and interface_path.is_dir(), 'CMake interface is outside its exact-tree lock')
    g.verify_candidate_file(root, prefix, interface['archive'])
    g.require(interface['archive']['sha256'] == INTERFACE_SHA, 'Wrong Ubuntu22.04 interface archive')
    for evidence in lock['review']['evidence']:
        g.verify_candidate_file(root, prefix, evidence)
    g.require(interface['cmake_ref']['base'] == 'candidate', 'Interface must be candidate-local')
    build = lock['build']
    g.require(build['source_date_epoch'] == 1788454736 and build['parallel_jobs'] == 2, 'Unreviewed build settings')
    g.require(build['minimum_free_bytes'] >= 4 * 1024**3, 'Missing conservative disk floor')
    return prefix, resolved_commands

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--root', type=pathlib.Path, required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--expected-index-sha256', required=True)
    args = parser.parse_args()
    g = anchored_import(args.root, args.expected_index_sha256)
    root = g.root_guard(args.root)
    g.verify_index(root, args.expected_index_sha256)
    lock = g.read_json(root / 'reviewed-inputs.lock.json')
    g.require_complete(lock)
    guard = load('build_guard', root / 'recipe/build_guard.py')
    network = guard.network_guard(lock['network'])
    g.fresh_outputs(root)
    prefix, commands = verify_prepared(root, args.python, lock, g)
    # Verification can take time: recheck isolation, freshness, and anchor before writes.
    guard.network_guard(lock['network'])
    g.verify_index(root, args.expected_index_sha256)
    g.fresh_outputs(root)
    print(json.dumps({'prepared_inputs_verified': True, 'build_executed': False, **network}))
    if args.execute:
        runner = load('run_build', root / 'recipe/run_build.py')
        runner.run(root, pathlib.Path(args.python), prefix, commands, lock, args.expected_index_sha256)

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:
        print('Refusing: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
