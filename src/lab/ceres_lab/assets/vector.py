"""Field / road vector maps (GeoJSON): CRS detection, explicit projection to local metres, semantic roles.

RFC 7946 GeoJSON is WGS84 longitude/latitude; degrees are never used as metres. Geographic data is
projected to the UTM zone of its centroid (pyproj) and shifted to a local origin; projected data
(legacy ``crs`` member, e.g. EPSG:32650 or EPSG:4547) is shifted only, after its units are checked.
The transform is stored so local coordinates can be mapped back. Holes and properties are kept.
"""
from __future__ import annotations

import json
import math
import re

from pyproj import CRS, Transformer

ROLES = {
    'boundary': ('boundary', 'field', 'farm', '边界', '田界', '场地', '农场'),
    'plot': ('plot', 'parcel', 'block', 'farmland', 'orchard', 'vineyard', '地块', '田块', '果园', '小区'),
    'road': ('road', 'track', 'highway', 'path', '道路', '机耕路', '路'),
    'headland': ('headland', '地头'),
    'nogo': ('nogo', 'no-go', 'keepout', 'keep_out', 'forbidden', 'restricted', '禁行', '禁区'),
    'unload': ('unload', 'supply', 'refill', 'loading', '卸货', '补给', '装卸', '卸载'),
    'depot': ('depot', 'garage', 'base', 'start', 'home', '车库', '基地', '起点', '仓库'),
    'row': ('row', 'crop_row', '作物行', '行'),
    'task': ('task', 'sample', 'target', 'waypoint', '任务', '采样', '巡检点', '目标'),
}
OBSTACLE_WORDS = {'pond': ('pond', 'water', '池塘', '水塘', '水面'), 'building': ('building', 'house', 'shed', '建筑', '房', '棚屋'),
                  'greenhouse': ('greenhouse', '温室', '大棚'), 'tree': ('tree', '树'), 'pole': ('pole', 'pylon', '电杆', '杆'),
                  'ditch': ('ditch', 'canal', '沟', '渠'), 'rock': ('rock', 'stone', '石')}
OBSTACLE_GENERIC = ('obstacle', 'barrier', '障碍')
TEXT_KEYS = ('role', 'type', 'kind', 'class', 'category', 'landuse', 'highway', 'name', 'label')


def role_of(props: dict, geom_type: str) -> tuple[str, str | None]:
    explicit = str(props.get('role') or '').strip().lower()
    text = ' '.join(str(props.get(k, '')) for k in TEXT_KEYS).lower()
    for role, words in ROLES.items():
        if explicit and explicit in words:
            return role, None
    for kind, words in OBSTACLE_WORDS.items():
        if (explicit and explicit in words) or any(w in text for w in words):
            return 'obstacle', kind
    if (explicit and explicit in OBSTACLE_GENERIC) or any(w in text for w in OBSTACLE_GENERIC):
        return 'obstacle', 'other'
    for role, words in ROLES.items():
        if any(re.search(rf'(^|[^a-z]){re.escape(w)}([^a-z]|$)', text) if w.isascii() else w in text for w in words):
            return role, None
    if geom_type in ('Point', 'MultiPoint'):
        return 'task', None
    return 'unknown', None


def features(doc) -> list[dict]:
    if not isinstance(doc, dict):
        raise ValueError('不是 GeoJSON 对象')
    t = doc.get('type')
    if t == 'FeatureCollection':
        feats = doc.get('features')
        if not isinstance(feats, list):
            raise ValueError('FeatureCollection 缺少 features 数组')
    elif t == 'Feature':
        feats = [doc]
    elif t in ('Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon', 'GeometryCollection'):
        feats = [{'type': 'Feature', 'geometry': doc, 'properties': {}}]
    else:
        raise ValueError(f'不支持的 GeoJSON 类型 {t!r}')
    out = []
    for i, f in enumerate(feats):
        g = (f or {}).get('geometry')
        if not isinstance(g, dict) or 'type' not in g:
            continue
        out.append({'id': f.get('id', i), 'geometry': g, 'properties': f.get('properties') or {}})
    if not out:
        raise ValueError('GeoJSON 中没有带几何的要素')
    return out


def _positions(geom: dict):
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Point':
        yield c
    elif t in ('MultiPoint', 'LineString'):
        yield from c
    elif t in ('MultiLineString', 'Polygon'):
        for ring in c:
            yield from ring
    elif t == 'MultiPolygon':
        for poly in c:
            for ring in poly:
                yield from ring
    elif t == 'GeometryCollection':
        for g in geom.get('geometries') or []:
            yield from _positions(g)


