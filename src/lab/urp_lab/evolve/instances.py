"""Field manifest (development vs sealed test fields) and deterministic instance builders.

Split rule (fixed before any run, docs/持续进化决策与调度.md §6): Dutch fields and ``ee_field_130`` were already
used by earlier studies and are development data; every other Estonian and Lithuanian field is a sealed test
candidate. Sealed builders are disabled in this integration until an independently
validated protocol-authorization gate exists. The historical token helper checks
text only; it is advisory and does not verify a freeze or authorize sealed access.
"""
from __future__ import annotations

import hashlib
import math
import zipfile
from pathlib import Path

from ..library import fields
from . import features as F

CONTAMINATED = {'ee_field_130'}
PROFILES = {  # machine profiles; headland = two passes; turn model fixed per profile (forward-only Dubins)
    'robot_s': {'label': '小型机器人', 'swath_width': 3.0, 'robot_width': 1.2, 'turn_radius': 1.0, 'headland_width': 6.0},
    'tractor_m': {'label': '中型农机（工作台默认）', 'swath_width': 6.0, 'robot_width': 2.5, 'turn_radius': 4.5, 'headland_width': 12.0},
    'sprayer_l': {'label': '大型喷杆', 'swath_width': 12.0, 'robot_width': 3.0, 'turn_radius': 7.0, 'headland_width': 24.0},
}
MIN_INNER_SHARE = 0.2   # geometric admission rule, applied before any solver run


def split_of(field_id: str) -> str:
    return 'dev' if field_id.startswith('nl_') or field_id in CONTAMINATED else 'sealed'


def sealed_token(protocol_path: str | Path) -> str:
    """Legacy advisory digest, NOT proof of protocol approval or permission for sealed access."""
    text = Path(protocol_path).read_text(encoding='utf-8')
    if '状态：FROZEN' not in text and 'Status: FROZEN' not in text:
        raise PermissionError('协议尚未冻结：封存测试田块不可访问')
    return 'FROZEN:' + hashlib.sha256(text.encode('utf-8')).hexdigest()


def _guard(field_id: str, token: str | None):
    if split_of(field_id) == 'sealed':
        raise PermissionError(f'{field_id}: sealed execution disabled pending a verified protocol-authorization gate')


def manifest() -> dict:
    """All fields with country, split and SHA-256 of the original WKT member (no geometry is processed)."""
    zpath = fields.DIR / 'wkt.zip'
    if not zpath.is_file():
        raise FileNotFoundError('Fields2Benchmark 尚未下载（资料 → 真实田块）')
    rows = []
    with zipfile.ZipFile(zpath) as z:
        names = {n.rsplit('/', 1)[-1].rsplit('.', 1)[0]: n for n in z.namelist() if n.lower().endswith('.wkt')}
        for fid in sorted(names, key=lambda k: (k[:2], int(''.join(c for c in k if c.isdigit()) or 0))):
            rows.append({'field': fid, 'country': fid[:2], 'split': split_of(fid),
                         'wkt_sha256': hashlib.sha256(z.read(names[fid])).hexdigest()})
    counts = {}
    for r in rows:
        counts[(r['country'], r['split'])] = counts.get((r['country'], r['split']), 0) + 1
    return {'source': 'Fields2Benchmark (Zenodo 10.5281/zenodo.14524735)',
            'wkt_zip_sha256': hashlib.sha256(zpath.read_bytes()).hexdigest(),
            'rule': '荷兰田块与 ee_field_130 为开发数据；其余爱沙尼亚、立陶宛田块为封存测试候选',
            'counts': {f'{c}/{s}': n for (c, s), n in sorted(counts.items())}, 'fields': rows}


def coverage_instance(field_id: str, profile: str, token: str | None = None) -> tuple[dict, dict]:
    """(scenario, instance) for one field × machine profile, using the library's projected local geometry."""
    _guard(field_id, token)
    spec = PROFILES[profile]
    scenario = fields.scenario(field_id)
    inst = dict(scenario['instance'])
    inst.update({k: spec[k] for k in ('swath_width', 'robot_width', 'turn_radius', 'headland_width')})
    inst.update({'angle_deg': None, 'start': inst['field'][0]})
    scenario['instance'] = inst
    scenario['id'] = f"{scenario['id']}-{profile}"
    scenario['provenance'] = dict(scenario['provenance'], evolve_profile=profile)
    return scenario, inst


def coverage_admission(inst: dict) -> dict:
    """Geometric admission decided before any run: the inner working area must keep ≥ 20 % of the field."""
    feats = F.coverage_features(inst['field'], inst['obstacles'], inst['swath_width'], inst['turn_radius'], inst['headland_width'])
    ok = feats['inner_area_share'] >= MIN_INNER_SHARE and feats['inner_empty'] == 0.0
    return {'admitted': ok, 'inner_area_share': feats['inner_area_share'],
            'reason': None if ok else f"内缩作业区占比 {feats['inner_area_share']:.3f} < {MIN_INNER_SHARE}", 'features': feats}


def fleet_instance(field_id: str, n_tasks: int, distribution: str, travel: str, objective: str, seed: int,
                   multi_trip: bool, token: str | None = None) -> tuple[dict, dict]:
    """(scenario, validated instance) for a multi-vehicle task map inside a real field boundary."""
    _guard(field_id, token)
    from ..compose import generate as gen
    from ..compose.nl import fill_defaults
    from ..scenarios.validate import validate_instance
    pts = fields.scenario(field_id)['instance']['field']
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    outline = [[x - min(xs), y - min(ys)] for x, y in pts]
    vehicles = max(2, round(n_tasks / 20))
    capacity = math.ceil((0.55 if multi_trip else 1.3) * n_tasks / vehicles)
    spec, _ = fill_defaults({'size': [max(xs) - min(xs), max(ys) - min(ys)], 'shape': 'rect', 'tasks': n_tasks,
                             'vehicles': vehicles, 'capacity': capacity, 'plots': 4, 'distribution': distribution,
                             'objective': objective, 'obstacles': {'pond': 1, 'tree': 2}, 'kind': 'sampling'})
    raw = gen.generate(spec, seed, outline)
    raw['vehicles']['travel'] = travel
    inst = validate_instance('fleet_routing', raw)
    key = f'{field_id}|n{n_tasks}|{distribution}|{travel}|{objective}|{"multi" if multi_trip else "single"}|s{seed}'
    scenario = {'id': 'evolve-fleet-' + hashlib.sha256(key.encode()).hexdigest()[:12], 'title': key,
                'problem_class': 'fleet_routing', 'frame': {'kind': 'field', 'units': 'm', 'crs': 'local'}, 'cost_unit': 'm',
                'instance': inst, 'provenance': {'generator': 'urp_lab.evolve.instances.fleet_instance', 'key': key,
                                                 'seed': seed, 'field': field_id, 'spec': spec}}
    return scenario, inst
