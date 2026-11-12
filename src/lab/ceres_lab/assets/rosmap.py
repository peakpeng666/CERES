"""ROS occupancy-grid maps (map_server YAML + PGM/PNG), read with Nav2 map_server semantics.

Value of a pixel: shade = mean of its colour channels / 255; occ = shade if negate else 1 - shade.
  trinary: occ > occupied_thresh → occupied (100), occ < free_thresh → free (0), else unknown (-1)
  scale:   as trinary, but values in between scale linearly 0..100; transparent pixels are unknown
  raw:     the pixel value itself (0..100), anything else unknown
Image row 0 is the top of the map: pixel (u, v) has its centre at
  map frame (x, y) = origin + R(yaw) · ((u + 0.5)·res, (H − v − 0.5)·res)
so a map drawn with that transform shows exactly what planners see. Many ROS tools ignore yaw;
a non-zero yaw is kept and reported.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

Image.MAX_IMAGE_PIXELS = 120_000_000
MODES = ('trinary', 'scale', 'raw')
COLORS = {0: (246, 246, 244, 255), 100: (44, 46, 52, 255), -1: (196, 201, 210, 255)}


def read_meta(path: Path) -> tuple[dict, list[str], list[str]]:
    """YAML → normalised metadata, errors, warnings."""
    errors, warnings = [], []
    try:
        doc = yaml.safe_load(path.read_text(errors='replace'))
    except yaml.YAMLError as exc:
        return {}, [f'{path.name} 不是有效的 YAML：{exc}'], []
    if not isinstance(doc, dict) or 'image' not in doc:
        return {}, [f'{path.name} 缺少 image 字段（不是 ROS 地图描述）'], []
    meta = {'yaml': path.name, 'image': str(doc.get('image'))}
    try:
        meta['resolution'] = float(doc.get('resolution'))
        if not 0 < meta['resolution'] <= 10:
            errors.append(f'resolution={meta["resolution"]} 超出合理范围（0, 10] m/像素')
    except (TypeError, ValueError):
        errors.append('resolution 缺失或不是数字')
    origin = doc.get('origin')
    if isinstance(origin, (list, tuple)) and len(origin) in (2, 3) and all(isinstance(v, (int, float)) for v in origin):
        meta['origin'] = [float(origin[0]), float(origin[1]), float(origin[2]) if len(origin) == 3 else 0.0]
        if abs(meta['origin'][2]) > 1e-9:
            warnings.append(f'origin 含 yaw={meta["origin"][2]:.3f} rad；很多 ROS 工具忽略 yaw，请确认与规划/仿真一致')
    else:
        errors.append('origin 应为 [x, y, yaw]')
    meta['negate'] = int(doc.get('negate', 0) or 0)
    meta['occupied_thresh'] = float(doc.get('occupied_thresh', 0.65))
    meta['free_thresh'] = float(doc.get('free_thresh', 0.25))
    meta['mode'] = str(doc.get('mode', 'trinary')).lower()
    if meta['mode'] not in MODES:
        errors.append(f'mode={meta["mode"]} 不受支持（trinary / scale / raw）')
    if not 0 <= meta['free_thresh'] < meta['occupied_thresh'] <= 1:
        errors.append('需要 0 ≤ free_thresh < occupied_thresh ≤ 1')
    return meta, errors, warnings


def load_image(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Gray value per pixel (float 0..255) and alpha (or None), image row 0 = top."""
    with Image.open(path) as im:
        im.load()
        if im.mode in ('I;16', 'I;16B', 'I'):
            arr = np.asarray(im, dtype=np.float64)
            peak = 65535.0 if arr.max() > 255 else 255.0
            return arr * (255.0 / peak), None
        if im.mode == 'P':
            im = im.convert('RGBA')
        if im.mode == '1':
            im = im.convert('L')
        arr = np.asarray(im, dtype=np.float64)
    if arr.ndim == 2:
        return arr, None
    alpha = arr[..., 3] if arr.shape[2] in (2, 4) else None
    color = arr[..., :3] if arr.shape[2] >= 3 else arr[..., :1]
    return color.mean(axis=2), alpha


