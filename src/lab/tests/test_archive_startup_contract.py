"""Service diagnostics share the launcher interpreter and preserve process states."""
import os
import shutil
from types import SimpleNamespace
from unittest.mock import patch
import time

import pytest
from urp_lab import services


@pytest.fixture
def manager(tmp_path):
    with patch.object(services, 'http_json', return_value=(503, {'error': 'offline'})), \
            patch.object(services, 'pid_listening', return_value=None):
        yield services.ServiceManager(runtime=tmp_path)


def process(*, alive, code=None, stopped=False, age=0):
    return SimpleNamespace(alive=alive, proc=SimpleNamespace(pid=123), returncode=code,
                           stop_requested=stopped, started_at=time.monotonic() - age,
                           started_utc='2026-10-30T00:00:00Z')


def test_root_environment_without_legacy_backend_venv_is_startable(manager, tmp_path, monkeypatch):
    launcher = tmp_path / 'src/start.sh'
    launcher.parent.mkdir()
    shutil.copyfile(services.paths.REPO / 'src/start.sh', launcher)
    interpreter = tmp_path / '.venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to('/bin/sh')
    monkeypatch.setattr(services.paths, 'REPO', tmp_path)
    monkeypatch.delenv('URP_PYTHON', raising=False)
    state = manager._archive_service()
    assert state['state'] == 'stopped'
    assert state['controls'] == ['start']
    assert not (tmp_path / 'src/backend/.venv').exists()


def test_missing_override_has_exact_unavailable_diagnostic(manager):
    with patch.dict(os.environ, {'URP_PYTHON': '/urp-missing/python'}):
        state = manager._archive_service()
    assert state['state'] == 'unavailable'
    assert '/urp-missing/python' in state['detail']


@pytest.mark.parametrize('alive,code,stopped,age,expected', [
    (True, None, False, 0, 'starting'),
    (True, None, False, 40, 'degraded'),
    (False, 127, False, 0, 'failed'),
])
def test_process_state_not_masked_by_missing_interpreter(manager, alive, code, stopped, age, expected):
    manager.archive = process(alive=alive, code=code, stopped=stopped, age=age)
    with patch.dict(os.environ, {'URP_PYTHON': '/urp-missing/python'}):
        state = manager._archive_service()
    assert state['state'] == expected
    if expected == 'failed':
        assert '127' in state['detail']
        assert state['controls'] == ['start']


def test_stopped_process_can_restart(manager):
    manager.archive = process(alive=False, code=-2, stopped=True)
    with patch.dict(os.environ, {'URP_PYTHON': '/bin/sh'}):
        state = manager._archive_service()
    assert state['state'] == 'stopped'
    assert state['controls'] == ['start']


def test_healthy_owned_and_external_control_boundaries(manager):
    with patch.object(services, 'http_json', return_value=(200, {'status': 'ok'})):
        assert manager._archive_service()['controls'] == []
        manager.archive = process(alive=True)
        assert manager._archive_service()['controls'] == ['stop', 'restart']


@pytest.mark.parametrize('environment,disabled,required', [('1', False, True), ('0', False, False), ('1', True, False)])
def test_health_exposes_effective_mode_not_archive_liveness(tmp_path, monkeypatch, environment, disabled, required):
    from fastapi.testclient import TestClient
    from urp_lab.gateway import Config, create_app
    from urp_lab.runtime_config import archive_autostart
    monkeypatch.setenv('URP_ARCHIVE_AUTOSTART', environment)
    monkeypatch.setattr(services.ServiceManager, 'start_archive', lambda self: None)
    app = create_app(Config(runtime=tmp_path, background=False, extra_hosts={'testserver'},
                            archive_autostart=archive_autostart(disabled)))
    with TestClient(app) as client:
        assert client.get('/lab/api/health').json()['archive_required'] is required
        # A configured required service remains required after a deliberate stop.
        app.state.lab.services.stop_archive()
        assert client.get('/lab/api/health').json()['archive_required'] is required
