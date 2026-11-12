"""Experience base: decision episodes from solve records, offline sweeps and field-simulation results.

Read-only: the run database is opened with ``mode=ro``; simulation result files and sweep records are only read.
Each episode keeps where it came from (``source``), the instance fingerprint, the action (algorithm + params),
the independently checked outcome and, when the run was executed in the field simulation, the planned and
actual quantities. Groups of episodes on the same instance fingerprint with different actions are
full-information samples for selection learning; planned/actual pairs feed the execution-feedback loop.
"""
from __future__ import annotations

import glob
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from .. import paths

SOURCES = ('workbench_run', 'field_execution', 'portfolio_sweep')


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def run_episodes(db: Path) -> list[dict]:
    if not db.is_file():
        return []
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        rows = con.execute('SELECT run_id, job_id, scenario_id, algorithm_id, state, status, created_utc, json FROM runs').fetchall()
    finally:
        con.close()
    out = []
    for run_id, job_id, scenario_id, algorithm_id, state, status, created, payload in rows:
        d = json.loads(payload)
        rec = d.get('recommendation_at_submission') or {}
        advice = rec.get('advice') or {}
        metrics = d.get('metrics') or {}
        out.append({'source': 'workbench_run', 'run_id': run_id, 'job_id': job_id, 'scenario_id': scenario_id,
                    'problem_class': d.get('problem_class'), 'instance_sha256': d.get('instance_sha256'),
                    'algorithm': algorithm_id, 'params': d.get('params'), 'state': state, 'status': status,
                    'valid': bool((d.get('check') or {}).get('valid')), 'objective': _num((d.get('objective') or {}).get('value')),
                    'wall_s': _num(metrics.get('wall_s')), 'created_utc': created,
                    'recommended': advice.get('recommended'), 'policy': advice.get('policy'),
                    'followed_recommendation': (advice.get('recommended') == algorithm_id) if advice.get('recommended') else None})
    return out


def execution_episodes(root: Path) -> list[dict]:
    out = []
    for path in sorted(glob.glob(str(root / 'field_*' / 'result.json'))):
        r = json.loads(Path(path).read_text(encoding='utf-8'))
        plan_path = Path(path).with_name('plan.json')
        plan = json.loads(plan_path.read_text(encoding='utf-8')) if plan_path.is_file() else {}
        reference = (plan.get('execution_timing_reference') or {}).get('vehicles') or {}
        plan_digest = None
        if plan.get('vehicles'):
            import hashlib
            plan_digest = hashlib.sha256(json.dumps([v.get('path') for v in plan['vehicles']], sort_keys=True).encode()).hexdigest()[:16]
        m = r.get('metrics') or {}
        vehicles = []
        for v in r.get('vehicles') or []:
            wait = _num(v.get('queue_wait_s'))
            dur = _num(v.get('duration_s'))
            vehicles.append({'name': v.get('name'), 'state': v.get('state'), 'planned_length_m': _num(v.get('planned_length_m')),
                             'actual_length_m': _num(v.get('distance_m')), 'planned_duration_s': _num(v.get('planned_duration_s')),
                             'actual_duration_s': dur, 'queue_wait_s': wait,
                             'driving_duration_s': (dur - wait) if dur is not None and wait is not None else dur,
                             'reference_duration_s': _num((reference.get(v.get('name')) or {}).get('duration_s')),
                             'tasks_done': v.get('tasks_done'), 'tasks_total': v.get('tasks_total'),
                             'cross_track_mean_m': _num(v.get('cross_track_mean_m'))})
        out.append({'source': 'field_execution', 'session': Path(path).parent.name, 'run_id': r.get('run_id'),
                    'scenario_id': r.get('scenario_id'), 'algorithm': (r.get('algorithm') or {}).get('id') if isinstance(r.get('algorithm'), dict) else r.get('algorithm'),
                    'state': r.get('state'), 'tasks_total': m.get('tasks_total'), 'tasks_reached': m.get('tasks_reached'),
                    'planned_makespan_s': _num(m.get('planned_makespan_s')), 'actual_makespan_s': _num(m.get('makespan_s')),
                    'planned_distance_m': _num(m.get('planned_total_distance_m')), 'actual_distance_m': _num(m.get('total_distance_m')),
                    'serial_traffic': any(v['queue_wait_s'] is not None for v in vehicles), 'plan_paths_sha256': plan_digest,
                    'vehicles': vehicles})
    return out


