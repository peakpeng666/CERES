"""Loopback shutdown and the deployment address defaults."""
import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient

from ceres_lab.gateway import Config, create_app
from ceres_lab.runtime_config import archive_autostart, archive_url, extra_hosts

_spec = importlib.util.spec_from_file_location('web_bind', Path(__file__).resolve().parents[1] / 'ros' / 'web_bind.py')
web_bind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(web_bind)


def _app(tmp_path, token=''):
    return create_app(Config(runtime=tmp_path, background=False, extra_hosts={'testserver'}, admin_token=token))


def test_remote_shutdown_is_rejected(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, client=('192.0.2.10', 4100)) as client:
        response = client.post('/lab/api/shutdown', json={})
        assert response.status_code == 403
        assert response.json()['error'] == 'shutdown_forbidden'
        assert app.state.lab.shutdown_event.is_set() is False


def test_loopback_shutdown_still_works(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, client=('127.0.0.1', 4100)) as client:
        response = client.post('/lab/api/shutdown', json={})
        assert response.status_code == 202
        assert app.state.lab.shutdown_event.is_set() is True


def test_token_allows_remote_shutdown_and_blocks_loopback_without_it(tmp_path):
    app = _app(tmp_path, token='desk-token')
    with TestClient(app, client=('192.0.2.10', 4100)) as client:
        denied = client.post('/lab/api/shutdown', json={})
        assert denied.status_code == 403
        allowed = client.post('/lab/api/shutdown', json={}, headers={'X-CERES-Admin-Token': 'desk-token'})
        assert allowed.status_code == 202
    app = _app(tmp_path, token='desk-token')
    with TestClient(app, client=('127.0.0.1', 4100)) as client:
        assert client.post('/lab/api/shutdown', json={}).status_code == 403
        assert client.post('/lab/api/shutdown', json={}, headers={'Authorization': 'Bearer desk-token'}).status_code == 202


def test_defaults_stay_on_the_laptop(monkeypatch):
    monkeypatch.delenv('CERES_EXTRA_HOSTS', raising=False)
    monkeypatch.delenv('CERES_ARCHIVE_URL', raising=False)
    monkeypatch.delenv('CERES_ARCHIVE_AUTOSTART', raising=False)
    monkeypatch.delenv('CERES_BIND_HOST', raising=False)
    monkeypatch.delenv('CERES_FOXGLOVE_BIND', raising=False)
    monkeypatch.delenv('CERES_BROWSER_HOST', raising=False)
    assert extra_hosts() == set()
    assert archive_url() == 'http://127.0.0.1:4180'
    assert archive_autostart(False) is True
    assert archive_autostart(True) is False
    assert os.environ.get('CERES_BIND_HOST', '127.0.0.1') == '127.0.0.1'
    assert web_bind.foxglove_address() == '127.0.0.1'
    assert web_bind.browser_ws(8765) == 'ws://127.0.0.1:8765'
    monkeypatch.setenv('CERES_EXTRA_HOSTS', 'lab.local, 10.0.0.5')
    assert extra_hosts() == {'lab.local', '10.0.0.5'}


def test_published_browser_host_is_explicit(monkeypatch):
    monkeypatch.setenv('CERES_FOXGLOVE_BIND', '0.0.0.0')
    monkeypatch.setenv('CERES_BROWSER_HOST', '127.0.0.1')
    assert web_bind.foxglove_address() == '0.0.0.0'
    assert web_bind.browser_ws(9002) == 'ws://127.0.0.1:9002'
