"""Probe whether the Fields2Cover route planner exposes a determinism switch (round 2, task T2).

``RP_RoutePlannerBase.genRoute`` defaults to a 1 s budget (``time_limit_seconds=1``) and
``search_for_optimum=False``, i.e. a time-limited search; ``computeBestRoute`` has
``use_guided_local_search``. This probe replays the exact decorator pipeline of ``candidates._native`` with
different route-planner arguments on a recorded task whose path hash changed between runs, and reports how many
distinct canonical path hashes each setting produces. It is research code only and never changes the adapters.

Usage from ``src/lab``::

    .venv/bin/python -c "from ceres_lab.evolve import route_determinism as P; print(P.probe('evolve-repair-20261023', '8792aeb4e5a72873'))"
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SETTINGS = {
    'default': {},
    'time_limit_10s': {'time_limit_seconds': 10.0},
    'optimum': {'search_for_optimum': True},
    'optimum_time_limit_10s': {'search_for_optimum': True, 'time_limit_seconds': 10.0},
}


def _pipeline(instance: dict, params: dict, route_kwargs: dict) -> tuple[list[list[float]], list[str]]:
    import fields2cover as f2c

    from ..solvers import f2c_coverage as C

    def ring(points):
        pts = list(points) + [points[0]]
        return f2c.LinearRing(f2c.VectorPoint([f2c.Point(float(x), float(y)) for x, y in pts]))
    cell = f2c.Cell(ring(instance['field']))
    for hole in instance.get('obstacles') or []:
        cell.addRing(ring(hole))
    cells = f2c.Cells(cell)
    robot = f2c.Robot(instance['robot_width'], instance['swath_width'])
    robot.setMinTurningRadius(instance['turn_radius'])
    hl = f2c.HG_Const_gen()
    width = instance['headland_width']
    mid_hl = hl.generateHeadlands(cells, width / 2)
    inner = hl.generateHeadlands(cells, width)
    decomposition = params.get('decomposition', 'boustrophedon')
    if C.DECOMPOSITIONS.get(decomposition):
        decomp = getattr(f2c, C.DECOMPOSITIONS[decomposition])()
        decomp.setSplitAngle(0.5 * math.pi)
        inner = decomp.decompose(inner)
    objective = getattr(f2c, C.SWATH_OBJECTIVES[params.get('swath_objective', 'n_swath')])()
    swaths = f2c.SG_BruteForce().generateBestSwaths(objective, robot.getCovWidth(), inner)
    planner = f2c.RP_RoutePlannerBase()
    if route_kwargs:
        route = planner.genRoute(mid_hl, swaths, False, 1e-4, True,
                                 int(route_kwargs.get('time_limit_seconds', 1)),
                                 bool(route_kwargs.get('search_for_optimum', False)))
    else:
        route = planner.genRoute(mid_hl, swaths)
    turner = getattr(f2c, C.TURN_MODELS[params.get('turn_model', 'dubins')])()
    turner.setDiscretization(C.STEP)
    path = f2c.PP_PathPlanning().planPath(robot, route, turner)
    states = [path.getState(i) for i in range(path.size())]
    end = path.atEnd()
    pts = [[s.point.getX(), s.point.getY()] for s in states] + [[end.getX(), end.getY()]]
    kinds = [C.SECTION.get(int(s.type), 'other') for s in states]
    return pts, kinds


def _one(args):
    field, profile, params, label, repeat, cores = args
    if cores and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, set(cores))
    os.environ.update({'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'})
    from . import instances as I
    _sc, inst = I.coverage_instance(field, profile)
    pts, kinds = _pipeline(inst, params, SETTINGS[label])
    digest = hashlib.sha256(json.dumps([pts], separators=(',', ':')).encode()).hexdigest()
    swaths = [[pts[i], pts[i + 1]] for i, k in enumerate(kinds) if k == 'swath' and math.dist(pts[i], pts[i + 1]) > 1e-6]
    from ..solvers import f2c_coverage as C
    check, metrics = C.check_path(inst, pts, swaths)
    length = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
    return label, repeat, digest, len(pts), length, bool(check['valid'])


def probe(run: str, job_id: str, repeats: int = 10, workers: int = 4, cores=(0, 1, 2, 3),
          settings=tuple(SETTINGS), write: bool = True) -> dict:
    root = Path(__file__).resolve().parents[4] / 'experiments' / 'runs' / run
    rec = json.loads((root / 'records' / f'{job_id}.json').read_text(encoding='utf-8'))
    field, profile = rec['instance']['field'], rec['instance']['profile']
    params = dict(rec['params'])
    jobs = [(field, profile, params, label, k, cores) for label in settings for k in range(repeats)]
    out = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for label, repeat, digest, n, length, valid in ex.map(_one, jobs):
            out.setdefault(label, []).append({'repeat': repeat, 'sha256': digest, 'points': n, 'length_m': length,
                                              'official_valid': valid})
    res = {'run': run, 'job_id': job_id, 'field': field, 'profile': profile, 'params': params,
           'recorded_sha256': rec.get('routes_sha256'), 'repeats': repeats, 'settings_requested': list(settings),
           'settings': {label: {'distinct_hashes': len({r['sha256'] for r in rows}),
                                'matches_recorded': sum(r['sha256'] == rec.get('routes_sha256') for r in rows),
                                'valid_runs': sum(r['official_valid'] for r in rows),
                                'length_m_range': [min(r['length_m'] for r in rows), max(r['length_m'] for r in rows)],
                                'runs': rows}
                        for label, rows in out.items()}}
    if write:
        (root / 'route_probe.json').write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return {k: (v['distinct_hashes'], v['matches_recorded']) for k, v in res['settings'].items()}
