"""/lab/api/assets — asset library endpoints (mounted by the gateway)."""
from __future__ import annotations

import copy
import json
import math
import mimetypes
import re
from typing import Any

from fastapi import APIRouter, Body, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ..services import ServiceError
from . import imagecal, importer, local, profiles, rosmap, safety, store

router = APIRouter()
MEDIA = {'.pgm': 'image/x-portable-graymap', '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.dae': 'model/vnd.collada+xml',
         '.stl': 'model/stl', '.obj': 'model/obj', '.mtl': 'text/plain', '.yaml': 'text/yaml', '.yml': 'text/yaml', '.npy': 'application/octet-stream',
         '.urdf': 'application/xml', '.sdf': 'application/xml', '.config': 'application/xml', '.geojson': 'application/geo+json', '.bin': 'application/octet-stream'}


def _err(status: int, code: str, message: str) -> ServiceError:
    return ServiceError(status, code, message)


def _body(payload: Any) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise _err(400, 'invalid_body', '请求体应为 JSON 对象')
    return payload


def _manifest(aid: str) -> dict:
    try:
        return store.load(aid)
    except KeyError:
        raise _err(404, 'unknown_asset', f'没有资产 {aid}') from None


def _ver(m: dict, v: Any) -> dict:
    try:
        return store.version(m, int(v))
    except (KeyError, ValueError, TypeError):
        raise _err(404, 'unknown_version', f"{m['id']} 没有版本 {v}") from None


def _media(path) -> str:
    return MEDIA.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'


def _url(aid: str, v: int, rel: str | None) -> str | None:
    return f'/lab/api/assets/{aid}/v/{v}/file/{rel}' if rel else None


def _confirmed(m: dict, ver: dict) -> bool | None:
    """Whether the user has confirmed what this version means (map frame / image calibration / model orientation)."""
    meta = ver.get('meta') or {}
    conf = meta.get('confirmed') or {}
    if m['kind'] == 'map':
        return bool(conf.get('calibration')) if m['subtype'] == 'image' else bool(conf.get('frame')) if m['subtype'] in ('ros_grid', 'vector') else None
    if m['kind'] in ('vehicle', 'facility'):
        return bool(m.get('builtin') or (meta.get('display') or {}).get('confirmed'))
    return None


def _summary(m: dict) -> dict:
    ver = store.version(m)
    caps = profiles.capabilities(m, ver)
    return {'confirmed': _confirmed(m, ver),'id': m['id'], 'kind': m['kind'], 'subtype': m['subtype'], 'name': m['name'], 'category': m.get('category', ''),
            'tags': m.get('tags', []), 'archived': m.get('archived', False), 'builtin': m.get('builtin', False), 'current': m['current'],
            'versions': len(m['versions']), 'locked': ver['locked'], 'updated_utc': m['updated_utc'], 'created_utc': m['created_utc'],
            'thumb': _url(m['id'], ver['v'], m.get('thumb') or (ver.get('preview') if str(ver.get('preview') or '').endswith('.png') else None)),
            'license': (ver.get('source') or {}).get('license'), 'source_type': (ver.get('source') or {}).get('type'),
            'level': caps.get('level'), 'errors': len((ver.get('validation') or {}).get('errors') or []),
            'warnings': len((ver.get('validation') or {}).get('warnings') or [])}


def _detail(m: dict, request: Request | None = None) -> dict:
    out = copy.deepcopy(m)
    for ver in out['versions']:
        ver['capabilities'] = profiles.capabilities(m, ver)
        ver['urls'] = {'preview': _url(m['id'], ver['v'], ver.get('preview')), 'base': f"/lab/api/assets/{m['id']}/v/{ver['v']}/file/"}
    out['summary'] = _summary(m)
    return out


# ------------------------------------------------------------------ list --
@router.get('/assets')
def list_assets(kind: str | None = None, q: str | None = None, archived: bool = False):
    importer.ensure_builtins()
    items = []
    for m in store.list_all(include_archived=archived):
        if kind and m['kind'] != kind:
            continue
        if q and q.lower() not in (m['name'] + ' ' + m.get('category', '') + ' ' + ' '.join(m.get('tags', []))).lower():
            continue
        items.append(_summary(m))
    items.sort(key=lambda s: (not s['builtin'], s['kind'], s['updated_utc']), reverse=False)
    return {'assets': items, 'kinds': store.KINDS}


