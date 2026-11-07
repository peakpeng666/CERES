"""Bridge from a checked field-operation plan to the field simulation stack.

Execution means the reference differential robot follows the planned path and reaches every line end in order.
The implement, its tank and the crop are not simulated. Only rigs whose platform has an execution adapter pass.
"""
from __future__ import annotations

import copy
import math

from shapely.geometry import Polygon

from ..field_physics import RADIUS
from . import check as checker


APRON_M = 6.0


class NotExecutable(ValueError):
    pass


def fleet_view(run: dict) -> dict:
    """The same plan, as the task-visiting record that Gazebo and MuJoCo consume. Geometry is unchanged."""
    if run.get('state') != 'done' or not run.get('routes'):
        raise NotExecutable('只有完成求解的方案可以进入仿真')
    if run.get('status') not in ('feasible', 'optimal') or (run.get('check') or {}).get('valid') is not True:
        raise NotExecutable('方案未通过独立检查，不能进入仿真')
    inst = run['instance']
    rig = inst['rig']
    if rig.get('tier') != 'simulation' or rig.get('adapter') != 'fieldsim-diffdrive':
        raise NotExecutable(f"{rig['name']} 没有执行模型；要跑仿真，请把平台换成田间差速机器人")
    again, _ = checker.check(inst, run['routes'])
    if not again['valid']:
        raise NotExecutable('仿真前重新验算失败：' + '；'.join(again['errors'][:3]))
    tasks, routes = [], []
    speed = rig['speed_work_mps']
    lines = {x['id']: x for x in inst['lines']}
    for r in run['routes']:
        poly = [list(p) for p in r['polyline']]
        services, position = [], 0
        order, reload_stops = [], []
        for seg in r['segments']:
            pts = seg['polyline']
            # positions index the merged polyline
            if seg['kind'] == 'service':
                reload_stops.append(list(pts[0]))
                continue
            for p in pts:
                if poly[position] != p:
                    position += 1
                    while position < len(poly) and poly[position] != p:
                        position += 1
                    if position >= len(poly):
                        raise NotExecutable('方案几何与分段不一致')
            if seg['kind'] == 'work':
                tid = seg['line']
                order.append(tid)
                services.append({'task': tid, 'position': position, 'stop': list(pts[-1])})
                tasks.append({'id': tid, 'label': f'第 {lines[tid]["row"] + 1} 行', 'xy': list(pts[-1]), 'demand': 1, 'service_s': 0})
        length = sum(math.dist(a, b) for a, b in zip(poly, poly[1:]))
        routes.append({'vehicle': r['vehicle'], 'vehicle_name': r['vehicle_name'], 'color': r.get('color'), 'polyline': poly, 'sequence': order,
                       'trips': r['trips'], 'services': services, 'reload_stops': reload_stops, 'cost': length,
                       'load': max(len(t) for t in r['trips']), 'duration_s': length / speed, 'field_duration_s': r['duration_s']})
    # The simulated world keeps a farm track around the field: parked robots wait there, clear of the mission paths.
    apron = Polygon(inst['field']).buffer(APRON_M, join_style=2)
    boundary = [[round(x, 3), round(y, 3)] for x, y in list(apron.exterior.coords)[:-1]]
    fleet = {'boundary': boundary, 'plots': [], 'obstacles': [{'id': f'O{i + 1}', 'kind': 'pond', 'polygon': o} for i, o in enumerate(inst.get('obstacles') or [])],
             'corridors': [], 'depot': inst['depot'], 'unload_points': [s['xy'] for s in inst['stations'] if list(s['xy']) != list(inst['depot'])] or None,
             'tasks': tasks, 'robot_radius': max(RADIUS, rig['clearance_width_m'] / 2),
             'vehicles': {'count': len(routes), 'capacity': len(tasks), 'speed_mps': speed, 'trips': 'multi', 'travel': 'free'}, 'objective': 'total_distance'}
    view = copy.copy(run)
    view.update(problem_class='fleet_routing', instance=fleet, routes=routes, source_problem_class='field_operation',
                raw_result={**(run.get('raw_result') or {}), 'model_contract': {**((run.get('raw_result') or {}).get('model_contract') or {}), 'schema': 'urp.fleet-model/2'}})
    return view
