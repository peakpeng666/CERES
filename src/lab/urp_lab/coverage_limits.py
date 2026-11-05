"""Coverage geometry and resource guards, not geometric repair or feasibility checks.

Counts are independent of area/swath²: a narrow field can have very many rows.
Keep the worker wall-clock limit as an additional, independent safety boundary.
"""
from __future__ import annotations

import math

from shapely.errors import GEOSException
from shapely.geometry import Polygon
from shapely.validation import explain_validity

# Bound GEOS intersections/unions separately from dense native path samples.
# A 20,000-row/segment panel job is already large; one million points permits
# up to 20 km of the existing 2 cm F2C sampling without changing its resolution.
MAX_SCAN_LINES = 20_000
MAX_SWATH_SEGMENTS = 20_000
MAX_PATH_POINTS = 1_000_000


def geometry(field, obstacles, headland):
    """Reject invalid shell/hole combinations before any constructive GEOS call."""
    try:
        area = Polygon(field, obstacles)
        if not area.is_valid or not math.isfinite(area.area) or area.area <= 0:
            raise ValueError('田块与障碍组合多边形无效：' + explain_validity(area))
        # Do not call buffer(0): invalid input must not be silently repaired.
        inside = area if headland == 0 else area.buffer(-headland)
        if (inside.is_empty or inside.geom_type not in ('Polygon', 'MultiPolygon')
                or not inside.is_valid or not math.isfinite(inside.area) or inside.area <= 0):
            raise ValueError('地头宽度过大或障碍导致内缩作业区为空/无效')
        return area, inside
    except GEOSException as error:
        raise ValueError('田块与障碍的作业域几何运算失败，请检查边界和孔洞') from error


def scan_range(lo, hi, step):
    """Check the actual baseline midpoint/step rule without iterating the rows."""
    span = hi - lo
    if not all(math.isfinite(v) for v in (lo, hi, span, step)) or span <= 0 or step <= 0:
        raise ValueError('覆盖扫描范围或幅宽不是有限正数')
    first = lo + min(step / 2, span / 2)
    if not lo < first < hi or first + step <= first or hi + step <= hi:
        raise ValueError('覆盖扫描浮点步进不能前进；请平移坐标原点或调整幅宽')
    ratio = (hi - first) / step
    if not math.isfinite(ratio) or ratio > MAX_SCAN_LINES:
        raise ValueError(f'覆盖扫描行数超出预算 {MAX_SCAN_LINES}；请调整作业方向或幅宽')
    return first, max(1, math.ceil(ratio))


def scan_budget(inside, step, angle_deg=0):
    """Project the actual working polygon perpendicular to the selected swaths.

    The panel always selects angle 0; F2C may select a different angle. Do not
    use the longest edge, bounding-box diagonal, or area as a row count.
    """
    angle = float(angle_deg) % 360
    if angle == 0:
        return scan_range(inside.bounds[1], inside.bounds[3], step)[1]
    radians = math.radians(angle)
    nx, ny = -math.sin(radians), math.cos(radians)
    # Exact cardinal directions avoid spurious projected width for long strips.
    if angle % 90 == 0:
        nx, ny = round(nx), round(ny)
    x0, y0, _, _ = inside.bounds
    polygons = [inside] if inside.geom_type == 'Polygon' else inside.geoms
    values = [nx * (x - x0) + ny * (y - y0)
              for polygon in polygons for x, y in polygon.exterior.coords]
    return scan_range(min(values), max(values), step)[1]


def output_budget(*, segments=0, points=0):
    if segments > MAX_SWATH_SEGMENTS:
        raise ValueError(f'覆盖作业分段数超出预算 {MAX_SWATH_SEGMENTS}，不能截断后作为成功结果')
    if points > MAX_PATH_POINTS:
        raise ValueError(f'覆盖路径点数超出预算 {MAX_PATH_POINTS}，不能抽稀后作为成功结果')