@router.get('/assets/local/catalog')
def local_catalog():
    return local.catalog()


@router.get('/assets/local/file/{root}/{rel:path}')
def local_file(root: str, rel: str):
    try:
        path = local.local_file(root, rel)
    except (KeyError, FileNotFoundError):
        raise _err(404, 'not_found', f'本机资源中没有 {root}:{rel}') from None
    return FileResponse(path, media_type=_media(path), headers={'Cache-Control': 'max-age=3600'})


@router.post('/assets/local/import', status_code=201)
def local_import(payload: Any = Body(None)):
    body = _body(payload)
    try:
        m = importer.import_local(body)
    except (KeyError, PermissionError):
        raise _err(403, 'forbidden', '只能引用 Gazebo Fuel 缓存、已安装 ROS 包和 Gazebo 示例世界里的资源') from None
    except FileNotFoundError:
        raise _err(404, 'not_found', f"没有本机资源 {body.get('path')}") from None
    except ValueError as exc:
        raise _err(422, 'import_failed', str(exc)) from None
    return _detail(m)


@router.post('/assets/calibrate')
def calibrate(payload: Any = Body(None)):
    body = _body(payload)
    try:
        if body.get('known_distance'):
            k = body['known_distance']
            return imagecal.known_distance(k['p1'], k['p2'], float(k['distance_m']), k.get('origin_px') or k['p1'], float(k.get('rotation_deg') or 0))
        return imagecal.solve(body.get('points') or [], body.get('model') or 'similarity')
    except (KeyError, TypeError, ValueError) as exc:
        raise _err(400, 'calibration_failed', str(exc)) from None


# ----------------------------------------------------------- import flow --
@router.post('/assets/inspect')
async def inspect_upload(files: list[UploadFile] = File(...), paths: str = Form('[]'), kind: str = Form(''), assume_metres: bool = Form(False)):
    try:
        rel = json.loads(paths) if paths else []
    except ValueError:
        rel = []
    items = []
    for i, f in enumerate(files):
        name = rel[i] if i < len(rel) and rel[i] else (f.filename or f'file{i}')
        data = await f.read(safety.MAX_FILE_BYTES + 1)
        items.append((name, data))
    try:
        return importer.stage_upload(items, kind_hint=kind, assume_metres=assume_metres)
    except safety.ImportRejected as exc:
        raise _err(422, 'import_rejected', str(exc)) from None


@router.post('/assets/staging/{token}/reinspect')
def reinspect(token: str, payload: Any = Body(None)):
    body = _body(payload)
    try:
        return importer.restage(token, kind_hint=body.get('kind') or '', assume_metres=bool(body.get('assume_metres')))
    except KeyError:
        raise _err(404, 'unknown_staging', '导入会话已过期，请重新选择文件') from None


@router.get('/assets/staging/{token}/file/{rel:path}')
def staging_file(token: str, rel: str):
    try:
        base = store.staging(token).resolve()
    except KeyError:
        raise _err(404, 'unknown_staging', '导入会话已过期') from None
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file():
        raise _err(404, 'not_found', rel)
    return FileResponse(path, media_type=_media(path), headers={'Cache-Control': 'no-store'})


@router.delete('/assets/staging/{token}', status_code=204)
def drop_staging(token: str):
    import shutil
    try:
        shutil.rmtree(store.staging(token), ignore_errors=True)
    except KeyError:
        pass
    return Response(status_code=204)


@router.post('/assets/commit', status_code=201)
def commit(payload: Any = Body(None)):
    body = _body(payload)
    try:
        m = importer.commit(str(body.get('token') or ''), body)
    except KeyError:
        raise _err(404, 'unknown_staging', '导入会话已过期，请重新选择文件') from None
    except ValueError as exc:
        raise _err(422, 'import_invalid', str(exc)) from None
    return _detail(m)


# ---------------------------------------------------------------- detail --
@router.get('/assets/{aid}')
def get_asset(aid: str):
    importer.ensure_builtins()
    return _detail(_manifest(aid))


