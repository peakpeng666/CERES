#!/usr/bin/env python3
"""Legacy exploratory protocol: tire slip (friction), ground friction zones and
localization error, in single-factor, combined and should-fail conditions.

Run from src/lab: .venv/bin/python e2e/nonideal_backend.py --out /absolute/evidence/path
Requires the existing ROS/Gazebo installation. Isolated store, ROS domain and runtime;
never controls foreign sessions and never writes production records.

Historical directional assertions (retained to reproduce earlier expectations):
- ideal: mission succeeds; no-slip reference still consistent; odom excess < 0.3 m.
- slip_uniform (μ=0.3 everywhere, truth localization): mission completes; odom-truth
  divergence grows beyond 0.5 m (ideal reference: ≤0.10 m by contract, observed ~0.006)
  — slip enters through physics, not post-processing.
- ground_zones (μ=0.12 strips, truth localization): mission completes; the zone-
  crossing vehicle's divergence grows beyond 0.5 m while a vehicle whose route misses
  the zones stays below 0.3 m (single-factor isolation); zones recorded and audited.
- loc_error (ideal physics, biased/drifty fixes): mission completes; estimate error
  mean 0.03–0.4 m; no physical slip (odom divergence < 0.3 m); no interventions.
- outage (10 s GNSS gap < 15 s hold threshold): drops recorded with reasons; no
  localization hold; honest outcome either way.
- outage_hold (light noise + 20 s gap > threshold): localization hold fires, vehicle
  resumes after the gap, mission completes; hold counted as an intervention.
- combined (wet zone + drift/outage): executed with both observables present.
- extreme (μ=0.05 mud + severe localization): NOT a clean success — either an honest
  failure or at least one recorded online intervention.

These expectations were revised during GLM debugging, not independently preregistered.
Current acceptance is e2e/nonideal_audit.py. The current stricter truth dwell gate may
reject outcomes that this old protocol expected to succeed; keep those failures.

Amendment history (metric choices corrected after observed results):
- Round 1 found two integration defects, fixed before the rerun: (1) zone-box contacts
  were misclassified as foreign collisions; (2) physics-deviated sessions still ran the
  ideal no-slip gate. The extreme case's "20 s outage triggers hold" assertion moved to
  the dedicated outage_hold case because extreme legitimately ends earlier via slip
  protection.
- Round 2 showed the friction-slip mechanism at these μ manifests as *heading* slip:
  odom-truth divergence reaches 14.5–14.7 m while scalar path-length excess stays
  0.13–0.28 m. The slip_uniform/ground_zones directional assertions therefore use
  divergence (the same quantity the no-slip reference contract bounds) instead of
  path-length excess; loc_error's no-slip assertion likewise uses divergence.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
p = argparse.ArgumentParser()
p.add_argument('--out', type=Path, required=True)
p.add_argument('--cases', default='ideal,slip_uniform,ground_zones,loc_error,outage,outage_hold,combined,extreme')
p.add_argument('--rtf', type=float, default=8.0)
args = p.parse_args()
if not args.out.is_absolute():
    p.error('--out must be absolute')
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
os.environ['CERES_LAB_RUNTIME'] = str(out / 'runtime')
os.environ['CERES_ROS_DOMAIN_ID'] = '143'

from fastapi.testclient import TestClient
from ceres_lab.gateway import Config, create_app
from ceres_lab.scenarios.validate import validate_instance

CASES = {
    'ideal': {'physics': None, 'execution': None},
    'slip_uniform': {'physics': {'ground_friction': 0.3, 'wheel_friction': 0.3}, 'execution': None},
    'ground_zones': {'physics': None, 'execution': {'ground_preset': 'loose_strip', 'localization_preset': 'truth', 'seed': 20261018}},
    'loc_error': {'physics': None, 'execution': {'localization_preset': 'light', 'seed': 20261018}},
    'outage': {'physics': None, 'execution': {'localization_preset': 'drift_outage', 'seed': 20261018}},
    'outage_hold': {'physics': None, 'execution': {'localization_preset': 'hold', 'seed': 20261018}},
    'combined': {'physics': None, 'execution': {'ground_preset': 'wet_patch', 'localization_preset': 'drift_outage', 'seed': 20261018}},
    'extreme': {'physics': None, 'execution': {'ground_preset': 'mud_extreme', 'localization_preset': 'severe', 'seed': 20261018}},
}


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
    raise TimeoutError(str(state)[:1800])


app = create_app(Config(runtime=out / 'runtime', port=4141, field_sim_port=4194,
                        background=False, archive_autostart=False, extra_hosts={'testserver'}))
results = {}
try:
    with TestClient(app) as c:
        api = '/lab/api'
        sc = c.post(api + '/compose', json={'text': '60×40米果园，2台车，8个采样点，每车容量4，1个池塘',
                                            'seed': 11, 'overrides': {'plots': 1}}).json()
        save('scenario.json', sc)
        inst = json.loads(json.dumps(sc['instance']))
        inst['vehicles'].update(travel='free', speed_mps=1.0)
        for t in inst['tasks']:
            t['service_s'] = 2.0
        inst = validate_instance('fleet_routing', inst)
        submitted = c.post(api + '/solve', json={'scenario_id': sc['id'], 'instance': inst,
                           'algorithms': [{'id': 'pyvrp.fleet', 'params': {'time_limit_s': 1, 'seed': 42}}]})
        assert submitted.status_code == 202, submitted.text
        rid = wait_for(lambda: c.get(api + '/runs/' + submitted.json()['runs'][0]).json(),
                       lambda r: r['state'] not in ('queued', 'running'), 30)
        assert rid['state'] == 'done' and rid['check']['valid'], rid['log']
        run_id = rid['run_id']
        save('planning.json', c.get(api + f'/runs/{run_id}/export').json())

        for case in args.cases.split(','):
            spec = CASES[case]
            began = time.monotonic()
            body = {'run_id': run_id, 'real_time_factor': args.rtf}
            if spec['physics']:
                body['physics'] = spec['physics']
            if spec['execution']:
                body['execution'] = spec['execution']
            started = c.post(api + '/fieldsim', json=body)
            assert started.status_code == 202, started.text
            sid = started.json()['session_id']
            runtime = out / 'runtime' / 'fieldsim' / sid

            def state():
                return c.get(api + '/fieldsim').json()

            final = wait_for(state, lambda s: s['phase'] in ('finished', 'failed') or not s['running'], 700)
            c.post(api + '/fieldsim/stop', json={}).raise_for_status()
            closed = wait_for(state, lambda s: not s['running'], 120)
            result = c.get(api + f'/fieldsim/results/{sid}').json()
            report = json.loads((runtime / 'session-report.json').read_text()) if (runtime / 'session-report.json').is_file() else None
            m = result.get('metrics', {})
            er = result.get('execution_report') or {}
            checks, failures = [], []

            def expect(label, ok):
                checks.append({'check': label, 'ok': bool(ok)})
                if not ok:
                    failures.append(label)

            expect('会话清理 PASS', (report or {}).get('cleanup_status') == 'PASS')
            expect('任务结果真实记录', result.get('state') in ('succeeded', 'failed', 'canceled'))
            dyn = ((result.get('check') or {}).get('dynamics') or {})
            if case == 'ideal':
                expect('理想参考任务完成', result['state'] == 'succeeded')
                expect('独立检查通过', (result.get('check') or {}).get('valid') is True)
                expect('无滑移一致性保持', all(v.get('no_slip_consistent') is True for v in dyn.get('vehicles', [])))
                expect('里程计发散 < 0.3 m', (m.get('odom_divergence_max_m') or 0) < 0.3)
                expect('无非理想字段', result.get('pose_estimates') is None and result.get('execution_events') is None)
            if case == 'slip_uniform':
                expect('均匀低附着任务仍完成', result['state'] == 'succeeded')
                expect('物理滑移使里程计发散 > 0.5 m', (m.get('odom_divergence_max_m') or 0) > 0.5)
                expect('真值轨迹跟踪门禁以真值判定', (result.get('check') or {}).get('valid') is True)
            if case == 'ground_zones':
                expect('摩擦分区任务完成', result['state'] == 'succeeded')
                slip_rows = er.get('slip', {}).get('per_vehicle') or []
                diverged = [r for r in slip_rows if (r.get('odom_truth_divergence_max_m') or 0) > 0.5]
                clean = [r for r in slip_rows if (r.get('odom_truth_divergence_max_m') or 0) < 0.3]
                expect('跨分区车辆发散 > 0.5 m', bool(diverged))
                expect('不跨分区车辆保持 < 0.3 m（单因素隔离）', bool(clean))
                zones = ((result.get('execution') or {}).get('ground') or {}).get('zones') or []
                expect('分区已记录并审计', len(zones) >= 2 and (runtime / 'plan.json').is_file()
                       and json.loads((runtime / 'plan.json').read_text()).get('ground_zones_audit', {}).get('valid'))
            if case == 'loc_error':
                expect('定位误差下任务完成', result['state'] == 'succeeded')
                est_err = m.get('localization_position_error_mean_m')
                expect('估计误差在模型包络内 (0.03–0.4 m)', est_err is not None and 0.03 <= est_err <= 0.4)
                expect('理想物理无滑移 (里程计发散 < 0.3 m)', (m.get('odom_divergence_max_m') or 0) < 0.3)
                expect('无在线干预', (m.get('interventions') or 0) == 0)
                per = (er.get('localization') or {}).get('per_vehicle') or {}
                expect('观测按声明频率生成', all(v.get('fixes_generated', 0) > 40 for v in per.values()))
            if case == 'outage':
                per = (er.get('localization') or {}).get('per_vehicle') or {}
                expect('失锁丢弃已记录', sum(v.get('fixes_dropped', 0) for v in per.values()) >= 8)
                events = result.get('execution_events') or []
                expect('10 s 失锁不触发停车保持', not any(e['kind'] == 'localization_hold' for e in events))
                expect('任务诚实完成或失败', result['state'] in ('succeeded', 'failed'))
                if result['state'] == 'succeeded':
                    expect('真值跟踪门禁仍通过', (result.get('check') or {}).get('valid') is True)
            if case == 'combined':
                est_err = m.get('localization_position_error_mean_m')
                expect('组合扰动下估计误差已评价', est_err is not None)
                expect('组合扰动物理效应已观测', (m.get('odom_divergence_max_m') is not None))
                if result['state'] == 'succeeded':
                    expect('成功则须通过独立检查', (result.get('check') or {}).get('valid') is True)
                else:
                    expect('失败原因已记录', bool(result.get('errors')))
            if case == 'outage_hold':
                events = result.get('execution_events') or []
                kinds = [e['kind'] for e in events]
                expect('20 s 失锁触发定位保持', 'localization_hold' in kinds)
                expect('失锁恢复后继续执行', 'localization_resume' in kinds)
                expect('保持计为干预', (m.get('interventions') or 0) >= 1)
                expect('保持后任务完成', result['state'] == 'succeeded')
                if result['state'] == 'succeeded':
                    expect('成功须通过独立检查', (result.get('check') or {}).get('valid') is True)
            if case == 'extreme':
                events = result.get('execution_events') or []
                clean = result['state'] == 'succeeded' and not (m.get('interventions') or 0)
                expect('极端条件不是干净成功（失败或干预）', not clean)
                expect('估计器模式下无真值直连', result.get('pose_estimates') is not None)
            results[case] = {
                'session_id': sid, 'state': result.get('state'), 'returncode': closed.get('returncode'), 'cleanup_status': (report or {}).get('cleanup_status'),
                'metrics': {k: m.get(k) for k in ('tasks_reached', 'tasks_total', 'makespan_s', 'total_distance_m',
                                                  'cross_track_mean_m', 'cross_track_max_m', 'odom_excess_total_m',
                                                  'odom_divergence_max_m', 'localization_position_error_mean_m',
                                                  'localization_position_error_max_m', 'interventions',
                                                  'localization_outage_total_s')},
                'execution': result.get('execution', {}).get('mode', 'ideal') if result.get('execution') else 'ideal',
                'ground_preset': ((result.get('execution') or {}).get('ground') or {}).get('preset'),
                'localization_preset': ((result.get('execution') or {}).get('localization') or {}).get('preset'),
                'events': result.get('execution_events'),
                'slip': (er.get('slip') or {}).get('per_vehicle'),
                'localization': {k: v for k, v in (er.get('localization') or {}).items() if k != 'per_vehicle'},
                'errors': result.get('errors'), 'checks': checks,
                'wall_s': round(time.monotonic() - began, 1),
                'runtime_dir': str(runtime),
            }
            save(f'{case}-result.json', result)
            save(f'{case}-summary.json', results[case])
            print(f"[{case}] state={result.get('state')} checks={'OK' if not failures else failures}", flush=True)

    overall = all(not f for r in results.values() for f in [c['check'] for c in r['checks'] if not c['ok']])
    summary = {'schema': 'urp.nonideal-execution-acceptance/1', 'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'run_id': run_id, 'rtf': args.rtf, 'cases': results,
               'status': 'PASS' if overall else 'FAIL',
               'scope': '历史探索协议复跑：保留原期望及失败，不代表当前非理想独立验收；当前入口为 nonideal_audit.py',
               'thresholds': {'ideal_odom_divergence_m': 0.3, 'slip_uniform_odom_divergence_m': 0.5,
                              'ground_zones_divergence_m': [0.5, 0.3], 'loc_error_band_m': [0.03, 0.4],
                              'hold_threshold_s': 15}}
    save('summary.json', summary)
    print(json.dumps({'status': summary['status'], 'cases': {k: v['state'] for k, v in results.items()}}, ensure_ascii=False))
    sys.exit(0 if overall else 1)
except AssertionError as exc:
    save('summary.json', {'status': 'ERROR', 'error': str(exc)[:2000], 'cases': results})
    print(f'ERROR: {exc}', file=sys.stderr)
    sys.exit(2)
