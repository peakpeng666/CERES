"""Safe integer costs and node-domain constraints for fleet routing models."""
from __future__ import annotations

import numpy as np


def _scaled_costs(values, scale, max_arc_cost, reachable=None):
    """Round finite costs only; zero placeholders must be excluded by the model.

    Bound before the int64 cast (and compare as Python ints at its upper edge).
    A supplied reachability mask also detects arithmetic overflow on real arcs,
    rather than silently treating that overflow as a newly missing edge.
    """
    values = np.asarray(values, dtype=float)
    if np.isnan(values).any() or (values < 0).any():
        raise ValueError('求解器距离/时间须为非负有限值或不可达的正无穷')
    reachable = np.isfinite(values) if reachable is None else reachable
    with np.errstate(over='ignore', invalid='ignore'):
        rounded = np.rint(values[reachable] * scale)
    if not np.isfinite(rounded).all() or (rounded.size and int(rounded.max()) > max_arc_cost):
        raise ValueError('求解器距离/时间超出安全整数标度范围')
    costs = np.zeros(values.shape, dtype=np.int64)
    costs[reachable] = rounded.astype(np.int64)
    return costs, reachable


def _forbid_unreachable_arcs(routing, manager, reachable):
    """Forbid absent arcs in NextVar domains, including every start/end copy."""
    if reachable.all():
        return
    nodes = [manager.IndexToNode(i) for i in range(manager.GetNumberOfIndices())]
    sources = {}
    for i in range(routing.Size()):  # end indices have no NextVar
        sources.setdefault(nodes[i], []).append(i)
    for node, indices in sources.items():
        blocked = np.flatnonzero(~reachable[node, nodes]).tolist()
        for i in indices:
            routing.NextVar(i).RemoveValues(blocked)


def _disable_unused_depots(routing, manager, nd, homes):
    """Original depots without a home vehicle are not service/reload clients.

    RoutingIndexManager otherwise exposes them as mandatory ordinary nodes.
    Make them optional AND force inactivity: a zero penalty alone still permits
    gratuitous depot visits. Actual reload copies are modelled separately.
    """
    for depot in set(range(nd)) - set(homes):
        idx = manager.NodeToIndex(depot)
        routing.AddDisjunction([idx], 0)
        routing.ActiveVar(idx).SetValue(0)