@router.patch('/assets/{aid}')
def patch_asset(aid: str, payload: Any = Body(None)):
    _manifest(aid)
    return _detail(store.update(aid, _body(payload)))


@router.get('/assets/{aid}/v/{v}/file/{rel:path}')
def asset_file(aid: str, v: int, rel: str):
    m = _manifest(aid)
    ver = _ver(m, v)
    try:
        path = store.file_path(aid, v, rel)
    except FileNotFoundError:
        raise _err(404, 'not_found', f'{aid} v{v} 没有文件 {rel}') from None
    cache = 'public, max-age=31536000, immutable' if ver['locked'] else 'no-cache'
    return FileResponse(path, media_type=_media(path), headers={'Cache-Control': cache})


AXES = ('+x', '-x', '+y', '-y', '+z', '-z')


def _num(v: Any, what: str, lo: float, hi: float, *, allow_none: bool = True, integer: bool = False):
    if v is None and allow_none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise _err(400, 'invalid_meta', f'{what} 应在 {lo:g}–{hi:g} 之间')
    if integer:
        if int(v) != v:
            raise _err(400, 'invalid_meta', f'{what} 应为整数')
        return int(v)
    return float(v)


def _clean_meta(m: dict, ver: dict, patch: dict) -> dict:
    """Checks the user-editable parts of a version's metadata; derived geometry (affine, bounds) is recomputed here."""
    out = dict(patch)
    if 'frame' in out:
        if m['subtype'] != 'ros_grid':
            raise _err(400, 'invalid_meta', '只有 ROS 栅格地图有分辨率 / 原点')
        f = out['frame'] or {}
        old = ver['meta'].get('frame') or {}
        origin = f.get('origin', old.get('origin'))
        if not isinstance(origin, list) or len(origin) != 3:
            raise _err(400, 'invalid_meta', 'origin 应为 [x, y, yaw]')
        meta = {'resolution': _num(f.get('resolution', old.get('resolution')), '分辨率（m/像素）', 1e-4, 100, allow_none=False),
                'origin': [_num(origin[0], '原点 x', -1e6, 1e6, allow_none=False), _num(origin[1], '原点 y', -1e6, 1e6, allow_none=False),
                           _num(origin[2], '原点 yaw', -math.pi * 2, math.pi * 2, allow_none=False)]}
        out['frame'] = rosmap.frame(meta, int(old['width_px']), int(old['height_px']))
    if 'planning' in out:
        p = out['planning'] or {}
        if not isinstance(p, dict):
            raise _err(400, 'invalid_meta', 'planning 应为对象')
        clean: dict = {}
        if 'footprint' in p:
            fp = p['footprint']
            if fp is None:
                clean['footprint'] = None
            elif not isinstance(fp, dict) or fp.get('type') not in ('rect', 'circle', 'polygon'):
                raise _err(400, 'invalid_meta', '足迹类型应为 rect / circle / polygon')
            elif fp['type'] == 'rect':
                clean['footprint'] = {'type': 'rect', 'length_m': _num(fp.get('length_m'), '足迹长度', 0.01, 60, allow_none=False),
                                      'width_m': _num(fp.get('width_m'), '足迹宽度', 0.01, 60, allow_none=False)}
            elif fp['type'] == 'circle':
                clean['footprint'] = {'type': 'circle', 'radius_m': _num(fp.get('radius_m'), '足迹半径', 0.01, 30, allow_none=False)}
            else:
                pts = fp.get('points')
                if not isinstance(pts, list) or len(pts) < 3 or len(pts) > 64:
                    raise _err(400, 'invalid_meta', '多边形足迹需要 3–64 个点')
                clean['footprint'] = {'type': 'polygon', 'points': [[_num(x, '足迹点', -30, 30, allow_none=False), _num(y, '足迹点', -30, 30, allow_none=False)] for x, y in pts]}
        if 'capacity' in p:
            clean['capacity'] = _num(p['capacity'], '容量', 1, 100000, integer=True)
        if 'payload' in p:
            if p['payload'] not in (None, 'none', 'load'):
                raise _err(400, 'invalid_meta', 'payload 应为 none（不载货）或 load')
            clean['payload'] = p['payload']
        if 'speed_mps' in p:
            clean['speed_mps'] = _num(p['speed_mps'], '车速（m/s）', 0.01, 15)
        if 'kinematics' in p:
            if p['kinematics'] not in (None, *profiles.KINEMATICS):
                raise _err(400, 'invalid_meta', f"运动学类型应为 {' / '.join(profiles.KINEMATICS)}")
            clean['kinematics'] = p['kinematics']
        if 'min_turn_radius_m' in p:
            clean['min_turn_radius_m'] = _num(p['min_turn_radius_m'], '最小转弯半径', 0, 100)
        if 'work_width_m' in p:
            clean['work_width_m'] = _num(p['work_width_m'], '作业幅宽', 0.01, 60)
        if 'height_m' in p:
            clean['height_m'] = _num(p['height_m'], '高度', 0, 200)
        if 'obstacle_kind' in p:
            clean['obstacle_kind'] = str(p['obstacle_kind'])[:40]
        if 'footprint_source' in p:
            clean['footprint_source'] = str(p['footprint_source'] or '')[:120] or None
        out['planning'] = clean
    if 'display' in out:
        d = out['display'] or {}
        if not isinstance(d, dict):
            raise _err(400, 'invalid_meta', 'display 应为对象')
        clean = {}
        for k in ('forward', 'up'):
            if k in d:
                if d[k] not in AXES:
                    raise _err(400, 'invalid_meta', f'{k} 应为 {" / ".join(AXES)}')
                clean[k] = d[k]
        if clean.get('forward') and clean.get('up') and clean['forward'][1] == clean['up'][1]:
            raise _err(400, 'invalid_meta', '前方与向上不能是同一根轴')
        if 'scale' in d:
            clean['scale'] = _num(d['scale'], '缩放', 1e-5, 1e5, allow_none=False)
        if 'size_m' in d:
            sz = d['size_m']
            clean['size_m'] = None if sz is None else {k: _num((sz or {}).get(k), f'尺寸 {k}', 0, 1000, allow_none=False)
                                                      for k in ('length_m', 'width_m', 'height_m')}
        if 'confirmed' in d:
            clean['confirmed'] = bool(d['confirmed'])
        out['display'] = clean
    if 'simulation' in out:
        sim = out['simulation'] or {}
        out['simulation'] = {'adapter': sim.get('adapter') or None} if isinstance(sim, dict) else {}
    if 'confirmed' in out:
        out['confirmed'] = {k: bool(v) for k, v in (out['confirmed'] or {}).items() if k in ('frame', 'calibration')}
    if 'calibration' in out and out['calibration'] is not None:
        c = out['calibration']
        if not isinstance(c, dict) or not isinstance(c.get('matrix'), list) or len(c['matrix']) != 2:
            raise _err(400, 'invalid_meta', '标定结果应包含 2×3 矩阵')
        mat = [[_num(x, '标定矩阵', -1e9, 1e9, allow_none=False) for x in row] for row in c['matrix']]
        if any(len(r) != 3 for r in mat) or abs(mat[0][0] * mat[1][1] - mat[0][1] * mat[1][0]) < 1e-15:
            raise _err(400, 'invalid_meta', '标定矩阵不可逆')
        out['calibration'] = {**c, 'matrix': mat}
    return out


