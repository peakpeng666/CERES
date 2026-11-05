#!/usr/bin/env python3
"""Freeze materialized official inputs and a new wheel, without loading native code."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

spec = importlib.util.spec_from_file_location('validation', Path(__file__).with_name('validate_jammy_candidate.py'))
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
SCOPES = ['src/lab', 'src/research', 'src/frontend/core', 'src/robotics', 'src/backend',
          'setup/2026-10-15/deployment', 'setup/2026-10-15/apt-sources', 'deploy', 'data/research/cvrp-selection-crc', 'data/packages']

def measured_system_runtime(root):
    runtime_evidence = root / 'preparation-evidence/runtime-interface.json'
    runtime = json.loads(runtime_evidence.read_text())
    system_providers = {}
    system_dirs = []
    for name, row in runtime['libraries'].items():
        if row['origin'] != 'system':
            continue
        file = row['file']
        v.require(file['ref']['base'] == 'system' and file['resolved_ref']['base'] == 'system', 'Non-system provider reference')
        path = Path('/') / file['ref']['path']; resolved = Path('/') / file['resolved_ref']['path']
        v.require(path.resolve(strict=True) == resolved and v.sha(path) == file['sha256'], 'Measured system provider drift')
        system_providers[name] = {'path': str(resolved), 'sha256': file['sha256']}
        for directory in [str(path.parent), str(resolved.parent)]:
            if directory not in system_dirs: system_dirs.append(directory)
    v.require(system_providers and system_dirs, 'Empty measured system provider closure')
    return system_providers, system_dirs, v.sha(runtime_evidence)

def freeze_libz_policy(root, deps):
    runtime = json.loads((root / 'preparation-evidence/runtime-interface.json').read_text())
    alternatives = runtime['provider_alternatives']
    v.require(set(alternatives) == {'libz.so.1'}, 'Only libz alternates permitted')
    row = alternatives['libz.so.1']
    v.require(row['policy'] == 'exactly-one-first-loaded-compatible-provider', 'Wrong alternate-provider policy')
    wheel = row['wheel']; system = row['system']
    v.require(wheel['ref']['base'] == 'candidate' and wheel['resolved_ref']['base'] == 'candidate', 'Non-wheel libz alternative')
    v.require(wheel['ref']['path'].endswith('/ortools/.libs/libz.so.1'), 'Unexpected wheel libz path')
    original = root / wheel['ref']['path']
    v.require(original.resolve(strict=True) == root / wheel['resolved_ref']['path'] and v.sha(original) == wheel['sha256'], 'Build wheel libz changed')
    v.require(v.sha(deps / 'ortools/.libs/libz.so.1') == wheel['sha256'], 'Validation wheel libz differs')
    v.require(system['ref']['base'] == 'system' and system['resolved_ref']['base'] == 'system', 'Non-system libz alternative')
    original_system = Path('/') / system['ref']['path']; resolved_system = Path('/') / system['resolved_ref']['path']
    v.require(original_system.resolve(strict=True) == resolved_system and v.sha(original_system) == system['sha256'], 'System libz changed')
    return {'libz.so.1': {'policy': row['policy'],
        'wheel': {'relative_path': 'ortools/.libs/libz.so.1', 'sha256': wheel['sha256']},
        'system': {'path': str(resolved_system), 'sha256': system['sha256']}}}

def freeze(a):
    source = a.source.resolve(strict=True); root = a.root.resolve(strict=True); data = a.data.resolve(strict=True)
    v.require(not a.config.exists() and not a.config.is_symlink(), 'Never overwrite config')
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    head = subprocess.check_output(['/usr/bin/git', '-C', str(source), 'rev-parse', 'HEAD'], env=env, text=True).strip()
    status = subprocess.check_output(['/usr/bin/git', '-C', str(source), 'status', '--porcelain'], env=env, text=True).strip()
    v.require(head == v.SOURCE_COMMIT and not status, 'Source must be exact clean application185 checkout')
    lock = json.loads((root / 'reviewed-inputs.lock.json').read_text())
    index = json.loads((root / 'artifact-index.json').read_text())
    v.require(index['status'] == 'reviewed-complete', 'Build inputs not frozen')
    build = json.loads((root / 'output/evidence/build-result.json').read_text())
    wheel = root / 'output/wheels' / build['wheel']
    v.require(v.sha(wheel) == build['wheel_sha256'], 'New wheel differs from build result')
    files = v.wheel_files(wheel); binary = hashlib.sha256(files[v.EXTENSION]).hexdigest()
    v.require(binary == build['extension_sha256'], 'Extension differs from build result')
    deps = data / 'dependency-site'
    v.require(not any(deps.glob('fields2cover*')), 'Historical or existing F2C prohibited')
    v.require(not list(deps.rglob('*.pth')) and not list(deps.rglob('sitecustomize.py')) and not list(deps.rglob('usercustomize.py')), 'Unreviewed interpreter startup hook')
    data_spec = importlib.util.spec_from_file_location('official_validation_data', Path(__file__).with_name('prepare_validation_data.py'))
    official = importlib.util.module_from_spec(data_spec); data_spec.loader.exec_module(official)
    receipt = json.loads((data / 'materialization.json').read_text())
    v.require(receipt['application_commit'] == head and receipt['lab_lock_sha256'] == v.sha(source / 'src/lab/uv.lock'), 'Wrong materialization source lock')
    v.require(official.check_tree(deps) == receipt['dependency_files'], 'Dependency materialization drift')
    for name, digest in official.MAPS.items():
        v.require(v.sha(data / 'maps' / name) == digest, 'Official map hash mismatch')
    for name, (size, digest) in official.DATA.items():
        p = data / 'runtime/library/fields2benchmark' / name
        v.require(p.stat().st_size == size and v.sha(p) == digest, 'Official dataset hash mismatch')
    tools = {}
    for name, path in [('python', a.python), ('node', a.node), ('readelf', Path('/usr/bin/readelf'))]:
        path = path.absolute(); tools[name] = {'path': str(path), 'resolved': str(path.resolve(strict=True)), 'sha256': v.sha(path)}
    trees = []
    for scope in SCOPES:
        p = source / scope
        trees.append({'source': str(p), 'allowed_root': str(source), 'destination': 'checkout/' + scope, 'entries': v.tree(p, source)})
    for scope, destination in [('dependency-site', 'dependency-site'), ('runtime/library', 'runtime/library'), ('maps', 'maps')]:
        p = data / scope; trees.append({'source': str(p), 'destination': destination, 'entries': v.tree(p, excludes=set())})
    historical = v.sha(source / v.MANIFEST)
    harness = {str(root / path): row['sha256'] for path, row in index['files'].items() if path.startswith('recipe/')}
    for name in ['validate_jammy_candidate.py', 'freeze_validation_config.py']:
        p = Path(__file__).with_name(name).resolve(); harness[str(p)] = v.sha(p)
    system_providers, system_dirs, runtime_digest = measured_system_runtime(root)
    config = {'schema': 1, 'source_commit': head, 'source_root': str(source), 'candidate_root': str(root),
        'output': str(a.output.absolute()), 'tools': tools, 'python_prefix': lock['host']['python']['prefix'],
        'network': lock['network'], 'harness': harness, 'build_index_sha256': v.sha(root / 'artifact-index.json'),
        'frontend_bundle_scope': 'Generated frontend dist is not copied or validated; unchanged Lab suite remains mandatory',
        'trees': trees, 'historical_manifest_sha256': historical, 'system_library_dirs': system_dirs, 'system_providers': system_providers, 'runtime_interface_sha256': runtime_digest,
        'provider_alternatives': freeze_libz_policy(root, deps),
        'wheel': {'path': str(wheel), 'sha256': v.sha(wheel), 'extension_sha256': binary},
        'manifest': {'version': '2.1.0', 'wheel': wheel.name,
            'metadata_sha256': hashlib.sha256(files['fields2cover-2.1.0.dist-info/METADATA']).hexdigest(),
            'source': 'https://pypi.org/project/fields2cover/2.1.0/',
            'build_identity': 'fields2cover-2.1.0-jammy-gcc11-ortools915-' + binary[:16],
            'acceptance_state': 'candidate_under_validation_not_production_accepted',
            'build_provenance': str(root / 'output/evidence/build-result.json'),
            'historical_manifest_sha256': historical, 'platform_scope': 'Ubuntu22.04 x86_64 CPython3.12.13 candidate; not production accepted'}}
    # Preserve materializer receipt identity, not merely its generated paths.
    config['materialization_sha256'] = v.sha(data / 'materialization.json')
    a.config.parent.mkdir(parents=True, exist_ok=True)
    with a.config.open('x') as f: json.dump(config, f, indent=2); f.write('\n')
    print(v.sha(a.config))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ['source', 'root', 'data', 'output', 'config', 'python', 'node']: p.add_argument('--' + flag, type=Path, required=True)
    freeze(p.parse_args())

if __name__ == '__main__': main()
