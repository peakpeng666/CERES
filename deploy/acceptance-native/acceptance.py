#!/usr/bin/env python3
"""Pinned-source real headless acceptance; all raw evidence stays private.
No solver/ROS replacement, status rewriting, TF tolerance changes or ABI bypass.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from public_summary import SOURCE, project
from shutdown_diagnostics import capture as capture_shutdown
from startup_diagnostics import session_id, collect
from execution_diagnostics import collect as collect_execution
from execution_protocol import maze_receipt
from undock_observer.lifecycle import ObserverProcess
from undock_observer.projection import project as project_observer

def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default

def api(base, method, suffix, data=None, timeout=5):
    req = urllib.request.Request(base + '/lab/api' + suffix, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as f: return json.load(f)

def owned(token):
    """Only this invocation's inherited random marker establishes ownership."""
    found = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid(): continue
        try:
            if ('URP_ACCEPTANCE_OWNER=' + token).encode() in p.joinpath('environ').read_bytes().split(b'\0'):
                # Zombies cannot act; Popen children are reaped separately.
                stat = p.joinpath('stat').read_text().rsplit(')', 1)[1].split()
                if stat[0] != 'Z': found.append((int(p.name), stat[19]))
        except (FileNotFoundError, ProcessLookupError, PermissionError): pass
    return found

def signal_owned(token, sig):
    for pid, start in owned(token):
        try:
            stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            if stat[19] == start: os.kill(pid, sig)
        except (FileNotFoundError, ProcessLookupError, PermissionError): pass

def verify_source(source):
    # Same raw-blob verifier as installation; no clean-filter/CRLF rewriting.
    from runner.verify_worktree import verify
    return verify(source, SOURCE)

def wait_for_solver(base, timeout=120, clock=time.monotonic, sleep=time.sleep):
    deadline=clock()+timeout
    while clock()<deadline:
        inventory=api(base,'GET','/services',timeout=10)
        state=next((r.get('state') for r in inventory.get('services',[]) if r.get('id')=='solver'),None)
        if state in ('ready','running','degraded'):return
        if state in ('failed','unavailable'):raise RuntimeError('solver_service_unavailable')
        sleep(.5)
    raise TimeoutError('solver_service_not_ready')

def failure_diagnostic(report, solutions):
    names={'gateway health':'health','service inventory':'services','load scenario':'scenario','recommendation':'recommendation','submit solve job':'solve_submit','all solver runs terminal':'solve_terminal','recommended run valid and checked':'solve_validation','map check collision-free':'map_validation','start simulation session':'session_start','session ready':'session_ready','submit sim run (follow_path)':'sim_submit','sim run follow_path succeeded':'sim_execute','sim run follow_path reached every task':'sim_tasks','shutdown / process cleanup':'shutdown'}
    failed=next((r.get('name') for r in report.get('steps',[]) if isinstance(r,dict) and r.get('ok') is False),report.get('failed_step'))
    step=names.get(failed,'export' if isinstance(failed,str) and (failed.startswith('export ') or failed in ('compare export','sim export follow_path')) else 'none' if report.get('status')=='PASS' else 'unknown')
    failed_runs=[r for r in solutions or [] if isinstance(r,dict) and r.get('state') in ('error','timeout')]
    errors=[report.get('error')]+[r.get('error') for r in failed_runs]
    text=json.dumps(errors,ensure_ascii=True).lower()
    identity_failed=any((r.get('process') or {}).get('source_identity_verified') is False or (r.get('reproducibility') or {}).get('worker_environment_verified') is False for r in failed_runs)
    kind='module_missing' if 'modulenotfounderror' in text else 'source_identity' if identity_failed else 'timeout' if 'timeouterror' in text else 'http' if 'httperror' in text else 'value' if 'valueerror' in text else 'runtime' if 'runtimeerror' in text else 'none' if step=='none' else 'unknown'
    return {'simulation_failure_step':step,'simulation_error_kind':kind}

