#!/usr/bin/env python3
"""Exercise an already running source Electron Lab through its real simulation UI.

Run with Lab's Python: source_sim2sim.py --cdp http://127.0.0.1:42873 --out /new/absolute/path
Creates one small planning record, then clicks the actual Gazebo start/close and
MuJoCo comparison controls for serial and coordinated traffic. Never calls a model,
changes existing records, or stops a session it did not start. Raw runs stay in Lab.
"""
import argparse
import base64
import json
from pathlib import Path
import time
import urllib.request

from websockets.sync.client import connect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cdp', default='http://127.0.0.1:42873')
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
if not args.out.is_absolute():
    parser.error('--out must be absolute')
args.out.mkdir(parents=True, exist_ok=False)
summary = {'scope': 'real Electron controls, Gazebo Fortress and frozen-plan MuJoCo replay; no real robot validation', 'cases': []}
pages = json.load(urllib.request.urlopen(args.cdp + '/json/list', timeout=5))
page = next(p for p in pages if p.get('type') == 'page' and p.get('url', '').startswith('http://127.0.0.1:'))
connection = connect(page['webSocketDebuggerUrl'], max_size=32 * 1024 * 1024)
ws = connection.__enter__()
seq = 0
owned_session = None


def command(method, params=None):
    global seq
    seq += 1
    ws.send(json.dumps({'id': seq, 'method': method, 'params': params or {}}))
    while True:
        value = json.loads(ws.recv(timeout=30))
        if value.get('id') == seq:
            if value.get('error'):
                raise RuntimeError(value['error'])
            return value.get('result', {})


