"""Official TurtleBot4 occupancy maps: metadata, PNG rendering and clearance (distance transform)."""
from __future__ import annotations

import io
import math
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .. import paths

ROBOT_RADIUS_M = 0.175
MAP_CHECK_MARGIN_M = 0.05
UNDOCK_BACKUP_M = 0.35   # 实测 nav-candidate-3：生成 (0,0,0) → 脱离充电座后 (-0.353, 0, 3.133)
DOCK_OFFSET_M = (0.16, 0.35)  # 充电座位于生成朝向前方 0.157 m（turtlebot4_spawn.launch.py），底板至约 0.35 m

# 每个世界的默认生成位姿（Gazebo/launch 参数）；官方 map 帧与 world 帧对齐。
WORLDS = {
    'warehouse': {'title': '仓库 warehouse', 'spawn': None},   # 由 farm-rows-warehouse 的地图分析给出
    'depot': {'title': '车间 depot', 'spawn': {'x': -6.0, 'y': 0.0, 'yaw': 0.0}},
    'maze': {'title': '迷宫 maze', 'spawn': {'x': 0.0, 'y': 0.0, 'yaw': 0.0}},
}


@dataclass(frozen=True)
class MapGrid:
    world: str
    yaml: Path
    image_path: Path
    resolution: float
    origin: tuple
    width: int
    height: int
    pixels: np.ndarray        # 原始灰度（行 0 = y 最大）
    free: np.ndarray          # bool：map_server trinary 判为空闲
    clearance: np.ndarray     # 米：到最近非空闲栅格的保守距离（未知按占据）

    def rc(self, x: float, y: float) -> tuple[int, int]:
        return (self.height - 1 - int(math.floor((y - self.origin[1]) / self.resolution)),
                int(math.floor((x - self.origin[0]) / self.resolution)))

    def xy(self, r: int, c: int) -> tuple[float, float]:
        return (self.origin[0] + (c + 0.5) * self.resolution, self.origin[1] + (self.height - r - 0.5) * self.resolution)

    def clearance_at(self, x: float, y: float) -> float:
        r, c = self.rc(x, y)
        if not (0 <= r < self.height and 0 <= c < self.width):
            return 0.0
        return float(self.clearance[r, c])

    def bounds(self) -> tuple[float, float, float, float]:
        ox, oy = self.origin[0], self.origin[1]
        return ox, oy, ox + self.width * self.resolution, oy + self.height * self.resolution


