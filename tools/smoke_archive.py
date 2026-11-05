#!/usr/bin/env python3
"""Check archive Jobs and owned-service lifecycle through the workbench gateway.

Job JSON is the existing Jobs UI export contract; no separate export API is invented.
Uses a small synthetic engineering-validation instance, not a research experiment.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(url, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url.rstrip('/') + path, data=data, method=method,
                  headers={'Content-Type': 'application/json'} if data else {})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def wait_archive(url, state, timeout=60, previous_pid=None):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            services = request(url, 'GET', '/lab/api/services')['services']
            last = next(item for item in services if item['id'] == 'archive')
            if last['state'] == state and (previous_pid is None or last.get('pid') != previous_pid):
                return last
            if last['state'] in ('failed', 'unavailable') and state != 'failed':
                raise RuntimeError(f'Archive startup failed: {last}')
        except (HTTPError, URLError) as exc:
            last = str(exc)
        time.sleep(.2)
    raise TimeoutError(f'Archive did not reach {state}: {last}')


def smoke(url, output):
    output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    if request(url, 'GET', '/lab/api/health').get('status') != 'ok':
        raise RuntimeError('Lab is not healthy')
    running = wait_archive(url, 'running')
    if not {'stop', 'restart'} <= set(running['controls']):
        raise RuntimeError('Archive is external or lacks owned lifecycle controls')
    health = request(url, 'GET', '/api/health')
    if health.get('status') != 'ok':
        raise RuntimeError(f'Archive health failed: {health}')
    save('health.json', health)
    catalog = request(url, 'GET', '/api/algorithms')
    if not any(a['id'] == 'savings' and a['available'] for a in catalog['algorithms']):
        raise RuntimeError('Required savings algorithm unavailable')
    save('algorithms.json', catalog)
    receipt = request(url, 'POST', '/api/jobs', {
        'coordinates': [[0, 0], [1, 0], [2, 0]], 'demands': [0, 1, 1],
        'capacity': 2, 'K': 1, 'algorithm': 'savings', 'seed': 0, 'budget_seconds': 1})
    job_path = '/api/jobs/' + receipt['id']
    deadline = time.monotonic() + 30
    while True:
        job = request(url, 'GET', job_path)
        if job['status'] not in ('queued', 'running'):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f'Job timeout: {receipt}')
        time.sleep(.1)
    save('job-export.json', job)
    result = job.get('result') or {}
    checks = result.get('checkpoint_checks') or []
    if (job['status'] != 'completed' or result.get('feasible') is not True
            or result.get('cost') != 4 or not checks or not all(c.get('valid') is True for c in checks)
            or result.get('payload_sha256') != job['payload_sha256']):
        raise RuntimeError(f'Job/check/identity failed: {job}')
    states = [running]
    request(url, 'POST', '/lab/api/services/archive/stop', {})
    stopped = wait_archive(url, 'stopped')
    if 'start' not in stopped['controls']:
        raise RuntimeError('Stopped archive lost its start control')
    states.append(stopped)
    try:
        request(url, 'GET', '/api/health')
    except HTTPError as exc:
        if exc.code != 503:
            raise
    else:
        raise RuntimeError('Stopped archive unexpectedly healthy')
    request(url, 'POST', '/lab/api/services/archive/start', {})
    states.append(wait_archive(url, 'running'))
    request(url, 'POST', '/lab/api/services/archive/restart', {})
    states.append(wait_archive(url, 'running', previous_pid=states[-1]['pid']))
    restored = request(url, 'GET', job_path)
    if restored != job:
        raise RuntimeError('Job export changed across archive restart')
    save('lifecycle.json', states)
    save('summary.json', {'status': 'passed', 'job_id': job['id'],
                          'scope': 'same-origin archive health, catalog, checked Job JSON export, stop/start/restart persistence'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:4100')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    smoke(args.url, args.output)


if __name__ == '__main__':
    main()
