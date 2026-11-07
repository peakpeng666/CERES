"""Factorial study of field-operation sequencing. See experiments/protocols/FIELD_OPS_STUDY_20261107.md.

python -m urp_lab.agri.study run --out DIR [--workers 16]    every run, failures included, into records.jsonl
python -m urp_lab.agri.study analyse --out DIR               summary.json and report.md from the records
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform as host
import subprocess
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

from . import catalog as cat
from . import mission as missions
from . import rig as rigs
from . import solve as solver

RIGS = {
    'R1_boom': dict(environment='open_field', crop='wheat', operation='spraying_blanket', platform='row_tractor_120', implement='boom_mounted', overrides={'rate_per_ha': 150}),
    'R2_trailed': dict(environment='open_field', crop='wheat', operation='spraying_blanket', platform='row_tractor_120', implement='boom_trailed', overrides={'rate_per_ha': 150}),
    'R3_tillage': dict(environment='open_field', crop=None, operation='tillage', platform='agxeed_5115t2', implement='disk_harrow', overrides={}),
    'R4_combine': dict(environment='open_field', crop='wheat', operation='combine_harvest', platform='combine_generic', implement='combine_header', overrides={}),
    'R5_orchard': dict(environment='orchard', crop='apple', operation='spraying_blanket', platform='jd_5075gn', implement='air_blast', overrides={'rate_per_ha': 500}),
    'R6_robot': dict(environment='open_field', crop='wheat', operation='spraying_blanket', platform='field_diffdrive', implement='spray_module', overrides={'rate_per_ha': 200}),
}
REAL_RIGS = ('R1_boom', 'R3_tillage', 'R4_combine')
SHAPES = ('rect', 'trapezoid', 'l_shape', 'pentagon')
ROWS = (16, 40)
FILLS = (1, 2, 4, 8)
HEADLAND = (1.0, .7)
SEEDS = (1, 2, 3, 4, 5)
BUDGET_S = 3.0
REAL_COUNT = 10


def real_fields() -> dict[str, dict]:
    """Every n-th Dutch development field; sealed fields are never opened."""
    import zipfile
    from ..evolve import instances
    from ..library import fields
    with zipfile.ZipFile(fields.DIR / 'wkt.zip') as z:    # names only: no sealed member is read
        names = [n.rsplit('/', 1)[-1].rsplit('.', 1)[0] for n in z.namelist() if n.lower().endswith('.wkt')]
    ids = sorted((f for f in names if f.startswith('nl_') and instances.split_of(f) == 'dev'), key=lambda k: int(''.join(c for c in k if c.isdigit()) or 0))
    step = max(1, len(ids) // REAL_COUNT)
    out = {}
    for fid in ids[::step][:REAL_COUNT]:
        inst = fields.scenario(fid)['instance']
        out[fid] = {'ring': inst['field'], 'obstacles': inst.get('obstacles') or []}
    return out


def _generated_size(base: dict, rows: int) -> tuple[float, float]:
    width = cat.value(cat.by_id('implements', base['implement'])['width_m'])
    side = 60 * max(width, 4)
    probe = missions.compile_mission({**base, 'field': {'shape': 'rect', 'width_m': side, 'height_m': side}})
    pitch, head = probe['pitch_m'], probe['recommended_headland_m']
    height = rows * pitch + 2 * head
    return round(height * 1.5, 1), round(height, 1)


def plan_instances() -> list[dict]:
    items = []
    reals = real_fields()
    for rig_id, base in RIGS.items():
        fields = []
        for rows in ROWS:
            w, h = _generated_size(base, rows)
            for shape in SHAPES:
                fields.append((f'gen-{shape}-{rows}', {'shape': shape, 'width_m': w, 'height_m': h}, 0.))
        if rig_id in REAL_RIGS:
            for fid, geo in reals.items():
                fields.append((fid, geo, missions.long_axis(geo['ring'])))
        for name, field, angle in fields:
            for headland in HEADLAND:
                for fills in FILLS:
                    if fills > 1 and rig_id == 'R3_tillage':
                        continue
                    items.append({'rig': rig_id, 'field': name, 'geometry': field, 'angle_deg': angle, 'headland_scale': headland, 'fills': fills})
    return items


def build(spec: dict) -> dict:
    base = RIGS[spec['rig']]
    m = {**base, 'field': spec['geometry'], 'angle_deg': spec['angle_deg'], 'objective': 'nonworking', 'fleet': 1, 'overrides': dict(base['overrides'])}
    first = missions.compile_mission(m)
    m['headland_m'] = round(first['recommended_headland_m'] * spec['headland_scale'], 2)
    second = missions.compile_mission(m)
    inst = second['instance']
    if spec['fills'] > 1:
        total = sum(x['demand'] for x in inst['lines'])
        m['overrides'] = {**m['overrides'], 'capacity': round(total / spec['fills'] * 1.02, 3)}
        second = missions.compile_mission(m)
    elif inst['rig']['resource']['kind'] != 'none':
        total = sum(x['demand'] for x in inst['lines'])
        m['overrides'] = {**m['overrides'], 'capacity': round(total * 1.02 + 1, 3)}
        second = missions.compile_mission(m)
    return second


def one(spec: dict) -> list[dict]:
    key = hashlib.sha256(json.dumps({k: spec[k] for k in ('rig', 'field', 'headland_scale', 'fills')}, sort_keys=True).encode()).hexdigest()[:12]
    head = {k: spec[k] for k in ('rig', 'field', 'headland_scale', 'fills')} | {'instance': key}
    try:
        compiled = build(spec)
    except Exception as exc:
        return [{**head, 'algorithm': None, 'status': 'excluded', 'reason': f'{type(exc).__name__}: {exc}'[:300]}]
    if compiled['blocked']:
        first = next(c for c in compiled['checks'] if c['level'] == 'block')
        return [{**head, 'algorithm': None, 'status': 'excluded', 'reason': f"{first['title']}：{first['detail']}"}]
    inst = compiled['instance']
    rig = inst['rig']
    shape = dict(lines=len(inst['lines']), pitch_m=inst['pitch_m'], radius_m=rig['min_turn_radius_m'], turn_ratio=round(2 * rig['min_turn_radius_m'] / inst['pitch_m'], 3),
                 headland_m=inst['headland_m'], field_ha=compiled['estimate']['field_ha'], instance_sha256=hashlib.sha256(json.dumps(inst, sort_keys=True).encode()).hexdigest())
    jobs = [('agri.sequential', None), ('agri.skip', None), ('ortools.field', None)] + [('agri.split_anneal', s) for s in SEEDS]
    if len(inst['lines']) <= 10:
        jobs.append(('agri.exact', None))
    out = []
    for algorithm, seed in jobs:
        params = {'time_limit_s': BUDGET_S, 'seed': seed or 0}
        t0, c0 = time.perf_counter(), time.process_time()
        try:
            routes, chk, metrics, raw, status, _ = solver.solve({}, inst, algorithm, params)
        except Exception as exc:
            routes, chk, metrics, raw, status = [], {'valid': False, 'errors': [f'{type(exc).__name__}: {exc}'[:300]]}, {}, {}, 'error'
        keep = ('total_distance_m', 'working_distance_m', 'nonworking_distance_m', 'turn_distance_m', 'transfer_distance_m', 'reverse_distance_m', 'turn_counts',
                'refills', 'makespan_s', 'field_efficiency', 'coverage_fraction', 'overlap_fraction', 'trafficked_fraction', 'crop_crossing_m', 'rebuilt_arcs')
        out.append({**head, **shape, 'algorithm': algorithm, 'seed': seed, 'status': status, 'valid': bool(chk.get('valid')), 'errors': chk.get('errors', [])[:3],
                    'warnings': chk.get('warnings', [])[:2], 'wall_s': round(time.perf_counter() - t0, 3), 'cpu_s': round(time.process_time() - c0, 3),
                    'iterations': (raw or {}).get('iterations'), **{k: metrics.get(k) for k in keep}})
    return out


def _pin(_):
    cores = [int(x) for x in os.environ.get('STUDY_CPUS', '').split(',') if x]
    if cores:
        os.sched_setaffinity(0, cores)


def run(out: Path, workers: int):
    out.mkdir(parents=True, exist_ok=True)
    items = plan_instances()
    repo = Path(__file__).resolve().parents[4]
    manifest = {'schema': 'urp.field-ops-study/1', 'protocol': 'experiments/protocols/FIELD_OPS_STUDY_20261107.md', 'catalog_version': cat.load()['version'],
                'git_commit': subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
                'git_dirty': bool(subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', 'src/lab/urp_lab/agri'], capture_output=True, text=True).stdout.strip()),
                'python': sys.version.split()[0], 'host': host.platform(), 'budget_s': BUDGET_S, 'seeds': SEEDS, 'rigs': list(RIGS), 'fills': FILLS, 'headland_scale': HEADLAND,
                'planned_instances': len(items), 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'cpus': os.environ.get('STUDY_CPUS'), 'workers': workers}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    with (out / 'records.jsonl').open('w', encoding='utf-8') as sink, Pool(workers, initializer=_pin, initargs=(None,)) as pool:
        for done, rows in enumerate(pool.imap_unordered(one, items), 1):
            for row in rows:
                sink.write(json.dumps(row, ensure_ascii=False) + '\n')
            sink.flush()
            if done % 25 == 0:
                print(done, '/', len(items), flush=True)
    manifest['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _cluster_ci(pairs, seed=20261107, draws=2000):
    """Bootstrap the median over field geometries: instances sharing a field are not independent."""
    import numpy as np
    rng = np.random.default_rng(seed)
    groups = defaultdict(list)
    for geometry, value in pairs:
        groups[geometry].append(value)
    keys = list(groups)
    if not keys:
        return None
    values = []
    for _ in range(draws):
        pick = rng.integers(0, len(keys), len(keys))
        sample = [x for i in pick for x in groups[keys[i]]]
        values.append(float(np.median(sample)))
    return [round(float(np.quantile(values, .025)), 4), round(float(np.quantile(values, .975)), 4)]


def analyse(out: Path):
    from scipy.stats import wilcoxon
    rows = [json.loads(x) for x in (out / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
    excluded = [r for r in rows if r['status'] == 'excluded']
    runs = [r for r in rows if r['algorithm']]
    by = defaultdict(lambda: defaultdict(list))
    meta = {}
    for r in runs:
        by[r['instance']][r['algorithm']].append(r)
        meta[r['instance']] = r
    algos = ['agri.sequential', 'agri.skip', 'ortools.field', 'agri.split_anneal']
    feas = {}
    for a in algos + ['agri.exact']:
        items = [r for r in runs if r['algorithm'] == a]
        ok = [r for r in items if r['status'] == 'feasible' and r['valid']]
        feas[a] = {'runs': len(items), 'feasible': len(ok), 'rate': round(len(ok) / len(items), 4) if items else None}

    def value(instance, algorithm):
        got = [r['nonworking_distance_m'] for r in by[instance].get(algorithm, []) if r['status'] == 'feasible' and r['valid']]
        expected = len(by[instance].get(algorithm, []))
        return _median(got) if got and len(got) == expected else None

    table = []
    for k, r in meta.items():
        v = {a: value(k, a) for a in algos}
        table.append({'instance': k, 'rig': r['rig'], 'field': r['field'], 'fills': r['fills'], 'headland': r['headland_scale'], 'turn_ratio': r['turn_ratio'], 'lines': r['lines'],
                      'real': r['field'].startswith('nl_'), **v})
    gains = {}
    for a in algos[1:]:
        pairs = [(f"{t['rig']}|{t['field']}", 1 - t[a] / t['agri.sequential']) for t in table if t[a] is not None and t['agri.sequential']]
        gains[a] = {'n': len(pairs), 'median_gain': round(_median([p[1] for p in pairs]), 4) if pairs else None, 'ci95_by_geometry': _cluster_ci(pairs),
                    'share_below_2pct': round(sum(p[1] < .02 for p in pairs) / len(pairs), 4) if pairs else None}

    def slices(key, label):
        groups = defaultdict(list)
        for t in table:
            if t['agri.split_anneal'] is not None and t['agri.sequential']:
                groups[key(t)].append((f"{t['rig']}|{t['field']}", 1 - t['agri.split_anneal'] / t['agri.sequential']))
        return {'by': label, 'rows': [{'level': str(k), 'n': len(v), 'median_gain': round(_median([x[1] for x in v]), 4), 'ci95_by_geometry': _cluster_ci(v)} for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))]}

    def ratio_bin(t):
        x = t['turn_ratio']
        return '0 (原地转向)' if x == 0 else '≤1' if x <= 1 else '1–2' if x <= 2 else '2–4' if x <= 4 else '>4'

    moderators = [slices(ratio_bin, '掉头比 2r/w'), slices(lambda t: t['fills'], '加注次数（总需求/容量）'), slices(lambda t: t['headland'], '地头系数'),
                  slices(lambda t: t['rig'], '机组'), slices(lambda t: 'real' if t['real'] else 'generated', '田块来源')]
    comps = []
    for a, b in (('agri.split_anneal', 'agri.sequential'), ('agri.split_anneal', 'agri.skip'), ('agri.split_anneal', 'ortools.field'), ('ortools.field', 'agri.sequential'), ('agri.skip', 'agri.sequential')):
        d = [(f"{t['rig']}|{t['field']}", t[a] - t[b], 1 - t[a] / t[b]) for t in table if t[a] is not None and t[b]]
        diffs = [x[1] for x in d if abs(x[1]) > 1e-9]
        p = float(wilcoxon(diffs).pvalue) if len(diffs) >= 5 else None
        walsh = sorted((x + y) / 2 for i, x in enumerate([q[1] for q in d]) for y in [q[1] for q in d][i:i + 200])
        comps.append({'a': a, 'b': b, 'n': len(d), 'p': p, 'hodges_lehmann_m': round(_median(walsh), 2) if walsh else None,
                      'median_gain_vs_b': round(_median([x[2] for x in d]), 4) if d else None, 'ci95_by_geometry': _cluster_ci([(x[0], x[2]) for x in d]),
                      'wins': sum(x[1] < -1e-6 for x in d), 'losses': sum(x[1] > 1e-6 for x in d)})
    ordered = sorted([c for c in comps if c['p'] is not None], key=lambda c: c['p'])
    m = len(ordered)
    running = 0.
    for i, c in enumerate(ordered):
        running = max(running, min(1., c['p'] * (m - i)))
        c['p_holm'] = running
    exact = []
    for k, r in meta.items():
        best = value(k, 'agri.exact')
        if best is None:
            continue
        row = {'instance': k, 'lines': r['lines'], 'exact_m': best}
        for a in algos:
            v = value(k, a)
            row[a] = None if v is None or best <= 0 else round(v / best - 1, 4)
        exact.append(row)
    gap = {a: {'n': len([e for e in exact if e[a] is not None]), 'median_gap': _median([e[a] for e in exact if e[a] is not None]), 'worst_gap': max([e[a] for e in exact if e[a] is not None], default=None),
               'negative': sum(1 for e in exact if e[a] is not None and e[a] < -1e-6)} for a in algos}
    reasons = defaultdict(int)
    for r in excluded:
        reasons[r['reason'].split('：')[0]] += 1
    fail = defaultdict(int)
    for r in runs:
        if not (r['status'] == 'feasible' and r['valid']):
            fail[(r['algorithm'], (r['errors'] or ['?'])[0][:40])] += 1
    summary = {'instances': len(meta), 'excluded': len(excluded), 'exclusion_reasons': dict(reasons), 'feasibility': feas, 'gain_vs_sequential': gains, 'moderators': moderators,
               'pairwise': comps, 'exact_gap': gap, 'failures': [{'algorithm': k[0], 'error': k[1], 'count': v} for k, v in sorted(fail.items(), key=lambda kv: -kv[1])]}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('run', 'analyse', 'plan'))
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=16)
    args = ap.parse_args()
    if args.cmd == 'plan':
        items = plan_instances()
        print(len(items))
    elif args.cmd == 'run':
        run(args.out, args.workers)
    else:
        print(json.dumps(analyse(args.out), ensure_ascii=False, indent=1)[:4000])


if __name__ == '__main__':
    main()