def sweep_episodes(runs_root: Path) -> list[dict]:
    out = []
    for plan_path in sorted(runs_root.glob('*/plan.json')):
        run_id = json.loads(plan_path.read_text(encoding='utf-8')).get('run_id')
        for p in sorted((plan_path.parent / 'records').glob('*.json')):
            r = json.loads(p.read_text(encoding='utf-8'))
            out.append({'source': 'portfolio_sweep', 'run_id': run_id, 'job_id': r['job_id'], 'family': r['family'],
                        'instance': r['instance'], 'config': r['config'], 'algorithm': r['algorithm'], 'status': r['status'],
                        'valid': r.get('valid'), 'objective': _num(r.get('objective')),
                        'wall_s': _num((r.get('metrics') or {}).get('wall_s') or r.get('worker_wall_s'))})
    return out


def _ratio_stats(pairs):
    vals = sorted(a / p for p, a in pairs if p and a and p > 0)
    if not vals:
        return None
    q = lambda f: vals[min(len(vals) - 1, int(round(f * (len(vals) - 1))))]  # noqa: E731
    return {'n': len(vals), 'median': q(0.5), 'p10': q(0.1), 'p90': q(0.9), 'min': vals[0], 'max': vals[-1]}


def summarise(episodes: list[dict]) -> dict:
    by_source = defaultdict(int)
    for e in episodes:
        by_source[e['source']] += 1
    runs = [e for e in episodes if e['source'] == 'workbench_run']
    groups = defaultdict(set)
    for e in runs:
        if e['instance_sha256'] and e['state'] == 'done':
            groups[(e['problem_class'], e['instance_sha256'])].add(e['algorithm'])
    full = {k: v for k, v in groups.items() if len(v) >= 2}
    per_class = defaultdict(lambda: {'runs': 0, 'instances': 0, 'multi_algorithm_instances': 0})
    for e in runs:
        per_class[e['problem_class']]['runs'] += 1
    for (pc, _sha), algs in groups.items():
        per_class[pc]['instances'] += 1
        per_class[pc]['multi_algorithm_instances'] += int(len(algs) >= 2)
    followed = [e['followed_recommendation'] for e in runs if e['followed_recommendation'] is not None]
    execs = [e for e in episodes if e['source'] == 'field_execution']
    ok = [e for e in execs if e['state'] == 'succeeded']
    veh = [v for e in ok for v in e['vehicles'] if v['state'] == 'done']
    sweeps = [e for e in episodes if e['source'] == 'portfolio_sweep']
    return {
        'episodes': dict(by_source),
        'workbench_runs': {'per_problem_class': dict(per_class), 'full_information_instances': len(full),
                           'submissions_matching_recommendation': [sum(followed), len(followed)],
                           'note': '提交与推荐一致不等于用户采纳了推荐；多算法同实例记录才是全信息样本'},
        'field_execution': {'sessions': len(execs), 'succeeded': len(ok),
                            'makespan_actual_over_planned': _ratio_stats([(e['planned_makespan_s'], e['actual_makespan_s']) for e in ok]),
                            'distance_actual_over_planned': _ratio_stats([(e['planned_distance_m'], e['actual_distance_m']) for e in ok]),
                            'vehicle_duration_actual_over_planned': _ratio_stats([(v['planned_duration_s'], v['actual_duration_s']) for v in veh]),
                            'vehicle_driving_over_planned': _ratio_stats([(v['planned_duration_s'], v['driving_duration_s']) for v in veh]),
                            'vehicle_driving_over_reference': _ratio_stats([(v['reference_duration_s'], v['driving_duration_s']) for v in veh
                                                                            if v['reference_duration_s']]),
                            'distinct_plans': len({e['plan_paths_sha256'] for e in ok if e['plan_paths_sha256']}),
                            'serial_traffic_sessions': sum(1 for e in ok if e['serial_traffic']),
                            'note': '会话含同一方案的重复执行，独立样本按不同方案计；串行预约会话的最晚完成含排队，驾驶用时=用时−排队等待；'
                                    '规划用时为匀速代理，停—转—走参考为解析参考，均不是控制器时间预测'},
        'portfolio_sweeps': {'records': len(sweeps), 'runs': sorted({e['run_id'] for e in sweeps})},
    }


def build() -> list[dict]:
    return (run_episodes(paths.RUNTIME / 'lab.sqlite3') + execution_episodes(paths.RUNTIME / 'fieldsim')
            + sweep_episodes(paths.REPO / 'experiments' / 'runs'))


def write(out: Path) -> dict:
    episodes = build()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8') as fh:
        for e in episodes:
            fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + '\n')
    summary = summarise(episodes)
    out.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return summary
