#!/usr/bin/env python3
"""Real gateway shutdown, including a connected SSE client, in an isolated store/port."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

p = argparse.ArgumentParser()
p.add_argument('--out', type=Path, required=True)
args = p.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
base = 'http://127.0.0.1:4141/lab/api'
rows = []
for i in range(3):
    stream = None
    runtime = out / f'run-{i}'
    with (out / f'gateway-{i}.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'ceres_lab', 'serve', '--port', '4141', '--no-archive'],
                                env={**os.environ, 'CERES_LAB_RUNTIME': str(runtime)},
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            end = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(base + '/health', timeout=1) as f:
                        assert json.load(f)['status'] == 'ok'
                    break
                except OSError:
                    assert proc.poll() is None and time.monotonic() < end
                    time.sleep(.1)
            if i:
                stream = urllib.request.urlopen(base + '/events', timeout=5)
                assert stream.headers['content-type'].startswith('text/event-stream')
            t = time.monotonic()
            req = urllib.request.Request(base + '/shutdown', data=b'{}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as f:
                assert json.load(f)['accepted']
            rc = proc.wait(timeout=30)
            row = {'iteration': i, 'sse_connected': bool(i), 'returncode': rc, 'shutdown_wall_s': time.monotonic() - t}
            rows.append(row)
            (out / 'summary.json').write_text(json.dumps(rows, indent=2))
            print(json.dumps(row), flush=True)
            assert rc == 0, row
        finally:
            if stream:
                stream.close()
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
