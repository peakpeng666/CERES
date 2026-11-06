#!/usr/bin/env python3
"""Does a staged backend work on its own? Starts it with a bare environment and walks the main path over HTTP.

    python3 src/desktop/test/backend_smoke.py --backend <dir with app/ and python> --out <new dir> [--node <path>] [--port 4250]

The directory is the staged tree or the resources/backend of an installed app. Only the Python standard library is used.
"""
import argparse
import functools
import http.server
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--backend', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--node', default=shutil.which('node'))
parser.add_argument('--port', type=int, default=4250)
parser.add_argument('--no-scenario', action='store_true', help='stop after the catalog checks')
parser.add_argument('--launcher', help='prefix for the backend command, such as wine for a Windows tree on Linux')
parser.add_argument('--packs', type=Path, help='directory holding the data pack zips; serves them locally and installs one')
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
base = f'http://127.0.0.1:{args.port}'
steps = []
SKIP_ROUTES = {'/lab/api/openapi.json', '/lab/api/events'}   # the second one is a stream and never ends


def step(name, **extra):
    steps.append({'name': name, **extra})
    print('✓', name, json.dumps(extra, ensure_ascii=False) if extra else '', flush=True)


def call(method, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f'{base}/lab/api{path}', data=data, method=method, headers={'Content-Type': 'application/json'} if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def until(sid, states, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s = call('GET', f'/agent/sessions/{sid}')
        if s['state'] in states:
            return s
        time.sleep(.5)
    raise TimeoutError(s['state'])


root = args.backend.resolve()
lab = root / 'app' / 'src' / 'lab'
python = root / 'python' / 'python.exe' if (root / 'python' / 'python.exe').exists() else lab / '.venv' / 'bin' / 'python'
home = Path(tempfile.mkdtemp(prefix='urp-smoke-'))
env = {'HOME': str(home), 'USERPROFILE': str(home), 'PATH': os.environ.get('SystemRoot', '') and os.environ['PATH'] or '/usr/bin:/bin', 'URP_LAB_RUNTIME': str(out / 'runtime'),
       'URP_CONFIG_DIR': str(home / 'config'), 'URP_LLM_CONFIG': str(home / 'config' / 'llm.json'), 'URP_LLM_CREDENTIALS': str(home / 'config' / 'credentials.json'),
       'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1', 'URP_DESKTOP': '1'}
for key in ('SystemRoot', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'WINEPREFIX', 'WINEDEBUG', 'WINEDLLOVERRIDES', 'DISPLAY', 'LANG', 'LC_ALL'):   # Wine stores non-ASCII file names through the locale
    if key in os.environ:
        env[key] = os.environ[key]
if args.launcher:
    env['PATH'] = os.environ['PATH']
if args.node:
    env['URP_NODE'] = args.node
if args.packs:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.packs.resolve()))
    handler.log_message = lambda *a: None
    host = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=host.serve_forever, daemon=True).start()
    env['URP_PACKS_BASE_URL'] = f'http://127.0.0.1:{host.server_port}'
