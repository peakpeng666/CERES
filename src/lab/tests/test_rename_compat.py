"""Existing environment and exported datasets remain readable after the rename."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from ceres_lab.env_compat import config_directory, normalize_environment
from ceres_lab.runtime_transfer import export_bundle, verify_bundle


def test_environment_prefers_explicit_ceres_values():
    result = normalize_environment({'URP_DATA_HOME': '/old', 'CERES_DATA_HOME': '', 'URP_PORT': '4200'})
    assert result['CERES_DATA_HOME'] == ''
    assert result['CERES_PORT'] == '4200'


def test_legacy_environment_is_applied_before_paths_import(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CERES_', 'URP_'))}
    env['URP_DATA_HOME'] = str(tmp_path)
    out = subprocess.check_output([sys.executable, '-c', 'from ceres_lab.paths import DATA_HOME, RUNTIME; print(DATA_HOME); print(RUNTIME)'], env=env, text=True)
    assert out.splitlines() == [str(tmp_path), str(tmp_path / 'runtime')]


def test_configuration_reuses_existing_directory_without_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.delenv('CERES_CONFIG_DIR', raising=False)
    monkeypatch.delenv('URP_CONFIG_DIR', raising=False)
    old = tmp_path / '.config/urp-workbench'
    old.mkdir(parents=True)
    assert config_directory() == old
    current = tmp_path / '.config/ceres-workbench'
    current.mkdir()
    assert config_directory() == current


def test_legacy_bundle_schema_remains_readable(tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'record.txt').write_text('unchanged')
    # Preserve the legacy dataset identity, rather than silently regenerating it.
    dataset = {'schema': 'urp.dataset/1', 'dataset_id': 'legacy-id'}
    (tmp_path / 'dataset.json').write_text(json.dumps(dataset))
    current = tmp_path / 'current.zip'
    export_bundle(runtime, current)
    legacy = tmp_path / 'legacy.zip'
    with zipfile.ZipFile(current) as source, zipfile.ZipFile(legacy, 'w') as destination:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == 'manifest.json':
                manifest = json.loads(content)
                manifest['schema'] = 'urp.runtime-bundle/1'
                content = json.dumps(manifest).encode()
            destination.writestr(item, content)
    assert verify_bundle(legacy)['verified']
