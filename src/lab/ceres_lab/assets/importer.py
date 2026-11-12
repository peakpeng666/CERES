"""Import pipeline: stage the upload → inspect (the wizard shows the report) → commit as a versioned asset.

Also: references from this machine (Gazebo Fuel cache, installed ROS description packages, Gazebo
worlds), the built-in vehicles, reference tracking, and turning a map asset into planning geometry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shlex
import shutil
import subprocess
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union

from .. import paths
from . import imagecal, local, models, profiles, rosmap, safety, store, vector

MAP_SUBTYPES = ('ros_grid', 'vector', 'image', 'gazebo_world')
MODEL_SUBTYPES = ('gltf', 'urdf', 'sdf_model', 'mesh', 'builtin')
EDITOR_OBSTACLE_KINDS = ('pond', 'building', 'greenhouse', 'tree', 'ditch', 'rock', 'other')
COPY_LIMIT = 64 << 20


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False))


def _is_ros_map_yaml(p: Path) -> bool:
    try:
        head = p.read_text(errors='replace')[:4096]
    except OSError:
        return False
    return 'image' in head and 'resolution' in head


def _is_geojson(p: Path) -> bool:
    try:
        head = p.read_text(errors='replace')[:2048]
    except OSError:
        return False
    return '"type"' in head and ('FeatureCollection' in head or '"Feature"' in head or '"coordinates"' in head)


# ------------------------------------------------------------- inspection --
def inspect_dir(d: Path, *, kind_hint: str = '', assume_metres: bool = False, warnings: list[str] | None = None) -> dict:
    root = d / 'files'
    derived = d / 'derived'
    files = sorted(p for p in root.rglob('*') if p.is_file())
    by: dict[str, list[Path]] = {}
    for p in files:
        by.setdefault(safety.kind_of(p), []).append(p)
    rep = {'files': [{'path': p.relative_to(root).as_posix(), 'size': p.stat().st_size, 'kind': safety.kind_of(p)} for p in files],
           'warnings': list(warnings or []), 'errors': [], 'subtype': None, 'kind': None, 'primary': None, 'inspection': {},
           'others': [], 'preview': None, 'thumb': None}

    def pick(cands: list[Path]) -> Path:
        rep['others'] = [p.relative_to(root).as_posix() for p in cands[1:]]
        if rep['others']:
            rep['warnings'].append(f"包含多个候选（{', '.join(rep['others'][:4])}），按第一个导入：{cands[0].relative_to(root).as_posix()}")
        rep['primary'] = cands[0].relative_to(root).as_posix()
        return cands[0]

    yamls = [p for p in by.get('yaml', []) if _is_ros_map_yaml(p)]
    geo = by.get('geojson', []) + [p for p in by.get('json', []) if _is_geojson(p)]
    gltf = by.get('glb', []) + by.get('gltf', [])
    sdfs = sorted(by.get('sdf', []), key=lambda p: (p.name != 'model.sdf', len(p.parts)))
    if yamls:
        y = pick(yamls)
        ins = rosmap.inspect(y, root)
        occ = ins.pop('occ', None)
        rep.update(subtype='ros_grid', kind='map')
        if occ is not None:
            derived.mkdir(exist_ok=True)
            np.save(derived / 'occupancy.npy', occ)
            png = rosmap.preview_png(occ)
            (derived / 'preview.png').write_bytes(png)
            (derived / 'thumb.png').write_bytes(rosmap.thumbnail_png(png))
            rep.update(preview='derived/preview.png', thumb='derived/thumb.png')
    elif geo:
        g = pick(geo)
        ins = vector.inspect(g, assume_metres=assume_metres)
        feats = ins.pop('features', None)
        rep.update(subtype='vector', kind='map')
        if feats is not None:
            _write_json(derived / 'features_local.json', {'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'id': f['id'], 'geometry': f['geometry'], 'properties': {**f['properties'], 'role': f['role'], 'obstacle_kind': f['obstacle_kind']}}
                for f in feats]})
            try:
                (derived / 'thumb.png').write_bytes(vector.thumbnail_png(feats))
                rep.update(thumb='derived/thumb.png')
            except Exception:  # noqa: BLE001 - a thumbnail is cosmetic
                pass
    elif by.get('urdf'):
        u = pick(by['urdf'])
        ins = models.inspect_urdf(u, root)
        rep.update(subtype='urdf', kind=kind_hint if kind_hint in ('vehicle', 'facility') else 'vehicle')
    elif by.get('xacro'):
        pick(by['xacro'])
        ins = {'errors': ['上传的 Xacro 不在工作台里展开（宏表达式与 include 可执行任意逻辑）。请先运行 xacro 生成 URDF 再导入，'
                          '或在「本机资源」里引用已安装的 ROS 描述包。'], 'warnings': []}
        rep.update(subtype='urdf', kind='vehicle')
    elif sdfs:
        s = pick(sdfs)
        ins = models.inspect_sdf(s, root)
        if ins.get('kind') == 'world':
            rep.update(subtype='gazebo_world', kind='map')
            ins.setdefault('warnings', []).append('Gazebo 世界作为参考与预览导入；规划几何需在任务地图上另外确认')
        else:
            default = 'vehicle' if ins.get('drive') else 'facility'
            rep.update(subtype='sdf_model', kind=kind_hint if kind_hint in ('vehicle', 'facility') else default)
    elif gltf:
        g = pick(gltf)
        ins = models.inspect_gltf(g, root)
        rep.update(subtype='gltf', kind=kind_hint if kind_hint in ('vehicle', 'facility') else 'vehicle')
    elif by.get('mesh'):
        m = pick(by['mesh'])
        ins = models.inspect_mesh(m)
        rep.update(subtype='mesh', kind=kind_hint if kind_hint in ('vehicle', 'facility') else 'facility')
    elif by.get('image'):
        im = pick(by['image'])
        try:
            ins = {'image': imagecal.inspect(im), 'errors': [], 'warnings': ['普通图片只作为背景：标定后可在上面绘制田块与障碍，不会自动变成可通行区域']}
            from PIL import Image
            with Image.open(im) as pic:
                pic.thumbnail((360, 360))
                derived.mkdir(exist_ok=True)
                pic.convert('RGBA').save(derived / 'thumb.png', 'PNG')
            rep.update(thumb='derived/thumb.png', preview='files/' + rep['primary'])
        except Exception as exc:  # noqa: BLE001
            ins = {'errors': [f'{im.name} 不是可读取的图像：{exc}'], 'warnings': []}
        rep.update(subtype='image', kind='map')
    else:
        names = ', '.join(f['path'] for f in rep['files'][:6])
        ins = {'errors': [f'没有识别出地图或模型文件（{names}）。支持：ROS 地图 YAML+PGM/PNG、GeoJSON、图片、GLB/glTF、URDF、SDF、STL/OBJ/DAE，或它们的 zip'], 'warnings': []}
    rep['errors'] += ins.pop('errors', [])
    rep['warnings'] += ins.pop('warnings', [])
    rep['inspection'] = ins
    return rep


def stage_upload(items: list[tuple[str, bytes]], *, kind_hint: str = '', assume_metres: bool = False) -> dict:
    token, d = store.new_staging()
    try:
        warnings = safety.stage(items, d / 'files')
        rep = inspect_dir(d, kind_hint=kind_hint, assume_metres=assume_metres, warnings=warnings)
    except safety.ImportRejected:
        shutil.rmtree(d, ignore_errors=True)
        raise
    rep['token'] = token
    _write_json(d / 'report.json', rep)
    return rep


def restage(token: str, *, kind_hint: str = '', assume_metres: bool = False) -> dict:
    d = store.staging(token)
    shutil.rmtree(d / 'derived', ignore_errors=True)
    (d / 'derived').mkdir()
    rep = inspect_dir(d, kind_hint=kind_hint, assume_metres=assume_metres)
    rep['token'] = token
    _write_json(d / 'report.json', rep)
    return rep


# ----------------------------------------------------------------- commit --
def _initial_meta(rep: dict, kind: str, body: dict) -> dict:
    ins, primary = rep['inspection'], 'files/' + (rep['primary'] or '')
    meta: dict = {'inspection': ins, 'primary': primary}
    st = rep['subtype']
    if st == 'ros_grid':
        meta.update(frame=ins.get('frame'), stats=ins.get('stats'), occupancy='derived/occupancy.npy', image='files/' + ins.get('image_rel', ''),
                    confirmed={'frame': False}, planning={'passable': 'free 栅格；未知区默认按障碍处理'})
    elif st == 'vector':
        meta.update(transform=ins.get('transform'), crs=ins.get('crs'), summary=ins.get('summary'), features='derived/features_local.json',
                    confirmed={'frame': False})
    elif st == 'image':
        meta.update(image=primary, size_px=[ins['image']['width_px'], ins['image']['height_px']] if ins.get('image') else None,
                    calibration=None, confirmed={'calibration': False}, planning={'passable': False})
    elif st == 'gazebo_world':
        meta.update(world=ins)
    elif st in ('gltf', 'mesh', 'urdf', 'sdf_model'):
        size = None
        b = ins.get('bounds')
        if b and st == 'gltf':
            size = models.gltf_size_robot(b)
        elif b:
            size = {'length_m': b['size'][0], 'width_m': b['size'][1], 'height_m': b['size'][2]}
        meta['display'] = {'model': primary if st in ('gltf', 'mesh') else None, 'urdf': primary if st == 'urdf' else None,
                           'sdf': primary if st == 'sdf_model' else None, 'forward': '+z' if st == 'gltf' else '+x',
                           'up': '+y' if st == 'gltf' else '+z', 'scale': 1.0, 'size_m': size, 'confirmed': False}
        if kind == 'vehicle':
            fp = {'type': 'rect', 'length_m': size['length_m'], 'width_m': size['width_m']} if size else None
            meta['planning'] = {'footprint': fp, 'capacity': None, 'speed_mps': None,
                                'kinematics': {'differential': 'differential', 'skid_steer': 'skid_steer', 'tracked': 'tracked',
                                               'ackermann': 'ackermann', 'omni': 'omni'}.get(ins.get('drive') or '', None),
                                'min_turn_radius_m': None, 'work_width_m': None, 'footprint_source': '模型包围盒（待确认）' if fp else None}
            meta['simulation'] = {'adapter': None}
        else:
            meta['planning'] = {'footprint': None, 'obstacle_kind': body.get('obstacle_kind') or 'building', 'height_m': size['height_m'] if size else None}
    return meta


def commit(token: str, body: dict) -> dict:
    d = store.staging(token)
    rep = json.loads((d / 'report.json').read_text())
    if rep['errors']:
        raise ValueError('导入检查还有错误，不能保存：' + '；'.join(rep['errors'][:3]))
    kind = body.get('kind') or rep['kind']
    st = rep['subtype']
    if st in MAP_SUBTYPES and kind != 'map':
        raise ValueError('这是地图类文件，只能保存为地图资产')
    if st in MODEL_SUBTYPES and kind not in ('vehicle', 'facility'):
        raise ValueError('模型文件只能保存为车辆或障碍物 / 设施资产')
    name = str(body.get('name') or Path(rep['primary'] or 'asset').stem)[:120]
    meta = _initial_meta(rep, kind, body)
    source = {'type': 'upload', 'uri': str(body.get('source_uri') or '')[:400], 'license': str(body.get('license') or '未声明')[:80],
              'attribution': str(body.get('attribution') or '')[:300], 'authors': [str(a)[:80] for a in body.get('authors') or []][:10],
              'retrieved_utc': store.utc_now(), 'original_files': [f['path'] for f in rep['files']][:200]}
    if rep['subtype'] == 'gltf' and (rep['inspection'] or {}).get('copyright') and not body.get('attribution'):
        source['attribution'] = rep['inspection']['copyright']
    preview = rep.get('preview')
    validation = {'errors': [], 'warnings': rep['warnings']}
    m = store.create(kind, name, d, subtype=st, source=source, meta=meta, validation=validation, preview=preview,
                     category=str(body.get('category') or ''), tags=body.get('tags') or [])
    if rep.get('thumb'):
        m['thumb'] = rep['thumb']
        store.save(m)
    return m


# ----------------------------------------------------------- local import --
def import_local(body: dict) -> dict:
    root_key, rel, kind = body.get('root'), str(body.get('path') or ''), body.get('kind') or ''
    base = local.root_path(root_key).resolve()
    src = (base / rel).resolve()
    if base not in src.parents and src != base:
        raise PermissionError(rel)
    token, d = store.new_staging()
    files = d / 'files'
    source = {'type': 'local', 'uri': f'{root_key}:{rel}', 'license': str(body.get('license') or '未声明'), 'attribution': str(body.get('attribution') or ''),
              'retrieved_utc': store.utc_now()}
    try:
        if src.is_dir():                                        # a Fuel model folder
            size = sum(p.stat().st_size for p in src.rglob('*') if p.is_file())
            if size > COPY_LIMIT:
                raise ValueError(f'模型目录 {size >> 20} MB，超过复制上限 {COPY_LIMIT >> 20} MB；请在 Gazebo 世界里直接引用')
            shutil.copytree(src, files / src.parent.name, dirs_exist_ok=True)
            source.update(uri=body.get('source_uri') or source['uri'], license=str(body.get('license') or '见 Gazebo Fuel 页面'),
                          attribution=str(body.get('attribution') or 'Gazebo Fuel'))
        elif src.suffix == '.xacro' or src.name.endswith('.urdf.xacro'):
            if root_key != 'ros' or not src.parts[len(base.parts)].endswith('_description'):
                raise PermissionError('只展开已安装 ROS 描述包里的 Xacro')
            run = subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && exec xacro {shlex.quote(str(src))}'],
                                 capture_output=True, text=True, timeout=60)
            if run.returncode or '<robot' not in run.stdout:
                raise ValueError((run.stderr or 'xacro 没有输出').strip()[-300:])
            (files / (src.name.replace('.urdf.xacro', '').replace('.xacro', '') + '.urdf')).write_text(run.stdout)
            source.update(license=str(body.get('license') or _package_license(src, base)), attribution=str(body.get('attribution') or src.parts[len(base.parts)]))
        elif src.is_file():
            shutil.copy2(src, files / src.name)
        else:
            raise FileNotFoundError(rel)
        rep = inspect_dir(d, kind_hint=kind)
        rep['token'] = token
        _write_json(d / 'report.json', rep)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    m = commit(token, {**body, 'kind': kind or rep['kind'], 'name': body.get('name') or Path(rel).stem})
    ver = store.version(m)
    ver['source'] = {**ver['source'], **source}
    return store.save(m)


def _package_license(src: Path, base: Path) -> str:
    pkg = base / src.parts[len(base.parts)] / 'package.xml'
    try:
        from defusedxml import ElementTree as ET
        return (ET.fromstring(pkg.read_bytes()).findtext('license') or '未声明').strip()
    except Exception:  # noqa: BLE001
        return '未声明'


# ------------------------------------------------------------- built-ins --
def ensure_builtins() -> None:
    """Built-in vehicles are defined in code; a changed definition becomes a new locked version (old ones stay for references)."""
    for aid, spec in profiles.BUILTIN_VEHICLES.items():
        digest = 'builtin:' + hashlib.sha256(json.dumps({'meta': spec['meta'], 'source': spec['source']}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        now = store.utc_now()
        ver = lambda v: {'v': v, 'created_utc': now, 'sha256': digest, 'files': [], 'source': {**spec['source'], 'retrieved_utc': now},  # noqa: E731
                         'meta': copy.deepcopy(spec['meta']), 'validation': {'errors': [], 'warnings': []}, 'preview': None,
                         'locked': True, 'locked_utc': now, 'based_on': None}
        if (store.asset_dir(aid) / 'asset.json').is_file():
            m = store.load(aid)
            if any(x['sha256'] == digest for x in m['versions']):
                continue
            nv = max(x['v'] for x in m['versions']) + 1
            (store.asset_dir(aid) / f'v{nv}' / 'files').mkdir(parents=True, exist_ok=True)
            m['versions'].append({**ver(nv), 'based_on': m['current']})
            m.update(current=nv, name=spec['name'])
            store.save(m)
            continue
        (store.asset_dir(aid) / 'v1' / 'files').mkdir(parents=True, exist_ok=True)
        store.save({'id': aid, 'kind': 'vehicle', 'subtype': spec['subtype'], 'name': spec['name'], 'category': spec['category'], 'tags': ['内置'],
                    'notes': '', 'archived': False, 'created_utc': now, 'updated_utc': now, 'current': 1, 'builtin': True, 'versions': [ver(1)]})


# ------------------------------------------------------------ references --
def _refs_of(obj: dict) -> list[dict]:
    refs = list(obj.get('assets') or [])
    inst = obj.get('instance') if isinstance(obj.get('instance'), dict) else obj
    refs += list((inst or {}).get('asset_refs') or [])
    return [r for r in refs if isinstance(r, dict) and r.get('id')]


def references(aid: str, lab_store=None, limit_runs: int = 3000) -> dict:
    scenarios, runs = [], []
    for p in sorted(paths.USER_SCENARIOS.glob('*.json')):
        try:
            sc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for r in _refs_of(sc):
            if r['id'] == aid:
                scenarios.append({'id': sc.get('id'), 'title': sc.get('title'), 'version': r.get('version'), 'role': r.get('role')})
                break
    if lab_store is not None:
        for run in lab_store.list_runs(limit=limit_runs):
            for r in ((run.get('instance') or {}).get('asset_refs') or []):
                if isinstance(r, dict) and r.get('id') == aid:
                    runs.append({'run_id': run['run_id'], 'scenario_id': run.get('scenario_id'), 'version': r.get('version'),
                                 'algorithm': (run.get('algorithm') or {}).get('name'), 'created_utc': run.get('created_utc')})
                    break
    for m in store.list_all(include_archived=True):
        if m['kind'] == 'scenario' and m['id'] != aid:
            snap = (store.version(m).get('meta') or {}).get('scenario') or {}
            if any(r['id'] == aid for r in _refs_of(snap)):
                scenarios.append({'id': m['id'], 'title': m['name'], 'version': None, 'role': 'template'})
    by_version: dict[str, int] = {}
    for r in scenarios + runs:
        key = str(r.get('version'))
        by_version[key] = by_version.get(key, 0) + 1
    return {'scenarios': scenarios, 'runs': runs, 'by_version': by_version, 'total': len(scenarios) + len(runs)}


# ------------------------------------------------------ planning geometry --
def _poly_list(geom) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, 'geoms', []) if isinstance(g, Polygon)]


def _ring(poly: Polygon) -> list[list[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in list(poly.exterior.coords)[:-1]]


def _rows_deg(poly: Polygon, props: dict) -> float:
    for k in ('rows_deg', 'row_direction', 'row_deg', 'orientation', '行向'):
        if k in props:
            try:
                return float(props[k]) % 180
            except (TypeError, ValueError):
                pass
    rect = poly.minimum_rotated_rectangle
    pts = list(rect.exterior.coords)
    best = max(zip(pts, pts[1:]), key=lambda e: math.dist(*e))
    return round(math.degrees(math.atan2(best[1][1] - best[0][1], best[1][0] - best[0][0])) % 180, 1)


def planning_geometry(m: dict, ver: dict, body: dict) -> dict:
    """Suggested planning geometry for a task map built on this map asset; the user edits it before saving."""
    meta, st = ver['meta'], m['subtype']
    vd = store.vdir(m['id'], ver['v'])
    notes, layers = [], []
    if st == 'ros_grid':
        occ = np.load(vd / meta['occupancy'])
        vec = rosmap.vectorize(occ, meta['frame'], unknown_as=body.get('unknown_as', 'obstacle'),
                               min_area_m2=float(body.get('min_area_m2', 0.05)), simplify_m=body.get('simplify_m'))
        obstacles = [{'id': f'M{i + 1}', 'kind': 'other', 'name': '地图占据区', 'polygon': o['polygon'], 'source': 'map'} for i, o in enumerate(vec['obstacles'])]
        notes.append(f"由占据栅格提取：可通行区域 {vec['stats']['free_area_m2']} m²，障碍 {len(obstacles)} 个，顶点 {vec['stats']['vertices']}，简化容差 {vec['stats']['simplify_m']} m")
        if vec['stats']['dropped_free_regions']:
            notes.append(f"另有 {vec['stats']['dropped_free_regions']} 块与主区域不连通的空闲区未纳入")
        return {'boundary': vec['boundary'], 'plots': [], 'obstacles': obstacles, 'tasks': [], 'depot': None, 'notes': notes,
                'layers': layers, 'stats': vec['stats']}
    if st == 'vector':
        doc = json.loads((vd / meta['features']).read_text())
        bounds, plots, obstacles, tasks, depot, extra = [], [], [], [], None, []
        for f in doc['features']:
            props = f.get('properties') or {}
            role, okind = props.get('role'), props.get('obstacle_kind')
            try:
                g = shape(f['geometry'])
            except Exception:  # noqa: BLE001
                notes.append(f"要素 {f.get('id')} 几何无效，已跳过")
                continue
            if role == 'boundary':
                bounds += _poly_list(g)
            elif role == 'plot':
                for p in _poly_list(g):
                    if p.interiors:
                        notes.append(f"地块 {props.get('name') or f.get('id')} 有 {len(p.interiors)} 个孔洞：任务地图的地块不支持孔洞，已按外轮廓处理（孔洞保留在底图图层）")
                    plots.append({'id': str(props.get('id') or f'P{len(plots) + 1}'), 'name': str(props.get('name') or f'地块 {len(plots) + 1}'),
                                  'crop': str(props.get('crop') or props.get('作物') or ''), 'rows_deg': _rows_deg(p, props), 'polygon': _ring(p)})
            elif role in ('obstacle', 'nogo'):
                geoms = _poly_list(g) if g.geom_type.endswith('Polygon') else [g.buffer(float(props.get('radius') or 0.6))]
                for p in geoms:
                    if p.interiors:
                        notes.append(f"障碍 {props.get('name') or f.get('id')} 的孔洞按实心处理（偏保守）")
                    # kinds the task-map editor draws; others (e.g. pole) keep their meaning in the name
                    kind = okind if role == 'obstacle' and okind in EDITOR_OBSTACLE_KINDS else 'other'
                    label = {'pole': '电杆'}.get(okind or '', '禁行区' if role == 'nogo' else '障碍')
                    obstacles.append({'id': f'O{len(obstacles) + 1}', 'kind': kind, 'nogo': role == 'nogo',
                                      'name': str(props.get('name') or label), 'polygon': _ring(Polygon(p.exterior))})
            elif role == 'task' and g.geom_type in ('Point', 'MultiPoint'):
                for pt in (g.geoms if g.geom_type == 'MultiPoint' else [g]):
                    tasks.append({'id': len(tasks) + 1, 'xy': [round(pt.x, 3), round(pt.y, 3)], 'demand': int(props.get('demand') or props.get('需求') or 1),
                                  'label': str(props.get('label') or props.get('name') or f'T{len(tasks) + 1}'), 'service_s': float(props.get('service_s') or 0)})
            elif role == 'depot' and g.geom_type == 'Point' and depot is None:
                depot = [round(g.x, 3), round(g.y, 3)]
            else:
                extra.append(role or 'unknown')
        if bounds:
            outer = unary_union(bounds)
            main = max(_poly_list(outer), key=lambda p: p.area)
            for hole in main.interiors:
                obstacles.append({'id': f'O{len(obstacles) + 1}', 'kind': 'other', 'name': '边界内孔洞', 'polygon': _ring(Polygon(hole))})
            boundary = _ring(Polygon(main.exterior))
            if len(_poly_list(outer)) > 1:
                notes.append('有多个互不相连的边界，只用面积最大的一块')
        else:
            allg = unary_union([shape(f['geometry']) for f in doc['features']])
            boundary = _ring(allg.envelope.buffer(3, join_style=2))
            notes.append('没有边界要素：用全部要素的外接矩形外扩 3 m 作为场地边界，请检查')
        counts: dict[str, int] = {}
        for r in extra:
            counts[r] = counts.get(r, 0) + 1
        names = {'road': '道路', 'headland': '地头', 'row': '作物行', 'unload': '卸货 / 补给点', 'unknown': '未识别'}
        for r, n in counts.items():
            notes.append(f"{names.get(r, r)} {n} 个：作为底图图层保留，规划暂不使用" + ('（后端只支持一个车库）' if r == 'unload' else ''))
        return {'boundary': boundary, 'plots': plots, 'obstacles': obstacles, 'tasks': tasks, 'depot': depot, 'notes': notes, 'layers': layers}
    if st == 'image':
        return {'boundary': None, 'plots': [], 'obstacles': [], 'tasks': [], 'depot': None, 'layers': layers,
                'notes': ['图片不提供规划几何：在标定后的底图上画出场地边界、地块与障碍']}
    raise ValueError('这类地图不能直接生成规划几何')