def occupancy(gray: np.ndarray, alpha: np.ndarray | None, meta: dict) -> np.ndarray:
    shade = gray / 255.0
    occ = shade if meta['negate'] else 1.0 - shade
    hi, lo = meta['occupied_thresh'], meta['free_thresh']
    out = np.full(gray.shape, -1, dtype=np.int8)
    if meta['mode'] == 'raw':
        val = np.rint(gray)
        ok = (val >= 0) & (val <= 100)
        out[ok] = val[ok].astype(np.int8)
        return out
    out[occ > hi] = 100
    out[occ < lo] = 0
    if meta['mode'] == 'scale':
        mid = (occ >= lo) & (occ <= hi)
        out[mid] = np.rint((occ[mid] - lo) / (hi - lo) * 100).astype(np.int8)
        if alpha is not None:
            out[alpha < 255] = -1
    return out


def frame(meta: dict, width: int, height: int) -> dict:
    """Pixel-corner affine (u right, v down) → map frame, plus the map's axis-aligned bounds."""
    res = meta['resolution']
    ox, oy, yaw = meta['origin']
    c, s = math.cos(yaw), math.sin(yaw)
    affine = [[c * res, s * res, ox - s * height * res], [s * res, -c * res, oy + c * height * res]]
    corners = [(0, 0), (width, 0), (0, height), (width, height)]
    pts = [(affine[0][0] * u + affine[0][1] * v + affine[0][2], affine[1][0] * u + affine[1][1] * v + affine[1][2]) for u, v in corners]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {'resolution': res, 'origin': [ox, oy, yaw], 'width_px': width, 'height_px': height,
            'width_m': width * res, 'height_m': height * res, 'affine': affine,
            'bounds': [min(xs), min(ys), max(xs), max(ys)], 'units': 'm', 'frame_id': 'map'}


def to_world(points_uv: np.ndarray, affine: list[list[float]]) -> list[list[float]]:
    a = np.asarray(affine)
    uv1 = np.c_[points_uv, np.ones(len(points_uv))]
    return [[round(float(x), 3), round(float(y), 3)] for x, y in (uv1 @ a.T)]


def stats(occ: np.ndarray, res: float) -> dict:
    n = occ.size
    free, occd, unknown = int((occ == 0).sum()), int((occ == 100).sum()), int((occ == -1).sum())
    return {'cells': n, 'free': free, 'occupied': occd, 'unknown': unknown, 'intermediate': n - free - occd - unknown,
            'free_area_m2': round(free * res * res, 2), 'unknown_fraction': round(unknown / n, 4) if n else 0}


def preview_png(occ: np.ndarray) -> bytes:
    rgba = np.zeros(occ.shape + (4,), dtype=np.uint8)
    for value, color in COLORS.items():
        rgba[occ == value] = color
    mid = (occ > 0) & (occ < 100)
    if mid.any():
        g = (246 - occ[mid].astype(np.int32) * 2).clip(44, 246).astype(np.uint8)
        rgba[mid] = np.stack([g, g, g, np.full_like(g, 255)], axis=1)
    buf = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def thumbnail_png(png: bytes, size: int = 360) -> bytes:
    with Image.open(io.BytesIO(png)) as im:
        im.thumbnail((size, size), Image.NEAREST)
        buf = io.BytesIO()
        im.save(buf, 'PNG')
    return buf.getvalue()


