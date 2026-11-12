"""Server-side pinned asset identities; UI declarations never grant execution capability."""
from __future__ import annotations

import copy
import hashlib
import json
import math

from ..services import ServiceError
from . import importer, profiles, store
from .. import field_physics

FIELD_VEHICLE = 'vehicle-field-diffdrive-builtin'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identity(ref):
    if (not isinstance(ref, dict) or not isinstance(ref.get('id'), str)
            or type(ref.get('version')) is not int or ref['version'] < 1
            or not isinstance(ref.get('sha256'), str) or not ref['sha256']):
        raise ServiceError(409, 'invalid_asset_reference', '资产引用必须包含 id、正整数 version 和 sha256')
    return {k: ref[k] for k in ('id', 'version', 'sha256')}


def resolve(ref, verify_files=False):
    ref = identity(ref)
    try:
        m = store.load(ref['id'])
        v = store.version(m, ref['version'])
    except KeyError:
        raise ServiceError(409, 'asset_version_missing', f"资产版本不存在：{ref['id']} v{ref['version']}") from None
    if not v['locked'] or v['sha256'] != ref['sha256']:
        raise ServiceError(409, 'asset_version_mismatch', '资产未锁定或版本哈希与方案不一致')
    if verify_files:
        for f in v.get('files', []):
            try:
                path = store.file_path(m['id'], v['v'], f['path'])
                valid = store.sha256_file(path) == f['sha256']
            except (OSError, ValueError):
                valid = False
            if not valid:
                raise ServiceError(409, 'asset_file_changed', f"资产文件缺失或已改变：{f['path']}")
    data = {'reference': ref, 'kind': m['kind'], 'name': m['name'],
            'version': copy.deepcopy(v)}
    data['metadata_sha256'] = digest({'reference': ref, 'meta': v['meta'], 'source': v.get('source'),
                                      'files': v.get('files'), 'validation': v.get('validation')})
    return data


def snapshot(instance, *, problem_class=None):
    refs, fleet = references(instance, problem_class=problem_class)
    refs = refs + ([fleet['asset']] if 'asset' in fleet else [])
    unique = {}
    for ref in refs:
        key = identity(ref)
        index = (key['id'], key['version'])
        if index in unique and unique[index]['reference'] != key:
            raise ServiceError(409, 'asset_version_mismatch', '同一资产版本被引用了不同哈希')
        if index not in unique:
            unique[index] = resolve(key)
    return list(unique.values())


def references(instance, *, problem_class=None):
    refs = instance.get('asset_refs', [])
    if not isinstance(refs, list):
        raise ServiceError(409, 'invalid_asset_reference', 'asset_refs 必须是数组')
    fleet = instance.get('fleet', {})
    # CVRP uses this legacy field for the abstract fleet model, not an asset.
    # Require its problem context so a field execution binding stays strict.
    if problem_class == 'euclidean_cvrp' and fleet == 'unlimited_identical_vehicles':
        fleet = {}
    if not isinstance(fleet, dict):
        raise ServiceError(409, 'invalid_asset_reference', 'fleet 必须是资产绑定对象')
    veh = instance.get('vehicles')
    if isinstance(veh, dict) and isinstance(veh.get('fleet'), list):
        # heterogeneous fleet classes carry their own pinned vehicle references
        refs = list(refs) + [{'role': 'vehicle', **identity(c['asset'])}
                             for c in veh['fleet'] if isinstance(c, dict) and c.get('asset') is not None]
    for ref in refs:
        identity(ref)
    if 'asset' in fleet:
        identity(fleet['asset'])
    return refs, fleet


