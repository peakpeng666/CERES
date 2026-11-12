"""Fields2Benchmark (Mier et al., Zenodo 10.5281/zenodo.14524735): 350 real agricultural fields for coverage planning."""
from __future__ import annotations

import copy
import json
import urllib.request
import zipfile
from functools import lru_cache

from . import CACHE

RECORD = 'https://zenodo.org/api/records/14524735/files'
DIR = CACHE / 'fields2benchmark'
INDEX = DIR / 'index.json'
COUNTRIES = {'nl': '荷兰', 'ee': '爱沙尼亚', 'lt': '立陶宛'}
# Typical mid-size machinery: 6 m implement, 2 m track, two passes of headland.
DEFAULTS = {'swath_width': 6.0, 'robot_width': 2.5, 'headland_width': 12.0, 'turn_radius': 4.5}


def _download(name: str):
    DIR.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(f'{RECORD}/{name}/content', headers={'User-Agent': 'CERES-Lab/0.2'})
    with urllib.request.urlopen(request, timeout=600) as response:
        data = response.read()
    tmp = DIR / f'{name}.part'
    tmp.write_bytes(data)
    tmp.replace(DIR / name)


def fetch(images: bool = False) -> dict:
    for name in ('README.md', 'LICENSE', 'wkt.zip') + (('imgs.zip',) if images else ()):
        if not (DIR / name).is_file():
            _download(name)
    _index.cache_clear()
    return status()


def _zip_names(archive: str, suffix: str) -> dict[str, str]:
    path = DIR / archive
    if not path.is_file():
        return {}
    with zipfile.ZipFile(path) as z:
        return {n.rsplit('/', 1)[-1].rsplit('.', 1)[0]: n for n in z.namelist() if n.lower().endswith(suffix)}