log = (out / 'gateway.log').open('wb')
started = time.monotonic()
proc = subprocess.Popen([*([args.launcher] if args.launcher else []), str(python), '-m', 'urp_lab', 'serve', '--port', str(args.port), '--no-archive', '--exit-with-stdin'], cwd=lab, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log)
failed = None
try:
    end = time.monotonic() + 120
    while True:
        try:
            if call('GET', '/health', timeout=2)['status'] == 'ok':
                break
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if proc.poll() is not None or time.monotonic() > end:
            raise RuntimeError(f'网关没有就绪，返回码 {proc.poll()}')
        time.sleep(.3)
    step('后端就绪', seconds=round(time.monotonic() - started, 1))

    catalog = call('GET', '/algorithms')['algorithms']
    missing = {a['id']: a['unavailable_reason'] for a in catalog if not a['available']}
    step('算法目录', total=len(catalog), unavailable=missing)
    assert len(catalog) >= 28 and 'pyvrp.fleet' not in missing and 'ortools.fleet' not in missing

    page = urllib.request.urlopen(f'{base}/', timeout=10).read().decode()
    assert '<div id="root">' in page or 'id="root"' in page
    step('界面可以打开')
    engines = call('GET', '/physics/engines')['engines']
    assert engines[0]['available'], engines
    capabilities = call('GET', '/agent/capabilities', timeout=90)
    step('能力清单', simulators={c['id']: c['status'] for c in capabilities['simulators']}, learners=len(capabilities['learners']))
    providers = call('GET', '/agent/providers')
    assert providers['selected'] is None and providers['catalog']['services'] > 100
    step('模型目录', services=providers['catalog']['services'])

    # Every read-only page data source, so a machine without ROS shows up as a failure here and not as a blank panel.
    spec = json.loads(urllib.request.urlopen(f'{base}/lab/api/openapi.json', timeout=30).read())
    routes = sorted(path for path, ops in spec['paths'].items() if 'get' in ops and '{' not in path and path.startswith('/lab/api/') and path not in SKIP_ROUTES)
    broken = {}
    for path in routes:
        try:
            urllib.request.urlopen(f'{base}{path}', timeout=90).read()
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                broken[path] = f'{exc.code} {exc.read()[:200].decode(errors="replace")}'
    assert not broken, broken
    step('只读接口全部可用', routes=len(routes))

    if not args.no_scenario:
        s = call('POST', '/agent/sessions', {'mode': 'rule', 'text': '在 60×40 米的菜地里，2 台车访问 6 个点，单车容量 4'})
        sid = s['session_id']
        s = until(sid, ('awaiting_confirmation', 'failed'), 60)
        assert s['state'] == 'awaiting_confirmation', s.get('error')
        call('POST', f'/agent/sessions/{sid}/control', {'action': 'confirm'})
        s = until(sid, ('done', 'failed'), 240)
        assert s['state'] == 'done', s.get('error')
        evidence = s['report']['evidence']
        assert evidence['best_verified'] and evidence['physics'], evidence
        passed = [r for r in evidence['physics'] if r['state'] == 'succeeded' and r['check_valid']]
        assert passed, evidence['physics']
        step('任务走完：建图、求解、复核、物理预演、报告', best=evidence['best_verified']['algorithm'], physics=f"{len(passed)}/{len(evidence['physics'])}", wall_s=[r['wall_s'] for r in evidence['physics']])
        together = [r['coordinated'] for r in passed if (r.get('coordinated') or {}).get('check_valid')]
        assert together, evidence['physics']  # Still requires real coordinated physics, never a mock.
        best = evidence['best_verified']
        estimate = best.get('execution_estimate') or {}
        predicted = best.get('predicted_makespan_s')
        if estimate.get('status') == 'estimated':
            assert type(predicted) in (int, float) and math.isfinite(predicted) and predicted > 0, estimate
            assert isinstance(estimate.get('model'), str) and estimate['model'], estimate
        else:
            assert estimate.get('status') in ('unavailable', 'unsupported') and estimate.get('reason'), estimate
            assert predicted is None, estimate
        step('并行执行与可选工期预测', predicted=predicted, execution_estimate=estimate,
             one_by_one=[r['metrics']['makespan_s'] for r in passed], together=[r['makespan_s'] for r in together])
        if args.node:
            scenario = next(x for x in call('GET', '/scenarios')['scenarios'] if x['problem_class'] == 'euclidean_cvrp')
            job = call('POST', '/solve', {'scenario_id': scenario['id'], 'algorithms': [{'id': 'urp.js.savings', 'params': {'time_limit_s': 5}}]})
            end = time.monotonic() + 60
            while True:
                run = call('GET', f"/runs/{job['runs'][0]}")
                if run['state'] not in ('queued', 'running') or time.monotonic() > end:
                    break
                time.sleep(.3)
            assert run['state'] == 'done', run.get('error')
            step('JS 基线', state=run['state'], status=run['status'])

    if args.packs:
        assert urllib.request.urlopen(f'{base}/favicon.svg', timeout=5).status == 200
        pack = call('GET', '/packs')['packs'][0]
        assert pack['state'] == 'absent', pack
        call('POST', f"/packs/{pack['id']}/install", {})
        end = time.monotonic() + 120
        while True:
            pack = call('GET', '/packs')['packs'][0]
            if pack['state'] not in ('downloading', 'installing') or time.monotonic() > end:
                break
            time.sleep(.3)
        assert pack['state'] == 'installed', pack
        catalog_bytes = len(urllib.request.urlopen(f'{base}/data/catalog.json', timeout=10).read())
        step('数据包下载、校验、解压，/data 可用', bytes=pack['bytes'], catalog_bytes=catalog_bytes)
        end = time.monotonic() + 90
        while True:
            try:
                archive = json.loads(urllib.request.urlopen(f'{base}/api/health', timeout=3).read())
                break
            except (urllib.error.URLError, OSError, ValueError):
                if time.monotonic() > end:
                    raise
                time.sleep(1)
        step('档案后端随数据包启动', mode=archive.get('mode'))

    call('POST', '/shutdown', {})
    proc.wait(30)
    step('有序退出', code=proc.returncode)
    assert proc.returncode == 0
    # Exercise the shipped CLI, SQLite WAL handling and Windows paths, not only the host's source environment.
    transfer = [*([args.launcher] if args.launcher else []), str(python), '-m', 'urp_lab', 'data']
    def transfer_call(*words):
        result = subprocess.run([*transfer, *words], cwd=lab, env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    backup = out / 'runtime-backup.zip'
    receipt = transfer_call('export', '--runtime', str(out / 'runtime'), '--out', str(backup))
    restored = out / 'relocated-data'
    checked = transfer_call('restore', '--bundle', str(backup), '--sha256', receipt['sha256'], '--dest', str(restored))
    assert receipt['dataset'] == checked['dataset']
    import sqlite3
    with sqlite3.connect(out / 'runtime/agent.sqlite3') as original, sqlite3.connect(restored / 'runtime/agent.sqlite3') as relocated:
        assert original.execute('SELECT session_id,json FROM sessions ORDER BY session_id').fetchall() == relocated.execute('SELECT session_id,json FROM sessions ORDER BY session_id').fetchall()
    step('安装内数据迁移：导出、校验、异地恢复，完整会话相同', sha256=receipt['sha256'], files=receipt['files'])
except Exception as exc:  # noqa: BLE001 - the record is the point
    failed = f'{type(exc).__name__}: {exc}'
    print('✗', failed, flush=True)
    proc.kill()
finally:
    (out / 'smoke.json').write_text(json.dumps({'passed': failed is None, 'failure': failed, 'python': str(python), 'steps': steps}, ensure_ascii=False, indent=1))
    log.close()
    shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if failed else 0)