def _source_patch(src: Any) -> dict:
    """Provenance fields a user may correct (license, attribution, origin URI, authors); everything else is recorded by the import."""
    if src in (None, {}):
        return {}
    if not isinstance(src, dict):
        raise _err(400, 'invalid_source', 'source 应为对象')
    out = {}
    for k in ('license', 'attribution', 'uri'):
        if k in src:
            if not isinstance(src[k], str) or len(src[k]) > 600:
                raise _err(400, 'invalid_source', f'source.{k} 应为不超过 600 字的文本')
            out[k] = src[k].strip()
    if 'authors' in src:
        if not isinstance(src['authors'], list) or not all(isinstance(a, str) for a in src['authors']):
            raise _err(400, 'invalid_source', 'source.authors 应为文本列表')
        out['authors'] = [a.strip()[:120] for a in src['authors'] if a.strip()][:20]
    return out


@router.post('/assets/{aid}/v/{v}/meta')
def patch_meta(aid: str, v: int, payload: Any = Body(None)):
    m = _manifest(aid)
    ver = _ver(m, v)
    if m.get('builtin'):
        raise _err(403, 'builtin', '内置资产的参数是固定的（仿真车体与之对应）；车辆数、容量与车速在任务地图的车队设置里调整')
    body = _body(payload)
    patch = body.get('meta') or {}
    if not isinstance(patch, dict):
        raise _err(400, 'invalid_meta', 'meta 应为对象')
    source = _source_patch(body.get('source'))
    patch = _clean_meta(m, ver, patch)
    for k in ('inspection', 'primary', 'occupancy', 'features'):
        patch.pop(k, None)                         # derived from the files, not editable
    sim = patch.get('simulation')
    if isinstance(sim, dict) and 'adapter' in sim:
        why = profiles.check_adapter(m, ver, sim.get('adapter'))
        if why:
            raise _err(422, 'adapter_incompatible', why)
    if ver['locked']:                              # editing a locked version makes the next one
        return _detail(store.fork(aid, v, patch, source={**ver['source'], **source} if source else None))
    try:
        return _detail(store.update_version_meta(aid, v, patch, source=source))
    except PermissionError as exc:
        raise _err(409, 'locked', str(exc)) from None


