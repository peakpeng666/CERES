"""GeoJSON ⇄ fleet task maps. Roles come from feature properties (role / type / kind); lon/lat is projected to UTM metres."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

OBSTACLE_WORDS = {'pond': ('pond', 'water', '池塘', '水塘', '水'), 'building': ('building', 'house', '建筑', '房'),
                  'greenhouse': ('greenhouse', '温室', '大棚'), 'tree': ('tree', 'pole', '树', '杆'),
                  'ditch': ('ditch', 'canal', '沟', '渠'), 'rock': ('rock', 'stone', '石')}


def _role(props: dict) -> tuple[str, str | None]:
    text = ' '.join(str(props.get(k, '')) for k in ('role', 'type', 'kind', 'class', 'name', 'category')).lower()
    if any(w in text for w in ('boundary', 'field', '边界', '田界', '场地')):
        return 'boundary', None
    if any(w in text for w in ('unload', 'supply', '补给', '卸货', '卸载点')):
        return 'unload', None
    if any(w in text for w in ('depot', 'base', 'garage', '车库', '仓库', '基地', '起点')):
        return 'depot', None
    if any(w in text for w in ('task', 'sample', 'point', 'target', '任务', '采样', '点')):
        return 'task', None
    if any(w in text for w in ('road', 'track', 'headland', 'corridor', '道路', '机耕道', '机耕路', '田间路', '地头', '作业道')):
        return 'corridor', None
    for kind, words in OBSTACLE_WORDS.items():
        if any(w in text for w in words):
            return 'obstacle', kind
    if any(w in text for w in ('obstacle', 'block', '障碍')):
        return 'obstacle', 'other'
    if any(w in text for w in ('plot', 'parcel', 'block', '地块', '田块')):
        return 'plot', None
    return 'unknown', None


def _lonlat(coords) -> bool:
    flat = [c for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
    return bool(flat) and all(abs(x) <= 180 and abs(y) <= 90 for x, y, *_ in flat) and \
        (max(x for x, *_ in flat) - min(x for x, *_ in flat)) < 1.0


def _all_coords(geom):
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Point':
        return [c]
    if t in ('LineString', 'MultiPoint'):
        return list(c)
    if t == 'Polygon':
        return [p for ring in c for p in ring]
    if t == 'MultiPolygon':
        return [p for poly in c for ring in poly for p in ring]
    return []


def import_geojson(obj: dict, title: str | None = None, vehicles: dict | None = None) -> dict:
    if obj.get('type') == 'Feature':
        obj = {'type': 'FeatureCollection', 'features': [obj]}
    feats = obj.get('features') if isinstance(obj, dict) else None
    if not isinstance(feats, list) or not feats:
        raise ValueError('需要 GeoJSON FeatureCollection')
    every = [p for f in feats for p in _all_coords(f.get('geometry') or {})]
    if not every:
        raise ValueError('GeoJSON 中没有几何')
    project = None
    crs = 'local'
    if _lonlat(every):
        from pyproj import Transformer
        lon = sum(p[0] for p in every) / len(every)
        lat = sum(p[1] for p in every) / len(every)
        epsg = (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1
        tr = Transformer.from_crs('EPSG:4326', f'EPSG:{epsg}', always_xy=True)
        xs, ys = tr.transform([p[0] for p in every], [p[1] for p in every])
        x0, y0 = min(xs), min(ys)
        project = lambda p: [round(tr.transform(p[0], p[1])[0] - x0, 3), round(tr.transform(p[0], p[1])[1] - y0, 3)]
        crs = f'EPSG:{epsg}'
    else:
        x0, y0 = min(p[0] for p in every), min(p[1] for p in every)
        shift = x0 < 0 or y0 < 0
        project = (lambda p: [round(p[0] - x0, 3), round(p[1] - y0, 3)]) if shift else (lambda p: [float(p[0]), float(p[1])])

    def ring(coords):
        pts = [project(p) for p in coords]
        return pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts
    boundary, plots, obstacles, tasks, depot, corridors, depot_pts, unload_pts = None, [], [], [], None, [], [], []
    for i, f in enumerate(feats):
        g, props = f.get('geometry') or {}, f.get('properties') or {}
        role, kind = _role(props)
        polys = [g['coordinates']] if g.get('type') == 'Polygon' else g.get('coordinates', []) if g.get('type') == 'MultiPolygon' else []
        if g.get('type') == 'Point':
            if role == 'unload':
                unload_pts.append(project(g['coordinates']))
            elif role == 'depot' or (role == 'unknown' and not depot_pts and not tasks and props.get('depot')):
                depot_pts.append(project(g['coordinates']))
            else:
                tasks.append({'id': len(tasks) + 1, 'xy': project(g['coordinates']), 'demand': int(props.get('demand') or 1),
                              'label': str(props.get('label') or props.get('name') or f'T{len(tasks) + 1}')})
        if role == 'corridor' and g.get('type') == 'LineString':
            width = props.get('width_m', props.get('width', 2.5))
            if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or not 1 <= width <= 8:
                raise ValueError('道路 width_m / width 须为 1–8 米的有限数值')
            from shapely.geometry import LineString
            line = LineString([project(p) for p in g['coordinates']]).buffer(width / 2)
            corridors.append({'id': f'C{len(corridors) + 1}', 'name': str(props.get('name') or ''), 'width_m': width,
                              'polygon': [[round(x, 3), round(y, 3)] for x, y in line.exterior.coords[:-1]]})
        for poly in polys:
            outer = ring(poly[0])
            if role == 'boundary' and boundary is None:
                boundary = outer
            elif role == 'obstacle':
                obstacles.append({'id': f'O{len(obstacles) + 1}', 'kind': kind or 'other', 'name': str(props.get('name') or ''), 'polygon': outer})
            elif role == 'corridor':
                corridors.append({'id': f'C{len(corridors) + 1}', 'name': str(props.get('name') or ''), 'polygon': outer})
            elif role in ('plot', 'unknown'):
                plots.append({'id': chr(ord('A') + len(plots) % 26), 'name': str(props.get('name') or f'地块 {len(plots) + 1}'), 'polygon': outer})
    from shapely.geometry import MultiPoint, Polygon
    if boundary is None:
        hull = MultiPoint([tuple(p) for p in ([q for pl in plots for q in pl['polygon']] + [t['xy'] for t in tasks])]).convex_hull.buffer(8, join_style=2)
        boundary = [[round(x, 2), round(y, 2)] for x, y in list(hull.exterior.coords)[:-1]]
    if not depot_pts:
        b = Polygon(boundary).buffer(-2.0, join_style=2)
        x, y = b.exterior.coords[0]
        depot_pts.append([round(x, 2), round(y, 2)])
    depot = depot_pts[0]
    if not tasks:
        raise ValueError('GeoJSON 中没有任务点（Point 要素）')
    K = int((vehicles or {}).get('count') or (obj.get('properties') or {}).get('vehicles') or 2)
    demand = sum(t['demand'] for t in tasks)
    instance = {'boundary': boundary, 'plots': plots, 'obstacles': obstacles, 'depot': depot, 'tasks': tasks,
                **({'corridors': corridors} if corridors else {}),
                **({'depots': depot_pts} if len(depot_pts) > 1 else {}),
                **({'unload_points': unload_pts} if unload_pts else {}),
                'vehicles': {'count': K, 'capacity': int((vehicles or {}).get('capacity') or math.ceil(demand / K * 1.3)),
                             'speed_mps': float((vehicles or {}).get('speed_mps') or 1.0), 'trips': 'multi'},
                'objective': 'total_distance', 'robot_radius': 0.5, 'units': 'm'}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return {'id': f'import-{stamp}', 'title': (title or '导入的地图')[:60],
            'summary': f"导入 · {len(tasks)} 个任务点 · {len(obstacles)} 处障碍 · {len(plots)} 块地",
            'problem_class': 'fleet_routing', 'frame': {'kind': 'field', 'units': 'm', 'crs': crs}, 'cost_unit': 'm',
            'provenance': {'generator': 'urp_lab.compose.geo', 'params': {'crs': crs}, 'seed': None, 'note': 'GeoJSON 导入；经纬度已投影到 UTM 并平移到局部坐标。'},
            'sim': {'supported': False, 'world': None, 'spawn': None, 'reason': '导入的地图不是 Gazebo 世界'},
            'instance': instance, 'editable': {'tasks': True, 'depot': True, 'capacity': True, 'demands': True}, 'layers': {}, 'origin': 'user'}


def export_geojson(scenario: dict, solution: dict | None = None) -> bytes:
    inst = (solution or {}).get('instance') or scenario['instance']
    poly = lambda pts: {'type': 'Polygon', 'coordinates': [list(pts) + [pts[0]]]}
    feats = [{'type': 'Feature', 'properties': {'role': 'boundary'}, 'geometry': poly(inst['boundary'])}]
    feats += [{'type': 'Feature', 'properties': {'role': 'plot', 'name': p.get('name'), 'crop': p.get('crop')}, 'geometry': poly(p['polygon'])} for p in inst.get('plots') or []]
    feats += [{'type': 'Feature', 'properties': {'role': 'obstacle', 'kind': o['kind'], 'name': o.get('name')}, 'geometry': poly(o['polygon'])} for o in inst.get('obstacles') or []]
    feats += [{'type': 'Feature', 'properties': {'role': 'corridor', 'name': c.get('name')}, 'geometry': poly(c['polygon'])} for c in inst.get('corridors') or []]
    feats.append({'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': inst['depot']}})
    feats += [{'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': d}}
              for d in (inst.get('depots') or [])[1:]]
    feats += [{'type': 'Feature', 'properties': {'role': 'unload'}, 'geometry': {'type': 'Point', 'coordinates': p}}
              for p in inst.get('unload_points') or []]
    feats += [{'type': 'Feature', 'properties': {'role': 'task', 'id': t['id'], 'label': t['label'], 'demand': t['demand']},
               'geometry': {'type': 'Point', 'coordinates': t['xy']}} for t in inst['tasks']]
    for r in (solution or {}).get('routes') or []:
        feats.append({'type': 'Feature', 'properties': {'role': 'route', 'vehicle': r.get('vehicle_name'), 'color': r.get('color'),
                                                        'length_m': r.get('cost'), 'tasks': r.get('sequence'), 'trips': r.get('trips')},
                      'geometry': {'type': 'LineString', 'coordinates': r['polyline']}})
    doc = {'type': 'FeatureCollection', 'properties': {'units': 'm', 'crs': scenario.get('frame', {}).get('crs', 'local'),
                                                        'scenario': scenario.get('id'), 'algorithm': ((solution or {}).get('algorithm') or {}).get('name')},
           'features': feats}
    return json.dumps(doc, ensure_ascii=False, indent=1).encode()
