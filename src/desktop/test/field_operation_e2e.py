#!/usr/bin/env python3
"""Field operation through the real Electron controls: design, solve, verify, Gazebo, MuJoCo comparison.

Run with Lab's Python: field_operation_e2e.py --cdp http://127.0.0.1:42873 --out /new/absolute/path
Uses a small field for the reference robot, serial and coordinated traffic. Also checks that a planning-only
rig has no simulation entry. Never calls a model or stops a session it did not start.
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
summary = {'scope': 'field operation in real Electron controls, Gazebo Fortress and frozen-plan MuJoCo replay; no implement dynamics, no real robot', 'cases': []}
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


SIZE = {'width_m': 20, 'height_m': 12}


def set_number(label, value):
    assert js("""(([label,value])=>{
      const box=[...document.querySelectorAll('label')].find(x=>x.querySelector('span')?.textContent.startsWith(label));
      const el=box?.querySelector('input'); if(!el) return false;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,String(value));
      el.dispatchEvent(new Event('input',{bubbles:true})); return true;
    })(""" + json.dumps([label, value]) + ')')


def preview_lines():
    return js("document.querySelectorAll('[aria-label=\\u7530\\u5757\\u9884\\u89c8] line').length")


try:
    assert js('!!window.desktop'), 'This must be the Electron page'
    summary['health'] = api('/health')
    summary['engines'] = api('/physics/engines')
    assert summary['engines']['gazebo_gap'] is None, summary['engines']
    assert not api('/fieldsim')['running'], 'A pre-existing field session must remain untouched'

    # A planning-only rig: the plan exists, the simulation entry does not.
    js("location.hash='#/field'")
    click('大田植保', contains=True)
    until(lambda: preview_lines(), bool, 15)
    shot('design-boom')
    click('生成并求解')
    until(lambda: js('location.hash'), lambda h: h.startswith('#/plan/field-'), 20)
    click('方案', role='tab')
    until(lambda: js("document.body.innerText.includes('比顺序往复')"), bool, 40)
    time.sleep(1)
    shot('plan-boom')
    summary['planning_rig_has_gazebo'] = js("[...document.querySelectorAll('button')].some(x=>x.textContent.trim()==='Gazebo')")
    assert not summary['planning_rig_has_gazebo']

    # The reference robot, from the design page through to both engines.
    js("location.hash='#/field'")
    click('机器人田块', contains=True)
    time.sleep(.5)
    set_number('长', SIZE['width_m'])
    set_number('宽', SIZE['height_m'])
    time.sleep(1.2)
    shot('design-robot')
    click('生成并求解')
    until(lambda: js('location.hash'), lambda h: h.startswith('#/plan/field-'), 20)
    scenario_id = js('location.hash').split('/')[-1]
    click('方案', role='tab')
    until(lambda: js("document.body.innerText.includes('比顺序往复')"), bool, 40)
    runs = api('/runs?scenario_id=' + scenario_id + '&limit=10')['runs']
    done = [r for r in runs if r['state'] == 'done' and (r.get('check') or {}).get('valid')]
    assert len(done) >= 3, [(r['algorithm']['id'], r['state'], r['status']) for r in runs]
    summary['plans'] = {r['algorithm']['id']: {k: r['metrics'].get(k) for k in ('nonworking_distance_m', 'makespan_s', 'field_efficiency', 'refills', 'coverage_fraction')} for r in done}
    chosen = next(r for r in done if r['algorithm']['id'] == 'agri.split_anneal')
    rid = chosen['run_id']
    summary.update(run_id=rid, scenario_id=scenario_id)
    click('验算', role='tab')
    time.sleep(.5)
    shot('verify-robot')
    for traffic in ('serial', 'coordinated'):
        api('/fieldsim', {'run_id': rid, 'real_time_factor': 10, 'traffic': traffic, 'gui': False, 'rviz': False})
        state = until(lambda: api('/fieldsim'), lambda x: bool(x.get('session_id')) and x['running'] and x.get('run_id') == rid, 20)
        owned_session = state['session_id']
        js("location.hash='#/sim'")
        state = until(lambda: api('/fieldsim'), lambda x: x['phase'] in ('finished', 'failed'), 420)
        assert state['phase'] == 'finished', {'phase': state['phase'], 'errors': state.get('errors')}
        click('田间仿真', role='tab')
        time.sleep(1)
        shot(traffic + '-gazebo')
        click('关闭仿真')
        stopped = until(lambda: api('/fieldsim'), lambda x: not x['running'], 30)
        assert stopped.get('returncode') == 0, stopped.get('errors')
        gazebo = api('/fieldsim/results/' + owned_session)
        assert gazebo['state'] == 'succeeded' and gazebo['check']['valid'], gazebo.get('errors')
        case = {'traffic': traffic, 'session_id': owned_session, 'gazebo': {k: gazebo.get(k) for k in ('state', 'traffic_mode', 'metrics')}}
        prior = {r['physics_run_id'] for r in api('/physics/runs')['runs']}
        click('物理预演', role='tab')
        click('对比')
        rec = until(lambda: next((r for r in api('/physics/runs')['runs'] if r['physics_run_id'] not in prior and r.get('session_id') == owned_session), None), bool, 20)
        rec = until(lambda: api('/physics/runs/' + rec['physics_run_id']), lambda r: r['state'] not in ('queued', 'running'), 240)
        assert rec['state'] == 'succeeded' and rec['check']['valid'], rec.get('errors')
        assert rec['comparison']['available'], rec['comparison']
        case['mujoco'] = {k: rec.get(k) for k in ('physics_run_id', 'state', 'traffic_mode', 'metrics', 'comparison')}
        time.sleep(.5)
        shot(traffic + '-comparison')
        summary['cases'].append(case)
        (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps({'traffic': traffic, 'session': owned_session, 'gazebo': gazebo['state'], 'mujoco': rec['state'], 'path_max_m': rec['comparison'].get('path_max_m')}, ensure_ascii=False), flush=True)
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