@router.post('/assets/{aid}/v/{v}/lock')
def lock_version(aid: str, v: int):
    m = _manifest(aid)
    ver = _ver(m, v)
    meta, st = ver['meta'], m['subtype']
    missing = []
    if st in ('ros_grid', 'vector') and not (meta.get('confirmed') or {}).get('frame'):
        missing.append('坐标系 / 原点 / 单位尚未确认')
    if st == 'image' and not meta.get('calibration'):
        missing.append('图片尚未标定')
    if m['kind'] in ('vehicle', 'facility') and st != 'builtin' and not (meta.get('display') or {}).get('confirmed'):
        missing.append('模型朝向与缩放尚未确认')
    if (ver.get('validation') or {}).get('errors'):
        missing.append('导入检查仍有错误')
    if missing:
        raise _err(422, 'not_confirmed', '；'.join(missing))
    return _detail(store.lock(aid, v))


@router.post('/assets/{aid}/v/{v}/fork', status_code=201)
def fork_version(aid: str, v: int, payload: Any = Body(None)):
    m = _manifest(aid)
    _ver(m, v)
    if m.get('builtin'):
        raise _err(403, 'builtin', '内置资产不能派生版本')
    return _detail(store.fork(aid, v, (_body(payload).get('meta') or {})))


@router.post('/assets/{aid}/current')
def set_current(aid: str, payload: Any = Body(None)):
    m = _manifest(aid)
    v = _body(payload).get('version')
    _ver(m, v)
    return _detail(store.set_current(aid, int(v)))


@router.post('/assets/{aid}/replace', status_code=201)
async def replace(aid: str, files: list[UploadFile] = File(...), paths: str = Form('[]')):
    """New version from new files (same asset identity, references keep their pinned version)."""
    m = _manifest(aid)
    if m.get('builtin'):
        raise _err(403, 'builtin', '内置资产不能替换文件')
    try:
        rel = json.loads(paths) if paths else []
    except ValueError:
        rel = []
    items = [((rel[i] if i < len(rel) and rel[i] else f.filename) or f'file{i}', await f.read(safety.MAX_FILE_BYTES + 1)) for i, f in enumerate(files)]
    try:
        rep = importer.stage_upload(items, kind_hint=m['kind'])
    except safety.ImportRejected as exc:
        raise _err(422, 'import_rejected', str(exc)) from None
    if rep['errors']:
        raise _err(422, 'import_invalid', '新文件检查有错误：' + '；'.join(rep['errors'][:3]))
    if rep['subtype'] != m['subtype']:
        raise _err(422, 'subtype_mismatch', f"新文件是 {rep['subtype']}，与资产类型 {m['subtype']} 不同；请作为新资产导入")
    staged = store.staging(rep['token'])
    meta = importer._initial_meta(rep, m['kind'], {})
    base = store.version(m)
    for key in ('planning', 'simulation'):
        if key in base['meta'] and key in meta:
            meta[key] = base['meta'][key]
    out = store.fork(aid, m['current'], staged=staged, meta=meta, validation={'errors': [], 'warnings': rep['warnings']},
                     source={**base['source'], 'type': 'upload', 'retrieved_utc': store.utc_now(), 'original_files': [f['path'] for f in rep['files']][:200]},
                     preview=rep.get('preview'))
    return _detail(out)


