"""Fleet shape helpers shared by the solver, engines and checker modules."""
from __future__ import annotations


def fleet_classes(inst):
    """Ordered vehicle classes (`vehicles.fleet`); legacy homogeneous instances synthesise one."""
    veh = inst['vehicles']
    fleet = veh.get('fleet')
    if isinstance(fleet, list) and fleet:
        return fleet
    return [{'name': '同型车', 'count': veh['count'], 'capacity': veh['capacity'],
             'speed_mps': float(veh.get('speed_mps') or 1.0), 'robot_radius': inst['robot_radius'],
             'min_turn_radius_m': 0.0, 'payload': 'load'}]


def vehicle_table(inst):
    """[(vehicle_index, class_index, class, depot_index)] flattened in class order.

    Vehicle indexes match route `vehicle`. With multiple depots the vehicles are assigned
    round-robin over `depots` in table order — a deterministic, documented policy."""
    nd = len(depot_list(inst))
    out, idx = [], 0
    for ci, cls in enumerate(fleet_classes(inst)):
        for _ in range(cls['count']):
            out.append((idx, ci, cls, idx % nd))
            idx += 1
    return out


def depot_list(inst):
    """Ordered depot coordinates (`depots`), single `[depot]` for legacy instances."""
    depots = inst.get('depots')
    if isinstance(depots, list) and len(depots) >= 1:
        return depots
    return [inst['depot']]
