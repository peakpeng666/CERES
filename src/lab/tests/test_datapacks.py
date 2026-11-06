"""Downloading optional data: nothing half-finished is ever visible, and an installed pack is never overwritten."""
import hashlib
import json
import stat
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from urp_lab import datapacks
from urp_lab.datapacks import PackError, PackManager
from urp_lab.gateway import Config, create_app


def make_zip(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


class Host:
    def __init__(self, folder):
        self.folder, self.delay, self.requests = folder, 0, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append(self.headers.get('Range'))
                path = outer.folder / self.path.lstrip('/')
                if not path.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                data = path.read_bytes()
                start = int(self.headers['Range'].split('=')[1].rstrip('-')) if self.headers.get('Range') else 0
                self.send_response(206 if start else 200)
                self.send_header('Content-Length', str(len(data) - start))
                self.end_headers()
                for i in range(start, len(data), 4096):
                    time.sleep(outer.delay)
                    self.wfile.write(data[i:i + 4096])

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def host(tmp_path, monkeypatch):
    folder = tmp_path / 'host'
    folder.mkdir()
    h = Host(folder)
    monkeypatch.setenv('URP_PACKS_BASE_URL', h.url)
    yield h
    h.server.shutdown()


def publish(tmp_path, monkeypatch, host, files, *, sha=None, unpacked=None):
    archive = make_zip(host.folder / 'history-1.zip', files)
    data = archive.read_bytes()
    spec = {'id': 'history', 'name': '历史档案', 'summary': 's', 'file': 'history-1.zip', 'sha256': sha or hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'unpacked_bytes': unpacked or sum(len(v) for v in files.values()), 'version': 1}
    path = tmp_path / 'packs.json'
    path.write_text(json.dumps({'schema': 'urp.data-packs/1', 'base_url': 'http://nowhere.invalid', 'packs': [spec]}))
    monkeypatch.setattr(datapacks, 'MANIFEST', path)
    return spec


def settle(manager, pack='history', seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        row = next(p for p in manager.list() if p['id'] == pack)
        if row['state'] not in ('downloading', 'installing'):
            return row
        time.sleep(.02)
    raise AssertionError('still busy')


FILES = {'web/data/only-in-pack.json': '{"ok": true}', 'archive/research.sqlite3': 'x' * 2000}


def test_a_pack_arrives_whole_and_is_served(tmp_path, monkeypatch, host):
    publish(tmp_path, monkeypatch, host, FILES)
    m = PackManager(tmp_path / 'runtime')
    assert m.list()[0]['state'] == 'absent' and m.web_file('only-in-pack.json') is None
    m.install('history')
    assert settle(m)['state'] == 'installed'
    assert m.web_file('only-in-pack.json').read_text() == '{"ok": true}'
    assert m.web_file('../archive/research.sqlite3') is None
    assert not list((tmp_path / 'runtime' / 'packs').glob('*.part')) and not list((tmp_path / 'runtime' / 'packs').glob('.staging*'))
    with pytest.raises(PackError) as e:
        m.install('history')
    assert e.value.code == 'pack_installed'


def test_a_corrupt_download_leaves_nothing_and_can_be_retried(tmp_path, monkeypatch, host):
    publish(tmp_path, monkeypatch, host, FILES, sha='0' * 64)
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    row = settle(m)
    assert row['state'] == 'failed' and '校验不通过' in row['error']
    assert m.installed('history') is None and m.web_file('only-in-pack.json') is None
    assert not list((tmp_path / 'runtime' / 'packs').glob('history-1*'))
    publish(tmp_path, monkeypatch, host, FILES)
    m.install('history')
    assert settle(m)['state'] == 'installed'


def test_an_unpublished_pack_says_so(tmp_path, monkeypatch, host):
    publish(tmp_path, monkeypatch, host, FILES)
    (host.folder / 'history-1.zip').unlink()
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    row = settle(m)
    assert row['state'] == 'failed' and 'HTTP 404' in row['error']


def test_an_interrupted_download_resumes(tmp_path, monkeypatch, host):
    spec = publish(tmp_path, monkeypatch, host, {'web/data/big.bin': bytes(range(256)) * 400})
    root = tmp_path / 'runtime' / 'packs'
    root.mkdir(parents=True)
    data = (host.folder / 'history-1.zip').read_bytes()
    (root / 'history-1.zip.part').write_bytes(data[:100])
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    assert settle(m)['state'] == 'installed' and host.requests == ['bytes=100-']
    assert spec['sha256'] == hashlib.sha256(data).hexdigest()


def test_cancelling_keeps_nothing_visible(tmp_path, monkeypatch, host):
    publish(tmp_path, monkeypatch, host, {'web/data/big.bin': __import__('os').urandom(3 * 1024 * 1024)})
    host.delay = .002
    monkeypatch.setattr(datapacks, 'CHUNK', 4096)
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    m.cancel('history')
    row = settle(m)
    assert row['state'] == 'absent' and m.installed('history') is None


@pytest.mark.parametrize('name', ['../evil.txt', 'web/../../evil.txt', '/abs/evil.txt', 'web\\..\\evil.txt'])
def test_paths_that_leave_the_pack_are_refused(tmp_path, monkeypatch, host, name):
    publish(tmp_path, monkeypatch, host, {name: 'x'})
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    row = settle(m)
    assert row['state'] == 'failed' and '不安全' in row['error']
    assert not list(tmp_path.rglob('evil.txt'))


def test_a_symlink_in_the_archive_is_refused(tmp_path, monkeypatch, host):
    spec = publish(tmp_path, monkeypatch, host, {'web/data/a': 'x'})
    path = host.folder / 'history-1.zip'
    info = zipfile.ZipInfo('web/data/link')
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(info, '/etc/passwd')
    spec = publish_raw(tmp_path, monkeypatch, path, spec)
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    assert '不安全' in settle(m)['error']


def publish_raw(tmp_path, monkeypatch, path, spec):
    data = path.read_bytes()
    spec = {**spec, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'unpacked_bytes': 1000}
    (tmp_path / 'packs.json').write_text(json.dumps({'schema': 'urp.data-packs/1', 'base_url': 'http://x', 'packs': [spec]}))
    return spec


def test_an_archive_that_expands_past_its_declaration_is_refused(tmp_path, monkeypatch, host):
    publish(tmp_path, monkeypatch, host, {'web/data/a': 'x' * 10000}, unpacked=100)
    m = PackManager(tmp_path / 'runtime')
    m.install('history')
    assert '超过声明' in settle(m)['error']


def test_the_gateway_serves_and_removes_a_pack(tmp_path, monkeypatch, host):
    publish(tmp_path, monkeypatch, host, FILES)
    app = create_app(Config(runtime=tmp_path / 'runtime', background=False))
    with TestClient(app, base_url='http://127.0.0.1:4100') as client:
        assert client.get('/data/only-in-pack.json').status_code == 404
        assert client.get('/lab/api/packs').json()['packs'][0]['state'] == 'absent'
        assert client.post('/lab/api/packs/nothing/install', json={}).status_code == 404
        assert client.post('/lab/api/packs/history/install', json={}).status_code == 202
        settle(app.state.lab.packs)
        assert client.get('/data/only-in-pack.json').json() == {'ok': True}
        client.post('/lab/api/packs/history/remove', json={})
        assert client.get('/data/only-in-pack.json').status_code == 404


def test_an_installed_app_explains_the_archive_row(tmp_path, monkeypatch, host):
    monkeypatch.setenv('URP_DESKTOP', '1')
    app = create_app(Config(runtime=tmp_path / 'runtime', background=False, archive_autostart=False))
    with TestClient(app, base_url='http://127.0.0.1:4100') as client:
        lab = app.state.lab
        monkeypatch.setattr(lab.packs, 'start_archive', lambda services: setattr(services, 'started', True))

        def row():
            return next(r for r in client.get('/lab/api/services').json()['services'] if r['id'] == 'archive')

        monkeypatch.setattr(type(lab.services), '_archive_service', lambda self: {'id': 'archive', 'state': 'unavailable', 'detail': '缺少 src/backend/.venv', 'controls': []})
        assert '下载历史档案' in row()['detail'] and row()['state'] == 'unavailable'
        monkeypatch.setattr(lab.packs, 'installed', lambda pack: tmp_path)
        assert row()['state'] == 'stopped' and row()['controls'] == ['start']
        lab.services.start_archive()
        assert lab.services.started is True


def test_the_engine_list_says_why_gazebo_is_missing(tmp_path, monkeypatch):
    from urp_lab import paths
    monkeypatch.setattr(paths, 'gazebo_gap', lambda: '这台电脑没有 ROS 2 和 Gazebo')
    with TestClient(create_app(Config(runtime=tmp_path / 'runtime', background=False)), base_url='http://127.0.0.1:4100') as client:
        assert client.get('/lab/api/physics/engines').json()['gazebo_gap'] == '这台电脑没有 ROS 2 和 Gazebo'


def test_the_shipped_manifest_is_well_formed():
    data = json.loads(datapacks.MANIFEST.read_text(encoding='utf-8'))
    assert data['schema'] == 'urp.data-packs/1' and data['base_url'].startswith('https://')
    assert all({'id', 'name', 'file', 'sha256', 'bytes', 'unpacked_bytes', 'version'} <= set(p) for p in data['packs'])
