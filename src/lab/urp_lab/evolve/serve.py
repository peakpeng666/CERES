"""Offline, fail-closed coverage-order inspection; no live recommender integration.

Self-consistent hashes and historical status strings are not approval. An explicit
operator-supplied review identity is required. Only reviewed static experience
orders are supported; learned activation remains disabled pending independently
reviewable training membership and promotion evidence. No approval is configured.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import paths
from . import features as F

MODEL = paths.LAB / 'models' / 'coverage-order-v1.json'
SELECTOR_ID = 'lab.coverage.order.v1'
MACHINE_KEYS = ('swath_width', 'robot_width', 'turn_radius', 'headland_width')


@dataclass(frozen=True)
class ModelApproval:
    """Identity supplied by an independent operator review, never read from the model.

    Constructing this object is not itself proof a review occurred. Production
    code configures no instance; tests use synthetic identities only.
    """
    document_sha256: str
    training_membership_sha256: str
    review_reference: str


def _digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _reject_constant(value):
    raise ValueError('non-finite JSON constant')


@lru_cache(maxsize=4)
def _load(resolved_path: str, content_sha256: str, payload: bytes):
    # Path and bytes participate in cache identity; timestamps are never authority.
    if hashlib.sha256(payload).hexdigest() != content_sha256:
        raise ValueError('document identity mismatch')
    doc = json.loads(payload.decode('utf-8'), parse_constant=_reject_constant)
    artifact_payload = json.dumps(doc['artifact'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(artifact_payload).hexdigest() != doc['artifact_sha256']:
        raise ValueError('模型指纹不匹配')
    return doc


def _reviewed_document(model_path: Path, approval: ModelApproval):
    if not isinstance(approval, ModelApproval) or not _digest(approval.document_sha256) or not _digest(approval.training_membership_sha256) or not approval.review_reference.strip():
        raise ValueError('independent review identity missing')
    path = model_path.resolve()
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != approval.document_sha256:
        raise ValueError('document does not match reviewed identity')
    doc = _load(str(path), digest, payload)
    provenance = doc.get('provenance')
    if not isinstance(provenance, dict) or provenance.get('split') != 'development' or provenance.get('training_membership_sha256') != approval.training_membership_sha256:
        raise ValueError('reviewed development provenance missing or mismatched')
    if doc.get('status') != 'evaluated':
        raise ValueError('model is not evaluated')
    if doc['artifact'].get('kind') != 'static':
        raise ValueError('learned activation disabled pending independently reviewed membership and promotion')
    _validate_static(doc['artifact'])
    return copy.deepcopy(doc)


def _validate_static(art: dict):
    """Reject malformed/non-finite data before arithmetic or rendering."""
    from .plans import COVERAGE_PORTFOLIO
    names = art['features']
    if not names or len(names) != len(set(names)) or not set(names) <= set(F.COVERAGE):
        raise ValueError('unknown or duplicate features')
    if not set(art['log_features']) <= set(names):
        raise ValueError('unknown log features')
    n = len(names)
    vectors = [art['scaler']['mu'], art['scaler']['sd'], art['scope']['feature_min'], art['scope']['feature_max']]
    for vector in vectors:
        if np.asarray(vector).shape != (n,) or not np.isfinite(np.asarray(vector, dtype=float)).all():
            raise ValueError('invalid feature bounds or scaler')
    if np.any(np.asarray(art['scaler']['sd']) <= 0) or np.any(np.asarray(vectors[2]) > np.asarray(vectors[3])):
        raise ValueError('invalid feature interval or scale')
    Z = np.asarray(art['train_Z'], dtype=float)
    if Z.ndim != 2 or Z.shape[0] < 1 or Z.shape[1] != n or not np.isfinite(Z).all():
        raise ValueError('invalid training scope')
    limit = art['scope']['nn_distance_q99']
    if type(limit) not in (int, float) or not math.isfinite(limit) or limit < 0:
        raise ValueError('invalid nearest-distance limit')
    configs = art['configs']
    if not configs or len(configs) != len(set(configs)) or not set(configs) <= set(COVERAGE_PORTFOLIO):
        raise ValueError('unknown or duplicate configurations')
    order = art['static_order']
    if any(type(j) is not int for j in order) or sorted(order) != list(range(len(configs))):
        raise ValueError('static order is not a permutation')
    scores = np.asarray(art['train_rho_mean'], dtype=float)
    if scores.shape != (len(configs),) or not np.isfinite(scores).all() or np.any(scores < 0):
        raise ValueError('invalid expected ratios')
    for name in configs:
        alg, params = art['config_params'][name]
        expected_alg, expected_params = COVERAGE_PORTFOLIO[name]
        if alg != expected_alg or params != expected_params:
            raise ValueError('configuration does not match registered portfolio')
    if not art['profiles']:
        raise ValueError('missing machine profiles')
    for spec in art['profiles'].values():
        if any(type(spec.get(k)) not in (int, float) or not math.isfinite(spec[k]) or spec[k] <= 0 for k in MACHINE_KEYS):
            raise ValueError('invalid machine profile')


def _profile(inst: dict, profiles: dict) -> str | None:
    for name, spec in profiles.items():
        if all(type(inst.get(k)) in (int, float) and math.isfinite(inst[k]) and inst[k] > 0
               and math.isclose(inst[k], spec[k], rel_tol=1e-6) for k in MACHINE_KEYS):
            return name
    return None


def recommend(scenario: dict, instance: dict, *, approval: ModelApproval | None = None, model_path: Path | None = None) -> dict:
    base = {'id': SELECTOR_ID, 'status': 'unavailable', 'prediction': None, 'required': [str(MODEL)],
            'scope': '离线静态经验顺序检查；不是学习模型、部署或封存测试结论'}
    if scenario.get('problem_class') != 'coverage':
        return dict(base, status='not_applicable', reason=None, required=[])
    if approval is None:
        return dict(base, reason='未配置独立审核身份；历史模型不会自动启用')
    selected_path = Path(model_path) if model_path is not None else MODEL
    if not selected_path.is_file():
        return dict(base, reason='覆盖尝试顺序模型尚未导出；当前使用规则')
    try:
        doc = _reviewed_document(selected_path, approval)
        art = doc['artifact']
        base = dict(base, required=[], model_sha256=doc['artifact_sha256'], model_kind='static',
                    review_reference=approval.review_reference)
        if art['feature_version'] != F.VERSION:
            return dict(base, status='out_of_scope', reason=f"模型特征版本 {art['feature_version']} 与当前 {F.VERSION} 不一致；当前使用规则")
        prof = _profile(instance, art['profiles'])
        if prof is None:
            return dict(base, status='out_of_scope', reason='幅宽/机器人宽度/转弯半径/地头宽度不匹配已审核机具配置；当前使用规则')
        feats = F.coverage_features(instance['field'], instance.get('obstacles') or [], instance['swath_width'],
                                    instance['turn_radius'], instance['headland_width'])
        x = np.array([feats[k] for k in art['features']], dtype=float)
        for j, k in enumerate(art['features']):
            if k in art['log_features']:
                x[j] = math.log1p(max(x[j], 0.0))
        z = (x - np.array(art['scaler']['mu'])) / np.array(art['scaler']['sd'])
        Z = np.array(art.get('train_Z') or [])
        nn = float(np.sqrt(((Z - z) ** 2).sum(axis=1)).min()) if len(Z) else None
        lo, hi = np.array(art['scope']['feature_min']), np.array(art['scope']['feature_max'])
        outside = [art['features'][j] for j in range(len(x)) if x[j] < lo[j] - 1e-9 or x[j] > hi[j] + 1e-9]
        if not np.isfinite(x).all() or not np.isfinite(z).all() or nn is None or not math.isfinite(nn):
            return dict(base, status='out_of_scope', reason='模型或实例特征非有限或缺少训练范围；当前使用规则')
        if nn > art['scope']['nn_distance_q99'] or outside:
            return dict(base, status='out_of_scope', reason='田块特征超出已审核范围；静态顺序也不得外推')
        scores = np.array(art['train_rho_mean'])
        order = list(art['static_order'])
        from .plans import config_label
        steps = []
        for j in order:
            name = art['configs'][j]
            alg, params = art['config_params'][name]
            steps.append({'config': name, 'label': config_label(name), 'algorithm_id': alg, 'params': params,
                          'expected_ratio': float(scores[j])})
        what = '经指定身份审核的离线静态经验顺序（非学习模型；未部署）'
        return dict(base, status='available', reason=what + '；不表示封存测试结论，也不会自动求解或重试。',
                    prediction={'order': steps, 'kind': art['kind'], 'profile': prof, 'nearest_distance': nn, 'features': feats},
                    features_outside_range=outside,
                    gate='独立审核身份 + 完整机具配置 + 特征范围门控')
    except Exception as exc:  # noqa: BLE001 - a broken model must never break the recommender
        return dict(base, status='unavailable', reason='覆盖尝试顺序模型不可用：' + str(exc)[:200])