def js(expression):
    result = command('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
    if result.get('exceptionDetails'):
        raise RuntimeError(result['exceptionDetails'])
    return result['result'].get('value')


def api(path, body=None, method=None):
    method = method or ('POST' if body is not None else 'GET')
    value = js('''(async ([path, method, body]) => {
      const r = await fetch('/lab/api' + path, {method, headers: {'Content-Type':'application/json'}, body:body === null ? undefined : JSON.stringify(body)});
      return {status:r.status, body:await r.json()};
    })(''' + json.dumps([path, method, body]) + ')')
    assert value['status'] < 400, value
    return value['body']


def until(read, predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if predicate(value):
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(str(value)[:1000])
        time.sleep(.5)


def click(text, role=None, contains=False):
    expression = '''([label,role,contains]) => {
      const candidates=[...document.querySelectorAll(role ? '[role="'+role+'"]' : 'button')].filter(x=>x.getClientRects().length && !x.closest('[hidden]'));
      const el=candidates.find(x=>contains ? x.textContent.includes(label) : x.textContent.trim()===label);
      if(!el || el.disabled) return false;
      el.click(); return true;
    }'''
    until(lambda: js('(' + expression + ')(' + json.dumps([text, role, contains]) + ')'), bool, 15)


def select(label, value):
    assert js('''(([label,value])=>{
      const el=document.querySelector('select[aria-label="'+label+'"]');
      if(!el || el.disabled) return false;
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(el,value);
      el.dispatchEvent(new Event('change',{bubbles:true}));return el.value===value;
    })(''' + json.dumps([label, value]) + ')')


def shot(name):
    data = command('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
    (args.out / f'{name}.png').write_bytes(base64.b64decode(data['data']))


try:
    assert js('!!window.desktop'), 'This must be the Electron page'
    summary['health'] = api('/health')
    summary['engines'] = api('/physics/engines')
    assert summary['engines']['gazebo_gap'] is None, summary['engines']
    assert any(e['id'] == 'mujoco' and e['available'] for e in summary['engines']['engines'])
    assert not api('/fieldsim')['running'], 'A pre-existing field session must remain untouched'
    scene = api('/compose', {'text': '40×30米果园，2台车，2个采样点，每车容量1', 'seed': 1106, 'overrides': {'plots': 0}})
    instance = {'boundary': [[0, 0], [40, 0], [40, 30], [0, 30]], 'depot': [5, 5],
                'tasks': [{'id': 1, 'xy': [12, 6], 'service_s': 2, 'time_window': [90, 600]},
                          {'id': 2, 'xy': [22, 15], 'service_s': 2, 'time_window': [90, 600]}],
                'vehicles': {'count': 2, 'capacity': 1, 'travel': 'free', 'speed_mps': 1, 'trips': 'single'}}
    rid = api('/solve', {'scenario_id': scene['id'], 'instance': instance,
                         'algorithms': [{'id': 'ortools.fleet', 'params': {'time_limit_s': 2, 'seed': 1106}}]})['runs'][0]
    run = until(lambda: api('/runs/' + rid), lambda x: x['state'] not in ('queued', 'running'), 30)
    assert run['state'] == 'done' and run['check']['valid'] and len(run['routes']) == 2, run
    summary['run_id'] = rid
    summary['scenario_id'] = scene['id']
    for traffic in ('serial', 'coordinated'):
        js("location.hash='#/sim'")
        click('田间仿真', role='tab')
        select('Gazebo 交通方式', traffic)
        select('仿真倍速', '10')
        click(scene['id'], contains=True)
        state = until(lambda: api('/fieldsim'), lambda x: bool(x.get('session_id')) and x['running'] and x.get('run_id') == rid, 20)
        owned_session = state['session_id']
        until(lambda: api('/fieldsim'), lambda x: x['phase'] in ('running', 'finished', 'failed'), 100)
        click('Gazebo 场景', role='tab')
        time.sleep(2)
        shot(traffic + '-gazebo')
        state = until(lambda: api('/fieldsim'), lambda x: x['phase'] in ('finished', 'failed'), 180)
        assert state['phase'] == 'finished', {'phase': state['phase'], 'errors': state.get('errors')}
        click('关闭仿真')
        stopped = until(lambda: api('/fieldsim'), lambda x: not x['running'], 30)
        assert stopped.get('returncode') == 0, stopped.get('errors')
        gazebo = api('/fieldsim/results/' + owned_session)
        assert gazebo['state'] == 'succeeded' and gazebo['check']['valid'], gazebo.get('errors')
        assert gazebo['traffic_mode'] == traffic, gazebo.get('traffic_mode')
        assert gazebo['metrics']['tasks_completed'] == 2
        assert gazebo['time_windows']['all_on_time'] and not gazebo['time_windows'].get('early_starts'), gazebo['time_windows']
        assert gazebo['physics_audit']['valid']
        assert any(t.get('service_started_at', 0) - t.get('t_sim', 0) > 20 for t in gazebo['tasks']), 'Earliest-window waiting was not exercised'
        case = {'traffic': traffic, 'session_id': owned_session, 'gazebo': {k: gazebo.get(k) for k in ('state', 'traffic_mode', 'metrics', 'time_windows', 'plan_identity')}, 'web_components': sorted((state.get('web') or {}).keys())}
        case['gazebo']['check_valid'] = gazebo['check']['valid']
        prior = {r['physics_run_id'] for r in api('/physics/runs')['runs']}
        click('物理预演', role='tab')
        # This fixture's newly completed session is first in the newest-first result list.
        click('对比')
        rec = until(lambda: next((r for r in api('/physics/runs')['runs'] if r['physics_run_id'] not in prior and r.get('session_id') == owned_session), None), bool, 20)
        rec = until(lambda: api('/physics/runs/' + rec['physics_run_id']), lambda r: r['state'] not in ('queued', 'running'), 90)
        assert rec['state'] == 'succeeded' and rec['check']['valid'], rec.get('errors')
        assert rec['traffic_mode'] == traffic
        assert rec['comparison']['available'], rec['comparison']
        assert rec['comparison']['path_max_m'] is not None
        case['mujoco'] = {k: rec.get(k) for k in ('physics_run_id', 'state', 'traffic_mode', 'metrics', 'time_windows', 'comparison')}
        click(('逐车' if traffic == 'serial' else '并行') + ' · 最大差', contains=True)
        time.sleep(.5)  # let the disclosure animation finish before capturing its values
        shot(traffic + '-comparison')
        case['displayed_comparison'] = js("document.body.innerText.includes('轨迹差')")
        assert case['displayed_comparison']
        summary['cases'].append(case)
        (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps({'traffic': traffic, 'session_id': owned_session, 'gazebo': gazebo['state'], 'mujoco': rec['state'], 'path_max_m': rec['comparison']['path_max_m']}, ensure_ascii=False), flush=True)
        owned_session = None
    summary['passed'] = True
except BaseException as exc:
    summary.update(passed=False, failure={'type': type(exc).__name__, 'message': str(exc)[:1800]})
    try:
        shot('failure')
    except Exception:
        pass
    raise
finally:
    if owned_session:
        state = api('/fieldsim')
        if state.get('session_id') == owned_session and state.get('running'):
            api('/fieldsim/stop', {})
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    connection.__exit__(None, None, None)
