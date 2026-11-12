"""Agricultural structural features (E1) for coverage and fleet instances, version ``agfeat/1``.

Three groups, kept separate so ablations compare like with like:

- ``size``     control arm: area, perimeter and minimum-rotated-rectangle extent (what a size/extent
               descriptor already knows; the analogue of the pure Euclidean/scale features of H0);
- ``shape``    dimensionless field geometry: compactness, solidity, elongation, rectangularity,
               rectilinearity/orientation coherence of the boundary, reflex vertices, concave pockets, holes;
- ``machine``  field-to-machine quantities: headland/turning ratios, inner working area, estimated number
               of swaths, swath length relative to the turning diameter and the expected turn share from
               exact Dubins U-turn lengths (the "turn-cost share" of the pre-registered H3).

Shape features are invariant to translation, rotation, vertex start index and orientation, and to uniform
scaling; machine features change only with the field-to-machine scale. Simplification tolerances are relative
(0.5 % of √area) so vertex counts do not depend on the unit. All values are finite; an empty inner working area
yields zeros plus ``inner_empty = 1``. Invariance tests: ``tests/test_evolve_features.py``.
"""
from __future__ import annotations

import math

from .geometry import components, edge_moments, largest, mrr_sides, reflex_count, uturn_length

VERSION = 'agfeat/1'
SIMPLIFY_REL = 0.005

SIZE = ('area_ha', 'perimeter_m', 'mrr_long_m', 'mrr_short_m')
SHAPE = ('compactness', 'solidity', 'elongation', 'rectangularity', 'rectilinearity', 'orientation_coherence',
         'vertices_s', 'reflex_s', 'reflex_share', 'pocket_share', 'holes', 'hole_share')
MACHINE = ('turn_radius_over_swath', 'headland_over_turn_diameter', 'inner_area_share', 'inner_components',
           'inner_solidity', 'inner_reflex_s', 'swaths_est', 'swath_len_over_turn_diameter', 'uturn_over_swath',
           'turn_share_est', 'field_short_over_swath', 'inner_empty')
COVERAGE = SIZE + SHAPE + MACHINE
GROUPS = {'size': SIZE, 'shape': SHAPE, 'machine': MACHINE}
# Feature sets compared in selection ablations (control arm vs agricultural structure).
FEATURE_SETS = {'size': SIZE, 'agri': COVERAGE}


def field_polygon(ring, holes=()):
    """Valid shapely polygon from a local-metre ring and optional holes."""
    from shapely.geometry import Polygon
    poly = Polygon(ring, [h for h in holes if len(h) >= 3])
    if not poly.is_valid:
        poly = largest(poly.buffer(0))
    if poly is None or poly.is_empty or poly.area <= 0:
        raise ValueError('field polygon is empty or invalid')
    return poly


def _simplified(poly, scale):
    import shapely
    simple = shapely.simplify(poly, SIMPLIFY_REL * scale, preserve_topology=True)
    return largest(simple) or poly


def shape_features(poly) -> dict:
    area, perim = poly.area, poly.length
    scale = math.sqrt(area)
    hull = poly.convex_hull
    long_side, short_side = mrr_sides(poly)
    simple = _simplified(poly, scale)
    vertices = len(simple.exterior.coords) - 1
    reflex = reflex_count(simple)
    pockets = hull.difference(poly)
    pocket = largest(pockets)
    hole_area = sum(abs(_ring_area(h.coords)) for h in poly.interiors)
    return {
        'area_ha': area / 1e4, 'perimeter_m': perim, 'mrr_long_m': long_side, 'mrr_short_m': short_side,
        'compactness': 4 * math.pi * area / perim ** 2 if perim else 0.0,
        'solidity': area / hull.area if hull.area else 0.0,
        'elongation': long_side / short_side if short_side > 0 else 0.0,
        'rectangularity': area / (long_side * short_side) if short_side > 0 else 0.0,
        'rectilinearity': edge_moments(simple, 4),
        'orientation_coherence': edge_moments(simple, 2),
        'vertices_s': float(vertices), 'reflex_s': float(reflex), 'reflex_share': reflex / vertices if vertices else 0.0,
        'pocket_share': (pocket.area / area) if pocket is not None else 0.0,
        'holes': float(len(poly.interiors)), 'hole_share': hole_area / (area + hole_area) if area else 0.0,
    }


def _ring_area(coords) -> float:
    pts = list(coords)
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))


