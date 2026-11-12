"""Bridge selection must be reversible and fail closed after unvalidated changes."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('bridge_runtime', Path(__file__).parents[1] / 'ros/bridge_runtime.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def test_explicit_system_bridge_needs_no_local_build(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, 'SOURCE', tmp_path / 'missing')
    monkeypatch.setenv('CERES_BRIDGE_FIX', '0')
    assert bridge.bridge_environment() == {}


def test_invalid_bridge_choice_is_not_silently_enabled(monkeypatch):
    monkeypatch.setenv('CERES_BRIDGE_FIX', 'maybe')
    with pytest.raises(RuntimeError, match='0 或 1'):
        bridge.bridge_environment()


def _use_tree(monkeypatch, tmp_path, lib_bytes, binary_bytes, laptop_system_hash, laptop_exec_hash, profile):
    import hashlib
    source = tmp_path / 'bridge'
    build = tmp_path / 'build'
    source.mkdir()
    build.mkdir()
    lib = tmp_path / 'lib.so'
    lib.write_bytes(lib_bytes)
    binary = build / 'parameter_bridge'
    binary.write_bytes(binary_bytes)
    (source / 'manifest.json').write_text(json.dumps({
        'sources': {},
        'system_files': {str(lib): laptop_system_hash},
        'executable_sha256': laptop_exec_hash,
    }))
    if profile is not None:
        (build / 'profile.json').write_text(json.dumps(profile))
    monkeypatch.setattr(bridge, 'SOURCE', source)
    monkeypatch.setattr(bridge, 'BUILD', build)
    monkeypatch.setattr(bridge, 'EXECUTABLE', binary)
    monkeypatch.setenv('CERES_BRIDGE_FIX', '1')
    return binary


def test_laptop_audit_ignores_a_different_machine_profile(monkeypatch, tmp_path):
    import hashlib
    lib_hash = hashlib.sha256(b'laptop-lib').hexdigest()
    binary_hash = hashlib.sha256(b'laptop-bridge').hexdigest()
    _use_tree(monkeypatch, tmp_path, b'laptop-lib', b'laptop-bridge', lib_hash, binary_hash, {
        'sources': {},
        'system_files': {str(tmp_path / 'lib.so'): lib_hash},
        'executable_sha256': 'f' * 64,
    })
    env = bridge.bridge_environment()
    assert str(tmp_path / 'build' / 'overlay') in env['AMENT_PREFIX_PATH']


def test_machine_profile_applies_only_when_laptop_libraries_differ(monkeypatch, tmp_path):
    import hashlib
    image_hash = hashlib.sha256(b'image-bridge').hexdigest()
    _use_tree(monkeypatch, tmp_path, b'image-lib', b'image-bridge', '0' * 64, '1' * 64, {
        'sources': {},
        'system_files': {str(tmp_path / 'lib.so'): hashlib.sha256(b'image-lib').hexdigest()},
        'executable_sha256': image_hash,
    })
    env = bridge.bridge_environment()
    assert str(tmp_path / 'build' / 'overlay') in env['AMENT_PREFIX_PATH']


def test_image_profile_is_ignored_unless_explicitly_selected(monkeypatch, tmp_path):
    import hashlib
    image_hash = hashlib.sha256(b'image-bridge').hexdigest()
    _use_tree(monkeypatch, tmp_path, b'image-lib', b'image-bridge', hashlib.sha256(b'image-lib').hexdigest(), '1' * 64, {
        'scope': 'image',
        'sources': {},
        'system_files': {str(tmp_path / 'lib.so'): hashlib.sha256(b'image-lib').hexdigest()},
        'executable_sha256': image_hash,
    })
    with pytest.raises(RuntimeError, match='验收指纹不同'):
        bridge.bridge_environment()
    monkeypatch.setenv('CERES_SIM_PROFILE', 'image')
    env = bridge.bridge_environment()
    assert str(tmp_path / 'build' / 'overlay') in env['AMENT_PREFIX_PATH']


def test_changed_dependency_cannot_fall_back_to_system_bridge(monkeypatch, tmp_path):
    lib = tmp_path / 'dependency.so'
    lib.write_bytes(b'changed dependency')
    (tmp_path / 'manifest.json').write_text(json.dumps({'sources': {}, 'system_files': {str(lib): 'unvalidated'}}))
    monkeypatch.setattr(bridge, 'SOURCE', tmp_path)
    monkeypatch.setenv('CERES_BRIDGE_FIX', '1')
    with pytest.raises(RuntimeError, match='指纹变化'):
        bridge.bridge_environment()


@pytest.mark.parametrize('option,expected_issue', [('1', False), ('0', True), ('invalid', True)])
def test_gateway_bridge_status_matches_requested_mode(monkeypatch, option, expected_issue):
    from ceres_lab.services import bridge_known_issues, bridge_note
    from ceres_lab.simclient import stopped_state

    monkeypatch.setenv('CERES_BRIDGE_FIX', option)
    assert bool(bridge_known_issues()) == expected_issue
    assert stopped_state()['known_issues'] == bridge_known_issues()
    if option == '0':
        assert '未修复' in bridge_note()
    elif option == 'invalid':
        assert '阻止仿真启动' in bridge_note()
    else:
        assert '源码关闭修复' in bridge_note()