def _parse_yaml(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if ':' not in line or line.lstrip().startswith('#'):
            continue
        key, value = (s.strip() for s in line.split(':', 1))
        if value.startswith('['):
            out[key] = [float(v) for v in value.strip('[]').split(',')]
        else:
            try:
                out[key] = float(value) if key != 'image' and key != 'mode' else value
            except ValueError:
                out[key] = value
    return out


def known_worlds() -> list[str]:
    return [w for w in WORLDS if (paths.ROS_MAPS / f'{w}.yaml').exists()]


@lru_cache(maxsize=None)
def load(world: str) -> MapGrid:
    if world not in WORLDS:
        raise KeyError(world)
    yaml = paths.ROS_MAPS / f'{world}.yaml'
    if not yaml.exists():
        raise FileNotFoundError(f'缺少官方地图 {yaml}')
    meta = _parse_yaml(yaml)
    image_path = yaml.parent / meta['image']
    pixels = np.array(Image.open(image_path).convert('L'))
    occ = (255.0 - pixels) / 255.0 if not meta.get('negate') else pixels / 255.0
    free = occ < float(meta.get('free_thresh', 0.196))
    res = float(meta['resolution'])
    # Treat the exterior as occupied. Subtract both cell half-diagonals: obstacle
    # square extent and arbitrary query position within its cell.
    clearance = (ndimage.distance_transform_edt(np.pad(free, 1))[1:-1, 1:-1] * res - math.sqrt(2)*res).clip(min=0).astype(np.float32)
    h, w = pixels.shape
    return MapGrid(world, yaml, image_path, res, tuple(meta['origin']), w, h, pixels, free, clearance)


_SIGNATURES = {}


def fingerprint(world: str) -> str:
    """Fingerprint the grid actually used by this process, including trinary interpretation."""
    grid = load(world)
    previous = _SIGNATURES.get(world)
    if previous and previous[0] is grid:
        return previous[1]
    digest = hashlib.sha256(json.dumps({'world': world, 'resolution': grid.resolution,
        'origin': list(grid.origin), 'shape': [grid.height, grid.width],
        'clearance_rule': 'padded occupied exterior; subtract sqrt(2) cells'}, sort_keys=True).encode())
    digest.update(grid.pixels.tobytes())
    digest.update(grid.free.tobytes())
    value = digest.hexdigest()
    _SIGNATURES[world] = (grid, value)
    return value


def world_map(world: str) -> dict:
    g = load(world)
    return {'world': world, 'resolution': g.resolution, 'origin': list(g.origin), 'width': g.width, 'height': g.height,
            'image': f'/lab/api/maps/{world}.png', 'yaml': str(g.yaml), 'title': WORLDS[world]['title'],
            'free_rule': 'map_server trinary：空闲 p<free_thresh；未知按占据处理（间隙计算）',
            'default_spawn': default_spawn(world)}


@lru_cache(maxsize=None)
def map_png(world: str) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(load(world).pixels, mode='L').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def default_spawn(world: str) -> dict | None:
    spawn = WORLDS[world]['spawn']
    if spawn is None and world == 'warehouse':
        from .field import warehouse_layout
        spawn = warehouse_layout()['spawn']
    return dict(spawn) if spawn else None


def start_pose_after_undock(spawn: dict) -> dict:
    """官方 Create3 脱离充电座：沿生成朝向后退约 0.35 m 后原地转 180°。"""
    yaw = spawn['yaw']
    return {'x': round(spawn['x'] - UNDOCK_BACKUP_M * math.cos(yaw), 4),
            'y': round(spawn['y'] - UNDOCK_BACKUP_M * math.sin(yaw), 4),
            'yaw': round(math.atan2(math.sin(yaw + math.pi), math.cos(yaw + math.pi)), 6)}


def densify(points, step: float) -> list[tuple[float, float]]:
    out = []
    pts = [tuple(map(float, p)) for p in points]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(math.dist(a, b) / step)))
        out.extend((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n))
    if pts:
        out.append(pts[-1])
    return out


def segment_clearance(grid: MapGrid, a, b) -> tuple[float, tuple[float, float]]:
    """沿线段按 ≤ 半个栅格采样的最小间隙与位置。"""
    best, where = math.inf, tuple(a)
    for p in densify([a, b], grid.resolution / 2):
        c = grid.clearance_at(*p)
        if c < best:
            best, where = c, p
    # Distance is 1-Lipschitz; cover the unsampled space between samples too.
    return max(0.0, best - grid.resolution/4), where


def dock_footprint(spawn: dict) -> list[list[float]]:
    """绘图用：充电座近似占地（生成朝向前方 0.16–0.35 m，宽 0.36 m）。"""
    yaw = spawn['yaw']
    ux, uy, vx, vy = math.cos(yaw), math.sin(yaw), -math.sin(yaw), math.cos(yaw)
    out = []
    for s, t in ((DOCK_OFFSET_M[0], -0.18), (DOCK_OFFSET_M[1], -0.18), (DOCK_OFFSET_M[1], 0.18), (DOCK_OFFSET_M[0], 0.18)):
        out.append([round(spawn['x'] + s * ux + t * vx, 3), round(spawn['y'] + s * uy + t * vy, 3)])
    return out


def dock_clearance(grid: MapGrid, spawn: dict) -> float:
    poly = dock_footprint(spawn)
    pts = densify(poly + [poly[0]], grid.resolution / 2)
    return min(grid.clearance_at(*p) for p in pts)