def machine_features(poly, swath_width: float, turn_radius: float, headland_width: float) -> dict:
    if min(swath_width, turn_radius, headland_width) <= 0:
        raise ValueError('swath width, turning radius and headland width must be positive')
    area = poly.area
    inner = poly.buffer(-headland_width, join_style=2)
    main = largest(inner)
    uturn = uturn_length(swath_width, turn_radius)
    _long, short_side = mrr_sides(poly)
    base = {'turn_radius_over_swath': turn_radius / swath_width,
            'headland_over_turn_diameter': headland_width / (2 * turn_radius),
            'uturn_over_swath': uturn / swath_width, 'field_short_over_swath': short_side / swath_width}
    if main is None:
        return {**base, 'inner_area_share': 0.0, 'inner_components': 0.0, 'inner_solidity': 0.0, 'inner_reflex_s': 0.0,
                'swaths_est': 0.0, 'swath_len_over_turn_diameter': 0.0, 'turn_share_est': 0.0, 'inner_empty': 1.0}
    inner_area = inner.area
    hull = main.convex_hull
    _il, inner_short = mrr_sides(main)
    swaths = max(1.0, inner_short / swath_width)
    working = inner_area / swath_width                      # swath length needed to cover the inner area
    turning = (swaths - 1) * uturn
    return {**base, 'inner_area_share': inner_area / area, 'inner_components': float(components(inner)),
            'inner_solidity': main.area / hull.area if hull.area else 0.0,
            'inner_reflex_s': float(reflex_count(_simplified(main, math.sqrt(main.area)))),
            'swaths_est': swaths, 'swath_len_over_turn_diameter': working / swaths / (2 * turn_radius),
            'turn_share_est': turning / (turning + working) if turning + working > 0 else 0.0, 'inner_empty': 0.0}


def coverage_features(ring, holes, swath_width, turn_radius, headland_width) -> dict:
    """Ordered feature dict (``COVERAGE`` order) for one field and machine profile."""
    poly = field_polygon(ring, holes)
    values = {**shape_features(poly), **machine_features(poly, swath_width, turn_radius, headland_width)}
    out = {k: float(values[k]) for k in COVERAGE}
    bad = [k for k, v in out.items() if not math.isfinite(v)]
    if bad:
        raise ArithmeticError(f'non-finite features: {bad}')
    return out


def vector(features: dict, names) -> list[float]:
    return [float(features[k]) for k in names]


# ------------------------------------------------------------------ fleet tasks

FLEET = ('n_tasks', 'vehicles', 'demand_over_capacity', 'min_trips_per_vehicle', 'clark_evans', 'depot_eccentricity',
         'task_spread_m', 'area_ha', 'solidity', 'elongation', 'obstacle_share', 'row_travel')
FLEET_SIZE = ('n_tasks', 'vehicles', 'demand_over_capacity', 'task_spread_m', 'area_ha')


def fleet_features(inst: dict) -> dict:
    """Task, fleet and field descriptors of a fleet_routing instance (validated instance dict)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    tasks = inst['tasks']
    pts = [tuple(t['xy']) for t in tasks]
    n = len(pts)
    poly = field_polygon(inst['boundary'])
    shape = shape_features(poly)
    cap = float(inst['vehicles']['capacity'])
    K = int(inst['vehicles']['count'])
    demand = float(sum(t['demand'] for t in tasks))
    obstacles = [Polygon(o['polygon']) for o in inst.get('obstacles') or []]
    obs_area = unary_union(obstacles).area if obstacles else 0.0
    cx, cy = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n) if n else poly.centroid.coords[0]
    spread = math.sqrt(sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in pts) / n) if n else 0.0
    dx, dy = inst['depot'][0] - cx, inst['depot'][1] - cy
    nn = [min(math.dist(p, q) for j, q in enumerate(pts) if j != i) for i, p in enumerate(pts)] if n > 2 else []
    ce = (sum(nn) / len(nn)) / (0.5 * math.sqrt(poly.area / n)) if nn else 1.0
    out = {'n_tasks': float(n), 'vehicles': float(K), 'demand_over_capacity': demand / cap if cap else 0.0,
           'min_trips_per_vehicle': math.ceil(demand / cap) / K if cap and K else 0.0, 'clark_evans': ce,
           'depot_eccentricity': math.hypot(dx, dy) / math.sqrt(poly.area), 'task_spread_m': spread,
           'area_ha': shape['area_ha'], 'solidity': shape['solidity'], 'elongation': shape['elongation'],
           'obstacle_share': obs_area / poly.area, 'row_travel': 1.0 if inst['vehicles'].get('travel') == 'rows' else 0.0}
    return {k: float(out[k]) for k in FLEET}
