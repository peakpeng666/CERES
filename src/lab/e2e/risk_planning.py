#!/usr/bin/env python3
"""Engineering replication of ground-aware planning, with an in-zone task.

Three fixed choices: ignore, risk_adjust weight .2 (same crossing plus speed cap),
weight 1 (detour plus cap). Every mission must pass production truth/cap checks,
exact exit status and cleanup. Directional trade-offs are separate assertions,
not calibrated soil claims or evidence of algorithm superiority. The former
exploratory protocol inconsistently claimed a <.3 m detour drift bound without
asserting it; that historical result is retained, never called preregistration.
Writes protocol.json before running. Requires sequential local ROS/Gazebo sessions.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
p = argparse.ArgumentParser()
p.add_argument('--out', type=Path, required=True)
p.add_argument('--rtf', type=float, default=6.0)
args = p.parse_args()
if not args.out.is_absolute(): p.error('--out must be absolute')
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
os.environ['CERES_LAB_RUNTIME'] = str(out / 'runtime')
os.environ['CERES_ROS_DOMAIN_ID'] = '144'

from fastapi.testclient import TestClient
from ceres_lab.scenarios.validate import validate_instance
from ceres_lab.gateway import Config, create_app

INSTANCE = validate_instance('fleet_routing', {
    'boundary': [[0, 0], [34, 0], [34, 26], [0, 26]], 'depot': [3, 3], 'robot_radius': 0.5,
    'tasks': [{'id': 1, 'xy': [24, 14], 'demand': 1, 'service_s': 1.0},
              {'id': 2, 'xy': [30, 22], 'demand': 1, 'service_s': 1.0}],
    'vehicles': {'count': 1, 'capacity': 2, 'travel': 'free', 'speed_mps': 1.0},
    'objective': 'total_distance',
    'ground': {'zones': [{'rect': [18, 10, 30, 20], 'mu': 0.12, 'label': '低附着带'}]}})
ZONE = [18, 10, 30, 20]
CAP_TOLERANCE = 0.06      # cap compliance margin for truth speed inside the band
CASES = {'cross_ignore': 1.0, 'cross_capped': 0.2, 'detour_risk': 1.0}   # weight; ignore case uses policy ignore


def save(name, data):
    (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def wait_for(read, predicate, seconds):
    state = None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        state = read()
        if predicate(state):
            return state
        time.sleep(0.5)
    raise TimeoutError(str(state)[:1500])


save('protocol.json', {'schema': 'urp.ground-risk-planning-acceptance/2', 'instance': INSTANCE,
    'cases': CASES, 'rtf': args.rtf,
    'engineering_gates': ['same crossing geometry for the speed-only comparison', 'production fieldcheck including footprint truth speed',
                          'nonzero real wheel feedback', 'exact exit code and no residual owned processes'],
    'directional_checks': {'uncapped_drift_m_min': .5, 'capped_drift': 'below uncapped', 'detour_exposure_reduction_m_min': 5},
    'scope': 'engineering replication of an exploratory hypothesis; no fixed small detour-drift claim; no real soil calibration'})


app = create_app(Config(runtime=out / 'runtime', port=4142, field_sim_port=4195,
                        background=False, archive_autostart=False, extra_hosts={'testserver'}))
results = {}
try:
    with TestClient(app) as c:
        api = '/lab/api'
        sc = c.post(api + '/compose', json={'text': '34×26米田块，1台车，2个采样点，容量2', 'seed': 72}).json()
        runs = {}
        for case, weight in CASES.items():
            params = {'time_limit_s': 2, 'seed': 42, 'ground_risk_weight': weight}
            if case != 'cross_ignore':
                params['ground_policy'] = 'risk_adjust'
            submitted = c.post(api + '/solve', json={'scenario_id': sc['id'], 'instance': INSTANCE,
                                                     'algorithms': [{'id': 'pyvrp.fleet', 'params': params}]})
            assert submitted.status_code == 202, submitted.text
            rid = wait_for(lambda: c.get(api + '/runs/' + submitted.json()['runs'][0]).json(),
                           lambda r: r['state'] not in ('queued', 'running'), 30)
            assert rid['state'] == 'done' and rid['check']['valid'], rid.get('log')
            runs[case] = rid
            save(f'{case}-planning.json', c.get(api + f"/runs/{rid['run_id']}/export").json())

        assert runs['cross_ignore']['routes'][0]['polyline'] == runs['cross_capped']['routes'][0]['polyline'], 'speed-only comparison needs identical directed geometry'
        for case in CASES:
            began = time.monotonic()
            started = c.post(api + '/fieldsim', json={'run_id': runs[case]['run_id'],
                                                      'real_time_factor': args.rtf, 'web': False})
            assert started.status_code == 202, started.text
            sid = started.json()['session_id']
            runtime = out / 'runtime' / 'fieldsim' / sid

            def state():
                return c.get(api + '/fieldsim').json()

            try:
                wait_for(state, lambda s: s['phase'] in ('finished', 'failed') or not s['running'], 700)
            finally:
                c.post(api + '/fieldsim/stop', json={}).raise_for_status()
                closed = wait_for(state, lambda s: not s['running'], 120)
            result = c.get(api + f'/fieldsim/results/{sid}').json()
            plan = json.loads((runtime / 'plan.json').read_text())
            report = json.loads((runtime / 'session-report.json').read_text()) if (runtime / 'session-report.json').is_file() else {}
            route = runs[case]['routes'][0]
            stats = route.get('ground_stats') or {}
            checks, failures = [], []

            def expect(label, ok):
                checks.append({'check': label, 'ok': bool(ok)})
                if not ok:
                    failures.append(label)

            m = result.get('metrics', {})
            slip = ((result.get('execution_report') or {}).get('slip') or {}).get('per_vehicle') or []
            divergence = max((row.get('odom_truth_divergence_max_m') or 0 for row in slip), default=0.0)
            expect('求解后独立检查通过', (runs[case].get('check') or {}).get('valid') is True)
            gk = plan.get('ground_knowledge', {})
            expect('世界采用声明的已知地面图', gk.get('world_origin') == 'instance_ground'
                   and [[z['rect'], z['mu']] for z in (gk.get('planner') or [])]
                       == [[z['rect'], z['mu']] for z in (gk.get('world') or [])])
            expect('会话清理 PASS', report.get('cleanup_status') == 'PASS' and not report.get('remaining_owned_processes'))
            expect('真实退出码', closed.get('returncode') == (0 if result.get('state') == 'succeeded' else 1))
            expect('任务真值验收通过', result.get('state') == 'succeeded' and m.get('tasks_reached') == m.get('tasks_total'))
            if case == 'cross_ignore':
                expect('路线穿越低附着带 (>20 m，含区内任务停留)', stats.get('in_zone_length_m', 0) > 20)
                expect('无限速剖面（策略未用地面图）', not route.get('speed_profile'))
                expect('区内机动产生物理滑移：发散 > 0.5 m', divergence > 0.5)
            if case == 'cross_capped':
                expect('路线仍穿越 (>20 m)', stats.get('in_zone_length_m', 0) > 20)
                expect('规划限速剖面存在', bool(route.get('speed_profile')))
                expect('正式后端足迹真值限速检查通过', (result.get('check', {}).get('ground_speed') or {}).get('valid') is True)
                expect('限速降低滑移：发散 < 未限速穿越', divergence < results['cross_ignore']['divergence_m'])
            if case == 'detour_risk':
                expect('风险权重显著减少区内暴露 (>5 m)', stats.get('in_zone_length_m', 99) + 5 < runs['cross_ignore']['routes'][0]['ground_stats']['in_zone_length_m'])
                expect('滑移已量化记录（贴边返程段现象保留为发现）', slip != [])
                expect('绕行几何代价：里程 > 穿越路线', route['cost'] > runs['cross_ignore']['routes'][0]['cost'])
                expect('绕行时间代价：makespan > 穿越路线', (m.get('makespan_s') or 0) > (results['cross_ignore']['makespan_s']))
            results[case] = {
                'session_id': sid, 'state': result.get('state'),
                'route_geo_m': route.get('cost'), 'risk_cost': route.get('risk_cost'),
                'in_zone_m': stats.get('in_zone_length_m'), 'speed_profile': route.get('speed_profile'),
                'makespan_s': m.get('makespan_s'), 'total_distance_m': m.get('total_distance_m'),
                'cross_track_max_m': m.get('cross_track_max_m'),
                'divergence_m': round(divergence, 3), 'slip': slip,
                'check': result.get('check'), 'errors': result.get('errors'), 'returncode': closed.get('returncode'),
                'cleanup': report.get('cleanup_status'), 'remaining': report.get('remaining_owned_processes'),
                'checks': checks, 'wall_s': round(time.monotonic() - began, 1),
                'runtime_dir': str(runtime)}
            save(f'{case}-result.json', result)
            save(f'{case}-summary.json', results[case])
            print(f"[{case}] state={result.get('state')} div={divergence:.2f} checks={'OK' if not failures else failures}", flush=True)

    overall = all(c['ok'] for r in results.values() for c in r['checks'])
    summary = {'schema': 'urp.ground-risk-planning-acceptance/2',
               'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'status': 'PASS' if overall else 'FAIL',
               'cases': results,
               'zone': ZONE, 'cap_tolerance_mps': CAP_TOLERANCE,
               'scope': '已知地图条件下的风险规划：声明地面 → 风险边权/限速 → 独立检查 → 真值定位物理执行 → '
                        '滑移暴露与里程/时间代价对照；风险成本为当量米，不是失败概率',
               'thresholds': {'cross_divergence_m': 0.5, 'capped_speed_mps': 0.41}}
    save('summary.json', summary)
    print(json.dumps({'status': summary['status'],
                      'cases': {k: {'div': v['divergence_m'], 'in_zone': v['in_zone_m'],
                                    'geo': v['route_geo_m'], 't': v['makespan_s']} for k, v in results.items()}},
                     ensure_ascii=False))
    sys.exit(0 if overall else 1)
except Exception as exc:
    save('summary.json', {'status': 'ERROR', 'error': str(exc)[:2000], 'cases': results})
    print(f'ERROR: {exc}', file=sys.stderr)
    sys.exit(2)
