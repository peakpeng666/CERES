"""Native planner identity without importing or executing optional native libraries."""
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from urp_lab import jobs


@pytest.fixture
def native_tree(tmp_path, monkeypatch):
    site = tmp_path / 'site-packages'
    lab = tmp_path / 'lab'
    vendor = lab / 'vendor' / 'fields2cover-manifest.json'
    vendor.parent.mkdir(parents=True)
    adapter = lab / 'adapter.py'
    adapter.write_text('# unchanged first-party adapter\n')
    monkeypatch.setattr(jobs.paths, 'LAB', lab)
    monkeypatch.setattr(jobs, '_execution_files', lambda: [adapter])
    packages = {}
    for name, version, binaries in (
            ('ompl', '2.0.1', ['base/_base.cpython-312-x86_64-linux-gnu.so',
                              'geometric/_geometric.so', 'util/_util.so',
                              '.libs/libompl.so.18', '../ompl.libs/libboost.so.1']),
            ('fields2cover', '2.1.0', ['_fields2cover_python.so'])):
        root = site / name
        root.mkdir(parents=True)
        origin = root / '__init__.py'
        origin.write_text('raise AssertionError("native packages must not be imported for metadata")\n')
        native_files = []
        for filename in binaries:
            binary = root / filename
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b'original native binary')
            native_files.append(binary.resolve())
        metadata = site / f'{name}-{version}.dist-info' / 'METADATA'
        metadata.parent.mkdir(parents=True)
        metadata.write_text(f'Name: {name}\nVersion: {version}\n')
        files = [p.relative_to(site) for p in [origin, metadata, *native_files]]
        packages[name] = SimpleNamespace(root=root, origin=origin, binaries=native_files,
                                         version=version, files=files, locate_file=lambda p: site / p)
    vendor.write_text(json.dumps({'version': '2.1.0', 'wheel_sha256': 'fixture-wheel',
                                 'binary_sha256': hashlib.sha256(packages['fields2cover'].binaries[0].read_bytes()).hexdigest()}))
    original_spec = importlib.util.find_spec
    original_distribution = importlib.metadata.distribution
    original_version = importlib.metadata.version

    def find_spec(name, *args):
        if name in packages:
            package = packages[name]
            return importlib.util.spec_from_file_location(name, package.origin) if package.origin.exists() else None
        return original_spec(name, *args)

    def distribution(name):
        return packages[name] if name in packages else original_distribution(name)

    monkeypatch.setattr(importlib.util, 'find_spec', find_spec)
    monkeypatch.setattr(importlib.metadata, 'distribution', distribution)
    monkeypatch.setattr(importlib.metadata, 'version',
                        lambda name: packages[name].version if name in packages else original_version(name))
    return SimpleNamespace(packages=packages, vendor=vendor, site=site)


@pytest.mark.parametrize('index', range(5))
def test_manifest_binds_same_version_ompl_native_replacement(native_tree, index):
    before = jobs.execution_manifest()
    binary = native_tree.packages['ompl'].binaries[index]
    stat = binary.stat()
    binary.write_bytes(b'replaced native binary')  # same size and package version
    os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = jobs.execution_manifest()
    assert before['sha256'] != after['sha256']


