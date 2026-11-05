"""Restart must never expose the previous session's identity or readiness."""
import pytest
import os
import subprocess
import sys
import time
from types import SimpleNamespace

from urp_lab.services import ServiceManager
from urp_lab.simclient import SimClient, stopped_state
from urp_lab.store import Store


@pytest.fixture
def restarted(tmp_path, monkeypatch):
    monkeypatch.setattr('urp_lab.simclient.http_json', lambda *a, **kw: (None, 'offline'))
    db = Store(tmp_path / 'test.sqlite')
    services = ServiceManager(runtime=tmp_path)
    client = SimClient(db, None, services)
    client.state = {**stopped_state(), 'session_id': 'old', 'phase': 'ready', 'world': 'maze'}
    services.sim_meta = {'session_id': 'new', 'world': 'warehouse', 'spawn': {'x': -5, 'y': 0}}
    monkeypatch.setattr(services, 'sim_alive', lambda: True)
    yield client
    db.close()


def test_restart_response_carries_new_session(restarted):
    state = restarted.current_state()
    assert (state['session_id'], state['phase'], state['world']) == ('new', 'starting', 'warehouse')
    assert state['execution'] is None


def test_late_poll_cannot_publish_previous_session(restarted, monkeypatch):
    old = {**restarted.state, 'phase': 'failed', 'errors': ['old error']}
    monkeypatch.setattr('urp_lab.simclient.http_json', lambda *a, **kw: (200, old))
    restarted.poll_once()
    state = restarted.current_state()
    assert state['session_id'] == 'new' and not state['errors']
    assert restarted.state['phase'] == 'ready'  # stale response was not published


def test_late_gone_notification_cannot_fail_new_run(restarted):
    run = {'sim_run_id': 'execution-new', 'run_id': 'planning', 'session_id': 'new',
           'state': 'executing', 'created_utc': '2026-10-16T07:00:00Z'}
    restarted.store.put_sim_run(run)
    restarted._session_gone()
    assert restarted.store.get_sim_run('execution-new')['state'] == 'executing'


def test_adoption_requires_runtime_identity_and_expires_on_exit(tmp_path, monkeypatch):
    sid = 'session-owned'
    ready = tmp_path / 'child-ready'
    child = subprocess.Popen([sys.executable, '-c',
                              'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(ready),
                              '--runtime', str(tmp_path / 'sim' / sid)],
                             env={**os.environ, 'URP_SIM_SESSION': sid})
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.005)
        assert ready.exists(), 'child did not finish exec before the ownership check'
        manager = ServiceManager(runtime=tmp_path)
        foreign = ServiceManager(runtime=tmp_path / 'other-runtime')
        monkeypatch.setattr('urp_lab.services.pid_listening', lambda _: child.pid)
        monkeypatch.setattr(manager, 'changed', lambda: None)
        monkeypatch.setattr(foreign, 'changed', lambda: None)
        state = {'session_id': sid, 'world': 'maze'}
        foreign.adopt_sim(state)
        assert foreign.sim_adopted is None
        manager.sim = SimpleNamespace(alive=False, returncode=1)
        manager.adopt_sim(state)
        assert manager.sim_alive() and manager.sim is None and manager._sim_rc() is None
        manager.sim_adopted['start_ticks'] -= 1
        assert not manager.sim_alive()  # PID reuse must not retain ownership.
        manager.sim_adopted['start_ticks'] += 1
        child.terminate()
        child.wait(timeout=5)
        assert not manager.sim_alive()
        manager.note_sim_exit()
        assert manager.sim_adopted is None and manager.sim_exit
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
