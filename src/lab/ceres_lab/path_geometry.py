"""Ordered locations on a polyline, in metres; no ROS or geometry-library dependency."""
import math


def arc_lengths(points):
    out = [0.0]
    for a, b in zip(points, points[1:]):
        out.append(out[-1] + math.dist(a, b))
    return out


def locate(points, xy, after=0.0, tolerance=1e-5):
    """Earliest occurrence of xy at/after an arc length, including zero-length segments.

    Unlike distance-to-LineString, this preserves visit order on paths that double back.
    Returns None when no such occurrence exists.
    """
    if not points:
        return None
    lengths = arc_lengths(points)
    if len(points) == 1:
        return 0.0 if after <= tolerance and math.dist(points[0], xy) <= tolerance else None
    for i, (a, b) in enumerate(zip(points, points[1:])):
        if lengths[i + 1] < after - tolerance:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        size = lengths[i + 1] - lengths[i]
        if size <= 1e-12:
            if math.dist(a, xy) <= tolerance:
                return max(after, lengths[i])
            continue
        u = ((xy[0] - a[0]) * dx + (xy[1] - a[1]) * dy) / (size * size)
        lo = max(0.0, (after - lengths[i]) / size)
        u = max(lo, min(1.0, u))
        if math.dist((a[0] + u * dx, a[1] + u * dy), xy) <= tolerance:
            return lengths[i] + u * size
    return None