def derive(report, sim, solutions, session, raw):
    sim = sim or {}; report = report or {}; session = session or {}
    metrics = sim.get('metrics') or {}
    best = next((x for x in solutions or [] if x.get('run_id') == sim.get('run_id')), {})
    actual = (sim.get('actual') or {}).get('ground_truth')
    valid = isinstance(actual, list) and len(actual) >= 2
    if valid:
        valid = all(isinstance(p, list) and len(p) >= 3 and all(type(v) in (int, float) and math.isfinite(v) for v in p[:3]) for p in actual)
    motion = bool(valid and any(math.hypot(p[1]-actual[0][1], p[2]-actual[0][2]) > .05 for p in actual[1:]))
    raw.update({k: metrics.get(k) for k in ('tasks_total', 'tasks_reached', 'duration_sim_s', 'duration_wall_s', 'actual_length_m', 'final_error_m', 'cross_track_max_m')})
    raw.update(trajectory_samples=len(actual) if isinstance(actual, list) else None,
        trajectory_motion_verified=motion,
        map_collision_free=((best.get('map_check') or {}).get('collision_free') is True and (best.get('map_check') or {}).get('checked') is True),
        actual_map_collision_free=((metrics.get('actual_map_check') or {}).get('collision_free') is True and (metrics.get('actual_map_check') or {}).get('checked') is True),
        source_identity_verified=((best.get('process') or {}).get('source_identity_verified') is True and (best.get('reproducibility') or {}).get('worker_environment_verified') is True),
        physical_budget_s=sim.get('budget_s'),
        runtime_compatibility_verified=((session.get('bridge_runtime') or {}).get('mode') == 'source_shutdown_join' and all((session.get('compatibility') or {}).get(k) for k in ('tf','gui'))),
        assets_complete=(session.get('assets') or {}).get('complete'))
    if metrics.get('duration_wall_s') and metrics.get('duration_sim_s') is not None:
        raw['real_time_factor'] = metrics['duration_sim_s'] / metrics['duration_wall_s']
    if best:
        raw['scenario_sha256'] = sha(best.get('scenario_snapshot'))
        raw['solution_sha256'] = sha(best)
        raw['runtime_fingerprint_sha256'] = (best.get('reproducibility') or {}).get('execution_sha256')
    if session.get('assets'): raw['resource_manifest_sha256'] = sha(session['assets'])
    raw['cleanup_pass'] = bool(report.get('shutdown', {}).get('verified') is True and session.get('status') == 'PASS'
        and session.get('remaining_owned_processes') == [] and session.get('abnormal_exits') == [])
    raw['state'] = 'PASS' if report.get('status') == 'PASS' and sim.get('state') == 'succeeded' and sim.get('finished_utc') else 'FAIL'
    return raw