def test_manifest_binds_paired_f2c_binary_and_acceptance_manifest(native_tree):
    before = jobs.execution_manifest()
    binary = native_tree.packages['fields2cover'].binaries[0]
    binary.write_bytes(b'new accepted native binary')
    manifest = json.loads(native_tree.vendor.read_text())
    manifest['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    native_tree.vendor.write_text(json.dumps(manifest))
    after = jobs.execution_manifest()
    assert before['sha256'] != after['sha256']


def test_manifest_binds_acceptance_manifest_even_without_binary_change(native_tree):
    before = jobs.execution_manifest()
    manifest = json.loads(native_tree.vendor.read_text())
    manifest['wheel_sha256'] = 'different-accepted-wheel'
    native_tree.vendor.write_text(json.dumps(manifest))
    assert jobs.execution_manifest()['sha256'] != before['sha256']


def test_manifest_snapshots_native_files_without_importing_packages(native_tree):
    before_modules = set(sys.modules)
    manifest = jobs.execution_manifest()
    assert not ({'ompl', 'fields2cover'} & (set(sys.modules) - before_modules))
    assert manifest['packages']['ompl'] == '2.0.1'
    assert manifest['packages']['fields2cover'] == '2.1.0'
    for name, package in native_tree.packages.items():
        native = manifest['native_dependencies'][name]
        assert native['state'] == 'present'
        for file in [package.origin, *package.binaries]:
            assert native['files'][str(file)] == hashlib.sha256(file.read_bytes()).hexdigest()
    f2c = manifest['native_dependencies']['fields2cover']
    assert f2c['acceptance_manifest'] == str(native_tree.vendor)
    assert f2c['files'][str(native_tree.vendor)] == hashlib.sha256(native_tree.vendor.read_bytes()).hexdigest()


@pytest.mark.parametrize('change', ['add', 'remove', 'wrapper', 'version'])
def test_manifest_tracks_package_layout_and_version_changes(native_tree, change):
    before = jobs.execution_manifest()
    package = native_tree.packages['ompl']
    if change == 'add':
        # Not in RECORD: package scanning must still detect newly installed files.
        (package.root / '_new.pyd').write_bytes(b'new extension')
    elif change == 'remove':
        package.binaries[0].unlink()
    elif change == 'wrapper':
        package.origin.write_text('raise RuntimeError("different wrapper")\n')
    else:
        package.version = '2.0.2'
    assert jobs.execution_manifest()['sha256'] != before['sha256']


def test_missing_optional_package_and_manifest_are_explicit(native_tree, monkeypatch):
    original_distribution = importlib.metadata.distribution
    original_version = importlib.metadata.version

    def missing_distribution(name):
        if name in native_tree.packages:
            raise importlib.metadata.PackageNotFoundError(name)
        return original_distribution(name)

    def missing_version(name):
        if name in native_tree.packages:
            raise importlib.metadata.PackageNotFoundError(name)
        return original_version(name)

    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setattr(importlib.metadata, 'distribution', missing_distribution)
    monkeypatch.setattr(importlib.metadata, 'version', missing_version)
    native_tree.vendor.unlink()
    absent = jobs.execution_manifest()
    assert absent == jobs.execution_manifest()
    for name in native_tree.packages:
        assert absent['packages'][name] is None
        assert absent['native_dependencies'][name]['state'] == 'not_installed'
    assert absent['native_dependencies']['fields2cover']['files'][str(native_tree.vendor)] is None


def test_installing_optional_package_changes_identity(native_tree, monkeypatch):
    installed = jobs.execution_manifest()
    with monkeypatch.context() as changes:
        original_spec = importlib.util.find_spec
        original_distribution = importlib.metadata.distribution
        original_version = importlib.metadata.version

        def without_f2c(function):
            def call(name):
                if name == 'fields2cover':
                    raise importlib.metadata.PackageNotFoundError(name)
                return function(name)
            return call

        changes.setattr(importlib.util, 'find_spec',
                        lambda name: None if name == 'fields2cover' else original_spec(name))
        changes.setattr(importlib.metadata, 'distribution', without_f2c(original_distribution))
        changes.setattr(importlib.metadata, 'version', without_f2c(original_version))
        absent = jobs.execution_manifest()
    assert absent['native_dependencies']['fields2cover']['state'] == 'not_installed'
    assert installed['sha256'] != absent['sha256']
    assert jobs.execution_manifest() == installed


def test_package_native_artifacts_found_without_distribution_file_list(native_tree):
    package = native_tree.packages['ompl']
    package.files = None
    before = jobs.execution_manifest()
    assert before['native_dependencies']['ompl']['state'] == 'present'
    package.binaries[0].write_bytes(b'changed binary')
    assert jobs.execution_manifest()['sha256'] != before['sha256']


def test_missing_native_artifacts_are_not_reported_present(native_tree):
    package = native_tree.packages['fields2cover']
    package.binaries[0].unlink()
    manifest = jobs.execution_manifest()
    native = manifest['native_dependencies']['fields2cover']
    assert native['state'] == 'native_missing'
    assert native['files'][str(package.binaries[0])] is None


def test_native_hash_cache_reuses_only_unchanged_stat_identity(native_tree):
    from urp_lab import native_identity
    native_identity._hash_version.cache_clear()
    before = jobs.execution_manifest()
    initial = native_identity._hash_version.cache_info()
    assert jobs.execution_manifest() == before
    unchanged = native_identity._hash_version.cache_info()
    assert unchanged.misses == initial.misses and unchanged.hits > initial.hits
    binary = native_tree.packages['ompl'].binaries[0]
    old_stat = binary.stat()
    replacement = binary.with_name('replacement.tmp')
    replacement.write_bytes(b'replaced native binary')
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    replacement.replace(binary)
    assert jobs.execution_manifest()['sha256'] != before['sha256']
    assert native_identity._hash_version.cache_info().misses > unchanged.misses


def test_symlinked_native_subdirectory_is_fingerprinted_without_recursion(native_tree, tmp_path):
    target = tmp_path / 'linked-native'
    target.mkdir()
    binary = target / 'extra.dylib'
    binary.write_bytes(b'original native binary')
    link = native_tree.packages['ompl'].root / 'linked'
    link.symlink_to(target, target_is_directory=True)
    (target / 'cycle').symlink_to(link, target_is_directory=True)
    before = jobs.execution_manifest()
    binary.write_bytes(b'replaced native binary')
    assert jobs.execution_manifest()['sha256'] != before['sha256']


def test_native_read_error_fails_closed(native_tree, monkeypatch):
    from urp_lab import native_identity

    def denied(*args):
        raise PermissionError('injected unreadable native dependency')

    monkeypatch.setattr(native_identity, '_fingerprint', denied)
    with pytest.raises(jobs.SourceChangedError, match='无法核对 Lab 执行依赖'):
        jobs.execution_manifest()


def test_native_mutation_during_hash_fails_closed(native_tree, monkeypatch):
    from urp_lab import native_identity
    native_identity._hash_version.cache_clear()
    original_hash = native_identity.hashlib.file_digest

    def replace_while_reading(stream, algorithm):
        digest = original_hash(stream, algorithm)
        binary = Path(stream.name)
        if binary.suffix == '.so':
            binary.write_bytes(b'replaced during fingerprint')
        return digest

    monkeypatch.setattr(native_identity.hashlib, 'file_digest', replace_while_reading)
    with pytest.raises(jobs.SourceChangedError, match='核对时改变'):
        jobs.execution_manifest()


@pytest.mark.parametrize('target', ['ompl', 'fields2cover', 'manifest'])
def test_gateway_rejects_native_replacement_before_worker_start(native_tree, tmp_path, monkeypatch, target):
    from urp_lab.store import Store
    monkeypatch.setattr(jobs, '_GATEWAY_IMPORT_MANIFEST', jobs.execution_manifest())
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, command=[str(tmp_path / 'must-not-start')], concurrency=1)
    try:
        if target == 'manifest':
            native_tree.vendor.write_text('{"changed": true}')
        else:
            native_tree.packages[target].binaries[0].write_bytes(b'changed binary')
        with pytest.raises(jobs.SourceChangedError):
            manager._check_source()
        assert not manager._procs
    finally:
        manager.shutdown()
        db.close()