@router.get('/assets/{aid}/references')
def asset_references(aid: str, request: Request):
    _manifest(aid)
    lab = getattr(request.app.state, 'lab', None)
    return importer.references(aid, getattr(lab, 'store', None))


@router.delete('/assets/{aid}')
def delete_asset(aid: str, request: Request):
    m = _manifest(aid)
    if m.get('builtin'):
        raise _err(403, 'builtin', '内置资产不能删除')
    lab = getattr(request.app.state, 'lab', None)
    refs = importer.references(aid, getattr(lab, 'store', None))
    if refs['total']:
        raise _err(409, 'referenced', f"有 {len(refs['scenarios'])} 个场景、{len(refs['runs'])} 条运行记录引用它：可以归档隐藏，但不能删除")
    store.delete(aid)
    return {'deleted': aid}


# ------------------------------------------------------------- map tools --
@router.post('/assets/{aid}/v/{v}/planning-geometry')
def planning_geometry(aid: str, v: int, payload: Any = Body(None)):
    m = _manifest(aid)
    ver = _ver(m, v)
    if m['kind'] != 'map':
        raise _err(400, 'not_a_map', '只有地图资产能生成规划几何')
    try:
        return importer.planning_geometry(m, ver, _body(payload))
    except (ValueError, KeyError, OSError) as exc:
        raise _err(422, 'geometry_failed', str(exc)) from None


# ---------------------------------------------------------- scenarios --
SCENARIO_ID = re.compile(r'^[a-z0-9][a-z0-9._-]{1,63}$')


@router.post('/assets/scenario-template', status_code=201)
def save_template(payload: Any = Body(None)):
    """A reusable copy of a saved task map (geometry, tasks, vehicles, asset bindings)."""
    from ..scenarios import get_scenario
    body = _body(payload)
    try:
        sc = get_scenario(str(body.get('scenario_id') or ''))
    except KeyError:
        raise _err(404, 'unknown_scenario', f"没有场景 {body.get('scenario_id')}") from None
    token, d = store.new_staging()
    snap = copy.deepcopy(sc)
    (d / 'files' / 'scenario.json').write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    refs = importer._refs_of(snap)
    m = store.create('scenario', str(body.get('name') or sc.get('title') or sc['id']), d, subtype='task_map',
                     source={'type': 'derived', 'uri': f"scenario:{sc['id']}", 'license': 'Project', 'retrieved_utc': store.utc_now()},
                     meta={'scenario': snap, 'problem_class': sc.get('problem_class'), 'asset_refs': refs,
                           'summary': sc.get('summary'), 'from_scenario': sc['id']},
                     validation={'errors': [], 'warnings': []}, category=str(body.get('category') or ''), tags=body.get('tags') or [])
    return _detail(store.lock(m['id'], 1))


@router.post('/assets/{aid}/instantiate', status_code=201)
def instantiate(aid: str, payload: Any = Body(None)):
    from datetime import datetime, timezone
    from ..scenarios import save_user_scenario
    m = _manifest(aid)
    if m['kind'] != 'scenario':
        raise _err(400, 'not_a_template', '只有场景资产能实例化')
    body = _body(payload)
    ver = _ver(m, body.get('version') or m['current'])
    snap = copy.deepcopy(ver['meta']['scenario'])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    snap['id'] = f"{str(snap.get('id') or 'task')[:40].rstrip('-')}-{stamp}".lower()
    if not SCENARIO_ID.match(snap['id']):
        snap['id'] = f'task-{stamp}'
    snap['title'] = str(body.get('title') or f"{snap.get('title') or m['name']}（副本）")[:120]
    snap.setdefault('provenance', {})
    snap['provenance'] = {**snap['provenance'], 'template': {'id': aid, 'version': ver['v'], 'sha256': ver['sha256']}}
    try:
        return save_user_scenario(snap, overwrite=False)
    except ValueError as exc:
        raise _err(422, 'invalid_scenario', str(exc)) from None