def execute(args):
    raw = {'source_sha': SOURCE, 'harness_sha': args.harness_sha, 'stage': args.stage,
           'state': 'ERROR', 'overall_wall_budget_s': 1800, 'process_exit_codes': {}}
    private = args.private_dir.resolve()
    private.mkdir(parents=True, exist_ok=False, mode=0o700)
    runtime = private / 'runtime'; runtime.mkdir(mode=0o700)
    output = private / 'full-loop'
    token = secrets.token_hex(24)
    gateway = loop = observer = None; base = None; forced = False; sid = None; report = {}
    try:
        if args.stage != 'maze': raise RuntimeError('warehouse_replay_not_implemented')
        source = args.source.resolve(); verify_source(source)
        python = source / 'src/lab/.venv/bin/python'
        env = dict(os.environ, URP_LAB_RUNTIME=str(runtime), URP_ACCEPTANCE_OWNER=token,
                   ROS_LOCALHOST_ONLY='1', URP_ROS_DOMAIN_ID='146', ROS_DOMAIN_ID='146',
                   URP_BIND_HOST='127.0.0.1', PYTHONPATH=str(source / 'src/lab'))
        # Explicit opt-out flags would bypass the validated execution stack.
        for key in ('URP_TF_FIX', 'URP_GUI_FIX', 'URP_BRIDGE_FIX'):
            if key in env: raise RuntimeError('override_forbidden')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        with (private / 'gateway.log').open('w') as log:
            gateway = subprocess.Popen([str(python), '-m', 'urp_lab', 'serve', '--host', '127.0.0.1', '--port', str(port), '--no-archive'],
                cwd=source / 'src/lab', env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        end = time.monotonic() + 120
        while True:
            if gateway.poll() is not None or time.monotonic() > end: raise RuntimeError('gateway_failed')
            try:
                if api(base, 'GET', '/health').get('status') == 'ok': break
            except (OSError, ValueError): pass
            time.sleep(.5)
        raw['simulation_failure_step']='services'
        wait_for_solver(base)
        raw['simulation_failure_step']='none'
        rec = api(base, 'POST', '/recommend', {'scenario_id': 'maze-inspection'}, timeout=60)
        algorithm = rec.get('recommended')
        if not isinstance(algorithm, str) or not algorithm or algorithm.startswith('-'): raise RuntimeError('recommendation_missing')
        cmd = [str(python), str(source / 'src/lab/e2e/full_loop.py'), '--gateway', base,
               '--scenario', 'maze-inspection', '--algorithms', algorithm, '--modes', 'follow_path',
               '--localization', 'ground_truth', '--execution-time-basis', 'simulation', '--execution-wall-watchdog', '900', '--budget', '1800', '--runtime', str(runtime), '--output', str(output)]
        observer_output=private/'undock-observer.json'
        observer_env=dict(env,RMW_IMPLEMENTATION='rmw_cyclonedds_cpp')
        observer=ObserverProcess(['bash','-c','source /opt/ros/humble/setup.bash && exec bash "$1/setup/profile.sh" simulation /usr/bin/python3 "${@:2}"','observer',str(source),
            str(Path(__file__).resolve().parent/'undock_observer/observer.py'),'--port',str(port),'--output',str(observer_output)],
            observer_env,private/'undock-observer.log')
        if not observer.wait_ready(observer_output.with_suffix('.ready')):raise RuntimeError('observer_startup_failed')
        with (private / 'full-loop.log').open('w') as log:
            loop = subprocess.Popen(cmd, cwd=source, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            raw['process_exit_codes']['full_loop'] = loop.wait(timeout=2220)
        verify_source(source)
        report = read(output / 'report.json', {})
        raw.update(failure_diagnostic(report,read(output / 'solutions.json', [])))
        sid = session_id(report)
        if sid is None: raise RuntimeError('session_missing')
        raw['shutdown_diagnostics']={'at_full_loop_return':capture_shutdown(runtime,sid,report)}
        session = read(runtime / 'sim' / sid / 'session-report.json', {})
        derive(report, read(output / 'sim-follow_path.json', {}), read(output / 'solutions.json', []), session, raw)
        raw['execution_diagnostics'] = collect_execution(read(output / 'sim-follow_path.json', {}), read(output / 'solutions.json', []), source)
        raw['execution_protocol'] = maze_receipt(report, read(output / 'sim-follow_path.json', {}), read(output / 'solutions.json', []), source)
        # Hash actual verified overlay files, but never expose their paths.
        compat = session.get('compatibility') or {}
        binary = {k: hashlib.sha256(Path(compat[k]).read_bytes()).hexdigest() for k in ('tf', 'gui') if compat.get(k)}
        bridge_hash = (session.get('bridge_runtime') or {}).get('sha256')
        from public_summary import digest
        if len(binary) == 2 and digest(bridge_hash):
            binary['bridge'] = bridge_hash
            raw['overlay_binary_sha256'] = sha(binary)
        native = source / 'src/lab/ros/native'
        sources = {str(p.relative_to(native)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in native.rglob('*') if p.is_file() and (p.suffix in ('.cpp', '.cc', '.h', '.hh', '.patch', '.json', '.sh') or p.name == 'CMakeLists.txt')}
        deployment = source / 'setup/2026-10-15/deployment'
        for rel in ('tf-repro/buffer_candidate.cpp', 'gui-plugin-lifetime.cpp', 'tf2-overlay-manifest.json', 'gui-overlay-manifest.json'):
            sources['deployment/' + rel] = hashlib.sha256((deployment / rel).read_bytes()).hexdigest()
        if sources: raw['overlay_source_sha256'] = sha(sources)
    except BaseException as exc:
        raw['state'] = 'ERROR'
        # Raw diagnostic is private, never printed or forwarded by projection.
        (private / 'harness-error.txt').write_text(type(exc).__name__ + ': ' + str(exc))
    finally:
        if observer is not None:
            try:
                receipt=observer.close()
                raw['undock_observer_process']=receipt
                if receipt['exit_code']!=0 or receipt['forced_kill_count'] or receipt['remaining_owned_count']:forced=True
            except Exception:forced=True
            try:raw['undock_observer']=project_observer(read(private/'undock-observer.json',{}))
            except Exception:pass
        if base and gateway and gateway.poll() is None:
            try: api(base, 'POST', '/shutdown', {}, timeout=10)
            except Exception: pass
            try: gateway.wait(timeout=240)
            except subprocess.TimeoutExpired: forced = True
        if owned(token):
            forced = True; signal_owned(token, signal.SIGTERM)
            end = time.monotonic() + 15
            while owned(token) and time.monotonic() < end: time.sleep(.5)
        if owned(token):
            signal_owned(token, signal.SIGKILL); time.sleep(1)
        for proc in (loop, gateway):
            if proc:
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
        raw['process_exit_codes']['gateway'] = gateway.returncode if gateway else None
        raw['remaining_owned_process_count'] = len(owned(token))
        raw['cleanup_pass'] = bool(raw.get('cleanup_pass') and not forced and raw['remaining_owned_process_count'] == 0)
        # session-report.json is finalized by shutdown, including failed startup.
        # Diagnostic recovery never updates execution state or PASS evidence.
        try: raw['startup_diagnostics'] = collect(runtime,sid)
        except Exception: pass
        try:
            raw.setdefault('shutdown_diagnostics',{})['after_gateway_shutdown']=capture_shutdown(runtime,sid,report)
        except Exception: pass
        # This private stage result precedes supervisor cleanup. The public CLI
        # independently requires the final runner-status receipt.
        result = project(raw, require_runner=False)
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['state'] == 'PASS' else 1

def main():
    p = argparse.ArgumentParser(); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--private-dir', type=Path, required=True); p.add_argument('--summary', type=Path, required=True)
    p.add_argument('--stage', choices=['maze', 'warehouse'], default='maze')
    p.add_argument('--harness-sha', default=os.environ.get('URP_HARNESS_SHA'))
    args = p.parse_args()
    def stop(signum, frame): raise RuntimeError('interrupted')
    signal.signal(signal.SIGTERM, stop)
    try: return execute(args)
    except BaseException:
        # Includes private-dir collision; no traceback may leak to public stdout.
        return 1

if __name__ == '__main__': raise SystemExit(main())
