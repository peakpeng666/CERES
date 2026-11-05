"""End-to-end acceptance of the panel's vertical loop, driven only through the gateway HTTP API.

panel action → gateway → solver → checker → 2D result → (map frame) simulation → feedback → exports → ordered shutdown.
Every step is recorded with its raw response; a failed step stops the loop and is reported, never skipped silently.

    src/lab/.venv/bin/python src/lab/e2e/full_loop.py [--gateway http://127.0.0.1:4100] [--scenario farm-rows-warehouse]
        [--modes follow_path,nav2_waypoints] [--gui] [--rviz] [--keep-session] [--budget 1500]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from urp_lab.execution_timing import (bounded_seconds,
                                     execution_poll_wall_s, freeze_plan_budget, resolve_budget_policy)
from urp_lab.simplan import to_sim_plan

MAX_OVERALL_WALL_S = 21600.0
READY_WALL_S = 1200.0
SUBMIT_WALL_S = 30.0
TERMINAL_RUN = {'done', 'error', 'timeout', 'canceled'}
TERMINAL_SIM = {'succeeded', 'failed', 'canceled'}


class StepFailed(RuntimeError):
    pass


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip('/')

    def call(self, method: str, path: str, body=None, timeout=30, raw=False):
        data = None if body is None else json.dumps(body).encode()
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                return response.status, (payload if raw else json.loads(payload or b'null')), dict(response.headers)
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                parsed = json.loads(payload or b'null')
            except json.JSONDecodeError:
                parsed = payload.decode(errors='replace')
            return error.code, parsed, dict(error.headers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gateway', default='http://127.0.0.1:4100')
    parser.add_argument('--scenario', default='maze-inspection')
    parser.add_argument('--algorithms', default='', help='comma list; default = recommended + all other applicable ones')
    parser.add_argument('--modes', default='follow_path,nav2_waypoints')
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--rviz', action='store_true')
    parser.add_argument('--localization', choices=['ground_truth','amcl'], default='amcl')
    parser.add_argument('--keep-session', action='store_true', help='leave the simulation running afterwards')
    parser.add_argument('--skip-sim', action='store_true')
    parser.add_argument('--budget', type=float, default=1800, help='overall wall seconds')
    parser.add_argument('--execution-time-basis', choices=['wall', 'simulation'], default='wall',
                        help='explicitly opt into simulation-time execution; startup/cleanup remain wall-bounded')
    parser.add_argument('--execution-budget', type=float, help='execution seconds in the selected time basis (max 1800)')
    parser.add_argument('--execution-wall-watchdog', type=float,
                        help='required finite wall seconds for simulation mode (max 7200)')
    parser.add_argument('--output', type=Path, help='Evidence directory (must not already exist)')
    parser.add_argument('--runtime', type=Path, default=LAB / 'runtime', help='The target gateway runtime directory')
    args = parser.parse_args()
    modes = [m for m in args.modes.split(',') if m]
    if not modes or any(m not in ('follow_path', 'nav2_waypoints') for m in modes):
        parser.error('--modes must name follow_path and/or nav2_waypoints')
    try:
        bounded_seconds(args.budget, 'overall --budget', MAX_OVERALL_WALL_S)
        resolve_budget_policy(90, budget_s=args.execution_budget, time_basis=args.execution_time_basis,
                              wall_watchdog_s=args.execution_wall_watchdog)
    except ValueError as exc:
        parser.error(str(exc))

    started = time.monotonic()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out_dir = args.output.resolve() if args.output else LAB / 'runtime' / 'e2e' / stamp
    out_dir.mkdir(parents=True, exist_ok=False)
    api = Client(args.gateway)
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'gateway': args.gateway, 'scenario': args.scenario,
              'steps': [], 'status': 'INCOMPLETE', 'overall_wall_budget_s': args.budget,
              'execution_budget_requested': {'budget_s': args.execution_budget, 'time_basis': args.execution_time_basis,
                                             'wall_watchdog_s': args.execution_wall_watchdog}}

    def step(name, ok, **detail):
        row = {'name': name, 'ok': bool(ok), 'wall_s': round(time.monotonic() - started, 2), **detail}
        report['steps'].append(row)
        mark = 'PASS' if ok else 'FAIL'
        print(f'[{mark}] {name}' + (f" — {detail.get('note')}" if detail.get('note') else ''), flush=True)
        if not ok:
            raise StepFailed(name)
        return row

    def deadline(seconds):
        return min(time.monotonic() + seconds, started + args.budget)

    def wait_run(run_id, seconds=300):
        end = deadline(seconds)
        while time.monotonic() < end:
            code, run, _ = api.call('GET', f'/lab/api/runs/{run_id}')
            if code == 200 and run.get('state') in TERMINAL_RUN:
                return run
            time.sleep(0.5)
        raise StepFailed(f'run {run_id} did not finish in time')

    session_started = False
    try:
        code, health, _ = api.call('GET', '/lab/api/health')
        step('gateway health', code == 200 and health.get('status') == 'ok', response=health)
        code, services, _ = api.call('GET', '/lab/api/services')
        states = {s['id']: s['state'] for s in services.get('services', [])} if code == 200 else {}
        step('service inventory', code == 200 and states.get('solver') in ('ready', 'running', 'degraded'), states=states,
             note=', '.join(f'{k}={v}' for k, v in states.items()))

        code, scenario, _ = api.call('GET', f'/lab/api/scenarios/{args.scenario}')
        step('load scenario', code == 200 and scenario.get('id') == args.scenario,
             frame=scenario.get('frame', {}).get('kind') if code == 200 else None,
             sim=scenario.get('sim') if code == 200 else scenario,
             note=f"{scenario.get('problem_class')} · {scenario.get('frame', {}).get('kind')}" if code == 200 else str(scenario))

        code, rec, _ = api.call('POST', '/lab/api/recommend', {'scenario_id': args.scenario}, timeout=60)
        step('recommendation', code == 200 and rec.get('recommended'), recommended=rec.get('recommended') if code == 200 else None,
             learned_selector=rec.get('learned_selector') if code == 200 else None, ranking=rec.get('ranking') if code == 200 else rec,
             note=f"recommended {rec.get('recommended')}; learned selector {rec.get('learned_selector', {}).get('status')}" if code == 200 else str(rec))

        code, catalog, _ = api.call('GET', '/lab/api/algorithms')
        applicable = [a['id'] for a in catalog.get('algorithms', [])
                      if scenario['problem_class'] in a['problem_classes'] and a.get('available')]
        chosen = [x for x in args.algorithms.split(',') if x] or [rec['recommended']] + [a for a in applicable if a != rec['recommended']]
        params = {r['algorithm_id']: r.get('suggested_params') or {} for r in rec.get('ranking', [])}
        body = {'scenario_id': args.scenario, 'algorithms': [{'id': a, 'params': params.get(a, {})} for a in chosen], 'label': f'e2e {stamp}'}
        code, job, _ = api.call('POST', '/lab/api/solve', body)
        step('submit solve job', code in (200, 202) and job.get('runs'), job=job, note=f'{len(chosen)} algorithms: {", ".join(chosen)}')

        runs = [wait_run(run_id) for run_id in job['runs']]
        table = [{'algorithm': r['algorithm']['id'], 'source': r['algorithm']['source'], 'state': r['state'], 'status': r['status'],
                  'objective': r['objective'].get('value'), 'unit': r['objective'].get('unit'),
                  'check_valid': (r.get('check') or {}).get('valid'), 'check_errors': (r.get('check') or {}).get('errors'),
                  'map_collision_free': (r.get('map_check') or {}).get('collision_free'),
                  'min_clearance_m': (r.get('map_check') or {}).get('min_clearance_m'),
                  'length': r['metrics'].get('length'), 'wall_s': r['metrics'].get('wall_s'), 'run_id': r['run_id']} for r in runs]
        (out_dir / 'solutions.json').write_text(json.dumps(runs, ensure_ascii=False, indent=2))
        step('all solver runs terminal', all(r['state'] in TERMINAL_RUN for r in runs), table=table)
        best = next((r for r in runs if r['algorithm']['id'] == rec['recommended']), None)
        step('recommended run valid and checked', bool(best and best['state'] == 'done' and (best.get('check') or {}).get('valid')),
             run_id=best and best['run_id'], note=f"objective {best and best['objective']} check {best and best.get('check', {}).get('checker')}")
        if scenario['frame']['kind'] == 'map':
            step('map check collision-free', bool((best.get('map_check') or {}).get('collision_free')), map_check=best.get('map_check'))

        for fmt in ('json', 'csv', 'geojson'):
            code, payload, headers = api.call('GET', f"/lab/api/runs/{best['run_id']}/export?format={fmt}", raw=True)
            (out_dir / f'export.{fmt}').write_bytes(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
            step(f'export {fmt}', code == 200 and len(payload) > 0, bytes=len(payload), content_type=headers.get('content-type'))
        code, payload, _ = api.call('POST', '/lab/api/compare/export', {'run_ids': [r['run_id'] for r in runs], 'format': 'csv'}, raw=True)
        (out_dir / 'compare.csv').write_bytes(payload if isinstance(payload, bytes) else b'')
        step('compare export', code == 200 and len(payload) > 0, bytes=len(payload))

        if args.skip_sim or not scenario.get('sim', {}).get('supported'):
            report['sim_skipped'] = 'requested' if args.skip_sim else scenario.get('sim', {}).get('reason')
        else:
            sim = scenario['sim']
            frozen_plans = {mode: freeze_plan_budget(to_sim_plan(best, scenario, mode),
                budget_s=args.execution_budget, time_basis=args.execution_time_basis,
                wall_watchdog_s=args.execution_wall_watchdog) for mode in modes}
            required_wall = sum(SUBMIT_WALL_S + execution_poll_wall_s(frozen_plans[m]) for m in modes)
            remaining_wall = started + args.budget - time.monotonic()
            step('bounded simulation envelope', remaining_wall >= required_wall,
                 required_execution_wall_s=required_wall, remaining_wall_s=remaining_wall,
                 startup_cap_wall_s=READY_WALL_S, worst_case_with_startup_wall_s=READY_WALL_S+required_wall,
                 policies={m: frozen_plans[m]['budget_policy'] for m in modes},
                 note='submission + watchdogs + finalization; recheck after startup; shutdown is separately wall-bounded')
            code, state, _ = api.call('POST', '/lab/api/sim/session', {'world': sim['world'], 'spawn': sim['spawn'],
                                                                        'gui': args.gui, 'rviz': args.rviz,'localization':args.localization}, timeout=60)
            session_started = code in (200, 202)
            step('start simulation session', session_started, response=state)
            end = deadline(1200)  # Includes bounded 700s Undock; still capped by overall budget.
            phases = []
            while time.monotonic() < end:
                code, state, _ = api.call('GET', '/lab/api/sim/state')
                if code == 200 and (not phases or phases[-1] != state.get('phase')):
                    phases.append(state.get('phase'))
                    print(f'      phase → {state.get("phase")}', flush=True)
                if code == 200 and state.get('phase') in ('ready', 'failed','stopped'):
                    break
                time.sleep(1)
            step('session ready', state.get('phase') == 'ready', phases=phases, stages=state.get('stages'), errors=state.get('errors'),
                 note=' → '.join(p for p in phases if p))
            report['session_id'] = state['session_id']

            for mode in modes:
                expected = frozen_plans[mode]
                poll_wall = execution_poll_wall_s(expected)
                remaining_wall = started + args.budget - time.monotonic()
                step(f'execution envelope ({mode})', remaining_wall >= poll_wall + SUBMIT_WALL_S,
                     required_wall_s=poll_wall + SUBMIT_WALL_S, remaining_wall_s=remaining_wall)
                code, sim_run, _ = api.call('POST', '/lab/api/sim/runs', {
                    'run_id': best['run_id'], 'mode': mode, **expected['budget_policy']['requested']}, timeout=SUBMIT_WALL_S)
                step(f'submit sim run ({mode})', code in (200, 202), response=sim_run)
                sim_id = sim_run['sim_run_id']
                step(f'frozen execution policy ({mode})', sim_run.get('budget_policy') == expected['budget_policy'],
                     expected=expected['budget_policy'], actual=sim_run.get('budget_policy'))
                end = deadline(execution_poll_wall_s(sim_run))
                last = None
                while time.monotonic() < end:
                    code, sim_run, _ = api.call('GET', f'/lab/api/sim/runs/{sim_id}')
                    if code == 200 and sim_run.get('state') != last:
                        last = sim_run.get('state')
                        print(f'      {mode}: {last}', flush=True)
                    if code == 200 and sim_run.get('state') in TERMINAL_SIM and sim_run.get('finished_utc'):
                        break
                    time.sleep(2)
                (out_dir / f'sim-{mode}.json').write_text(json.dumps(sim_run, ensure_ascii=False, indent=2))
                metrics = sim_run.get('metrics', {})
                step(f'terminal execution policy ({mode})', sim_run.get('budget_policy') == expected['budget_policy'],
                     expected=expected['budget_policy'], actual=sim_run.get('budget_policy'))
                step(f'sim run {mode} succeeded', sim_run.get('state') == 'succeeded' and bool(sim_run.get('finished_utc')), state=sim_run.get('state'), metrics=metrics,
                     sources=sim_run.get('sources'), error=sim_run.get('error'),
                     budget_policy=sim_run.get('budget_policy'), execution_timing=sim_run.get('execution_timing'),
                     trajectory_samples=len(sim_run.get('actual', {}).get('ground_truth', [])),
                     note=f"tasks {metrics.get('tasks_reached')}/{metrics.get('tasks_total')}, "
                          f"cross-track mean {metrics.get('cross_track_mean_m')} max {metrics.get('cross_track_max_m')} m, "
                          f"{metrics.get('duration_sim_s')} sim s")
                if scenario['problem_class']=='coverage':
                    step(f'sim run {mode} measured actual coverage',metrics.get('actual_working_coverage_fraction') is not None)
                else:
                    step(f'sim run {mode} reached every task', metrics.get('tasks_total') and metrics.get('tasks_reached') == metrics.get('tasks_total'))
                code, payload, _ = api.call('GET', f'/lab/api/sim/runs/{sim_id}/export?format=csv', raw=True)
                step(f'sim export {mode}', code == 200 and len(payload) > 0, bytes=len(payload))
        report['status'] = 'PASS_SOLVER_ONLY' if report.get('sim_skipped') else 'PASS'
    except StepFailed as failure:
        report['status'] = 'FAIL'
        report['failed_step'] = str(failure)
    except Exception as error:  # report unexpected harness errors as failures, with the message
        report['status'] = 'ERROR'
        report['error'] = f'{type(error).__name__}: {error}'
        print(report['error'], file=sys.stderr)
    finally:
        if session_started and not args.keep_session:
            code, state, _ = api.call('DELETE', '/lab/api/sim/session', timeout=180)
            end = time.monotonic() + 180
            while time.monotonic() < end:
                code, state, _ = api.call('GET', '/lab/api/sim/state')
                if code == 200 and state.get('phase') in ('stopped', 'failed'):
                    break
                time.sleep(2)
            report['shutdown'] = {'phase': state.get('phase') if code == 200 else None, 'errors': state.get('errors') if code == 200 else state,
                                  'warnings': state.get('warnings') if code == 200 else None}
            print(f"[INFO] session shutdown → {report['shutdown']['phase']}", flush=True)
            session_file = args.runtime.resolve() / 'sim' / report.get('session_id', 'missing') / 'session-report.json'
            cleanup = json.loads(session_file.read_text()) if session_file.exists() else None
            report['shutdown']['session_report'] = str(session_file)
            report['shutdown']['verified'] = bool(cleanup and cleanup.get('status') == 'PASS'
                and not cleanup.get('remaining_owned_processes') and not cleanup.get('abnormal_exits')
                and not state.get('errors') and state.get('phase') == 'stopped')
            if not report['shutdown']['verified']:
                report['status'] = 'FAIL'
                report['failed_step'] = 'shutdown / process cleanup'
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report['wall_s'] = round(time.monotonic() - started, 1)
        (out_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'status': report['status'], 'report': str(out_dir / 'report.json')}, ensure_ascii=False), flush=True)
    return 0 if report['status'] in ('PASS','PASS_SOLVER_ONLY') else 1


if __name__ == '__main__':
    raise SystemExit(main())
