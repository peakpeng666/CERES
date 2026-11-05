"""Deterministic Node probe failures; no sleeping or replacement runtime needed."""
import runpy
import subprocess
from pathlib import Path

import pytest

from urp_lab import jobs


NODE = '/selected/node'


def _probe(monkeypatch, outcomes):
    """Only the process result is injected, never a cached manifest/version."""
    pending = iter(outcomes)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command == [NODE, '--version']
        assert kwargs == dict(capture_output=True, text=True, timeout=2.0, check=True)
        outcome = next(pending)  # an unexpected third attempt fails the test
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(command, 0, stdout=outcome, stderr='')

    monkeypatch.setattr(jobs.shutil, 'which', lambda name: NODE if name == 'node' else None)
    monkeypatch.setattr(jobs.subprocess, 'run', run)
    return calls


def _timeout():
    # Even plausible partial output cannot be substituted for a completed probe.
    return subprocess.TimeoutExpired([NODE, '--version'], 2.0, output=b'v24.0.0\n')


def test_cold_probe_retries_once_and_records_only_success(monkeypatch):
    calls = _probe(monkeypatch, [_timeout(), 'v24.19.0\n'])
    manifest = jobs.execution_manifest()
    assert len(calls) == 2
    assert manifest['node_executable'] == NODE
    assert manifest['node_version'] == 'v24.19.0'


def test_repeated_timeout_fails_closed_after_two_finite_attempts(monkeypatch):
    last = _timeout()
    calls = _probe(monkeypatch, [_timeout(), last])
    with pytest.raises(jobs.SourceChangedError, match='无法核对 Node 执行版本') as caught:
        jobs.execution_manifest()
    assert caught.value.__cause__ is last
    assert len(calls) == 2


@pytest.mark.parametrize('failure', [
    FileNotFoundError('selected runtime disappeared'),
    PermissionError('selected runtime cannot execute'),
    subprocess.CalledProcessError(1, [NODE, '--version'], output='v24.0.0'),
])
@pytest.mark.parametrize('after_timeout', [False, True])
def test_launch_or_nonzero_failure_is_not_retried_or_hidden(monkeypatch, failure, after_timeout):
    calls = _probe(monkeypatch, ([_timeout()] if after_timeout else []) + [failure])
    with pytest.raises(jobs.SourceChangedError, match='无法核对 Node 执行版本') as caught:
        jobs.execution_manifest()
    assert caught.value.__cause__ is failure
    assert len(calls) == 1 + after_timeout


@pytest.mark.parametrize('version', ['', '\n  \t'])
def test_empty_success_output_is_not_an_identity(monkeypatch, version):
    calls = _probe(monkeypatch, [version])
    with pytest.raises(jobs.SourceChangedError, match='版本输出为空'):
        jobs.execution_manifest()
    assert len(calls) == 1


def test_runtime_version_is_reprobed_and_changes_manifest(monkeypatch):
    calls = _probe(monkeypatch, ['v24.19.0\n', 'v24.20.0\n'])
    before = jobs.execution_manifest()
    after = jobs.execution_manifest()
    assert len(calls) == 2
    assert before['node_version'] != after['node_version']
    assert before['sha256'] != after['sha256']


def test_later_timeout_never_reuses_a_previously_verified_version(monkeypatch):
    calls = _probe(monkeypatch, ['v24.19.0\n', _timeout(), _timeout()])
    assert jobs.execution_manifest()['node_version'] == 'v24.19.0'
    with pytest.raises(jobs.SourceChangedError, match='无法核对 Node 执行版本'):
        jobs.execution_manifest()
    assert len(calls) == 3


def test_runtime_path_changes_manifest_without_version_change(monkeypatch):
    _probe(monkeypatch, ['v24.19.0\n'])
    before = jobs.execution_manifest()
    monkeypatch.setattr(jobs.shutil, 'which', lambda _: '/other/node')
    monkeypatch.setattr(jobs.subprocess, 'run', lambda command, **_: subprocess.CompletedProcess(
        command, 0, stdout='v24.19.0\n', stderr=''))
    after = jobs.execution_manifest()
    assert before['node_version'] == after['node_version']
    assert before['node_executable'] != after['node_executable']
    assert before['sha256'] != after['sha256']


def test_absent_node_remains_explicit_and_never_starts_a_probe(monkeypatch):
    calls = _probe(monkeypatch, [])
    monkeypatch.setattr(jobs.shutil, 'which', lambda _: None)
    manifest = jobs.execution_manifest()
    assert manifest['node_executable'] is None
    assert manifest['node_version'] is None
    assert calls == []


def test_import_time_manifest_survives_only_a_successful_retry(monkeypatch):
    calls = _probe(monkeypatch, [_timeout(), 'v24.19.0\n'])
    imported = runpy.run_path(str(Path(jobs.__file__)), run_name='urp_lab._node_probe_import')
    assert imported['_GATEWAY_IMPORT_MANIFEST']['node_version'] == 'v24.19.0'
    assert len(calls) == 2


def test_import_time_manifest_still_rejects_persistent_timeout(monkeypatch):
    calls = _probe(monkeypatch, [_timeout(), _timeout()])
    # run_path defines its own SourceChangedError class, as a fresh import does.
    with pytest.raises(RuntimeError, match='无法核对 Node 执行版本') as caught:
        runpy.run_path(str(Path(jobs.__file__)), run_name='urp_lab._node_probe_import')
    assert type(caught.value).__name__ == 'SourceChangedError'
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)
    assert len(calls) == 2
