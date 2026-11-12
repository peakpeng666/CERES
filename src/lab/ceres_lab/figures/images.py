"""Map assets as images / features in task-map metres (for the map → planning-geometry figure)."""
from __future__ import annotations

import json

import numpy as np
from PIL import Image

from ..assets import store


def asset_image(aid: str, v: int, meta: dict):
    """(RGB array, pixel→metre affine) for raster and calibrated image maps; None otherwise."""
    if meta.get('frame') and meta.get('occupancy'):
        try:
            img = Image.open(store.file_path(aid, v, 'derived/preview.png')).convert('RGB')
        except (FileNotFoundError, OSError):
            return None
        return np.asarray(img), meta['frame']['affine']
    cal = meta.get('calibration')
    if cal and meta.get('image'):
        try:
            img = Image.open(store.file_path(aid, v, meta['image'])).convert('RGB')
        except (FileNotFoundError, OSError):
            return None
        img.thumbnail((2400, 2400))
        scale = (meta.get('size_px') or img.size)[0] / img.size[0]
        m = np.asarray(cal['matrix'], float)
        m[:, :2] *= scale                          # thumbnail pixels → original pixels → metres
        return np.asarray(img), m.tolist()
    return None


def vector_features(aid: str, v: int, meta: dict) -> list[dict]:
    try:
        return json.loads(store.file_path(aid, v, meta.get('features') or 'derived/features_local.json').read_text()).get('features', [])
    except (FileNotFoundError, ValueError):
        return []