def detect_crs(doc: dict, feats: list[dict]) -> dict:
    legacy = ((doc.get('crs') or {}).get('properties') or {}).get('name') if isinstance(doc.get('crs'), dict) else None
    pts = [p for f in feats for p in _positions(f['geometry']) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        raise ValueError('GeoJSON 没有坐标')
    xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    if legacy:
        m = re.search(r'(?:EPSG::?|EPSG:)(\d+)', legacy) or re.search(r'CRS84', legacy)
        if m and m.group(0) == 'CRS84':
            return {'kind': 'geographic', 'epsg': 4326, 'source': 'crs 成员 CRS84', 'bbox': bbox}
        if m:
            crs = CRS.from_epsg(int(m.group(1)))
            unit = crs.axis_info[0].unit_name if crs.axis_info else ''
            kind = 'geographic' if crs.is_geographic else 'projected'
            return {'kind': kind, 'epsg': int(m.group(1)), 'name': crs.name, 'units': unit, 'source': f'crs 成员 {legacy}', 'bbox': bbox}
        return {'kind': 'unknown', 'epsg': None, 'source': f'无法解析的 crs 成员 {legacy}', 'bbox': bbox}
    if all(-180 <= x <= 180 for x in xs) and all(-90 <= y <= 90 for y in ys):
        return {'kind': 'geographic', 'epsg': 4326, 'source': 'RFC 7946 默认（WGS84 经纬度）', 'bbox': bbox}
    return {'kind': 'unknown', 'epsg': None, 'source': '坐标超出经纬度范围且未声明坐标系', 'bbox': bbox}


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def make_transform(crs: dict, *, assume_metres: bool = False, origin: list[float] | None = None) -> dict:
    """How source coordinates become local metres (x east, y north). Raises if the data's units are unknown."""
    x0, y0, x1, y1 = crs['bbox']
    if crs['kind'] == 'geographic':
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        target = utm_epsg(cx, cy)
        tr = Transformer.from_crs(crs.get('epsg') or 4326, target, always_xy=True)
        ex, ey = tr.transform(x0, y0)
        if origin is not None:
            ex, ey = tr.transform(*origin[:2])
        return {'source_crs': f"EPSG:{crs.get('epsg') or 4326}", 'projected_crs': f'EPSG:{target}', 'method': 'utm',
                'origin_projected': [round(ex, 3), round(ey, 3)], 'origin_source': origin or [x0, y0], 'units': 'm',
                'axes': 'x=东, y=北'}
    if crs['kind'] == 'projected':
        unit = (crs.get('units') or '').lower()
        if unit and unit not in ('metre', 'meter', 'm'):
            raise ValueError(f"{crs.get('name') or crs.get('epsg')} 的单位是 {unit}，不是米；请先转换为米制投影")
        ox, oy = origin[:2] if origin else (x0, y0)
        return {'source_crs': f"EPSG:{crs['epsg']}", 'projected_crs': f"EPSG:{crs['epsg']}", 'method': 'shift',
                'origin_projected': [ox, oy], 'origin_source': [ox, oy], 'units': 'm', 'axes': 'x=东, y=北'}
    if assume_metres:
        ox, oy = origin[:2] if origin else (x0, y0)
        return {'source_crs': 'unknown', 'projected_crs': 'local', 'method': 'shift', 'origin_projected': [ox, oy],
                'origin_source': [ox, oy], 'units': 'm', 'axes': '按文件坐标，x/y 已确认为米', 'assumed': True}
    raise ValueError('无法确定坐标系：坐标不是经纬度，也没有 crs 声明。若确认坐标单位是米，请在导入时勾选「坐标已是米」')


def apply(feats: list[dict], tf: dict) -> list[dict]:
    if tf['method'] == 'utm':
        tr = Transformer.from_crs(tf['source_crs'], tf['projected_crs'], always_xy=True)
        fwd = tr.transform
    else:
        def fwd(x, y):
            return x, y
    ox, oy = tf['origin_projected']

    def conv(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            x, y = fwd(float(c[0]), float(c[1]))
            return [round(x - ox, 3), round(y - oy, 3)]
        return [conv(v) for v in c]

    def geom(g):
        if g['type'] == 'GeometryCollection':
            return {'type': g['type'], 'geometries': [geom(x) for x in g.get('geometries') or []]}
        return {'type': g['type'], 'coordinates': conv(g['coordinates'])}

    return [{**f, 'geometry': geom(f['geometry'])} for f in feats]


def summarize(local: list[dict]) -> dict:
    counts: dict[str, int] = {}
    holes = 0
    for f in local:
        role, kind = role_of(f['properties'], f['geometry']['type'])
        f['role'], f['obstacle_kind'] = role, kind
        counts[role] = counts.get(role, 0) + 1
        g = f['geometry']
        if g['type'] == 'Polygon':
            holes += max(0, len(g['coordinates']) - 1)
        elif g['type'] == 'MultiPolygon':
            holes += sum(max(0, len(p) - 1) for p in g['coordinates'])
    pts = [p for f in local for p in _positions(f['geometry'])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {'roles': counts, 'holes': holes, 'bounds': [min(xs), min(ys), max(xs), max(ys)],
            'size_m': [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)], 'features': len(local)}


def inspect(path, *, assume_metres: bool = False) -> dict:
    out = {'errors': [], 'warnings': []}
    try:
        doc = json.loads(path.read_text(errors='replace'))
        feats = features(doc)
    except (ValueError, json.JSONDecodeError) as exc:
        out['errors'].append(f'{path.name}：{exc}')
        return out
    crs = detect_crs(doc, feats)
    out['crs'] = crs
    try:
        tf = make_transform(crs, assume_metres=assume_metres)
    except ValueError as exc:
        out['errors'].append(str(exc))
        return out
    local = apply(feats, tf)
    summary = summarize(local)
    out.update(transform=tf, summary=summary, features=local)
    if summary['size_m'][0] > 20000 or summary['size_m'][1] > 20000:
        out['warnings'].append(f"范围 {summary['size_m'][0]:.0f}×{summary['size_m'][1]:.0f} m 偏大，请确认坐标系")
    if 'boundary' not in summary['roles'] and 'plot' not in summary['roles']:
        out['warnings'].append('没有识别到边界或地块；可在属性里写 role=boundary / plot，或导入后在地图上补画')
    if summary['roles'].get('unknown'):
        out['warnings'].append(f"{summary['roles']['unknown']} 个要素没有识别出用途，导入后可逐个指定")
    if math.isfinite(summary['bounds'][0]) is False:
        out['errors'].append('坐标无效')
    return out


ROLE_COLOURS = {'boundary': ((246, 243, 233), (107, 91, 62)), 'plot': ((229, 242, 217), (91, 140, 62)), 'obstacle': ((189, 189, 189), (82, 82, 82)),
                'nogo': ((255, 225, 222), (192, 57, 43)), 'road': ((210, 210, 210), (120, 120, 120)), 'headland': ((236, 222, 190), (154, 123, 60))}


def thumbnail_png(local: list[dict], size: int = 360) -> bytes:
    """Small picture of the local-metre features for the asset library card (y up, equal scale)."""
    import io

    from PIL import Image, ImageDraw
    pts = [p for f in local for p in _positions(f['geometry'])]
    if not pts:
        raise ValueError('no geometry')
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    k = (size - 16) / max(x1 - x0, y1 - y0, 1e-9)
    tf = lambda p: (8 + (p[0] - x0) * k, size - 8 - (p[1] - y0) * k)  # noqa: E731
    img = Image.new('RGB', (size, size), (238, 240, 243))
    d = ImageDraw.Draw(img)
    order = {'boundary': 0, 'plot': 1, 'road': 2, 'headland': 2, 'obstacle': 3, 'nogo': 4}
    for f in sorted(local, key=lambda f: order.get(f['role'], 5)):
        g = f['geometry']
        fill, edge = ROLE_COLOURS.get(f['role'], ((220, 220, 220), (130, 130, 130)))
        polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates'] if g['type'] == 'MultiPolygon' else []
        for poly in polys:
            d.polygon([tf(p) for p in poly[0]], fill=fill, outline=edge)
            for hole in poly[1:]:
                d.polygon([tf(p) for p in hole], fill=(238, 240, 243), outline=edge)
        lines = [g['coordinates']] if g['type'] == 'LineString' else g['coordinates'] if g['type'] == 'MultiLineString' else []
        for line in lines:
            d.line([tf(p) for p in line], fill=edge, width=2)
        if g['type'] in ('Point', 'MultiPoint'):
            for p in ([g['coordinates']] if g['type'] == 'Point' else g['coordinates']):
                x, y = tf(p)
                d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(10, 132, 255))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()
