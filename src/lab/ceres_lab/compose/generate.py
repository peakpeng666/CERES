"""Generate an editable farm map (boundary, plots, obstacles, depot, tasks, fleet) from a task spec."""
from __future__ import annotations

import math

import numpy as np

CROPS = {'sampling': ['小麦', '玉米', '大豆', '油菜'], 'inspection': ['果树', '葡萄', '茶树', '玉米'],
         'spraying': ['果树', '小麦', '玉米', '蔬菜'], 'harvest': ['苹果', '柑橘', '番茄', '草莓'],
         'transport': ['蔬菜', '果树', '苗圃', '花卉'], 'seeding': ['小麦', '水稻', '玉米', '大豆']}
TASK_WORD = {'sampling': '采样点', 'inspection': '巡检点', 'spraying': '喷洒点', 'harvest': '采收点', 'transport': '投放点', 'seeding': '播种点'}


def _circle(cx, cy, rx, ry=None, n=18, rot=0.0):
    ry = rx if ry is None else ry
    c, s = math.cos(rot), math.sin(rot)
    return [[round(cx + rx * math.cos(a) * c - ry * math.sin(a) * s, 3), round(cy + rx * math.cos(a) * s + ry * math.sin(a) * c, 3)]
            for a in np.linspace(0, 2 * math.pi, n, endpoint=False)]


def _rect(x, y, w, h, rot=0.0):
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    c, s = math.cos(rot), math.sin(rot)
    return [[round(x + px * c - py * s, 3), round(y + px * s + py * c, 3)] for px, py in pts]


def boundary(W, H, shape, rng):
    if shape == 'L':
        cut_w, cut_h = W * 0.38, H * 0.42
        return [[0, 0], [W, 0], [W, H - cut_h], [W - cut_w, H - cut_h], [W - cut_w, H], [0, H]]
    if shape == 'irregular':
        pts = []
        for k, (x, y) in enumerate([(0, 0), (W * 0.5, 0), (W, 0), (W, H * 0.5), (W, H), (W * 0.5, H), (0, H), (0, H * 0.5)]):
            jx, jy = rng.uniform(-0.07, 0.07) * W, rng.uniform(-0.07, 0.07) * H
            pts.append([round(min(max(x + jx, -0.08 * W), 1.08 * W), 2), round(min(max(y + jy, -0.08 * H), 1.08 * H), 2)])
        return pts
    return [[0, 0], [W, 0], [W, H], [0, H]]