def field_vehicle(run, requested=None):
    """Resolve actual supported body; imported/display-only assets cannot borrow this adapter."""
    inst = run['instance']
    veh = inst.get('vehicles')
    if isinstance(veh, dict) and isinstance(veh.get('fleet'), list) and len(veh['fleet']) > 1:
        raise ServiceError(422, 'heterogeneous_fleet_execution',
                           '田间仿真当前生成同构内置差速机器人；异构车队方案支持求解与独立验算，暂不能进入执行')
    if isinstance(veh, dict) and isinstance(veh.get('fleet'), list):
        curved = [c for c in veh['fleet'] if isinstance(c, dict) and float(c.get('min_turn_radius_m') or 0) > 0]
        if curved:
            raise ServiceError(422, 'curvature_constrained_execution',
                               f"田间执行适配是可原地转向的差速模型；类别 {curved[0].get('name')} 的最小转弯半径 "
                               f"{curved[0].get('min_turn_radius_m')} m 的曲率约束路径支持求解与独立验算，暂不能进入执行")
    refs, fleet = references(inst)
    refs = [r for r in refs if r.get('role') == 'vehicle']
    bound = fleet.get('asset') or (refs[0] if refs else None)
    if bound is not None:
        ref = identity(bound)
        if any(identity(r) != ref for r in refs) or (requested is not None and identity(requested) != ref):
            raise ServiceError(409, 'vehicle_asset_mismatch', '请求车型与原方案绑定的资产版本不一致')
    else:
        importer.ensure_builtins()
        m = store.load(FIELD_VEHICLE)
        v = store.version(m)
        ref = {'id': m['id'], 'version': v['v'], 'sha256': v['sha256']}
        if requested is not None and identity(requested) != ref:
            raise ServiceError(409, 'vehicle_asset_mismatch', '无车型绑定的历史方案只使用明确记录的内置田间机器人')
    asset = resolve(ref, verify_files=True)
    if asset['kind'] != 'vehicle' or ref['id'] != FIELD_VEHICLE:
        raise ServiceError(422, 'unsupported_vehicle_asset', '此车型没有田间执行适配，不能用内置机器人替代；导入外观或 URDF 不代表可执行')
    meta = asset['version']['meta']
    p = meta.get('planning') or {}
    if ((meta.get('simulation') or {}).get('adapter') != 'fieldsim-diffdrive'
            or p.get('kinematics') != 'differential'
            or (meta.get('simulation') or {}).get('model') != field_physics.MODEL
            or p.get('footprint') != profiles.BUILTIN_VEHICLES[FIELD_VEHICLE]['meta']['planning']['footprint']):
        raise ServiceError(409, 'vehicle_model_mismatch', '资产的足迹或运动学与实际生成的差速模型不一致')
    for saved in run.get('asset_snapshot') or []:
        if saved['reference'] == ref and saved['metadata_sha256'] != asset['metadata_sha256']:
            raise ServiceError(409, 'asset_metadata_changed', '已锁定的车型参数与求解时快照不同')
    if bound is not None:
        values = inst['vehicles']
        if (values['capacity'] != p.get('capacity') or values['speed_mps'] != p.get('speed_mps')
                or inst['robot_radius'] + 1e-9 < profiles.footprint_radius(p.get('footprint'))):
            raise ServiceError(409, 'vehicle_parameters_mismatch', '方案的容量、速度或足迹与绑定车型不一致，请重新绑定并求解')
        for key in ('kinematics', 'footprint', 'capacity', 'speed_mps', 'min_turn_radius_m'):
            if key in fleet and fleet[key] != p.get(key):
                raise ServiceError(409, 'vehicle_parameters_mismatch', f'车型绑定参数 {key} 与锁定版本不同')
    return {**asset, 'binding': 'pinned_plan_asset' if bound else 'explicit_legacy_builtin',
            'adapter': 'fieldsim-diffdrive', 'generated_body': {'length_m': 0.8, 'width_m': 0.56,
                'overall_width_m': .96, 'collision_hull_m': field_physics.FOOTPRINT, 'model': field_physics.MODEL,
                'wheel_separation_m': 0.66, 'wheel_radius_m': 0.15, 'radius_m': math.hypot(.4, .28)}}
