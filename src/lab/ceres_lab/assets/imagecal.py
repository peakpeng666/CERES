"""Calibrating a plain image (aerial photo, scanned plan) into local metres.

Pixels are (u right, v down). A calibration maps them to the map frame (x east / right, y north / up):
  - known distance: two picked pixels and their real distance, an origin pixel, optional rotation;
  - control points: ≥ 2 pixel ↔ metre pairs, similarity (scale + rotation + shift, Umeyama) or,
    with ≥ 3 non-collinear points, a least-squares affine. Residuals are reported per point.
A calibrated image is still only a picture: it never becomes passable space unless the user draws
or confirms planning geometry on top of it.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image


def inspect(path: Path) -> dict:
    with Image.open(path) as im:
        info = {'width_px': im.width, 'height_px': im.height, 'format': im.format, 'mode': im.mode}
        dpi = im.info.get('dpi')
        if dpi:
            info['dpi'] = [float(d) for d in dpi]
    return info


def _report(matrix: np.ndarray, P: np.ndarray, W: np.ndarray, model: str) -> dict:
    pred = np.c_[P, np.ones(len(P))] @ matrix.T
    res = np.linalg.norm(pred - W, axis=1)
    lin = matrix[:, :2]
    scale = math.sqrt(abs(float(np.linalg.det(lin))))
    rot = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    out = {'model': model, 'matrix': [[float(v) for v in row] for row in matrix], 'points': int(len(P)),
           'residuals_m': [round(float(r), 4) for r in res], 'rmse_m': round(float(np.sqrt((res ** 2).mean())), 4),
           'scale_m_per_px': scale, 'rotation_deg': round(rot, 3), 'warnings': []}
    if len(P) == 2 and model == 'similarity':
        out['warnings'].append('两点相似变换恰好确定，无法估计误差；建议至少 3 个控制点')
    if len(P) == 3 and model == 'affine':
        out['warnings'].append('三点仿射恰好确定，无法估计误差；建议至少 4 个控制点')
    return out


def solve(points: list[dict], model: str = 'similarity') -> dict:
    P = np.array([p['px'] for p in points], dtype=float)
    W = np.array([p['world'] for p in points], dtype=float)
    if P.ndim != 2 or P.shape[1] != 2 or W.shape != P.shape:
        raise ValueError('控制点格式应为 {px: [u, v], world: [x, y]}')
    if len(P) < 2:
        raise ValueError('至少需要两个控制点')
    if model == 'affine':
        if len(P) < 3:
            raise ValueError('仿射标定至少需要三个控制点')
        A = np.c_[P, np.ones(len(P))]
        if np.linalg.matrix_rank(A) < 3:
            raise ValueError('控制点共线，无法确定仿射变换')
        M, *_ = np.linalg.lstsq(A, W, rcond=None)
        return _report(M.T, P, W, 'affine')
    if model != 'similarity':
        raise ValueError('model 须为 similarity 或 affine')
    Q = P * np.array([1.0, -1.0])          # image v points down; the map's y points up
    mq, mw = Q.mean(0), W.mean(0)
    Qc, Wc = Q - mq, W - mw
    var_q = float((Qc ** 2).sum() / len(P))
    if var_q < 1e-9:
        raise ValueError('控制点重合')
    U, S, Vt = np.linalg.svd(Wc.T @ Qc / len(P))
    d = np.sign(np.linalg.det(U @ Vt)) or 1.0
    D = np.diag([1.0, d])
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / var_q)
    t = mw - s * R @ mq
    matrix = np.c_[s * R[:, 0], -s * R[:, 1], t]
    return _report(matrix, P, W, 'similarity')


def known_distance(p1: list[float], p2: list[float], distance_m: float, origin_px: list[float], rotation_deg: float = 0.0) -> dict:
    d_px = math.dist(p1, p2)
    if d_px < 2:
        raise ValueError('两个参考像素太近')
    if not distance_m or distance_m <= 0:
        raise ValueError('已知距离须为正数（米）')
    s = distance_m / d_px
    a = math.radians(rotation_deg)
    c, sn = math.cos(a), math.sin(a)
    u0, v0 = origin_px
    # world = s · R(a) · (u − u0, −(v − v0))
    matrix = np.array([[s * c, s * sn, -s * c * u0 - s * sn * v0],
                       [s * sn, -s * c, -s * sn * u0 + s * c * v0]])
    out = _report(matrix, np.array([origin_px], float), np.array([[0.0, 0.0]]), 'known_distance')
    out.update(points=2, residuals_m=[], rmse_m=None, warnings=['按已知距离标定：比例来自两点距离，方向按图像上方为 +y（可设旋转）'])
    return out


def apply(matrix: list[list[float]], uv: list[list[float]]) -> list[list[float]]:
    M = np.asarray(matrix)
    return [[round(float(x), 3), round(float(y), 3)] for x, y in np.c_[np.asarray(uv, float), np.ones(len(uv))] @ M.T]