def inspect(yaml_path: Path, files_root: Path) -> dict:
    """Everything the import wizard shows; raises nothing (errors are reported)."""
    meta, errors, warnings = read_meta(yaml_path)
    out = {'meta': meta, 'errors': errors, 'warnings': warnings}
    if errors and 'image' not in meta:
        return out
    img = (yaml_path.parent / meta['image']).resolve()
    root = files_root.resolve()
    if root not in img.parents:
        out['errors'].append(f'image 指向导入文件夹之外：{meta["image"]}')
        return out
    if not img.is_file():
        out['errors'].append(f'缺少 YAML 引用的图像文件 {meta["image"]}（请把它与 YAML 一起导入）')
        return out
    try:
        gray, alpha = load_image(img)
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a bad file for the user
        out['errors'].append(f'无法读取图像 {meta["image"]}：{exc}')
        return out
    if out['errors']:
        return out
    occ = occupancy(gray, alpha, meta)
    h, w = occ.shape
    out.update(occ=occ, frame=frame(meta, w, h), stats=stats(occ, meta['resolution']),
               image_rel=img.relative_to(root).as_posix())
    s = out['stats']
    if s['free'] == 0:
        out['errors'].append('地图里没有空闲栅格：检查 negate / 阈值设置')
    if s['unknown_fraction'] > 0.6:
        out['warnings'].append(f'未知区域占 {s["unknown_fraction"]:.0%}')
    if max(w, h) * meta['resolution'] > 5000:
        out['warnings'].append('地图边长超过 5 km，请确认 resolution 的单位是 米/像素')
    return out


def vectorize(occ: np.ndarray, fr: dict, *, unknown_as: str = 'obstacle', min_area_m2: float = 0.05,
              simplify_m: float | None = None, max_vertices: int = 1500) -> dict:
    """Planning polygons from the grid: the largest free region becomes the boundary, the occupied (and,
    by default, unknown) regions it encloses become obstacles. Nothing here is applied until the user confirms."""
    import cv2
    res = fr['resolution']
    passable = (occ == 0) if unknown_as == 'obstacle' else (occ != 100) & (occ < 50)
    mask = passable.astype(np.uint8)
    n, labels, comp_stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    if n <= 1:
        raise ValueError('地图里没有可通行区域')
    biggest = 1 + int(np.argmax(comp_stats[1:, cv2.CC_STAT_AREA]))
    region = (labels == biggest).astype(np.uint8)
    contours, hierarchy = cv2.findContours(region, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError('无法提取可通行区域轮廓')
    outer = max(range(len(contours)), key=lambda i: cv2.contourArea(contours[i]) if hierarchy[0][i][3] < 0 else -1)
    min_px = max(1.0, min_area_m2 / (res * res))
    eps = max(0.5, (simplify_m or res) / res)
    for _ in range(12):
        boundary = cv2.approxPolyDP(contours[outer], eps, True).reshape(-1, 2)
        holes = []
        child = hierarchy[0][outer][2]
        while child >= 0:
            c = contours[child]
            if cv2.contourArea(c) >= min_px or len(c) * 1.0 >= 4 * math.sqrt(min_px):
                poly = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
                if len(poly) >= 3:
                    holes.append(poly)
            child = hierarchy[0][child][0]
        vertices = len(boundary) + sum(len(p) for p in holes)
        if vertices <= max_vertices:
            break
        eps *= 1.6
    # contour points are pixel indices; their centres are at +0.5
    bpoly = to_world(boundary + 0.5, fr['affine'])
    obstacles = [{'polygon': to_world(p + 0.5, fr['affine']), 'area_m2': round(float(cv2.contourArea(p)) * res * res, 3)} for p in holes]
    obstacles = [o for o in obstacles if o['area_m2'] >= min_area_m2 * 0.5]
    return {'boundary': bpoly, 'obstacles': obstacles,
            'stats': {'vertices': len(bpoly) + sum(len(o['polygon']) for o in obstacles), 'obstacles': len(obstacles),
                      'free_area_m2': round(float(region.sum()) * res * res, 2), 'simplify_m': round(eps * res, 4),
                      'unknown_as': unknown_as, 'min_area_m2': min_area_m2,
                      'dropped_free_regions': int(n - 2)}}