def test_custom_worker_does_not_claim_gateway_native_identity(native_tree, tmp_path, monkeypatch):
    from urp_lab.store import Store
    source = jobs.execution_manifest()
    monkeypatch.setattr(jobs, '_GATEWAY_IMPORT_MANIFEST', source)
    db = Store(tmp_path / 'lab.sqlite3')
    manager = jobs.JobManager(db, command=[str(tmp_path / 'custom-worker')])
    try:
        run = {'reproducibility': {'native_dependencies': source['native_dependencies']}}
        manager._stamp_worker_identity(run)
        assert run['reproducibility']['native_dependencies'] is None
        assert run['reproducibility']['gateway_native_dependencies'] == source['native_dependencies']
    finally:
        manager.shutdown()
        db.close()


def test_submission_snapshot_retains_native_artifact_evidence(native_tree, monkeypatch):
    from urp_lab import recommend
    from urp_lab.scenarios.benchmark import cvrp_scenarios
    monkeypatch.setattr(recommend, 'recommend', lambda *args, **kwargs: {})
    source = jobs.execution_manifest()
    scenario = cvrp_scenarios()[0]
    run = jobs.skeleton('job-native', scenario, scenario['instance'], {'id': 'pyvrp.ils'}, {}, None, source)
    assert run['reproducibility']['native_dependencies'] == source['native_dependencies']
    source['native_dependencies'].clear()
    assert run['reproducibility']['native_dependencies']['ompl']['state'] == 'present'