def _project(geom):
    """WGS84 lon/lat → UTM metres (zone from centroid), shifted so the field's bounding box starts at (0, 0)."""
    from pyproj import Transformer
    from shapely.ops import transform
    lon, lat = geom.centroid.x, geom.centroid.y
    zone = int((lon + 180) // 6) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    to_utm = Transformer.from_crs('EPSG:4326', f'EPSG:{epsg}', always_xy=True).transform
    utm = transform(to_utm, geom)
    x0, y0 = utm.bounds[0], utm.bounds[1]
    local = transform(lambda x, y, z=None: (x - x0, y - y0), utm)
    return local, {'crs': f'EPSG:{epsg}', 'offset': [round(x0, 3), round(y0, 3)], 'lonlat_centroid': [round(lon, 6), round(lat, 6)]}


@lru_cache(maxsize=1)
def _index() -> dict:
    if INDEX.is_file() and (DIR / 'wkt.zip').is_file() and INDEX.stat().st_mtime >= (DIR / 'wkt.zip').stat().st_mtime:
        return json.loads(INDEX.read_text(encoding='utf-8'))
    names = _zip_names('wkt.zip', '.wkt')
    if not names:
        return {'fields': []}
    from shapely import wkt
    rows = []
    with zipfile.ZipFile(DIR / 'wkt.zip') as z:
        for fid, member in sorted(names.items(), key=lambda kv: (kv[0][:2], int(''.join(c for c in kv[0] if c.isdigit()) or 0))):
            geom = wkt.loads(z.read(member).decode())
            if geom.geom_type == 'MultiPolygon':
                geom = max(geom.geoms, key=lambda g: g.area)
            local, proj = _project(geom)
            hull = local.convex_hull.area
            minx, miny, maxx, maxy = local.bounds
            rows.append({'id': fid, 'country': fid[:2], 'country_name': COUNTRIES.get(fid[:2], fid[:2]),
                         'area_ha': round(local.area / 1e4, 3), 'perimeter_m': round(local.length, 1),
                         'vertices': len(local.exterior.coords) - 1, 'holes': len(local.interiors),
                         'convexity': round(local.area / hull, 4) if hull else None,
                         'size_m': [round(maxx - minx, 1), round(maxy - miny, 1)], 'projection': proj})
    doc = {'fields': rows, 'source': 'Fields2Benchmark (Zenodo 10.5281/zenodo.14524735)', 'license': 'CC-BY-4.0（源数据条款见 LICENSE/README）'}
    INDEX.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return doc


def status() -> dict:
    idx = _index()
    return {'downloaded': (DIR / 'wkt.zip').is_file(), 'fields': len(idx['fields']),
            'images': (DIR / 'imgs.zip').is_file(), 'doi': '10.5281/zenodo.14524735', 'ref': 'mier2024fields2benchmark'}


def listing(country: str | None = None, query: str | None = None) -> list[dict]:
    images = _zip_names('imgs.zip', '.png') or _zip_names('imgs.zip', '.jpg')
    out = []
    for f in _index()['fields']:
        if country and f['country'] != country:
            continue
        if query and query.lower() not in f['id']:
            continue
        out.append({**f, 'scenario_id': scenario_id(f['id']), 'image': f['id'] in images})
    return out


def image(field_id: str) -> tuple[bytes, str] | None:
    for suffix, media in (('.png', 'image/png'), ('.jpg', 'image/jpeg'), ('.jpeg', 'image/jpeg')):
        names = _zip_names('imgs.zip', suffix)
        if field_id in names:
            with zipfile.ZipFile(DIR / 'imgs.zip') as z:
                return z.read(names[field_id]), media
    return None


def scenario_id(field_id: str) -> str:
    return 'field-' + field_id.replace('_', '-')


def field_from_scenario(sid: str) -> str | None:
    if not sid.startswith('field-'):
        return None
    wanted = sid[len('field-'):]
    return next((f['id'] for f in _index()['fields'] if f['id'].replace('_', '-') == wanted), None)


@lru_cache(maxsize=64)
def _scenario(field_id: str) -> dict:
    from shapely import wkt
    meta = next(f for f in _index()['fields'] if f['id'] == field_id)
    with zipfile.ZipFile(DIR / 'wkt.zip') as z:
        geom = wkt.loads(z.read(_zip_names('wkt.zip', '.wkt')[field_id]).decode())
    if geom.geom_type == 'MultiPolygon':
        geom = max(geom.geoms, key=lambda g: g.area)
    local, proj = _project(geom)
    local = local.simplify(0.05, preserve_topology=True)
    ring = [[round(x, 3), round(y, 3)] for x, y in list(local.exterior.coords)[:-1]]
    holes = [[[round(x, 3), round(y, 3)] for x, y in list(h.coords)[:-1]] for h in local.interiors]
    head = DEFAULTS['headland_width'] if local.buffer(-DEFAULTS['headland_width']).area > 0.2 * local.area else DEFAULTS['swath_width']
    return {
        'id': scenario_id(field_id), 'title': f"{meta['country_name']}田块 {field_id.split('_')[-1]}",
        'summary': f"Fields2Benchmark 真实农田 · {meta['area_ha']:g} ha · {meta['vertices']} 个边界顶点 · 凸性 {meta['convexity']:.2f}",
        'problem_class': 'coverage',
        'frame': {'kind': 'field', 'units': 'm', 'crs': proj['crs'], 'offset': proj['offset']},
        'cost_unit': 'm',
        'provenance': {'generator': 'Fields2Benchmark', 'params': {'field': field_id, **proj}, 'seed': None,
                       'note': '来自 EuroCrops 的真实地块边界，WGS84 投影到 UTM 后平移到局部坐标；作业参数取中型农机常用值，可在检查器中修改。',
                       'ref': 'mier2024fields2benchmark', 'url': 'https://doi.org/10.5281/zenodo.14524735'},
        'sim': {'supported': False, 'world': None, 'spawn': None, 'reason': '真实田块尺度远大于仿真世界'},
        'instance': {'field': ring, 'obstacles': holes, **DEFAULTS, 'headland_width': head,
                     'start': ring[0], 'route_pattern': 'boustrophedon', 'angle_deg': None},
        'editable': {'tasks': False, 'depot': False, 'capacity': False, 'demands': False},
        'layers': {'field_polygon': ring}, 'origin': 'fields2benchmark',
        'library': {'kind': 'fields', 'field': field_id, 'image': f"/lab/api/library/fields/{field_id}/image"},
    }


def scenario(field_id: str) -> dict:
    return copy.deepcopy(_scenario(field_id))


def thumbnail_svg(field_id: str) -> str:
    """Small vector outline for gallery cards (no satellite image required)."""
    s = _scenario(field_id)
    pts = s['instance']['field']
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs) or 1, max(ys) - min(ys) or 1
    k = 88 / max(w, h)
    path = ' '.join(f'{6 + (x - min(xs)) * k:.1f},{94 - (y - min(ys)) * k:.1f}' for x, y in pts)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><polygon points="{path}" fill="#d9ead3" stroke="#3a7d44" stroke-width="1.2"/></svg>'


def all_ids() -> list[str]:
    return [f['id'] for f in _index()['fields']]