def generate(spec: dict, seed: int, outline: list | None = None) -> dict:
    """Build a farm map; `outline` (local metres) replaces the generated boundary, e.g. a real field from Fields2Benchmark."""
    from shapely.geometry import Point, Polygon, box
    from shapely.ops import unary_union
    rng = np.random.default_rng(seed)
    W, H = spec['size']
    outer = Polygon(outline) if outline else Polygon(boundary(W, H, spec['shape'], rng))
    if not outer.is_valid:
        outer = outer.buffer(0)
    radius = max(0.3, spec['robot_width'] / 2)
    margin = max(5.0, min(W, H) * 0.07)
    road = max(3.0, min(W, H) * 0.035)
    inner = outer.buffer(-margin, join_style=2)
    # plots on a grid inside the headland, separated by farm roads
    n_plots = max(1, int(spec['plots']))
    cols = max(1, round(math.sqrt(n_plots * W / H)))
    rows = math.ceil(n_plots / cols)
    x0, y0, x1, y1 = inner.bounds
    pw, ph = (x1 - x0 - road * (cols - 1)) / cols, (y1 - y0 - road * (rows - 1)) / rows
    plots, crops = [], CROPS.get(spec['kind'], CROPS['sampling'])
    for k in range(n_plots):
        r, c = divmod(k, cols)
        cell = box(x0 + c * (pw + road), y0 + r * (ph + road), x0 + c * (pw + road) + pw, y0 + r * (ph + road) + ph).intersection(inner)
        if cell.is_empty or cell.area < 0.15 * pw * ph:
            continue
        if cell.geom_type not in ('Polygon', 'MultiPolygon'):
            continue
        if cell.geom_type != 'Polygon':
            cell = max(cell.geoms, key=lambda g: g.area)
        letter = chr(ord('A') + len(plots))
        plots.append({'id': letter, 'name': f'{letter} 区', 'crop': crops[len(plots) % len(crops)],
                      'rows_deg': 0 if (len(plots) % 2 == 0) == (pw >= ph) else 90,
                      'polygon': [[round(x, 2), round(y, 2)] for x, y in list(cell.exterior.coords)[:-1]]})
    depot_xy = {'sw': (margin * 0.55, margin * 0.55), 'se': (W - margin * 0.55, margin * 0.55), 'nw': (margin * 0.55, H - margin * 0.55),
                'ne': (W - margin * 0.55, H - margin * 0.55), 'center': (W / 2, H / 2), 's': (W / 2, margin * 0.55),
                'n': (W / 2, H - margin * 0.55), 'w': (margin * 0.55, H / 2), 'e': (W - margin * 0.55, H / 2)}[spec['depot']]
    if spec['depot'] == 'center' and plots:
        depot_xy = (W / 2, y0 + (rows > 1) * (ph + road / 2) if rows > 1 else margin * 0.55)
    depot = Point(depot_xy)
    if not outer.buffer(-radius - 0.3).contains(depot):
        # Nearest point of the drivable ring to the requested corner (real fields are rarely rectangles).
        ring = outer.buffer(-max(radius + 1.5, margin * 0.55))
        ring = max(ring.geoms, key=lambda g: g.area) if ring.geom_type == 'MultiPolygon' else ring
        from shapely.ops import nearest_points
        depot = nearest_points(ring.exterior, depot)[0] if not ring.is_empty else outer.representative_point()
        if not outer.buffer(-radius - 0.3).contains(depot):
            depot = outer.buffer(-radius - 1).representative_point()
    obstacles, taken = [], [depot.buffer(margin * 1.2)]
    plot_union = unary_union([Polygon(p['polygon']) for p in plots]) if plots else inner

    def place(make, region, tries=200):
        for _ in range(tries):
            poly = Polygon(make())
            if not poly.is_valid or not region.contains(poly):
                continue
            if any(poly.buffer(radius * 2 + 1.0).intersects(t) for t in taken):
                continue
            taken.append(poly)
            return poly
        return None
    size = min(W, H)
    band = outer.buffer(-radius - 1.0).difference(inner.buffer(-1.0))
    makers = {
        'pond': lambda: _circle(*_pick(rng, plot_union), rng.uniform(0.06, 0.11) * size, rng.uniform(0.04, 0.08) * size, 20, rng.uniform(0, math.pi)),
        'building': lambda: _rect(*_pick(rng, band), rng.uniform(8, 14), rng.uniform(6, 9), rng.choice([0, math.pi / 2])),
        'greenhouse': lambda: _rect(*_pick(rng, plot_union), rng.uniform(18, 28), rng.uniform(7, 10), rng.choice([0, math.pi / 2])),
        'tree': lambda: _circle(*_pick(rng, plot_union), rng.uniform(0.8, 1.6), None, 10),
        'ditch': lambda: _rect(*_pick(rng, plot_union), rng.uniform(0.35, 0.55) * size, 1.6, rng.choice([0, math.pi / 2])),
        'rock': lambda: _circle(*_pick(rng, plot_union), rng.uniform(1.0, 2.2), rng.uniform(0.8, 1.6), 7, rng.uniform(0, math.pi)),
    }
    labels = {'pond': '池塘', 'building': '建筑', 'greenhouse': '温室', 'tree': '树', 'ditch': '水渠', 'rock': '石块'}
    for kind, count in (spec.get('obstacles') or {}).items():
        for k in range(int(count)):
            region = band if kind == 'building' and not band.is_empty else plot_union
            poly = place(makers[kind], region)
            if poly is not None:
                obstacles.append({'id': f'O{len(obstacles) + 1}', 'kind': kind, 'name': f'{labels[kind]} {k + 1}',
                                  'polygon': [[round(x, 3), round(y, 3)] for x, y in list(poly.exterior.coords)[:-1]]})
    blocked = unary_union([Polygon(o['polygon']).buffer(radius + 1.0) for o in obstacles]) if obstacles else None
    area = plot_union.buffer(-radius - 0.6)
    if blocked is not None:
        area = area.difference(blocked)
    tasks = _tasks(spec, area, plots, rng, radius)
    demand = max(1, int(spec.get('demand') or 1))
    for t in tasks:
        t['demand'] = demand if spec['kind'] not in ('harvest', 'transport') or spec.get('demand') else int(rng.integers(1, 4))
    total = sum(t['demand'] for t in tasks)
    K = max(1, int(spec['vehicles']))
    capacity = int(spec.get('capacity') or max(max(t['demand'] for t in tasks), math.ceil(total / K * 1.3)))
    return {
        'boundary': [[round(x, 2), round(y, 2)] for x, y in list(outer.exterior.coords)[:-1]],
        'plots': plots, 'obstacles': obstacles, 'depot': [round(depot.x, 2), round(depot.y, 2)], 'tasks': tasks,
        'vehicles': {'count': K, 'capacity': capacity, 'speed_mps': float(spec['speed_mps']), 'travel': 'rows',
                     'names': [f'{chr(0x2460 + i)} 号车' if i < 20 else f'车{i + 1}' for i in range(K)]},
        'objective': spec['objective'], 'robot_radius': round(radius, 3), 'units': 'm',
        'meta': {'task_word': TASK_WORD.get(spec['kind'], '任务点'), 'margin_m': round(margin, 2), 'road_m': round(road, 2)},
    }


