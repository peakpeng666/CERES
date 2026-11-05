#!/usr/bin/env python3
"""Exercise a running workbench: compose, recommend, solve, check, export and compare."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def smoke(url, output):
    output.mkdir(parents=True, exist_ok=False)

    def request(method, path, body=None):
        content = json.dumps(body).encode() if body is not None else None
        req = Request(url.rstrip('/') + path, data=content, method=method,
                      headers={'Content-Type': 'application/json'} if content else {})
        with urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw

    def save(name, data):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    health = request('GET', '/lab/api/health')
    if health.get('status') != 'ok':
        raise RuntimeError(f'Gateway health failed: {health}')
    page = request('GET', '/')
    if not isinstance(page, bytes) or b'<html' not in page.lower():
        raise RuntimeError('Frontend entry did not return HTML')
    scenario = request('POST', '/lab/api/compose', {'text': '60×40米果园，2台车，8个采样点，每车容量4，1个池塘'})
    save('scenario.json', scenario)
    save('recommendation.json', request('POST', '/lab/api/recommend', {'scenario_id': scenario['id']}))
    submitted = request('POST', '/lab/api/solve', {'scenario_id': scenario['id'], 'algorithms': [
        {'id': 'pyvrp.fleet', 'params': {'time_limit_s': .3, 'seed': 42}}, 'panel.savings_fleet']})
    deadline = time.monotonic() + 60
    runs = []
    for rid in submitted['runs']:
        while True:
            run = request('GET', f'/lab/api/runs/{rid}')
            if run['state'] not in ('queued', 'running'):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f'Solver did not finish: {rid}')
            time.sleep(.1)
        save(f'{rid}.json', run)
        if run['state'] != 'done' or not (run.get('check') or {}).get('valid'):
            raise RuntimeError(f"Solver/check failed: {rid}, {run['state']}")
        exported = request('GET', f'/lab/api/runs/{rid}/export?format=json')
        if exported['instance'] != run['instance'] or exported['scenario_snapshot']['id'] != scenario['id']:
            raise RuntimeError(f'Export input mismatch: {rid}')
        save(f'{rid}-export.json', exported)
        save(f'{rid}-record.json', request('GET', f'/lab/api/runs/{rid}/record'))
        runs.append({'run_id': rid, 'algorithm': run['algorithm']['id'], 'cost': run['objective']['value'],
                     'checked': run['check']['valid'], 'returncode': run['process']['returncode']})
    analysis = request('POST', '/lab/api/analysis', {'run_ids': submitted['runs']})
    save('analysis.json', analysis)
    if analysis.get('empty') or analysis.get('n_runs') != 2 or analysis.get('n_complete_instances') != 1:
        raise RuntimeError('Comparison did not contain both checked runs on one instance')
    summary = {'status': 'passed', 'scenario_id': scenario['id'], 'runs': runs,
               'scope': 'Live HTTP gateway and real solvers/checkers; HTML entry, JSON exports and analysis.'}
    save('summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:4100')
    parser.add_argument('--output', type=Path, default=ROOT / '.local-state' / 'smoke' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = parser.parse_args()
    smoke(args.url, args.output)


if __name__ == '__main__':
    main()
