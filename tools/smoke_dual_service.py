#!/usr/bin/env python3
"""Launch isolated services; verify failure is visible, real Jobs and clean shutdown.

Snapshot all present frozen database bytes before launch. Missing original snapshots
are recorded as missing, never presented as a verified hash.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import socket
import time
from urllib.error import URLError

from smoke_archive import request, smoke, wait_archive
from smoke_workbench import smoke as smoke_workbench

ROOT = Path(__file__).resolve().parents[1]


def frozen_databases():
    files = {ROOT / 'data/backend/research.sqlite3'}
    for folder in ('data', 'experiments', 'protocol'):
        for suffix in ('*.sqlite', '*.sqlite3', '*.db'):
            files.update((ROOT / folder).rglob(suffix))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            for p in sorted(files)}


def wait_gateway(url, proc):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'Gateway exited: {proc.returncode}')
        try:
            if request(url, 'GET', '/lab/api/health').get('status') == 'ok':
                return
        except (URLError, TimeoutError):
            pass  # Readiness only; all terminal failures below fail the check.
        time.sleep(.2)
    raise TimeoutError('Gateway readiness timed out; see gateway log')


def require_free_ports(*ports):
    # Refuse to control a pre-existing gateway/archive. Also verify no child listener
    # survives graceful shutdown, rather than treating parent exit as sufficient.
    for port in ports:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', port))


def run(output, port, archive_port):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = frozen_databases()
    url = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'URP_API_PORT': str(archive_port),
           'URP_ARCHIVE_URL': f'http://127.0.0.1:{archive_port}', 'URP_ARCHIVE_AUTOSTART': '1',
           'URP_BIND_HOST': '127.0.0.1', 'URP_ADMIN_TOKEN': ''}
    try:
        for phase in ('startup-failure', 'healthy'):
            phase_dir = output / phase
            phase_dir.mkdir()
            phase_env = {**env, 'URP_API_DB': str(phase_dir / 'archive.sqlite3'),
                         'URP_LAB_RUNTIME': str(phase_dir / 'lab'),
                         'URP_PYTHON': '/urp-deliberately-missing/python' if phase == 'startup-failure'
                         else str(ROOT / '.venv/bin/python')}
            require_free_ports(port, archive_port)
            with (phase_dir / 'gateway.log').open('w') as log:
                proc = subprocess.Popen(['bash', 'setup/run.sh', 'lab-code', '--port', str(port)],
                                        cwd=ROOT, env=phase_env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_gateway(url, proc)
                    if phase == 'startup-failure':
                        state = wait_archive(url, 'failed')
                        (phase_dir / 'archive-state.json').write_text(json.dumps(state, indent=2) + '\n')
                        try:
                            smoke(url, phase_dir / 'smoke')
                        except RuntimeError as exc:
                            if 'Archive startup failed' not in str(exc):
                                raise
                            (phase_dir / 'expected-failure.txt').write_text(str(exc) + '\n')
                        else:
                            raise RuntimeError('Archive smoke incorrectly passed with broken backend')
                    else:
                        smoke(url, phase_dir / 'archive-smoke')
                        smoke_workbench(url, phase_dir / 'workbench-smoke')
                    request(url, 'POST', '/lab/api/shutdown', {})
                    if proc.wait(timeout=30) != 0:
                        raise RuntimeError(f'Gateway shutdown failed: {proc.returncode}')
                    require_free_ports(port, archive_port)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                            raise RuntimeError('Gateway did not stop gracefully; inspect owned child processes')
    finally:
        after = frozen_databases()
        (output / 'frozen-databases.json').write_text(json.dumps(
            {'before': before, 'after': after, 'unchanged': before == after,
             'missing': [name for name, digest in before.items() if digest is None]}, indent=2) + '\n')
        if before != after:
            raise RuntimeError('Frozen database bytes changed')
    (output / 'summary.json').write_text(json.dumps({'status': 'passed',
        'scope': 'dual-service lifecycle, failure injection, archive Jobs, workbench HTTP; no browser/ROS'}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=4186)
    parser.add_argument('--archive-port', type=int, default=4187)
    args = parser.parse_args()
    run(args.output, args.port, args.archive_port)