def _pick(rng, region):
    from shapely.geometry import Point
    x0, y0, x1, y1 = region.bounds
    for _ in range(400):
        p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        if region.contains(Point(p)):
            return p
    rp = region.representative_point()
    return rp.x, rp.y


def _tasks(spec, area, plots, rng, radius):
    from shapely.geometry import LineString, Point, Polygon
    n = max(1, int(spec['tasks']))
    pts = []
    min_gap = max(1.2, radius * 2.5)

    def ok(p):
        return area.contains(Point(p)) and all(math.dist(p, q) >= min_gap for q in pts)
    if spec['distribution'] == 'clustered':
        centers = [_pick(rng, area) for _ in range(max(2, round(n / 8)))]
        sigma = max(3.0, min(*spec['size']) * 0.06)
        tries = 0
        while len(pts) < n and tries < n * 400:
            tries += 1
            c = centers[int(rng.integers(len(centers)))]
            p = (c[0] + rng.normal(0, sigma), c[1] + rng.normal(0, sigma))
            if ok(p):
                pts.append(p)
    elif spec['distribution'] == 'rows' and plots:
        lines = []
        for pl in plots:
            poly = Polygon(pl['polygon']).intersection(area)
            if poly.is_empty:
                continue
            x0, y0, x1, y1 = poly.bounds
            spacing = max(3.0, min(x1 - x0, y1 - y0) / 6)
            if pl['rows_deg'] == 0:
                lines += [poly.intersection(LineString([(x0 - 1, y), (x1 + 1, y)])) for y in np.arange(y0 + spacing / 2, y1, spacing)]
            else:
                lines += [poly.intersection(LineString([(x, y0 - 1), (x, y1 + 1)])) for x in np.arange(x0 + spacing / 2, x1, spacing)]
        segs = [g for ln in lines for g in (getattr(ln, 'geoms', None) or [ln]) if g.geom_type == 'LineString' and g.length > 1]
        total = sum(s.length for s in segs)
        for s in segs:
            k = max(1, round(n * s.length / total)) if total else 1
            for d in np.linspace(s.length / (k + 1), s.length * k / (k + 1), k):
                p = s.interpolate(d)
                if len(pts) < n and ok((p.x, p.y)):
                    pts.append((p.x, p.y))
    tries = 0
    while len(pts) < n and tries < n * 600:
        tries += 1
        p = _pick(rng, area)
        if ok(p):
            pts.append(p)
    plot_polys = [(pl['id'], Polygon(pl['polygon'])) for pl in plots]
    counter, tasks = {}, []
    for i, (x, y) in enumerate(pts):
        pid = next((pid for pid, poly in plot_polys if poly.contains(Point(x, y))), None)
        counter[pid] = counter.get(pid, 0) + 1
        tasks.append({'id': i + 1, 'xy': [round(x, 2), round(y, 2)], 'demand': 1,
                      'label': f'{pid}{counter[pid]}' if pid else f'T{i + 1}', 'plot': pid})
    return tasks
